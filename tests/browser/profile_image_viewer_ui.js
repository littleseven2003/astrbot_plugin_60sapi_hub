/*
 * 任务“预览内容”的栏目长图与查看器回归。
 *
 * 验证：长图走 POST tasks/<id>/image 且只带快照 id；每栏目一图、图注含尺寸与状态；
 * 查看器标题/尺寸/复制下载文案、原始尺寸与适应宽度切换、Esc 与背板关闭后回焦、
 * 关闭后清空图片地址；重新获取预览会作废旧长图。
 *
 * 桩 bridge 的页面逻辑验证，不代表真实渲染出的长图内容。
 */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const http = require("node:http");
const path = require("node:path");
const { chromium } = require("playwright-core");

function resolveChrome() {
  const configured = process.env.CHROME_PATH;
  if (configured) return configured;
  try {
    return chromium.executablePath();
  } catch {
    return "";
  }
}

const CHROME = resolveChrome();
if (!CHROME || !fs.existsSync(CHROME)) {
  console.error("找不到可用的 Chromium：请设置 CHROME_PATH。");
  process.exit(2);
}

const VIEWS_DIR = path.resolve(__dirname, "..", "..", "views", "sites");
const MIME = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
};

function serve() {
  const server = http.createServer((request, response) => {
    const url = new URL(request.url, "http://127.0.0.1");
    const relative = url.pathname === "/" ? "/index.html" : url.pathname;
    const target = path.join(VIEWS_DIR, relative);
    if (!target.startsWith(VIEWS_DIR) || !fs.existsSync(target)) {
      response.writeHead(404).end("not found");
      return;
    }
    response.writeHead(200, {
      "Content-Type": MIME[path.extname(target)] || "application/octet-stream",
    });
    response.end(fs.readFileSync(target));
  });
  return new Promise((resolve) => {
    server.listen(0, "127.0.0.1", () => resolve({ server, port: server.address().port }));
  });
}

let assertions = 0;
function check(condition, message) {
  assertions += 1;
  assert.ok(condition, message);
}

const PNG_A = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==";
const PNG_B = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==";

const SITES = [
  { id: "site-a", name: "站点A", base_url: "https://a.example.test/n", enabled: true,
    timeout_seconds: 10, source_kind: "public", has_bearer_token: false, revision: 2,
    updated_at: "" },
];

const TASKS = [
  {
    id: "task-a", name: "任务A",
    items: [
      { content_type: "daily_news", label: "每日新闻", limit: 2 },
      { content_type: "zhihu_hot", label: "知乎热榜", limit: null },
    ],
    output_format: "text", output_label: "纯文字",
    umos: ["aiocqhttp:GroupMessage:g:1"], enabled: true, revision: 3,
    created_at: "", updated_at: "",
    schedule: null, schedule_raw: null, schedule_error: null,
    sites: [{ id: "site-a", name: "站点A", enabled: true }],
  },
];

function previewPayload() {
  return {
    task_id: "task-a", task_name: "任务A", task_revision: 3,
    items: TASKS[0].items.map((item) => ({ ...item })),
    site_id: "site-a", site_name: "站点A",
    success_count: 2, failure_count: 0,
    sections: [
      { content_type: "daily_news", label: "每日新闻", endpoint: "/60s?format=json",
        items: [{ title: "标题一" }], limit: 2, item_count: 1, total_item_count: 1,
        displayed_count: 1, cache: { hit: false, expires_at: null, ttl_seconds: 300 },
        status: "success", source_display: "a.example.test", source_kind: "public" },
      { content_type: "zhihu_hot", label: "知乎热榜", endpoint: "/zhihu?format=json",
        items: [{ title: "标题二" }], limit: null, item_count: 1, total_item_count: 1,
        displayed_count: 1, cache: { hit: true, expires_at: null, ttl_seconds: 300 },
        status: "success", source_display: "自建 60s 服务", source_kind: "self_built" },
    ],
    failed_sections: [],
    status: "complete",
    outputs: { text: "预览正文", markdown: "**预览正文**" },
    image_snapshot: { id: "snap-9", expires_at: "" },
  };
}

async function openPage(browser, port) {
  const page = await browser.newPage({
    viewport: { width: 1440, height: 900 },
    acceptDownloads: true,
  });
  await page.addInitScript((fixtures) => {
    window.__calls = [];
    window.AstrBotPluginView = {
      ready: async () => ({ pageTitle: "60sAPI Hub" }),
      apiGet: async (endpoint) => {
        if (endpoint === "sites") return { sites: fixtures.sites };
        if (endpoint === "tasks") return { tasks: fixtures.tasks };
        if (endpoint === "schedule-runs") return { claims: [], skips: [] };
        if (endpoint === "executions") return { executions: [], total: 0 };
        return {};
      },
      apiPost: async (endpoint, payload) => {
        window.__calls.push({ endpoint, payload });
        if (endpoint.endsWith("/preview")) return fixtures.preview;
        if (endpoint.endsWith("/image")) {
          return {
            images: [
              { index: 1, content_type: "daily_news", label: "每日新闻",
                status: "success", png_base64: fixtures.pngA,
                width: 200, height: 300, error: null },
              { index: 2, content_type: "zhihu_hot", label: "知乎热榜",
                status: "success", png_base64: fixtures.pngB,
                width: 240, height: 360, error: null },
            ],
          };
        }
        return {};
      },
    };
  }, { sites: SITES, tasks: TASKS, preview: previewPayload(), pngA: PNG_A, pngB: PNG_B });
  await page.goto("http://127.0.0.1:" + port + "/index.html", { waitUntil: "domcontentloaded" });
  return page;
}

(async () => {
  const { server, port } = await serve();
  const browser = await chromium.launch({ executablePath: CHROME, headless: true });
  try {
    const page = await openPage(browser, port);
    page.on("pageerror", (error) => console.error("PAGEERROR", error.message));
    await page.click('.nav-item[data-view="tasks"]');
    await page.waitForSelector("#tasks-list li");
    await page.locator('li[data-task-id="task-a"] button', { hasText: "预览内容" }).click();
    await page.waitForSelector("#task-preview-dialog[open]");
    await page.waitForFunction(() =>
      !document.getElementById("task-preview-image-button").disabled);

    await page.click("#task-preview-image-button");
    await page.waitForSelector("#task-preview-image-pages .preview-image-page img");
    const imageCall = await page.evaluate(() =>
      window.__calls.filter((call) => call.endpoint.endsWith("/image")).at(-1));
    check(JSON.stringify(imageCall.payload) === JSON.stringify({ snapshot_id: "snap-9" }),
      "长图请求只带快照 id：" + JSON.stringify(imageCall.payload));

    const captions = await page.$$eval("#task-preview-image-pages figcaption",
      (nodes) => nodes.map((node) => node.textContent));
    check(captions.length === 2, "每个栏目一张长图：" + captions.length);
    check(captions[0] === "1. 每日新闻 · 200×300 · 已生成", "第一张图注：" + captions[0]);
    check(captions[1] === "2. 知乎热榜 · 240×360 · 已生成", "第二张图注：" + captions[1]);
    check((await page.textContent("#task-preview-image-state")) === "已生成 2 张栏目长图",
      "长图状态文案正确");
    const firstImage = page.locator("#task-preview-image-pages .preview-image-page img").first();
    check(await firstImage.getAttribute("role") === "button", "长图可点击放大");
    check(await firstImage.getAttribute("aria-label") === "查看「每日新闻」大图",
      "长图有无障碍名称");
    check(await firstImage.getAttribute("width") === "200", "长图按后端尺寸渲染");

    await firstImage.click();
    await page.waitForSelector("#preview-image-viewer[open]");
    check((await page.textContent("#preview-image-viewer-title")).trim() === "1. 每日新闻",
      "查看器标题带序号与栏目名");
    const viewerImage = await page.$eval("#preview-image-viewer-img", (node) => ({
      src: node.getAttribute("src"),
      alt: node.alt,
      width: node.getAttribute("width"),
      height: node.getAttribute("height"),
    }));
    check(viewerImage.src === "data:image/png;base64," + PNG_A, "查看器加载同一张长图");
    check(viewerImage.alt === "每日新闻栏目长图（大图）", "查看器图片说明：" + viewerImage.alt);
    check(viewerImage.width === "200" && viewerImage.height === "300", "查看器保持原始尺寸");
    check(await page.evaluate(() =>
      document.documentElement.classList.contains("preview-image-viewer-open")),
    "查看器打开时锁定页面滚动");

    const zoom = await page.$eval("#preview-image-viewer-zoom", (node) => ({
      text: node.textContent.trim(),
      pressed: node.getAttribute("aria-pressed"),
    }));
    check(zoom.text === "原始尺寸" && zoom.pressed === "false",
      "缩放按钮初始为适应宽度：" + JSON.stringify(zoom));
    check((await page.textContent("#preview-image-viewer-hint")).trim() ===
      "已按屏幕宽度显示完整长图；需要细节时点“原始尺寸”。", "默认提示说明适应宽度");
    await page.click("#preview-image-viewer-zoom");
    const zoomed = await page.$eval("#preview-image-viewer-zoom", (node) => ({
      text: node.textContent.trim(),
      pressed: node.getAttribute("aria-pressed"),
    }));
    check(zoomed.text === "适应宽度" && zoomed.pressed === "true",
      "再次点击切到原始尺寸：" + JSON.stringify(zoomed));
    check(await page.evaluate(() =>
      document.getElementById("preview-image-viewer-body").classList.contains("is-actual")),
    "原始尺寸模式作用在查看器主体上");
    check((await page.textContent("#preview-image-viewer-hint")).trim() ===
      "原始 900px 显示，窄屏可左右滑动查看细节。", "原始尺寸提示已切换");

    const download = await page.$eval("#preview-image-viewer-download", (node) => ({
      text: node.textContent.trim(),
      label: node.getAttribute("aria-label"),
    }));
    check(download.text === "下载「每日新闻」PNG", "查看器下载按钮文案：" + download.text);
    check(download.label === "下载每日新闻栏目长图", "下载按钮无障碍名称：" + download.label);
    const viewerDownload = page.waitForEvent("download");
    await page.click("#preview-image-viewer-download");
    const firstFile = await viewerDownload;
    check(firstFile.suggestedFilename() ===
      "60sapi-task-task-a-daily_news-1.png", "长图下载文件名：" + firstFile.suggestedFilename());

    await page.keyboard.press("Escape");
    await page.waitForFunction(() => !document.getElementById("preview-image-viewer").open);
    check(true, "Esc 关闭查看器");
    check(await page.evaluate(() =>
      !document.documentElement.classList.contains("preview-image-viewer-open")),
    "关闭后解除滚动锁");
    check(await page.evaluate(() =>
      document.activeElement.getAttribute("aria-label") === "查看「每日新闻」大图"),
    "关闭后焦点回到触发长图");
    const cleared = await page.$eval("#preview-image-viewer-img", (node) => ({
      src: node.getAttribute("src"),
      width: node.getAttribute("width"),
    }));
    check(cleared.src === null && cleared.width === "900", "关闭后清空长图地址并复位宽度");

    await page.locator("#task-preview-image-pages .preview-image-page button",
      { hasText: "查看大图" }).nth(1).click();
    await page.waitForSelector("#preview-image-viewer[open]");
    check((await page.textContent("#preview-image-viewer-title")).trim() === "2. 知乎热榜",
      "第二张长图的标题");
    check((await page.textContent("#preview-image-viewer-download")).trim() ===
      "下载「知乎热榜」PNG", "第二张长图的下载文案");
    await page.evaluate(() => document.getElementById("preview-image-viewer").click());
    await page.waitForFunction(() => !document.getElementById("preview-image-viewer").open);
    check(true, "点击背板关闭查看器");

    const sectionDownload = page.waitForEvent("download");
    await page.locator("#task-preview-image-pages .preview-image-page button",
      { hasText: "下载「知乎热榜」PNG" }).click();
    const secondFile = await sectionDownload;
    check(secondFile.suggestedFilename() ===
      "60sapi-task-task-a-zhihu_hot-2.png", "面板下载文件名：" + secondFile.suggestedFilename());

    // 重新获取预览后旧快照失效，旧长图必须清空。
    await page.click("#task-preview-refresh-button");
    await page.waitForFunction(() =>
      document.querySelectorAll("#task-preview-image-pages .preview-image-page").length === 0);
    check(await page.isEnabled("#task-preview-image-button"), "新预览可重新生成长图");
    check(await page.evaluate(() =>
      document.getElementById("task-preview-image-state").hidden), "旧长图状态已清空");

    console.log("TASK PREVIEW IMAGE VIEWER PASS " + JSON.stringify({ assertions }));
  } finally {
    await browser.close();
    server.close();
  }
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
