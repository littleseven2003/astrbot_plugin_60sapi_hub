/*
 * 后台图片预览与逐张下载的可持久浏览器回归。
 * 站点/组合走真实 API；preview 与 image 接口用 route fixture 返回构造好的
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
const profileIds = { first: null, second: null };
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

const routeState = {
  previewData: null,
  imageData: null,
  imageStatus: 200,
  imageGate: null,
  imageCalls: 0,
};

function previewPayload(profileId, profileName, revision) {
  return {
    profile_id: profileId,
    profile_revision: revision,
    profile_name: profileName,
    site_name: "图片回归站点",
    status: "complete",
    success_count: 1,
    failure_count: 0,
    sections: [],
    outputs: { text: "组合正文", markdown: "# 组合正文" },
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

/* ---------- 真实 API：站点与组合 ---------- */
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

async function showProfiles(frame) {
  await frame.waitForFunction(
    () => document.getElementById("sites-list")?.getAttribute("aria-busy") === "false",
    null, { timeout: WAIT },
  );
  // 宿主 iframe 内合成点击偶发被 Dashboard 覆盖层吞掉，这里走同一套 hash 路由。
  await frame.evaluate(() => {
    if (document.getElementById("view-profiles")?.hidden !== false) {
      window.location.hash = "#profiles";
    }
  });
  await frame.waitForFunction(
    () => document.getElementById("view-profiles")?.hidden === false,
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
  await showProfiles(frame);
  return { page, frame };
}

async function selectProfile(frame, profileId) {
  await frame.waitForFunction((id) =>
    [...document.querySelectorAll("#profile-preview-select option")]
      .some((option) => option.value === id), profileId, { timeout: WAIT });
  await frame.selectOption("#profile-preview-select", profileId);
}

async function runPreview(frame) {
  await frame.evaluate(() => document.getElementById("profile-preview-button").click());
  await frame.waitForFunction(
    () => !document.getElementById("profile-image-button").disabled,
    null, { timeout: WAIT },
  );
}

async function runImage(frame) {
  await frame.evaluate(() => document.getElementById("profile-image-button").click());
}

async function figures(frame) {
  return frame.evaluate(() => {
    const host = document.getElementById("profile-image-pages");
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
    state: document.getElementById("profile-image-state").textContent,
    hint: document.getElementById("profile-image-hint").textContent,
    hintHidden: document.getElementById("profile-image-hint").hidden,
    panel: document.querySelector(".profile-image-panel").innerText,
  }));
}

(async () => {
  await login();
  const site = await api("POST", "/api/v1/plugins/extensions/" + PLUGIN + "/sites", {
    name: "图片回归站点" + stamp,
    base_url: "http://127.0.0.1:9/mock-upstream",
    enabled: true, timeout_seconds: 5,
  });
  siteId.value = site.body.data.id;
  const firstProfile = await api("POST", "/api/v1/plugins/extensions/" + PLUGIN + "/profiles", {
    name: "图片回归组合A" + stamp, site_id: siteId.value,
    items: [
      { content_type: "daily_news", params: {}, limit: 20 },
      { content_type: "zhihu_hot", params: {}, limit: 20 },
      { content_type: "bilibili_hot", params: {}, limit: 20 },
    ],
  });
  assert.ok([200, 201].includes(firstProfile.status), JSON.stringify(firstProfile.body));
  profileIds.first = firstProfile.body.data.id;
  const firstRevision = firstProfile.body.data.revision;
  const secondProfile = await api("POST", "/api/v1/plugins/extensions/" + PLUGIN + "/profiles", {
    name: "图片回归组合B" + stamp, site_id: siteId.value,
    items: [{ content_type: "douyin_hot", params: {}, limit: 20 }],
  });
  assert.ok([200, 201].includes(secondProfile.status), JSON.stringify(secondProfile.body));
  profileIds.second = secondProfile.body.data.id;
  const secondRevision = secondProfile.body.data.revision;

  routeState.previewData = previewPayload(profileIds.first, "图片回归组合A" + stamp, firstRevision);
  routeState.imageData = imagesPayload(SECTIONS, []);

  const browser = await chromium.launch({
    headless: true, executablePath: CHROME, args: ["--no-sandbox"],
  });
  let assertions = 0;
  const check = (condition, message) => {
    assertions += 1;
    assert.ok(condition, message);
  };
  try {
    for (const width of [1440, 390]) {
      routeState.imageGate = null;
      routeState.imageStatus = 200;
      routeState.imageData = imagesPayload(SECTIONS, []);
      routeState.previewData = previewPayload(profileIds.first, '图片回归组合A' + stamp, firstRevision);
      const { page, frame } = await openPage(browser, width);
      try {
        await page.route("**/profiles/*/preview", async (route) => {
          await route.fulfill({
            status: 200, contentType: "application/json",
            body: JSON.stringify({ status: "ok", data: routeState.previewData }),
          });
        });
        await page.route("**/profiles/*/image", async (route) => {
          routeState.imageCalls += 1;
          if (routeState.imageGate) await routeState.imageGate.promise;
          await route.fulfill({
            status: routeState.imageStatus, contentType: "application/json",
            body: JSON.stringify(routeState.imageStatus === 200
              ? { status: "ok", data: routeState.imageData }
              : { status: "error", message: "图片生成失败", data: { code: "image_render_failed" } }),
          });
        });

        /* 1) 多栏目顺序：每个成功栏目恰好一张，高度取自接口实际值 */
        await selectProfile(frame, profileIds.first);
        routeState.previewData = previewPayload(profileIds.first, "图片回归组合A" + stamp, firstRevision);
        await runPreview(frame);
        await runImage(frame);
        try {
          await frame.waitForFunction(
            () => document.querySelectorAll("#profile-image-pages figure").length === 3,
            null, { timeout: WAIT },
          );
        } catch (error) {
          throw new Error("首轮图片未渲染: " + JSON.stringify({
            imageCalls: routeState.imageCalls,
            figures: await figures(frame),
            state: await stateText(frame),
            button: await frame.evaluate(() =>
              document.getElementById("profile-image-button").textContent),
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
        await frame.evaluate(() => document.querySelector("#profile-image-pages figure button").click());
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
          () => document.querySelectorAll("#profile-image-pages figure").length === 3
            && document.querySelector('#profile-image-pages figure[data-status="failed"]') !== null,
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
          () => document.getElementById("profile-image-hint")?.hidden === false,
          null, { timeout: WAIT },
        );
        const allFailed = await stateText(frame);
        check((await figures(frame)).length === 0, "全部失败时不得留下图片");
        check(allFailed.hint.includes("图片生成失败"), "全部失败要有明确提示: " + allFailed.hint);
        routeState.imageStatus = 200;
        routeState.imageData = imagesPayload(SECTIONS, []);

        /* 4) 旧响应隔离：延迟旧请求，切换组合后旧响应不得覆盖 */
        routeState.imageGate = deferred();
        await runImage(frame);
        await frame.waitForFunction(
          () => document.getElementById("profile-image-state").textContent.includes("正在生成图片"),
          null, { timeout: WAIT },
        );
        await selectProfile(frame, profileIds.second);
        routeState.previewData = previewPayload(profileIds.second, "图片回归组合B" + stamp, secondRevision);
        await frame.waitForFunction(
          () => document.querySelectorAll("#profile-image-pages figure").length === 0,
          null, { timeout: WAIT },
        );
        routeState.imageGate.resolve();
        routeState.imageGate = null;
        await page.waitForTimeout(400);
        const afterStale = await figures(frame);
        check(afterStale.length === 0, "切换组合后旧响应不得写入: " + JSON.stringify(afterStale));
        check((await stateText(frame)).state === "", "旧响应不得恢复已清空的状态行");

        /* 切换回第一个组合后重新请求，必须拿到新结果（旧响应已失效） */
        routeState.imageData = imagesPayload([SECTIONS[0]], []);
        routeState.previewData = previewPayload("图片回归组合A" + stamp, [SECTIONS[0]]);
        await selectProfile(frame, profileIds.first);
        routeState.previewData = previewPayload(profileIds.first, "图片回归组合A" + stamp, firstRevision);
        await runPreview(frame);
        await runImage(frame);
        await frame.waitForFunction(
          () => document.querySelectorAll("#profile-image-pages figure").length === 1,
          null, { timeout: WAIT },
        );
        const fresh = await figures(frame);
        check(fresh.length === 1 && fresh[0].height === SECTIONS[0].height,
          "重新请求必须展示最新结果");

        /* 5) 关闭/失效：切走组合后预览结果清空，不再展示旧图 */
        await selectProfile(frame, profileIds.second);
        await frame.waitForFunction(
          () => document.querySelectorAll("#profile-image-pages figure").length === 0,
          null, { timeout: WAIT },
        );
        check(true, "切换组合后图片区必须清空");

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
    console.log("PROFILE IMAGE UI PASS", {
      assertions, imageCalls: routeState.imageCalls,
    });
  } finally {
    await browser.close();
    for (const [id, kind] of [
      [profileIds.first, "profiles"], [profileIds.second, "profiles"], [siteId.value, "sites"],
    ]) {
      if (!id) continue;
      const current = await api("GET", "/api/v1/plugins/extensions/" + PLUGIN + "/" + kind + "/" + id);
      if (current.status === 200) {
        await api("POST", "/api/v1/plugins/extensions/" + PLUGIN + "/" + kind + "/" + id + "/delete",
          { expected_revision: current.body.data.revision });
      }
    }
  }
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
