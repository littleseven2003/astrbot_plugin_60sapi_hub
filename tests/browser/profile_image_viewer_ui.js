/*
 * 后台栏目图片预览：多列布局、卡片不被拉齐、放大浮层的浏览器回归。
 *
 * 站点与组合走真实 API；preview / image 接口用 route fixture 返回构造好的
 * 900×H PNG（含一张高图、一张矮图、一个失败栏目），不请求真实上游、不向
 * 真实群聊或私聊发送任何消息。
 *
 * ASTRBOT_URL=... ASTRBOT_PASSWORD=... ASTRBOT_PLUGIN=... \
 * CHROME_PATH=... NODE_PATH=<playwright-core> \
 * node tests/browser/profile_image_viewer_ui.js
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

const WAIT = 30000;
const stamp = Date.now();
const PNG_SIGNATURE = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
let token;
const ids = { site: null, profile: null };
const upstreamCalls = [];

/* ---------- 真实 900×H PNG（不引入额外依赖） ---------- */
function chunk(type, data) {
  const length = Buffer.alloc(4);
  length.writeUInt32BE(data.length, 0);
  const body = Buffer.concat([Buffer.from(type, "ascii"), data]);
  const table = chunk.table || (chunk.table = (() => {
    const values = new Int32Array(256);
    for (let n = 0; n < 256; n += 1) {
      let c = n;
      for (let k = 0; k < 8; k += 1) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
      values[n] = c;
    }
    return values;
  })());
  let crc = -1;
  for (const byte of body) crc = table[(crc ^ byte) & 0xff] ^ (crc >>> 8);
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

/* 同排一高一矮：用于验证 align-items:start（矮卡不得被拉成高卡）。*/
const SECTIONS = [
  { index: 1, content_type: "daily_news", label: "每日新闻", height: 2548, rgb: [48, 52, 59] },
  { index: 2, content_type: "bilibili_hot", label: "Bilibili 热搜", height: 640, rgb: [161, 60, 98] },
];
const FAILED = {
  index: 3, content_type: "toutiao_hot", label: "今日头条热榜",
  status: "failed", width: 0, height: 0, error: "section_too_tall",
};
const PNG_CACHE = {};
function pngFor(section) {
  const key = section.content_type + "-" + section.height;
  if (!PNG_CACHE[key]) PNG_CACHE[key] = makePng(900, section.height, section.rgb);
  return PNG_CACHE[key];
}
function imageEntry(section) {
  return {
    index: section.index,
    content_type: section.content_type,
    label: section.label,
    status: section.status || "success",
    width: 900,
    height: section.height,
    png_base64: pngFor(section).toString("base64"),
  };
}

function previewPayload(profileId, revision) {
  return {
    profile_id: profileId,
    profile_revision: revision,
    profile_name: "图片浮层回归组合" + stamp,
    site_name: "图片浮层回归站点",
    status: "partial",
    success_count: 2,
    failure_count: 1,
    sections: [],
    outputs: { text: "组合正文", markdown: "# 组合正文" },
    image_snapshot: { id: "snapshot-" + stamp },
    failed_sections: [FAILED.label],
  };
}

function imagesPayload() {
  return {
    images: [...SECTIONS.map(imageEntry), FAILED],
    failed_sections: [FAILED.label],
    snapshot_id: "snapshot-" + stamp,
  };
}

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
  await page.goto(BASE + "/#/plugin-view/" + PLUGIN + "/sites", { waitUntil: "domcontentloaded" });
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

async function runPreview(frame) {
  await frame.evaluate(() => document.getElementById("profile-preview-button").click());
  await frame.waitForFunction(
    () => !document.getElementById("profile-image-button").disabled,
    null, { timeout: WAIT },
  );
}

async function runImage(frame) {
  await frame.evaluate(() => document.getElementById("profile-image-button").click());
  await frame.waitForFunction(
    () => document.querySelectorAll("#profile-image-pages figure").length === 3,
    null, { timeout: WAIT },
  );
}

/* 卡片布局：列数由同排卡片的最左偏移推算，高度取实际渲染高度。 */
async function layoutOf(frame) {
  return frame.evaluate(() => {
    const host = document.getElementById("profile-image-pages");
    const figures = [...host.querySelectorAll("figure")];
    const boxes = figures.map((figure) => {
      const rect = figure.getBoundingClientRect();
      const image = figure.querySelector("img");
      return {
        status: figure.dataset.status || null,
        top: Math.round(rect.top),
        left: Math.round(rect.left),
        width: Math.round(rect.width),
        height: Math.round(rect.height),
        imageWidth: image ? Math.round(image.getBoundingClientRect().width) : null,
        hasImage: Boolean(image),
      };
    });
    const rows = new Map();
    for (const box of boxes) {
      if (!rows.has(box.top)) rows.set(box.top, []);
      rows.get(box.top).push(box);
    }
    return {
      boxes,
      columns: Math.max(...[...rows.values()].map((row) => row.length)),
      rows: [...rows.values()].map((row) => row.map((box) => box.status)),
      hostWidth: Math.round(host.getBoundingClientRect().width),
    };
  });
}

async function viewerState(frame) {
  return frame.evaluate(() => {
    const viewer = document.getElementById("profile-image-viewer");
    const image = document.getElementById("profile-image-viewer-img");
    return {
      exists: Boolean(viewer),
      open: Boolean(viewer && viewer.open),
      htmlLocked: document.documentElement.classList.contains("profile-image-viewer-open"),
      title: document.getElementById("profile-image-viewer-title").textContent,
      downloadLabel: document.getElementById("profile-image-viewer-download").textContent,
      imageSrc: image.getAttribute("src") || "",
      imageWidth: Number(image.getAttribute("width")),
      imageHeight: Number(image.getAttribute("height")),
      renderedWidth: image.getBoundingClientRect().width,
      bodyScrollWidth: document.getElementById("profile-image-viewer-body").scrollWidth,
      bodyClientWidth: document.getElementById("profile-image-viewer-body").clientWidth,
      activeId: document.activeElement ? document.activeElement.id : null,
      activeTag: document.activeElement ? document.activeElement.tagName : null,
      activeLabel: document.activeElement ? document.activeElement.getAttribute("aria-label") : null,
      scrollY: Math.round(window.scrollY),
    };
  });
}

(async () => {
  await login();
  const site = await api("POST", "/api/v1/plugins/extensions/" + PLUGIN + "/sites", {
    name: "图片浮层回归站点" + stamp,
    base_url: "http://127.0.0.1:9/mock-upstream",
    enabled: true, timeout_seconds: 5,
  });
  assert.ok([200, 201].includes(site.status), JSON.stringify(site.body));
  ids.site = site.body.data.id;
  const profile = await api("POST", "/api/v1/plugins/extensions/" + PLUGIN + "/profiles", {
    name: "图片浮层回归组合" + stamp, site_id: ids.site,
    items: [
      { content_type: "daily_news", params: {}, limit: 20 },
      { content_type: "bilibili_hot", params: {}, limit: 20 },
      { content_type: "toutiao_hot", params: {}, limit: 20 },
    ],
  });
  assert.ok([200, 201].includes(profile.status), JSON.stringify(profile.body));
  ids.profile = profile.body.data.id;
  const revision = profile.body.data.revision;

  const browser = await chromium.launch({
    headless: true, executablePath: CHROME, args: ["--no-sandbox"],
  });
  let assertions = 0;
  const check = (condition, message) => {
    assertions += 1;
    assert.ok(condition, message);
  };
  const report = [];
  try {
    for (const width of [1400, 390, 1920]) {
      const layoutOnly = width === 1920;
      const { page, frame } = await openPage(browser, width);
      try {
        await page.route("**/profiles/*/preview", async (route) => {
          await route.fulfill({
            status: 200, contentType: "application/json",
            body: JSON.stringify({ status: "ok", data: previewPayload(ids.profile, revision) }),
          });
        });
        await page.route("**/profiles/*/image", async (route) => {
          await route.fulfill({
            status: 200, contentType: "application/json",
            body: JSON.stringify({ status: "ok", data: imagesPayload() }),
          });
        });

        await frame.selectOption("#profile-preview-select", ids.profile);
        await runPreview(frame);
        await runImage(frame);

        /* 1) 列规则：列数由内容区实际宽度决定（minmax(480px, 1fr) + 12px gap） */
        const grid = await layoutOf(frame);
        console.log("LAYOUT", width, JSON.stringify({
          host: grid.hostWidth,
          columns: grid.columns,
          boxes: grid.boxes.map((box) => [
            box.status, box.top, box.left, box.width, box.height, box.imageWidth,
          ]),
        }));
        check(grid.boxes.length === 3, "三个栏目必须有三个卡片: " + JSON.stringify(grid));
        const expectedColumns = Math.max(
          1, Math.min(grid.boxes.length, Math.floor((grid.hostWidth + 12) / (480 + 12))),
        );
        check(grid.columns === expectedColumns,
          width + "px 内容区宽 " + grid.hostWidth + "px 时列数应为 " + expectedColumns
          + ": " + JSON.stringify(grid.rows));
        check(grid.boxes.every((box) => Math.abs(box.width - grid.boxes[0].width) <= 1),
          "同列卡片宽度必须一致: " + JSON.stringify(grid.boxes.map((box) => box.width)));
        check(grid.boxes[0].width >= Math.min(480, grid.hostWidth),
          "卡片宽度必须填满列宽，不再被压到 200px 级别: " + grid.boxes[0].width);
        if (width === 390) {
          check(grid.boxes[0].width <= 390, "390px 卡片宽度不得超出视口: " + grid.boxes[0].width);
        }
        // 图片按列宽等比缩放：宽度必须接近卡片宽（旧版 4 列时只有 239px）。
        const successBoxes = grid.boxes.filter((box) => box.hasImage);
        check(successBoxes.every((box) => box.imageWidth >= box.width - 6),
          "图片实际渲染宽度必须接近卡片宽度: "
          + JSON.stringify(successBoxes.map((box) => [box.imageWidth, box.width])));

        /* 2) 干旱卡与失败卡按自身内容高度：不得被拉伸成同行最高卡片 */
        const failedCard = grid.boxes.find((box) => box.status === "failed");
        check(Boolean(failedCard), "失败栏目必须保留卡片");
        check(failedCard.hasImage === false && failedCard.height < 120,
          "失败卡片必须按自身内容高度，不得变成大空盒: " + JSON.stringify(failedCard));
        const tallestCard = Math.max(...grid.boxes.map((box) => box.height));
        const shortCard = grid.boxes[1];
        check(shortCard.height < tallestCard - 400,
          "矮卡高度必须按自身内容，不得等于行内最高卡片: "
          + JSON.stringify([shortCard.height, tallestCard]));
        if (grid.columns >= 2) {
          const tall = grid.boxes[0];
          check(tall.top === shortCard.top, "两列时高图与矮图必须在同一行: "
            + JSON.stringify([tall.top, shortCard.top]));
          check(failedCard.top > tall.top, "失败卡另起一行: " + JSON.stringify([failedCard.top, tall.top]));
        } else {
          check(grid.boxes[0].top < shortCard.top,
            "单列时卡片逐行排列: " + JSON.stringify([grid.boxes[0].top, shortCard.top]));
        }

        /* 3) 页面与插件页在窄屏无横向溢出 */
        if (width === 390) {
          const outer = await page.evaluate(() => ({
            scrollWidth: document.documentElement.scrollWidth,
            clientWidth: document.documentElement.clientWidth,
          }));
          check(outer.scrollWidth <= outer.clientWidth,
            "390px 宿主页不得横向溢出: " + JSON.stringify(outer));
          const inner = await frame.evaluate(() => ({
            scrollWidth: document.documentElement.scrollWidth,
            clientWidth: document.documentElement.clientWidth,
          }));
          check(inner.scrollWidth <= inner.clientWidth,
            "390px 插件页不得横向溢出: " + JSON.stringify(inner));
        }

        if (layoutOnly) {
          report.push({
            width,
            layoutOnly: true,
            columns: grid.columns,
            cardWidth: grid.boxes[0].width,
            imageWidth: grid.boxes[0].imageWidth,
            cardHeights: grid.boxes.map((box) => box.height),
            rows: grid.rows,
          });
          continue;
        }

        /* 4) 点击图片打开浮层：900px 原图、标题与下载按钮绑定栏目 */
        await frame.evaluate(() => {
          const image = document.querySelectorAll("#profile-image-pages figure img")[0];
          image.setAttribute("data-test-trigger", "image");
          image.click();
        });
        await frame.waitForFunction(
          () => document.getElementById("profile-image-viewer")?.open === true,
          null, { timeout: WAIT },
        );
        let viewer = await viewerState(frame);
        check(viewer.open, "点击图片必须打开浮层");
        check(viewer.htmlLocked, "浮层打开时必须有滚动锁 class");
        check(viewer.imageSrc.startsWith("data:image/png;base64,"), "浮层必须复用同一张 PNG: "
          + viewer.imageSrc.slice(0, 32));
        check(viewer.imageWidth === 900 && viewer.imageHeight === SECTIONS[0].height,
          "浮层图片宽高必须是 900×栏目高度: " + JSON.stringify([viewer.imageWidth, viewer.imageHeight]));
        check(viewer.title.includes(SECTIONS[0].label), "浮层标题必须带栏目名: " + viewer.title);
        check(viewer.downloadLabel.includes(SECTIONS[0].label),
          "浮层下载按钮必须标注栏目名: " + viewer.downloadLabel);
        if (width === 390) {
          check(Math.round(viewer.renderedWidth) === 900,
            "390px 下浮层仍按 900px 原始像素显示: " + viewer.renderedWidth);
          check(viewer.bodyScrollWidth > viewer.bodyClientWidth,
            "390px 下浮层内容区可横向滑动: "
            + JSON.stringify([viewer.bodyScrollWidth, viewer.bodyClientWidth]));
        }

        /* 5) Esc 关闭：焦点回触发图片、滚动锁移除、滚动位置恢复 */
        // 先关闭当前浮层，把页面滚到 400 再打开，关闭后必须回到 400。
        await page.keyboard.press("Escape");
        await frame.waitForFunction(
          () => document.getElementById("profile-image-viewer")?.open === false,
          null, { timeout: WAIT },
        );
        await frame.evaluate(() => window.scrollTo(0, 400));
        await frame.waitForFunction(() => Math.round(window.scrollY) >= 399, null, { timeout: WAIT });
        const beforeEscape = await viewerState(frame);
        check(beforeEscape.scrollY > 0, "插件页应可纵向滚动: " + beforeEscape.scrollY);
        await frame.evaluate(() => {
          document.querySelectorAll("#profile-image-pages figure img")[0].click();
        });
        await frame.waitForFunction(
          () => document.getElementById("profile-image-viewer")?.open === true,
          null, { timeout: WAIT },
        );
        const openedWhileScrolled = await viewerState(frame);
        check(openedWhileScrolled.htmlLocked, "滚动后再打开仍必须加滚动锁");
        await page.keyboard.press("Escape");
        await frame.waitForFunction(
          () => document.getElementById("profile-image-viewer")?.open === false,
          null, { timeout: WAIT },
        );
        viewer = await viewerState(frame);
        check(!viewer.open, "Esc 必须关闭浮层");
        check(!viewer.htmlLocked, "关闭后必须移除滚动锁 class");
        check(viewer.activeTag === "IMG" && viewer.activeLabel === "查看「" + SECTIONS[0].label + "」大图",
          "关闭后焦点必须回到触发图片: " + JSON.stringify([viewer.activeTag, viewer.activeLabel]));
        check(viewer.scrollY === beforeEscape.scrollY,
          "关闭后必须恢复原滚动位置: " + JSON.stringify([viewer.scrollY, beforeEscape.scrollY]));
        check(viewer.imageSrc === "", "关闭后必须清掉浮层图片 src");

        /* 6) 动作栏「查看大图」按钮打开 → 关闭按钮关闭，焦点回到该按钮 */
        await frame.evaluate(() => {
          const actions = document.querySelectorAll("#profile-image-pages .profile-image-actions")[1];
          const view = [...actions.querySelectorAll("button")]
            .find((node) => node.textContent.includes("查看大图"));
          view.setAttribute("data-test-trigger", "button");
          view.click();
        });
        await frame.waitForFunction(
          () => document.getElementById("profile-image-viewer")?.open === true,
          null, { timeout: WAIT },
        );
        viewer = await viewerState(frame);
        check(viewer.title.includes(SECTIONS[1].label), "按钮打开的是对应栏目: " + viewer.title);
        await frame.evaluate(() => document.getElementById("profile-image-viewer-close").click());
        await frame.waitForFunction(
          () => document.getElementById("profile-image-viewer")?.open === false,
          null, { timeout: WAIT },
        );
        viewer = await viewerState(frame);
        check(!viewer.htmlLocked, "关闭按钮后也必须移除滚动锁");
        check(viewer.activeTag === "BUTTON" && viewer.activeLabel === "查看" + SECTIONS[1].label + "大图",
          "关闭后焦点必须回到「查看大图」按钮: " + JSON.stringify([viewer.activeTag, viewer.activeLabel]));

        /* 7) 键盘打开：聚焦图片后 Enter / Space */
        for (const key of ["Enter", " "]) {
          await frame.evaluate(() => {
            const image = document.querySelectorAll("#profile-image-pages figure img")[0];
            image.focus();
          });
          await page.keyboard.press(key);
          await frame.waitForFunction(
            () => document.getElementById("profile-image-viewer")?.open === true,
            null, { timeout: WAIT },
          );
          viewer = await viewerState(frame);
          check(viewer.open && viewer.title.includes(SECTIONS[0].label),
            "键盘 " + JSON.stringify(key) + " 必须能打开浮层");
          await page.keyboard.press("Escape");
          await frame.waitForFunction(
            () => document.getElementById("profile-image-viewer")?.open === false,
            null, { timeout: WAIT },
          );
        }

        /* 8) 点遮罩关闭：原生 dialog 的 backdrop 点击目标是 dialog 自身 */
        await frame.evaluate(() => {
          document.querySelectorAll("#profile-image-pages figure img")[0].click();
        });
        await frame.waitForFunction(
          () => document.getElementById("profile-image-viewer")?.open === true,
          null, { timeout: WAIT },
        );
        /* 8b) 真鼠标点遮罩：先确认该点由 dialog 接收，再点击；>=2 次才算不稳定。 */
        let backdropRetries = 0;
        let backdropHit = null;
        for (let attempt = 0; attempt < 2; attempt += 1) {
          const iframeBox = await page.evaluate(() => {
            const element = document.querySelector("iframe.plugin-view-frame");
            const rect = element.getBoundingClientRect();
            return { left: rect.left, top: rect.top, width: rect.width, height: rect.height };
          });
          const dialogBox = await frame.evaluate(() => {
            const rect = document.getElementById("profile-image-viewer").getBoundingClientRect();
            return { left: rect.left, right: rect.right, top: rect.top, bottom: rect.bottom };
          });
          check(dialogBox.left >= 2, "dialog 左侧必须留出遮罩边距: " + dialogBox.left);
          const point = {
            x: Math.max(2, Math.floor(dialogBox.left / 2)),
            y: Math.min(300, Math.floor(iframeBox.height / 2)),
          };
          backdropHit = await frame.evaluate(({ x, y }) => {
            const element = document.elementFromPoint(x, y);
            return element ? { tag: element.tagName, id: element.id || null } : null;
          }, point);
          check(backdropHit && backdropHit.id === "profile-image-viewer",
            "遮罩区域的命中目标必须是 dialog 本身: " + JSON.stringify(backdropHit));
          await page.mouse.click(iframeBox.left + point.x, iframeBox.top + point.y);
          const closed = await frame.evaluate(async () => {
            for (let tick = 0; tick < 20; tick += 1) {
              if (!document.getElementById("profile-image-viewer").open) return true;
              await new Promise((done) => requestAnimationFrame(done));
            }
            return !document.getElementById("profile-image-viewer").open;
          });
          if (closed) break;
          backdropRetries += 1;
        }
        check(backdropRetries < 2, "遮罩点击必须稳定关闭浮层，实测需要 " + backdropRetries + " 次重试");
        check(backdropRetries <= 1, "遮罩点击不应依赖重试: " + backdropRetries);
        report.push({ width, backdropRetries });
        await frame.waitForFunction(
          () => document.getElementById("profile-image-viewer")?.open === false,
          null, { timeout: WAIT },
        );
        viewer = await viewerState(frame);
        check(!viewer.open && !viewer.htmlLocked, "点遮罩必须关闭浮层并解锁滚动");

        /* 9) 浮层内下载：文件名含 content_type 与序号，PNG 头与尺寸正确 */
        await frame.evaluate(() => {
          document.querySelectorAll("#profile-image-pages figure img")[0].click();
        });
        await frame.waitForFunction(
          () => document.getElementById("profile-image-viewer")?.open === true,
          null, { timeout: WAIT },
        );
        const downloadPromise = page.waitForEvent("download", { timeout: WAIT });
        await frame.evaluate(() => document.getElementById("profile-image-viewer-download").click());
        const download = await downloadPromise;
        const filename = download.suggestedFilename();
        check(filename.includes(SECTIONS[0].content_type), "浮层下载文件名必须含 content_type: " + filename);
        check(filename.includes("-1.png"), "浮层下载文件名必须含序号: " + filename);
        const bytes = fs.readFileSync(await download.path());
        check(bytes.subarray(0, 8).equals(PNG_SIGNATURE), "浮层下载内容必须是 PNG");
        check(bytes.readUInt32BE(16) === 900 && bytes.readUInt32BE(20) === SECTIONS[0].height,
          "浮层下载 PNG 尺寸必须是 900×" + SECTIONS[0].height
          + ": " + JSON.stringify([bytes.readUInt32BE(16), bytes.readUInt32BE(20)]));
        await page.keyboard.press("Escape");
        await frame.waitForFunction(
          () => document.getElementById("profile-image-viewer")?.open === false,
          null, { timeout: WAIT },
        );

        /* 10) 重新生成图片时浮层与滚动锁必须被清理 */
        await frame.evaluate(() => {
          document.querySelectorAll("#profile-image-pages figure img")[0].click();
        });
        await frame.waitForFunction(
          () => document.getElementById("profile-image-viewer")?.open === true,
          null, { timeout: WAIT },
        );
        await frame.evaluate(() => document.getElementById("profile-image-button").click());
        await frame.waitForFunction(
          () => document.getElementById("profile-image-viewer")?.open === false,
          null, { timeout: WAIT },
        );
        viewer = await viewerState(frame);
        check(!viewer.open, "重新生成图片后浮层必须收起");
        check(!viewer.htmlLocked, "重新生成图片后不得残留滚动锁");
        check(viewer.imageSrc === "", "重新生成图片后不得残留旧图 src");
        check(viewer.activeTag !== "IMG", "浮层因刷新收起时焦点不得留在被移除的图片上: "
          + JSON.stringify([viewer.activeTag, viewer.activeLabel]));
        const leftovers = await frame.evaluate(() => ({
          dialogs: document.querySelectorAll("dialog#profile-image-viewer").length,
          figureCount: document.querySelectorAll("#profile-image-pages figure").length,
          lockClass: document.documentElement.classList.contains("profile-image-viewer-open"),
        }));
        check(leftovers.dialogs === 1, "浮层节点不得重复创建: " + leftovers.dialogs);
        check(leftovers.figureCount === 3, "重新生成后仍是三个卡片: " + leftovers.figureCount);
        check(!leftovers.lockClass, "不得残留滚动锁");

        /* 11) 失败卡片不提供放大入口 */
        const failedButtons = await frame.evaluate(() => {
          const failed = document.querySelector('#profile-image-pages figure[data-status="failed"]');
          return failed ? [...failed.querySelectorAll("button")].map((node) => node.textContent) : ["missing"];
        });
        check(failedButtons.length === 0, "失败卡片不得有放大或下载按钮: " + JSON.stringify(failedButtons));

        report.push({
          width,
          columns: grid.columns,
          cardWidth: grid.boxes[0].width,
          imageWidth: grid.boxes[0].imageWidth,
          cardHeights: grid.boxes.map((box) => box.height),
          rows: grid.rows,
        });
      } finally {
        await page.unrouteAll({ behavior: "ignoreErrors" });
        await page.close();
      }
    }

    const external = upstreamCalls.filter((url) => !url.startsWith(BASE)
      && !url.startsWith("https://cloud.astrbot.app/"));
    check(external.length === 0, "不得请求外部地址: " + JSON.stringify(external));
    const upstreamHits = upstreamCalls.filter((url) => url.includes("127.0.0.1:9"));
    check(upstreamHits.length === 0, "不得请求真实上游: " + JSON.stringify(upstreamHits));
    console.log("PROFILE IMAGE VIEWER UI PASS", { assertions, report });
  } finally {
    await browser.close();
    for (const [id, kind] of [[ids.profile, "profiles"], [ids.site, "sites"]]) {
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
