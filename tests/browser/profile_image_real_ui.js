/*
 * 真实后端渲染的图片预览与逐张下载回归。
 * 站点指向本机模拟上游（返回真实 60s JSON），组合含两个栏目；预览与生成
 * 图片都走真实插件后端与真实渲染器，不使用 route fixture 伪造 images。
 *
 * ASTRBOT_URL=... ASTRBOT_PASSWORD=... ASTRBOT_PLUGIN=... \
 * CHROME_PATH=... NODE_PATH=<playwright-core> \
 * node tests/browser/profile_image_real_ui.js
 */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const http = require("node:http");
const { chromium } = require("playwright-core");

const BASE = process.env.ASTRBOT_URL;
const PASSWORD = process.env.ASTRBOT_PASSWORD;
const PLUGIN = process.env.ASTRBOT_PLUGIN || "astrbot_plugin_60sapi_hub";
const CHROME = process.env.CHROME_PATH;
if (!BASE || !PASSWORD || !CHROME) {
  console.error("ASTRBOT_URL, ASTRBOT_PASSWORD and CHROME_PATH are required");
  process.exit(2);
}

const WAIT = 60000;
const stamp = Date.now();
const PNG_SIGNATURE = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
let token;
const ids = { site: null, profile: null };
const upstreamCalls = [];
const NEWS_COUNT = 40;
const ZHIHU_COUNT = 45;

function newsBody() {
  return {
    code: 200, message: "ok",
    data: {
      date: "2026-11-06",
      news: Array.from({ length: NEWS_COUNT }, (_, index) =>
        "第" + (index + 1) + "条真实链路新闻标题，用于验证长图纵向排版与中文换行"),
      tip: "示例微语", link: "https://example.test/60s",
    },
  };
}

function zhihuBody() {
  return {
    code: 200, message: "ok",
    data: Array.from({ length: ZHIHU_COUNT }, (_, index) => ({
      title: "知乎热榜第" + (index + 1) + "条标题，检查长图标题换行与分隔线",
      detail: "摘要文本 " + (index + 1) + "，检查灰色摘要折行。",
      hot_value_desc: "热度 " + (2000 + index),
      hot_value: 2000 + index,
      link: "https://example.test/item/" + (index + 1),
    })),
  };
}

const upstream = http.createServer((request, response) => {
  upstreamCalls.push(request.url);
  const body = request.url.endsWith("/v2/60s") ? newsBody() : zhihuBody();
  response.writeHead(200, { "Content-Type": "application/json" });
  response.end(JSON.stringify(body));
});

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

function pngSize(bytes) {
  assert.ok(bytes.subarray(0, 8).equals(PNG_SIGNATURE), "下载内容必须是 PNG");
  return { width: bytes.readUInt32BE(16), height: bytes.readUInt32BE(20) };
}

async function frameOf(page) {
  const iframe = page.locator("iframe.plugin-view-frame").first();
  await iframe.waitFor({ state: "visible" });
  const frame = await (await iframe.elementHandle()).contentFrame();
  assert.ok(frame, "plugin iframe must load");
  await frame.waitForFunction(() => document.getElementById("sites-list"), null, { timeout: WAIT });
  return frame;
}

async function openProfilePreview(page, frame) {
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
  await frame.waitForFunction((id) =>
    [...document.querySelectorAll("#profile-preview-select option")]
      .some((option) => option.value === id), ids.profile, { timeout: WAIT });
  await frame.selectOption("#profile-preview-select", ids.profile);
}

async function openPage(browser, width) {
  const page = await browser.newPage({ viewport: { width, height: 1000 }, acceptDownloads: true });
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
  await openProfilePreview(page, frame);
  return { page, frame };
}

async function readFigures(frame) {
  return frame.evaluate(() => {
    const host = document.getElementById("profile-image-pages");
    return [...host.querySelectorAll("figure")].map((figure) => {
      const image = figure.querySelector("img");
      return {
        status: figure.dataset.status || null,
        hasImage: Boolean(image),
        renderedWidth: image ? Number(image.getAttribute("width")) : null,
        renderedHeight: image ? Number(image.getAttribute("height")) : null,
        naturalWidth: image ? image.naturalWidth : null,
        naturalHeight: image ? image.naturalHeight : null,
        captions: [...figure.querySelectorAll("figcaption")].map((node) => node.textContent),
        buttons: [...figure.querySelectorAll("button")].map((node) => node.textContent),
      };
    });
  });
}

(async () => {
  await new Promise((resolve) => upstream.listen(0, "127.0.0.1", resolve));
  const upstreamPort = upstream.address().port;
  await login();
  const site = await api("POST", "/api/v1/plugins/extensions/" + PLUGIN + "/sites", {
    name: "真实渲染站点" + stamp,
    base_url: "http://127.0.0.1:" + upstreamPort + "/mock",
    enabled: true, timeout_seconds: 20,
  });
  assert.ok([200, 201].includes(site.status), JSON.stringify(site.body));
  ids.site = site.body.data.id;
  const profile = await api("POST", "/api/v1/plugins/extensions/" + PLUGIN + "/profiles", {
    name: "真实渲染组合" + stamp, site_id: ids.site,
    items: [
      { content_type: "daily_news", params: {}, limit: 20 },
      { content_type: "zhihu_hot", params: {}, limit: 20 },
    ],
  });
  assert.ok([200, 201].includes(profile.status), JSON.stringify(profile.body));
  ids.profile = profile.body.data.id;

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
    for (const width of [1440, 390]) {
      const { page, frame } = await openPage(browser, width);
      try {
        await frame.evaluate(() => document.getElementById("profile-preview-button").click());
        await frame.waitForFunction(
          () => !document.getElementById("profile-image-button").disabled,
          null, { timeout: WAIT },
        );
        await frame.evaluate(() => document.getElementById("profile-image-button").click());
        try {
          await frame.waitForFunction(
            () => document.querySelectorAll("#profile-image-pages figure").length === 2,
            null, { timeout: WAIT },
          );
        } catch (error) {
          throw new Error("真实渲染未出图: " + JSON.stringify(await frame.evaluate(() => ({
            state: document.getElementById("profile-image-state").textContent,
            hint: document.getElementById("profile-image-hint").textContent,
            hintHidden: document.getElementById("profile-image-hint").hidden,
            figures: document.querySelectorAll("#profile-image-pages figure").length,
            button: document.getElementById("profile-image-button").textContent,
            previewStatus: document.getElementById("profile-preview-status").textContent,
          }))));
        }
        // 图片是 loading="lazy"：逐张滚入视口后再等解码，才能读到 naturalWidth/Height
        for (let position = 0; position < 2; position += 1) {
          await frame.evaluate((index) => {
            const figure = document.querySelectorAll("#profile-image-pages figure")[index];
            if (figure) figure.scrollIntoView({ block: "center" });
          }, position);
          await frame.waitForFunction((index) => {
            const image = document.querySelectorAll("#profile-image-pages figure")[index]
              ?.querySelector("img");
            return Boolean(image) && image.complete && image.naturalWidth > 0;
          }, position, { timeout: WAIT });
        }

        const figures = await readFigures(frame);
        check(figures.length === 2, "两个栏目必须各有一张长图: " + JSON.stringify(figures));
        check(figures.every((item) => item.hasImage), "每个栏目都必须真实出图");
        check(figures.every((item) => item.status === "success"), "真实渲染应全部成功");
        check(figures[0].captions.join("").includes("每日新闻"), "顺序：第 1 张是每日新闻");
        check(figures[1].captions.join("").includes("知乎热榜"), "顺序：第 2 张是知乎热榜");
        // 宽高必须来自真实渲染：IMG 的显示尺寸、naturalSize、文案三者一致
        for (const item of figures) {
          check(item.renderedWidth === 900, "渲染宽度必须是 900: " + item.renderedWidth);
          check(item.naturalWidth === 900, "图片实际像素宽度必须是 900: " + item.naturalWidth);
          check(item.naturalHeight === item.renderedHeight,
            "文案高度必须等于图片实际像素高度: " + JSON.stringify([item.naturalHeight, item.renderedHeight]));
          check(item.captions.join("").includes(String(item.naturalHeight)),
            "卡片文案必须回显真实高度: " + item.captions.join(""));
          check(item.naturalHeight > 600, "长图高度应随内容增长: " + item.naturalHeight);
        }
        check(figures[0].naturalHeight !== figures[1].naturalHeight,
          "不同栏目内容量不同，高度不应相同: "
          + JSON.stringify(figures.map((item) => item.naturalHeight)));

        // 逐张下载：文件名含 content_type 与序号，内容为真 PNG 且尺寸与展示一致
        for (const [index, expected] of [[0, "daily_news"], [1, "zhihu_hot"]]) {
          const downloadPromise = page.waitForEvent("download", { timeout: WAIT });
          await frame.evaluate((position) => {
            const figure = document.querySelectorAll("#profile-image-pages figure")[position];
            figure.querySelector("button").click();
          }, index);
          const download = await downloadPromise;
          const filename = download.suggestedFilename();
          check(filename.includes(expected), "文件名必须含 content_type: " + filename);
          check(filename.includes("-" + (index + 1) + ".png"), "文件名必须含序号: " + filename);
          const bytes = fs.readFileSync(await download.path());
          const size = pngSize(bytes);
          check(size.width === 900, "下载 PNG 宽度必须是 900: " + size.width);
          check(size.height === figures[index].naturalHeight,
            "下载 PNG 高度必须等于展示高度: " + JSON.stringify([size.height, figures[index].naturalHeight]));
        }

        const state = await frame.evaluate(() => ({
          state: document.getElementById("profile-image-state").textContent,
          panel: document.querySelector(".profile-image-panel").innerText,
        }));
        check(state.state.includes("已生成 2 张栏目长图"), "状态行按张计数: " + state.state);
        check(!state.panel.includes("页"), "面板不出现分页字样");

        if (width === 390) {
          const layout = await page.evaluate(() => ({
            scrollWidth: document.documentElement.scrollWidth,
            clientWidth: document.documentElement.clientWidth,
          }));
          check(layout.scrollWidth <= layout.clientWidth,
            "390px 不得出现横向溢出: " + JSON.stringify(layout));
          const inner = await frame.evaluate(() => ({
            scrollWidth: document.documentElement.scrollWidth,
            clientWidth: document.documentElement.clientWidth,
          }));
          check(inner.scrollWidth <= inner.clientWidth,
            "390px 插件页不得出现横向溢出: " + JSON.stringify(inner));
        }
        report.push({
          width,
          figures: figures.map((item) => ({
            label: item.captions.join(""),
            renderedWidth: item.renderedWidth,
            renderedHeight: item.renderedHeight,
            naturalHeight: item.naturalHeight,
          })),
        });
      } finally {
        await page.close();
      }
    }
    check(upstreamCalls.some((url) => url.endsWith("/v2/60s")), "必须真实请求过模拟上游 /v2/60s");
    check(upstreamCalls.some((url) => url.endsWith("/v2/zhihu")), "必须真实请求过模拟上游 /v2/zhihu");
    console.log("PROFILE IMAGE REAL UI PASS", { assertions, report, upstreamCalls: upstreamCalls.length });
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
    upstream.close();
  }
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
