/*
 * 后台图片预览与逐张下载的可持久浏览器回归。
 * 站点/任务走真实 API；preview 与 image 接口用 route fixture 返回构造好的
 * images（一个栏目一张长图），不请求真实上游、不连真实群聊/私聊。
 *
 * ASTRBOT_URL=... ASTRBOT_PASSWORD=... ASTRBOT_PLUGIN=... \
 * CHROME_PATH=... NODE_PATH=<playwright-core> \
 * node tests/browser/profile_image_ui.js
 */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const zlib = require("node:zlib");
const { chromium } = require("playwright-core");

const BASE = process.env.ASTRBOT_URL;
const PASSWORD = process.env.ASTRBOT_PASSWORD;
const PLUGIN = process.env.ASTRBOT_PLUGIN || "astrbot_plugin_60sapi_hub";
const CHROME = process.env.CHROME_PATH;
if (!BASE || !PASSWORD || !CHROME) {
  console.error("ASTRBOT_URL, ASTRBOT_PASSWORD and CHROME_PATH are required");
  process.exit(2);
}

const WAIT = 20000;
const stamp = Date.now();
let token;
const siteId = { value: null };
const taskIds = { first: null, second: null };
const upstreamCalls = [];

/* ---------- 构造真实的 900×H PNG（不引入额外依赖） ---------- */
const PNG_SIGNATURE = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);

function chunk(type, data) {
  const length = Buffer.alloc(4);
  length.writeUInt32BE(data.length, 0);
  const body = Buffer.concat([Buffer.from(type, "ascii"), data]);
  const crcTable = chunk.table || (chunk.table = (() => {
    const table = new Int32Array(256);
    for (let n = 0; n < 256; n += 1) {
      let c = n;
      for (let k = 0; k < 8; k += 1) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
      table[n] = c;
    }
    return table;
  })());
  let crc = -1;
  for (const byte of body) crc = crcTable[(crc ^ byte) & 0xff] ^ (crc >>> 8);
  const crcBuffer = Buffer.alloc(4);
  crcBuffer.writeUInt32BE((crc ^ -1) >>> 0, 0);
  return Buffer.concat([length, body, crcBuffer]);
}

function makePng(width, height, rgb) {
  const header = Buffer.alloc(13);
  header.writeUInt32BE(width, 0);
  header.writeUInt32BE(height, 4);
  header[8] = 8;
  header[9] = 2;
  const row = Buffer.alloc(1 + width * 3);
  for (let x = 0; x < width; x += 1) {
    row[1 + x * 3] = rgb[0];
    row[2 + x * 3] = rgb[1];
    row[3 + x * 3] = rgb[2];
  }
  const raw = Buffer.concat(Array.from({ length: height }, () => row));
  return Buffer.concat([
    PNG_SIGNATURE,
    chunk("IHDR", header),
    chunk("IDAT", zlib.deflateSync(raw, { level: 9 })),
    chunk("IEND", Buffer.alloc(0)),
  ]);
}

const SECTIONS = [
  { index: 1, content_type: "daily_news", label: "每日新闻", height: 1528, rgb: [36, 50, 74] },
  { index: 2, content_type: "zhihu_hot", label: "知乎热榜", height: 2548, rgb: [49, 95, 200] },
  { index: 3, content_type: "bilibili_hot", label: "Bilibili 热搜", height: 640, rgb: [108, 125, 148], status: "empty" },
];
const PNG_CACHE = {};
function pngFor(section) {
  const key = section.content_type + "-" + section.height;
  if (!PNG_CACHE[key]) PNG_CACHE[key] = makePng(900, section.height, section.rgb);
  return PNG_CACHE[key];
}
function imageEntry(section) {
  const status = section.status || "success";
  return {
    index: section.index,
    content_type: section.content_type,
    label: section.label,
    status,
    width: 900,
    height: section.height,
    png_base64: pngFor(section).toString("base64"),
  };
}

/* ---------- 可控响应（route fixture + 门闩） ---------- */
function deferred() {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
}

async function waitUntil(predicate, message, timeoutMs) {
  const deadline = Date.now() + (timeoutMs || WAIT);
  while (Date.now() < deadline) {
    if (predicate()) return;
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  throw new Error(message || "等待超时");
}

const routeState = {
  previewData: null,
  imageData: null,
  imageStatus: 200,
  // 按请求顺序排队的放行门闩：每次图片请求取走队首，没有门闩就立刻放行。
  imageGates: [],
  imageCalls: 0,
  // 被拦截的图片响应真正交回浏览器的次数（route.fulfill 完成后自增）。
  imageFulfilled: 0,
  // 页面里 apiPost 图片请求 promise 已结算的次数（由页面内探针维护）。
  imageSettledInPage: 0,
};

/*
 * ---------- 旧响应的确定性等待 ----------
 * 页面里的失效判断（views/sites/app.js 的 generatePreviewImage：requestId/snapshotId/taskId
 * 不一致就 return，且 finally 也被 requestId 拦住）不产生任何 DOM 变化，所以“页面已处理完旧响应”
 * 只能从执行层取证，分三步：
 *   1. route.fulfill 完成 → routeState.imageFulfilled 增长（旧请求确实交回了浏览器）；
 *   2. 页面内 apiPost 的 promise 结算 → __hubImageProbe.settled 增长（页面确实拿到了响应）；
 *   3. 让出两帧 → 页面的 await 续体（失效判断 + finally）已执行完，再读 DOM。
 * 任何一步超时都抛带上下文的错误，不用固定毫秒兜底。
 */
async function installImageSettleProbe(frame) {
  const result = await frame.evaluate(() => {
    const host = window.AstrBotPluginView;
    if (!host || typeof host.apiPost !== "function") {
      return "页面没有 window.AstrBotPluginView.apiPost";
    }
    if (host.__hubImageProbe) return "ok";
    const original = host.apiPost;
    const probe = { settled: 0, endpoint: null };
    const wrapped = function (endpoint, ...rest) {
      const call = original.call(this, endpoint, ...rest);
      if (!/\/image$/.test(String(endpoint || ""))) return call;
      const mark = () => {
        probe.settled += 1;
        probe.endpoint = String(endpoint);
      };
      return Promise.resolve(call).then(
        (value) => { mark(); return value; },
        (error) => { mark(); throw error; },
      );
    };
    try {
      host.apiPost = wrapped;
    } catch (error) {
      return "apiPost 不可写: " + error.message;
    }
    if (host.apiPost !== wrapped) return "apiPost 不可写（赋值被忽略）";
    host.__hubImageProbe = probe;
    return "ok";
  });
  if (result !== "ok") {
    throw new Error("无法安装图片响应完成探针: " + result);
  }
}

async function pageImageSettled(frame) {
  return frame.evaluate(() => window.AstrBotPluginView?.__hubImageProbe?.settled ?? -1);
}

// 1) 旧请求确实完成：route.fulfill 之后计数必须增长。
async function waitForImageFulfilled(before) {
  const message = "旧请求未完成：拦住的旧图片请求在 " + WAIT + "ms 内没有走完 route.fulfill";
  try {
    await waitUntil(() => routeState.imageFulfilled > before, message);
  } catch {
    throw new Error(message + JSON.stringify({
      before,
      imageFulfilled: routeState.imageFulfilled,
      imageCalls: routeState.imageCalls,
      settledInPage: routeState.imageSettledInPage,
      gated: routeState.imageGates.length > 0,
    }));
  }
}

// 2) 页面拿到了这次响应：页面内 apiPost 的 promise 已结算。
async function waitForPageImageHandled(frame, before) {
  const deadline = Date.now() + WAIT;
  let settled = null;
  while (Date.now() < deadline) {
    settled = await pageImageSettled(frame);
    if (settled < 0) {
      throw new Error("页面未处理完：页面里没有图片请求探针"
        + "（window.AstrBotPluginView.__hubImageProbe）");
    }
    if (settled > before) {
      routeState.imageSettledInPage = settled;
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 20));
  }
  throw new Error("页面未处理完：旧响应交回后，页面内图片请求 promise 在 " + WAIT
    + "ms 内没有结算" + JSON.stringify({ before, settled, imageFulfilled: routeState.imageFulfilled }));
}

// 3) 让出两帧：microtask 队列（页面的 await 续体）已在读 DOM 前跑完。
async function waitForFrameDrain(frame) {
  const message = "页面未处理完：旧响应结算后 " + WAIT + "ms 内没有完成两帧渲染";
  let timer;
  const drained = frame.evaluate(() => new Promise((resolve) => {
    requestAnimationFrame(() => requestAnimationFrame(() => resolve(true)));
  }));
  try {
    const result = await Promise.race([
      drained,
      new Promise((resolve) => { timer = setTimeout(() => resolve("timeout"), WAIT); }),
    ]);
    if (result === "timeout") throw new Error(message);
  } finally {
    clearTimeout(timer);
  }
}

// 挂一个门闩，拦住下一张发往 /image 的请求。
function holdImageRequest() {
  const gate = deferred();
  routeState.imageGates.push(gate);
  return gate;
}

// 放行门闩，并按上面的三步等页面真正处理完这次响应。
async function releaseImageRequest(frame, gate) {
  const before = {
    fulfilled: routeState.imageFulfilled,
    settled: await pageImageSettled(frame),
  };
  if (before.settled < 0) {
    throw new Error("页面未处理完：页面里没有图片请求探针"
      + "（window.AstrBotPluginView.__hubImageProbe）");
  }
  gate.resolve();
  await waitForImageFulfilled(before.fulfilled);
  await waitForPageImageHandled(frame, before.settled);
  await waitForFrameDrain(frame);
}

function previewPayload(taskId, taskName, revision) {
  return {
    task_id: taskId,
    task_revision: revision,
    task_name: taskName,
    items: [{ content_type: "daily_news", label: "每日新闻", limit: null }],
    site_name: "图片回归站点",
    status: "complete",
    success_count: 1,
    failure_count: 0,
    sections: [],
    outputs: { text: "任务正文", markdown: "# 任务正文" },
    image_snapshot: { id: "snapshot-" + stamp },
    failed_sections: [],
  };
}

function imagesPayload(sections, failedLabels) {
  return {
    images: sections.map(imageEntry),
    failed_sections: failedLabels || [],
    snapshot_id: "snapshot-" + stamp,
  };
}

/* ---------- 真实 API：站点与任务 ---------- */
async function api(method, path, data) {
  const response = await fetch(BASE + path, {
    method,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: "Bearer " + token } : {}),
    },
    body: data ? JSON.stringify(data) : undefined,
  });
  return { status: response.status, body: await response.json().catch(() => ({})) };
}

async function login() {
  const result = await api("POST", "/api/v1/auth/login", {
    username: "astrbot", password: PASSWORD,
  });
  token = result.body.data.token;
}

async function frameOf(page) {
  const iframe = page.locator("iframe.plugin-view-frame").first();
  await iframe.waitFor({ state: "visible" });
  const frame = await (await iframe.elementHandle()).contentFrame();
  assert.ok(frame, "plugin iframe must load");
  await frame.waitForFunction(() => document.getElementById("sites-list"), null, { timeout: WAIT });
  return frame;
}

async function showTasks(frame) {
  await frame.waitForFunction(
    () => document.getElementById("sites-list")?.getAttribute("aria-busy") === "false",
    null, { timeout: WAIT },
  );
  // 宿主 iframe 内合成点击偶发被 Dashboard 覆盖层吞掉，这里走同一套 hash 路由。
  await frame.evaluate(() => {
    if (document.getElementById("view-tasks")?.hidden !== false) {
      window.location.hash = "#tasks";
    }
  });
  await frame.waitForFunction(
    () => document.getElementById("view-tasks")?.hidden === false,
    null, { timeout: WAIT },
  );
}

async function openPage(browser, width) {
  const page = await browser.newPage({ viewport: { width, height: 1000 }, acceptDownloads: true });
  page.on("request", (request) => {
    if (!request.url().startsWith(BASE)) upstreamCalls.push(request.url());
  });
  await page.goto(BASE + "/", { waitUntil: "domcontentloaded" });
  await page.fill("input[name=username]", "astrbot");
  await page.fill("input[name=password]", PASSWORD);
  await Promise.all([
    page.waitForURL((url) => !url.toString().includes("/auth/login")),
    page.getByRole("button", { name: "登录", exact: true }).click(),
  ]);
  await page.goto(BASE + "/#/plugin-view/" + PLUGIN + "/sites", {
    waitUntil: "domcontentloaded",
  });
  await page.waitForTimeout(300);
  await page.evaluate(() => {
    for (const node of document.querySelectorAll(".v-overlay__scrim,.v-overlay-container")) {
      node.remove();
    }
  });
  const frame = await frameOf(page);
  await showTasks(frame);
  return { page, frame };
}

// 打开任务卡片上的“预览内容”：预览按已保存的任务配置发起，拿到快照后才能生成长图。
async function openTask(frame, taskId) {
  const card = frame.locator(`#tasks-list .task-card[data-task-id="${taskId}"]`).first();
  await card.waitFor({ state: "visible", timeout: WAIT });
  await card.locator(".task-card-actions button").first().click({ timeout: WAIT });
  await frame.waitForFunction(
    () => !document.getElementById("task-preview-image-button").disabled,
    null, { timeout: WAIT },
  );
}

async function runImage(frame) {
  // 真实 locator 点击：按钮 disabled 时 Playwright 会等它可用，不做 DOM click 绕过。
  await frame.locator("#task-preview-image-button").click({ timeout: WAIT });
}

// 用真实 locator 点应用里的关闭按钮，并等对话框确实关闭（关闭后的 <dialog> 是 display:none）。
async function closePreview(frame) {
  await frame.locator("#task-preview-close-button").click({ timeout: WAIT });
  await frame.locator("#task-preview-dialog").waitFor({ state: "hidden", timeout: WAIT });
  await frame.waitForFunction(
    () => !document.getElementById("task-preview-dialog")?.open,
    null, { timeout: WAIT },
  );
}

// 读图片区的忙状态：状态行文案 + 图片按钮可用性，用来判断“旧响应有没有清掉当前忙状态”。
async function imageBusy(frame) {
  return frame.evaluate(() => {
    const button = document.getElementById("task-preview-image-button");
    return {
      state: document.getElementById("task-preview-image-state").textContent,
      buttonText: button ? button.textContent : null,
      buttonDisabled: button ? button.disabled : null,
      figures: document.querySelectorAll("#task-preview-image-pages figure").length,
    };
  });
}

async function figures(frame) {
  return frame.evaluate(() => {
    const host = document.getElementById("task-preview-image-pages");
    return [...host.querySelectorAll("figure")].map((figure) => {
      const image = figure.querySelector("img");
      const captions = [...figure.querySelectorAll("figcaption")].map((node) => node.textContent);
      return {
        status: figure.dataset.status || null,
        hasImage: Boolean(image),
        width: image ? Number(image.getAttribute("width")) : null,
        height: image ? Number(image.getAttribute("height")) : null,
        alt: image ? image.getAttribute("alt") : null,
        captions,
        buttons: [...figure.querySelectorAll("button")].map((node) => node.textContent),
      };
    });
  });
}

async function stateText(frame) {
  return frame.evaluate(() => ({
    state: document.getElementById("task-preview-image-state").textContent,
    hint: document.getElementById("task-preview-image-hint").textContent,
    hintHidden: document.getElementById("task-preview-image-hint").hidden,
    panel: document.querySelector(".preview-image-panel").innerText,
  }));
}

(async () => {
  let assertions = 0;
  const check = (condition, message) => {
    assertions += 1;
    assert.ok(condition, message);
  };
  // 夹具 id 在每条创建成功的瞬间记录，后续断言失败也能按 id 清掉已建资源。
  const created = [];
  let browser = null;
  let firstRevision = null;
  let secondRevision = null;
  let passReport = null;
  let workError = null;
  let cleanupProblems = [];

  const briefBody = (body) => {
    const text = JSON.stringify(body === undefined ? null : body);
    return text.length > 300 ? text.slice(0, 300) + "…" : text;
  };

  // 清理逐条核对：POST delete 必须 200 且 body.status==='ok'，删除后 GET 必须 404。
  // 已不存在的资源不算失败，但要留下可诊断的记录。
  const reapFixtures = async () => {
    const problems = [];
    for (const { kind, id, label } of created.slice().reverse()) {
      const path = "/api/v1/plugins/extensions/" + PLUGIN + "/" + kind + "/" + id;
      try {
        const live = await api("GET", path);
        if (live.status === 404) {
          console.log("  · 夹具" + label + "（" + kind + "/" + id + "）已不存在，无需删除");
          continue;
        }
        if (live.status !== 200) {
          problems.push({
            step: "删除前读取夹具" + label, kind, id,
            status: live.status, body: briefBody(live.body),
          });
          continue;
        }
        const removed = await api("POST", path + "/delete", {
          expected_revision: live.body && live.body.data ? live.body.data.revision : undefined,
        });
        if (removed.status !== 200 || !removed.body || removed.body.status !== "ok") {
          problems.push({
            step: "删除夹具" + label, kind, id,
            status: removed.status, body: briefBody(removed.body),
          });
          continue;
        }
        const gone = await api("GET", path);
        if (gone.status !== 404) {
          problems.push({
            step: "删除后复核夹具" + label, kind, id,
            expect: 404, status: gone.status, body: briefBody(gone.body),
          });
        }
      } catch (error) {
        problems.push({
          step: "清理夹具" + label, kind, id,
          error: String(error && error.message || error),
        });
      }
    }
    return problems;
  };

  try {
    await login();
    const site = await api("POST", "/api/v1/plugins/extensions/" + PLUGIN + "/sites", {
      name: "图片回归站点" + stamp,
      base_url: "http://127.0.0.1:9/mock-upstream",
      enabled: true, timeout_seconds: 5,
    });
    check((site.status === 200 || site.status === 201)
      && Boolean(site.body && site.body.data && site.body.data.id),
    "站点创建失败: " + JSON.stringify({ status: site.status, body: site.body }));
    siteId.value = site.body.data.id;
    created.push({ kind: "sites", id: siteId.value, label: "站点" });
    const firstTask = await api("POST", "/api/v1/plugins/extensions/" + PLUGIN + "/tasks", {
      name: "图片回归任务A" + stamp,
      items: [
        { content_type: "daily_news", limit: 20 },
        { content_type: "zhihu_hot", limit: 20 },
        { content_type: "bilibili_hot", limit: 20 },
      ],
      sites: [siteId.value],
      output_format: "text",
      umos: [],
    });
    assert.ok([200, 201].includes(firstTask.status), JSON.stringify(firstTask.body));
    taskIds.first = firstTask.body.data.id;
    firstRevision = firstTask.body.data.revision;
    created.push({ kind: "tasks", id: taskIds.first, label: "任务A" });
    const secondTask = await api("POST", "/api/v1/plugins/extensions/" + PLUGIN + "/tasks", {
      name: "图片回归任务B" + stamp,
      items: [{ content_type: "douyin_hot", limit: 20 }],
      sites: [siteId.value],
      output_format: "text",
      umos: [],
    });
    assert.ok([200, 201].includes(secondTask.status), JSON.stringify(secondTask.body));
    taskIds.second = secondTask.body.data.id;
    secondRevision = secondTask.body.data.revision;
    created.push({ kind: "tasks", id: taskIds.second, label: "任务B" });

    routeState.previewData = previewPayload(taskIds.first, "图片回归任务A" + stamp, firstRevision);
    routeState.imageData = imagesPayload(SECTIONS, []);

    browser = await chromium.launch({
      headless: true, executablePath: CHROME, args: ["--no-sandbox"],
    });
    for (const width of [1440, 390]) {
      for (const gate of routeState.imageGates.splice(0)) gate.resolve();
      routeState.imageStatus = 200;
      routeState.imageData = imagesPayload(SECTIONS, []);
      routeState.previewData = previewPayload(taskIds.first, '图片回归任务A' + stamp, firstRevision);
      const { page, frame } = await openPage(browser, width);
      try {
        await installImageSettleProbe(frame);
        await page.route("**/tasks/*/preview", async (route) => {
          await route.fulfill({
            status: 200, contentType: "application/json",
            body: JSON.stringify({ status: "ok", data: routeState.previewData }),
          });
        });
        await page.route("**/tasks/*/image", async (route) => {
          routeState.imageCalls += 1;
          const gate = routeState.imageGates.shift();
          if (gate) await gate.promise;
          await route.fulfill({
            status: routeState.imageStatus, contentType: "application/json",
            body: JSON.stringify(routeState.imageStatus === 200
              ? { status: "ok", data: routeState.imageData }
              : { status: "error", message: "图片生成失败", data: { code: "image_render_failed" } }),
          });
          // 响应已交回浏览器，旧请求的完成信号。
          routeState.imageFulfilled += 1;
        });

        /* 1) 多栏目顺序：每个成功栏目恰好一张，高度取自接口实际值 */
        routeState.previewData = previewPayload(taskIds.first, "图片回归任务A" + stamp, firstRevision);
        await openTask(frame, taskIds.first);
        await runImage(frame);
        try {
          await frame.waitForFunction(
            () => document.querySelectorAll("#task-preview-image-pages figure").length === 3,
            null, { timeout: WAIT },
          );
        } catch (error) {
          throw new Error("首轮图片未渲染: " + JSON.stringify({
            imageCalls: routeState.imageCalls,
            figures: await figures(frame),
            state: await stateText(frame),
            button: await frame.evaluate(() =>
              document.getElementById("task-preview-image-button").textContent),
          }));
        }
        const happy = await figures(frame);
        check(happy.length === SECTIONS.length, "三个栏目必须各有卡片: " + JSON.stringify(happy));
        check(happy.every((item) => item.hasImage), "success/empty 栏目都必须出图");
        check(happy.filter((item) => item.status !== "failed").length === 3,
          "每个成功栏目恰好一张图");
        check(JSON.stringify(happy.map((item) => item.height))
          === JSON.stringify(SECTIONS.map((item) => item.height)),
          "高度必须取自接口实际值: " + JSON.stringify(happy.map((item) => item.height)));
        check(happy.every((item) => item.width === 900), "宽度固定 900");
        check(happy.map((item) => item.captions.join("")).every((text, index) =>
          text.includes(SECTIONS[index].label) && text.includes(String(SECTIONS[index].height))),
          "卡片顺序与文案必须对应配置顺序");
        const happyState = await stateText(frame);
        check(happyState.state.includes("已生成 3 张栏目长图"), "状态文案按张计数: " + happyState.state);
        check(!happyState.panel.includes("页"), "面板不出现分页字样");
        if (width === 390) {
          const scrollable = await frame.evaluate(() => ({
            scrollHeight: document.documentElement.scrollHeight,
            innerHeight: window.innerHeight,
          }));
          check(scrollable.scrollHeight > scrollable.innerHeight,
            "390px 插件页必须可纵向滚动: " + JSON.stringify(scrollable));
        }

        /* 2) 逐张下载：文件名含 content_type 与序号，内容为 PNG */
        const downloadPromise = page.waitForEvent("download", { timeout: WAIT });
        await frame.evaluate(() => document.querySelector("#task-preview-image-pages figure button").click());
        const download = await downloadPromise;
        const filename = download.suggestedFilename();
        check(filename.includes("daily_news"), "文件名必须含 content_type: " + filename);
        check(filename.includes("-1.png"), "文件名必须含展示序号: " + filename);
        const savedPath = await download.path();
        const bytes = fs.readFileSync(savedPath);
        check(bytes.subarray(0, 8).equals(PNG_SIGNATURE), "下载内容必须是 PNG");
        check(bytes.length === pngFor(SECTIONS[0]).length, "下载内容与接口负载一致");
        check(happy[0].buttons.join("").includes("每日新闻"), "下载按钮标注栏目名");

        /* 3a) 部分失败：失败栏目有提示、无图、无下载按钮 */
        routeState.imageData = imagesPayload([
          SECTIONS[0], SECTIONS[1],
          { ...SECTIONS[2], index: 3, content_type: "toutiao_hot", label: "今日头条热榜" },
        ], []);
        routeState.imageData.images[2] = {
          index: 3, content_type: "toutiao_hot", label: "今日头条热榜",
          status: "failed", width: 0, height: 0, error: "section_too_tall",
        };
        routeState.imageData.failed_sections = ["今日头条热榜"];
        await runImage(frame);
        await frame.waitForFunction(
          () => document.querySelectorAll("#task-preview-image-pages figure").length === 3
            && document.querySelector('#task-preview-image-pages figure[data-status="failed"]') !== null,
          null, { timeout: WAIT },
        );
        const partial = await figures(frame);
        const failedCard = partial.find((item) => item.status === "failed");
        check(Boolean(failedCard), "失败栏目必须可辨识");
        check(failedCard.hasImage === false, "失败栏目不得当作图片");
        check(failedCard.buttons.length === 0, "失败栏目不得有下载按钮");
        check(failedCard.captions.join("").includes("未生成"), "失败栏目要有明确提示");
        check(failedCard.captions.join("").includes("section_too_tall"), "失败原因要展示");
        const partialState = await stateText(frame);
        check(partialState.state.includes("2 张栏目长图"), "部分失败仍按实际张数计数");
        check(partialState.state.includes("1 个栏目未生成"), "部分失败要在状态行提示");

        /* 3b) 全部失败：不生成成功图片 */
        routeState.imageStatus = 422;
        await runImage(frame);
        await frame.waitForFunction(
          () => document.getElementById("task-preview-image-hint")?.hidden === false,
          null, { timeout: WAIT },
        );
        const allFailed = await stateText(frame);
        check((await figures(frame)).length === 0, "全部失败时不得留下图片");
        check(allFailed.hint.includes("图片生成失败"), "全部失败要有明确提示: " + allFailed.hint);
        routeState.imageStatus = 200;
        routeState.imageData = imagesPayload(SECTIONS, []);

        /*
         * 4) 旧响应隔离：任务 A 的图片请求在途时，用 Playwright locator 点真实关闭按钮关预览，
         *    再打开任务 B；B 自己的图片请求也在途时放行 A 的旧响应——
         *    旧响应既不得写入结果区，也不得清掉任务 B 当前的“正在生成图片…”忙状态。
         */
        const staleGate = holdImageRequest();
        const staleCallsBefore = routeState.imageCalls;
        await runImage(frame);
        await frame.waitForFunction(
          () => document.getElementById("task-preview-image-state").textContent.includes("正在生成图片"),
          null, { timeout: WAIT },
        );
        await waitUntil(() => routeState.imageCalls > staleCallsBefore,
          "旧图片请求未在关闭预览前发出");
        check(routeState.imageCalls > staleCallsBefore, "关闭预览前旧图片请求必须已发出");
        const staleBusy = await imageBusy(frame);
        check(staleBusy.buttonDisabled === true && staleBusy.buttonText.includes("正在生成"),
          "旧请求在途时图片按钮必须处于忙状态: " + JSON.stringify(staleBusy));

        /* 走应用里真实的关闭控件（locator 点击，走可操作性检查）：关闭对话框并清空图片面板，此时旧请求仍在途 */
        await closePreview(frame);
        const closedPreview = await frame.evaluate(() => ({
          dialogOpen: Boolean(document.getElementById("task-preview-dialog")?.open),
          figures: document.querySelectorAll("#task-preview-image-pages figure").length,
          images: document.querySelectorAll("#task-preview-image-pages img").length,
          state: document.getElementById("task-preview-image-state").textContent,
          stateHidden: document.getElementById("task-preview-image-state").hidden,
          hintHidden: document.getElementById("task-preview-image-hint").hidden,
          panel: document.querySelector(".preview-image-panel").innerText,
        }));
        check(closedPreview.dialogOpen === false, "关闭预览后对话框必须关闭");
        check(closedPreview.figures === 0 && closedPreview.images === 0,
          "关闭预览后图片区必须清空: " + JSON.stringify(closedPreview));
        check(closedPreview.state === "" && closedPreview.stateHidden,
          "关闭预览后图片状态行必须清空: " + JSON.stringify(closedPreview));
        check(closedPreview.hintHidden && !closedPreview.panel.includes("正在生成图片"),
          "关闭预览后不得残留生成中提示: " + JSON.stringify(closedPreview));

        /* 关闭之后才通过任务卡片的预览按钮打开任务 B */
        routeState.previewData = previewPayload(taskIds.second, "图片回归任务B" + stamp, secondRevision);
        await openTask(frame, taskIds.second);
        const reopenedPreview = await frame.evaluate(() => ({
          dialogOpen: Boolean(document.getElementById("task-preview-dialog")?.open),
          title: document.getElementById("task-preview-title").textContent,
        }));
        check(reopenedPreview.dialogOpen === true, "关闭后重新打开预览，对话框必须再次打开");
        check(reopenedPreview.title.includes("图片回归任务B" + stamp),
          "重新打开后必须预览任务 B: " + JSON.stringify(reopenedPreview));
        await frame.waitForFunction(
          () => document.querySelectorAll("#task-preview-image-pages figure").length === 0,
          null, { timeout: WAIT },
        );
        /* 任务 B 自己的图片请求也进门闩：此刻的忙状态属于 B，不属于 A */
        const freshGate = holdImageRequest();
        const freshCallsBefore = routeState.imageCalls;
        await runImage(frame);
        await frame.waitForFunction(
          () => document.getElementById("task-preview-image-state").textContent.includes("正在生成图片"),
          null, { timeout: WAIT },
        );
        await waitUntil(() => routeState.imageCalls > freshCallsBefore, "任务 B 的图片请求未发出");
        check(routeState.imageCalls > freshCallsBefore, "任务 B 的图片请求必须已发出");

        /* 放行 A 的旧响应：页面必须把它当废响应丢掉，既不写结果也不清忙状态 */
        await releaseImageRequest(frame, staleGate);
        const afterStale = await figures(frame);
        check(afterStale.length === 0, "切换任务后旧响应不得写入: " + JSON.stringify(afterStale));
        const busyAfterStale = await imageBusy(frame);
        check(busyAfterStale.state.includes("正在生成图片"),
          "旧响应的 finally 不得清掉任务 B 的忙状态行: " + JSON.stringify(busyAfterStale));
        check(busyAfterStale.buttonDisabled === true,
          "旧响应结算后任务 B 的图片按钮必须仍然禁用: " + JSON.stringify(busyAfterStale));
        const staleImagePanel = await frame.evaluate(() =>
          document.querySelector(".preview-image-panel").innerText);
        check(!staleImagePanel.includes("每日新闻") && !staleImagePanel.includes("Bilibili 热搜"),
          "旧任务 A 的长图不得出现在任务 B 的预览里: " + staleImagePanel.slice(0, 300));

        /* 放行 B 自己的请求：必须正常写入结果并结束忙状态 */
        await releaseImageRequest(frame, freshGate);
        await frame.waitForFunction(
          () => document.querySelectorAll("#task-preview-image-pages figure").length === 3,
          null, { timeout: WAIT },
        );
        const freshB = await figures(frame);
        check(freshB.length === 3,
          "任务 B 自己的响应必须正常写入: " + JSON.stringify(freshB.map((item) => item.status)));
        const freshBState = await imageBusy(frame);
        check(freshBState.state.includes("已生成 3 张栏目长图"),
          "任务 B 完成后状态行必须按张计数: " + JSON.stringify(freshBState));
        check(freshBState.buttonDisabled === false,
          "任务 B 完成后图片按钮必须恢复可用: " + JSON.stringify(freshBState));

        /* 切换回第一个任务后重新请求，必须拿到新结果（旧响应已失效） */
        routeState.imageData = imagesPayload([SECTIONS[0]], []);
        routeState.previewData = previewPayload(taskIds.first, "图片回归任务A" + stamp, firstRevision);
        await openTask(frame, taskIds.first);
        await runImage(frame);
        await frame.waitForFunction(
          () => document.querySelectorAll("#task-preview-image-pages figure").length === 1,
          null, { timeout: WAIT },
        );
        const fresh = await figures(frame);
        check(fresh.length === 1 && fresh[0].height === SECTIONS[0].height,
          "重新请求必须展示最新结果");

        /* 5) 关闭/失效：切走任务后预览结果清空，不再展示旧图 */
        routeState.previewData = previewPayload(taskIds.second, "图片回归任务B" + stamp, secondRevision);
        await openTask(frame, taskIds.second);
        await frame.waitForFunction(
          () => document.querySelectorAll("#task-preview-image-pages figure").length === 0,
          null, { timeout: WAIT },
        );
        check(true, "切换任务后图片区必须清空");

        if (width === 390) {
          const layout = await page.evaluate(() => ({
            scrollWidth: document.documentElement.scrollWidth,
            clientWidth: document.documentElement.clientWidth,
            scrollHeight: document.documentElement.scrollHeight,
            innerHeight: window.innerHeight,
          }));
          check(layout.scrollWidth <= layout.clientWidth,
            "390px 不得出现横向溢出: " + JSON.stringify(layout));
        }
      } finally {
        await page.unrouteAll({ behavior: "ignoreErrors" });
        await page.close();
      }
    }

    // 宿主自身会请求公告接口，这里只约束插件：不得访问真实上游或外部服务。
    const external = upstreamCalls.filter((url) => !url.startsWith(BASE)
      && !url.startsWith("https://cloud.astrbot.app/"));
    check(external.length === 0, "不得请求外部地址: " + JSON.stringify(external));
    const upstreamHits = upstreamCalls.filter((url) => url.includes("127.0.0.1:9"));
    check(upstreamHits.length === 0, "不得请求真实上游: " + JSON.stringify(upstreamHits));
    passReport = {
      assertions,
      imageCalls: routeState.imageCalls,
      imageFulfilled: routeState.imageFulfilled,
      imageSettledInPage: routeState.imageSettledInPage,
    };
  } catch (error) {
    workError = error;
  } finally {
    if (browser) {
      try {
        await browser.close();
      } catch (error) {
        cleanupProblems.push({ step: "关闭浏览器", error: String(error && error.message || error) });
      }
    }
    try {
      cleanupProblems = cleanupProblems.concat(await reapFixtures());
    } catch (error) {
      cleanupProblems.push({ step: "清理夹具", error: String(error && error.message || error) });
    }
  }

  // PASS 只在夹具删除、DELETE 响应与 404 复核全部通过之后打印。
  if (workError) {
    console.error(workError);
    process.exitCode = 1;
  }
  if (cleanupProblems.length > 0) {
    console.error("CLEANUP FAILED：夹具清理未通过（" + cleanupProblems.length + " 项）");
    for (const problem of cleanupProblems) console.error("  - " + JSON.stringify(problem));
    process.exitCode = 1;
  }
  if (workError || cleanupProblems.length > 0) return;
  console.log("PROFILE IMAGE UI PASS", {
    ...passReport,
    fixturesCleaned: created.length,
  });
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
