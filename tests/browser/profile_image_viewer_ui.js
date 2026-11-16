"use strict";
// 后台多栏目长图查看器回归：使用与 AstrBot 4.29.1 bridge 一致的
// “已解包业务 data + 异常即失败”契约，不访问真实上游。
const fs = require("node:fs");
const http = require("node:http");
const path = require("node:path");
const assert = require("node:assert");
const { chromium } = require("playwright");

function resolveChrome() {
  const fromEnv = process.env.CHROME_PATH;
  if (fromEnv && fs.existsSync(fromEnv)) return fromEnv;
  return "";
}
const CHROME = resolveChrome();
if (!CHROME) {
  console.error("请设置 CHROME_PATH");
  process.exit(2);
}
const VIEWS_DIR = path.resolve(__dirname, "..", "..", "views", "sites");
const MIME = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
};
const PNG_BASE64 = "iVBORw0KGgoAAAANSUhEUgAAA4QAAAEsCAIAAAAU/OrGAAAFrklEQVR4nO3YMREAMAwDsbT8OScwfpEIeP3z290BAIDCT1YBAECMAgBQ8owCAJARowAAZMQoAAAZMQoAQEaMAgCQEaMAAGTEKAAAGTEKAEBGjAIAkBGjAABkxCgAABkxCgBARowCAJARowAAZMQoAAAZMQoAQEaMAgCQEaMAAGTEKAAAGTEKAEBGjAIAkBGjAABkxCgAABkxCgBARowCAJARowAAZMQoAAAZMQoAQEaMAgCQEaMAAGTEKAAAGTEKAEBGjAIAkBGjAABkxCgAABkxCgBARowCAJARowAAZMQoAAAZMQoAQEaMAgCQEaMAAGTEKAAAGTEKAEBGjAIAkBGjAABkxCgAABkxCgBARowCAJARowAAZMQoAAAZMQoAQEaMAgCQEaMAAGTEKAAAGTEKAEBGjAIAkBGjAABkxCgAABkxCgBARowCAJARowAAZMQoAAAZMQoAQEaMAgCQEaMAAGTEKAAAGTEKAEBGjAIAkBGjAABkxCgAABkxCgBARowCAJARowAAZMQoAAAZMQoAQEaMAgCQEaMAAGTEKAAAGTEKAEBGjAIAkBGjAABkxCgAABkxCgBARowCAJARowAAZMQoAAAZMQoAQEaMAgCQEaMAAGTEKAAAGTEKAEBGjAIAkBGjAABkxCgAABkxCgBARowCAJARowAAZMQoAAAZMQoAQEaMAgCQEaMAAGTEKAAAGTEKAEBGjAIAkBGjAABkxCgAABkxCgBARowCAJARowAAZMQoAAAZMQoAQEaMAgCQEaMAAGTEKAAAGTEKAEBGjAIAkBGjAABkxCgAABkxCgBARowCAJARowAAZMQoAAAZMQoAQEaMAgCQEaMAAGTEKAAAGTEKAEBGjAIAkBGjAABkxCgAABkxCgBARowCAJARowAAZMQoAAAZMQoAQEaMAgCQEaMAAGTEKAAAGTEKAEBGjAIAkBGjAABkxCgAABkxCgBARowCAJARowAAZMQoAAAZMQoAQEaMAgCQEaMAAGTEKAAAGTEKAEBGjAIAkBGjAABkxCgAABkxCgBARowCAJARowAAZMQoAAAZMQoAQEaMAgCQEaMAAGTEKAAAGTEKAEBGjAIAkBGjAABkxCgAABkxCgBARowCAJARowAAZMQoAAAZMQoAQEaMAgCQEaMAAGTEKAAAGTEKAEBGjAIAkBGjAABkxCgAABkxCgBARowCAJARowAAZMQoAAAZMQoAQEaMAgCQEaMAAGTEKAAAGTEKAEBGjAIAkBGjAABkxCgAABkxCgBARowCAJARowAAZMQoAAAZMQoAQEaMAgCQEaMAAGTEKAAAGTEKAEBGjAIAkBGjAABkxCgAABkxCgBARowCAJARowAAZMQoAAAZMQoAQEaMAgCQEaMAAGTEKAAAGTEKAEBGjAIAkBGjAABkxCgAABkxCgBARowCAJARowAAZMQoAAAZMQoAQEaMAgCQEaMAAGTEKAAAGTEKAEBGjAIAkBGjAABkxCgAABkxCgBARowCAJARowAAZMQoAAAZMQoAQEaMAgCQEaMAAGTEKAAAGTEKAEBGjAIAkBGjAABkxCgAABkxCgBARowCAJARowAAZMQoAAAZMQoAQEaMAgCQEaMAAGTEKAAAGTEKAEBGjAIAkBGjAABkxCgAABkxCgBARowCAJARowAAZMQoAAAZMQoAQEaMAgCQEaMAAGTEKAAAGTEKAEBGjAIAkBGjAABkxCgAABkxCgBARowCAJARowAAZMQoAAAZMQoAQEaMAgCQEaMAAGTEKAAAGTEKAEBGjAIAkBGjAABkxCgAABkxCgBARowCAJARowAAZMQoAAAZMQoAQEaMAgCQEaMAAGTEKAAAGTEKAEBGjAIAkBGjAABkxCgAABkxCgBARowCAJARowAAZMQoAAAZMQoAQEaMAgCQEaMAAGTEKAAAGTEKAEBGjAIAMJUDOk8FVW+xo8oAAAAASUVORK5CYII=";

const PROFILES = [{
  id: "profile-1", name: "每日速览", site_id: "site-a", site_enabled: true,
  revision: 2, items: [{ content_type: "daily_news", params: {}, limit: 3 }],
  output_format: "text", created_at: "", updated_at: "",
}];

const PREVIEW = {
  profile_id: "profile-1",
  profile_revision: 2,
  profile_name: "每日速览",
  site_name: "公开站点",
  status: "complete",
  success_count: 2,
  failure_count: 0,
  sections: [{
    content_type: "daily_news", label: "每日新闻", status: "ok",
    items: [{ title: "新闻一" }, { title: "新闻二" }],
  }, {
    content_type: "zhihu_hot", label: "知乎热榜", status: "ok",
    items: [{ title: "热榜一", hot_value: 1000 }],
  }],
  outputs: { text: "正文", markdown: "# 正文" },
  image_snapshot: { id: "snap-1", site_id: "site-a", site_revision: 1 },
};

const IMAGES = {
  images: [{
    index: 1, content_type: "daily_news", label: "每日新闻", status: "success",
    width: 900, height: 300, png_base64: PNG_BASE64,
    source_url: "https://a.example.test/secret", source_kind: "public",
  }, {
    index: 2, content_type: "zhihu_hot", label: "知乎热榜", status: "success",
    width: 900, height: 300, png_base64: PNG_BASE64,
    source_url: "http://10.0.0.5:8080/x", source_kind: "self_built",
  }],
};

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

let assertions = 0;
function check(condition, message) {
  assertions += 1;
  assert.ok(condition, message);
}

async function openPage(browser, port, viewport) {
  const page = await browser.newPage({ viewport });
  await page.addInitScript((fixtures) => {
    window.__errors = [];
    window.addEventListener("error", (event) => {
      window.__errors.push(String(event.message));
    });
    window.__calls = [];
    window.__downloads = [];
    const realClick = HTMLAnchorElement.prototype.click;
    HTMLAnchorElement.prototype.click = function patched() {
      if (this.download) {
        window.__downloads.push(this.download);
        return;
      }
      return realClick.call(this);
    };
    window.AstrBotPluginView = {
      ready: async () => ({ pageTitle: "60sAPI Hub" }),
      apiGet: async (endpoint) => {
        if (endpoint === "profiles") return { profiles: fixtures.profiles };
        if (endpoint === "sites") return { sites: [] };
        if (endpoint === "tasks") return { tasks: [] };
        return { executions: [], total: 0, items: [] };
      },
      apiPost: async (endpoint, payload) => {
        window.__calls.push({ endpoint, payload });
        if (endpoint.endsWith("/preview")) return fixtures.preview;
        if (endpoint.endsWith("/image")) return fixtures.images;
        return {};
      },
    };
  }, { profiles: PROFILES, preview: PREVIEW, images: IMAGES });
  await page.goto("http://127.0.0.1:" + port + "/index.html", {
    waitUntil: "domcontentloaded",
  });
  await page.waitForFunction(
    () => Array.from(
      document.getElementById("profile-preview-select").options,
    ).some((option) => option.value === "profile-1"),
    null, { timeout: 15000 },
  );
  return page;
}

async function openViewer(page) {
  await page.click('.nav-item[data-view="profiles"]');
  await page.waitForFunction(
    () => document.getElementById("view-profiles").hidden === false,
  );
  await page.selectOption("#profile-preview-select", "profile-1");
  await page.click("#profile-preview-button");
  await page.waitForFunction(
    () => !document.getElementById("profile-image-button").disabled,
    null, { timeout: 15000 },
  );
  await page.click("#profile-image-button");
  await page.waitForFunction(
    () => document.querySelectorAll("#profile-image-pages figure").length === 2,
    null, { timeout: 15000 },
  );
}

(async () => {
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const port = server.address().port;
  const browser = await chromium.launch({
    headless: true, executablePath: CHROME, args: ["--no-sandbox"],
  });
  try {
    const page = await openPage(browser, port, { width: 1440, height: 900 });
    await openViewer(page);

    const cards = await page.$$eval("#profile-image-pages figure",
      (nodes) => nodes.map((node) => ({
        status: node.dataset.status,
        caption: node.textContent.trim(),
      })));
    check(cards.length === 2, "应渲染两个栏目卡片");
    check(cards.every((card) => card.status === "success"), "卡片状态为成功");
    check(cards[0].caption.includes("每日新闻"), "卡片标题来自栏目");

    // 点击图片打开放大查看器。
    await page.click("#profile-image-pages figure img");
    await page.waitForFunction(
      () => document.getElementById("profile-image-viewer").open === true);
    const opened = await page.evaluate(() => ({
      title: document.getElementById("profile-image-viewer-title").textContent,
      src: document.getElementById("profile-image-viewer-img")
        .getAttribute("src").slice(0, 22),
      width: document.getElementById("profile-image-viewer-img").width,
      height: document.getElementById("profile-image-viewer-img").height,
      bodyScroll: getComputedStyle(
        document.getElementById("profile-image-viewer-body"),
      ).overflowX,
      htmlLocked: document.documentElement.classList
        .contains("profile-image-viewer-open"),
    }));
    check(opened.title.includes("每日新闻"), "查看器标题为栏目名：" + opened.title);
    check(opened.src === "data:image/png;base64,", "查看器加载该栏目 PNG");
    check(opened.width === 900 && opened.height === 300, "查看器按原始尺寸显示");
    check(opened.bodyScroll === "auto", "长图区域可横向滚动");
    check(opened.htmlLocked, "打开时锁住页面滚动");

    // 下载。
    await page.click("#profile-image-viewer-download");
    const downloads = await page.evaluate(() => window.__downloads);
    check(downloads.length === 1, "下载触发一次：" + JSON.stringify(downloads));
    check(downloads[0].endsWith(".png"), "下载文件名为 PNG");

    // 键盘关闭。
    await page.keyboard.press("Escape");
    await page.waitForFunction(
      () => document.getElementById("profile-image-viewer").open === false);
    check(true, "Escape 可关闭查看器");

    // 关闭按钮。
    await page.click("#profile-image-pages figure img");
    await page.waitForFunction(
      () => document.getElementById("profile-image-viewer").open === true);
    await page.click("#profile-image-viewer-close");
    await page.waitForFunction(
      () => document.getElementById("profile-image-viewer").open === false);
    check(true, "关闭按钮可关闭查看器");

    // 第二个栏目单独打开。
    await page.locator("#profile-image-pages figure img").nth(1).click();
    await page.waitForFunction(
      () => document.getElementById("profile-image-viewer").open === true);
    const second = await page.textContent("#profile-image-viewer-title");
    check(second.includes("知乎热榜"), "第二个栏目标题正确：" + second);
    await page.click("#profile-image-viewer-close");

    await page.close();

    // 390px：卡片单列、查看器可用、无页面横向溢出。
    const mobile = await openPage(browser, port, { width: 390, height: 844 });
    await openViewer(mobile);
    const mobileInfo = await mobile.evaluate(() => ({
      docScrollWidth: document.documentElement.scrollWidth,
      clientWidth: document.documentElement.clientWidth,
      cards: document.querySelectorAll("#profile-image-pages figure").length,
      cardWidth: Math.round(
        document.querySelector("#profile-image-pages figure")
          .getBoundingClientRect().width,
      ),
    }));
    check(mobileInfo.docScrollWidth === mobileInfo.clientWidth,
      "390px 无横向溢出");
    check(mobileInfo.cards === 2, "390px 仍显示两个栏目卡片");
    check(mobileInfo.cardWidth <= 390, "卡片宽度不超出视口：" + mobileInfo.cardWidth);

    await mobile.click("#profile-image-pages figure img");
    await mobile.waitForFunction(
      () => document.getElementById("profile-image-viewer").open === true);
    const mobileViewer = await mobile.evaluate(() => {
      const dialog = document.getElementById("profile-image-viewer");
      const rect = dialog.getBoundingClientRect();
      return {
        width: Math.round(rect.width),
        fits: rect.right <= window.innerWidth + 1,
        closeVisible: document.getElementById("profile-image-viewer-close")
          .getBoundingClientRect().bottom <= window.innerHeight,
        downloadVisible: document.getElementById("profile-image-viewer-download")
          .getBoundingClientRect().bottom <= window.innerHeight,
      };
    });
    check(mobileViewer.fits, "390px 查看器不超出视口");
    check(mobileViewer.closeVisible && mobileViewer.downloadVisible,
      "390px 关闭与下载按钮在视口内");
    await mobile.click("#profile-image-viewer-close");
    await mobile.close();

    console.log("PROFILE IMAGE VIEWER UI PASS", { assertions });
  } finally {
    await browser.close();
    server.close();
  }
})().catch((error) => {
  console.error("FAILED:", error.stack || error.message);
  process.exit(1);
});
