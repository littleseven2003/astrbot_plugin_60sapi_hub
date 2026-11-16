/*
 * 批量站点连接测试面板的真实页面逻辑回归。
 *
 * 用真实 Chromium 加载 views/sites/index.html + app.js，只把宿主 bridge 换成
 * 记录型桩，验证：停用站点可显式勾选、五类内容一起提交、部分失败分别标注、
 * 结果过期时不覆盖、未选站点不发请求、窄屏不横向溢出。
 *
 * 这是「桩 bridge 的页面逻辑验证」：不连接真实 AstrBot 后台、不访问任何上游。
 * 真实宿主验收需在具备 WebUI 的环境单独完成。
 */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const http = require("node:http");
const path = require("node:path");
const { chromium } = require("playwright-core");

function resolveChrome() {
  const configured = process.env.CHROME_PATH;
  if (configured) {
    return configured;
  }
  try {
    return chromium.executablePath();
  } catch {
    return "";
  }
}

const CHROME = resolveChrome();
if (!CHROME || !fs.existsSync(CHROME)) {
  console.error(
    "找不到可用的 Chromium：请设置 CHROME_PATH，或安装 playwright 的浏览器。",
  );
  process.exit(2);
}
const VIEWS_DIR = path.resolve(__dirname, "..", "..", "views", "sites");
const MIME = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
};

const SITES = [
  {
    id: "site-ok", name: "公开站点", base_url: "https://a.example.test/n",
    enabled: true, timeout_seconds: 10, source_kind: "public",
    has_bearer_token: false, revision: 1, updated_at: "",
  },
  {
    id: "site-partial", name: "部分失败站点", base_url: "https://b.example.test/n",
    enabled: true, timeout_seconds: 10, source_kind: "public",
    has_bearer_token: false, revision: 1, updated_at: "",
  },
  {
    id: "site-disabled", name: "停用站点", base_url: "https://c.example.test/n",
    enabled: false, timeout_seconds: 10, source_kind: "self_built",
    has_bearer_token: false, revision: 1, updated_at: "",
  },
];

const FIVE_TYPES = [
  "daily_news", "bilibili_hot", "zhihu_hot", "douyin_hot", "toutiao_hot",
];

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

function batchBody(siteIds, contentTypes) {
  const results = [];
  for (const siteId of siteIds) {
    for (const contentType of contentTypes) {
      const broken = siteId === "site-partial" && contentType === "zhihu_hot";
      results.push({
        site_id: siteId,
        content_type: contentType,
        result: broken ? "error" : (contentType === "douyin_hot" ? "empty" : "success"),
        message: broken ? "上游返回了失败状态" : "连接成功",
        elapsed_ms: 12,
      });
    }
  }
  return {
    results,
    failed: results.filter((item) => item.result === "error").length,
  };
}

async function openPage(browser, port, viewport) {
  const page = await browser.newPage({ viewport });
  await page.addInitScript((sites) => {
    window.__siteFixtures = sites;
    window.__calls = [];
    window.AstrBotPluginView = {
      ready: async () => ({ pageTitle: "60sAPI Hub" }),
      apiGet: async (endpoint) => {
        if (endpoint === "sites") {
          return { sites: window.__siteFixtures };
        }
        if (endpoint === "profiles") {
          return { profiles: [] };
        }
        if (endpoint === "tasks") {
          return { tasks: [] };
        }
        if (endpoint === "schedule-runs") {
          return { claims: [], skips: [] };
        }
        if (endpoint === "executions") {
          return { executions: [], total: 0 };
        }
        return {};
      },
      apiPost: async (endpoint, payload) => {
        const call = {
          seq: window.__calls.length + 1, method: "POST", endpoint, payload,
        };
        window.__calls.push(call);
        if (endpoint === "sites/test-batch") {
          if (window.__batchGate) {
            window.__batchStarted = true;
            await window.__batchGate;
          }
          if (window.__batchReject) {
            // 真实 bridge 以带后端 message 的异常 reject。
            const error = new Error("插件当前不可用。");
            error.status = 503;
            throw error;
          }
          return window.__batchBody(payload);
        }
        return {};
      },
    };
  }, SITES);
  await page.goto("http://127.0.0.1:" + port + "/index.html", {
    waitUntil: "domcontentloaded",
  });
  await page.waitForFunction(
    () => document.getElementById("sites-list")?.getAttribute("aria-busy") === "false"
      && document.querySelectorAll("#sites-list li").length === 3,
    null, { timeout: 30000 },
  );
  await page.evaluate((types) => {
    window.__batchBody = (payload) => {
      const results = [];
      for (const siteId of payload.site_ids) {
        for (const contentType of payload.content_types) {
          const broken = siteId === "site-partial" && contentType === "zhihu_hot";
          results.push({
            site_id: siteId,
            content_type: contentType,
            result: broken ? "error" : "success",
            message: broken ? "上游返回了失败状态" : "连接成功",
            elapsed_ms: 12,
          });
        }
      }
      return {
        results,
        failed: results.filter((item) => item.result === "error").length,
      };
    };
    window.__fiveTypes = types;
  }, FIVE_TYPES);
  return page;
}

async function batchPosts(page) {
  return page.evaluate(() => window.__calls.filter(
    (call) => call.method === "POST" && call.endpoint === "sites/test-batch",
  ).length);
}

(async () => {
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const port = server.address().port;
  const browser = await chromium.launch({
    headless: true, executablePath: CHROME, args: ["--no-sandbox"],
  });
  try {
    const page = await openPage(browser, port, { width: 1440, height: 900 });

    // 面板列出全部站点，默认只勾选启用的，停用站点可显式勾选。
    const boxes = await page.$$eval(
      "#batch-test-sites input[type=checkbox]",
      (nodes) => nodes.map((node) => ({
        value: node.value, checked: node.checked,
      })),
    );
    check(boxes.length === 3, "批量面板列出全部已保存站点");
    check(boxes.find((item) => item.value === "site-disabled").checked === false,
      "停用站点默认不勾选");
    check(boxes.find((item) => item.value === "site-ok").checked === true,
      "启用站点默认勾选");
    const disabledLabel = await page.textContent(
      "#batch-test-sites label:has(input[value=site-disabled])",
    );
    check(disabledLabel.includes("已停用"), "停用站点在面板中标注已停用");
    await page.click("#batch-test-sites input[value=site-disabled]");
    check(
      await page.isChecked("#batch-test-sites input[value=site-disabled]"),
      "停用站点可以显式勾选参与测试",
    );

    // 全部五类：一次提交五类内容，包含新支持的今日头条，不再出现微博。
    check(await page.inputValue("#batch-test-type") === "all", "默认选择全部五类");
    const options = await page.$$eval(
      "#batch-test-type option", (nodes) => nodes.map((node) => node.value));
    check(JSON.stringify(options) === JSON.stringify(["all", ...FIVE_TYPES]),
      "类型下拉就是五类内容：" + options.join(","));
    await page.click("#batch-test-run");
    await page.waitForFunction(
      () => document.querySelectorAll("#batch-test-results li").length > 0,
      null, { timeout: 10000 },
    );
    const posted = await page.evaluate(() => window.__calls.filter(
      (call) => call.method === "POST" && call.endpoint === "sites/test-batch",
    )[0].payload);
    check(JSON.stringify(posted.content_types) === JSON.stringify(FIVE_TYPES),
      "全部五类按固定顺序提交");
    check(posted.site_ids.includes("site-disabled"),
      "停用站点作为所选站点一起提交");

    const rows = await page.$$eval("#batch-test-results li", (nodes) =>
      nodes.map((node) => ({
        result: node.dataset.result,
        text: node.textContent,
      })));
    check(rows.length === 15, "三个站点五类内容共 15 行结果");
    check(rows.filter((row) => row.result === "error").length === 1,
      "只有真正失败的一项被标记不可用");
    check(rows.some((row) => row.text.includes("停用站点")),
      "停用站点的结果同样展示");
    const failedRow = rows.find((row) => row.result === "error");
    check(failedRow.text.includes("不可用"), "失败项显示不可用");
    check(failedRow.text.includes("上游返回了失败状态"), "失败项展示后端消息");
    const summary = await page.textContent("#batch-test-state");
    check(summary.includes("完成 15 项") && summary.includes("1 项不可用"),
      "汇总显示总数与不可用数量：" + summary);
    check(await page.isEnabled("#batch-test-run"), "测试结束后按钮恢复可用");

    // 单类：只提交该类型。
    await page.selectOption("#batch-test-type", "toutiao_hot");
    await page.click("#batch-test-run");
    await page.waitForFunction(
      () => window.__calls.filter(
        (call) => call.method === "POST" && call.endpoint === "sites/test-batch",
      ).length === 2,
      null, { timeout: 10000 },
    );
    const single = await page.evaluate(() => window.__calls.filter(
      (call) => call.method === "POST" && call.endpoint === "sites/test-batch",
    )[1].payload);
    check(JSON.stringify(single.content_types) === JSON.stringify(["toutiao_hot"]),
      "单选今日头条时只提交一个类型");

    // 未选站点：不发请求，给出提示。
    const before = await batchPosts(page);
    await page.click("#batch-test-sites input[value=site-ok]");
    await page.click("#batch-test-sites input[value=site-partial]");
    await page.click("#batch-test-sites input[value=site-disabled]");
    await page.click("#batch-test-run");
    const emptyState = await page.textContent("#batch-test-state");
    check(emptyState.includes("请至少选择一个站点"), "未选站点给出提示");
    check(await batchPosts(page) === before, "未选站点不发送请求");

    // 结果过期：请求在途时站点选择变化，回来的结果不得覆盖界面。
    await page.click("#batch-test-sites input[value=site-ok]");
    await page.evaluate(() => {
      window.__batchGate = new Promise((resolve) => {
        window.__releaseBatch = resolve;
      });
    });
    await page.click("#batch-test-run");
    await page.waitForFunction(() => window.__batchStarted === true,
      null, { timeout: 10000 });
    check(await page.isDisabled("#batch-test-run"), "测试在途时按钮禁用");
    await page.click("#batch-test-sites input[value=site-partial]");
    const hiddenBefore = await page.isHidden("#batch-test-results");
    await page.evaluate(() => {
      window.__releaseBatch();
      window.__batchGate = null;
    });
    await page.waitForFunction(
      () => document.getElementById("batch-test-run").disabled === false,
      null, { timeout: 10000 },
    );
    check(hiddenBefore, "在途期间不展示上一次的结果");
    check(await page.isHidden("#batch-test-results"),
      "选择变化后过期结果不会覆盖界面");

    // 后端失败：显示友好错误，不残留旧结果。
    await page.evaluate(() => { window.__batchReject = true; });
    await page.click("#batch-test-run");
    await page.waitForFunction(
      () => !document.getElementById("batch-test-state").textContent.includes("正在测试"),
      null, { timeout: 10000 },
    );
    const errorText = await page.textContent("#batch-test-state");
    check(errorText.includes("插件当前不可用"), "后端失败展示后端消息：" + errorText);
    check(await page.isEnabled("#batch-test-run"), "失败后按钮恢复可用");

    // 窄屏：390px 下批量面板与结果不横向溢出，长名称不被裁切到不可操作。
    await page.setViewportSize({ width: 390, height: 844 });
    await page.evaluate(() => { window.__batchReject = false; });
    await page.click("#batch-test-run");
    await page.waitForFunction(
      () => document.querySelectorAll("#batch-test-results li").length > 0,
      null, { timeout: 10000 },
    );
    const overflow = await page.evaluate(() => {
      const panel = document.getElementById("batch-test-sites").closest("section, div");
      const doc = document.documentElement;
      return {
        docOverflow: doc.scrollWidth - doc.clientWidth,
        panelRight: panel ? panel.getBoundingClientRect().right : 0,
        resultsRight: document.getElementById("batch-test-results")
          .getBoundingClientRect().right,
        runVisible: document.getElementById("batch-test-run")
          .getBoundingClientRect().width > 0,
      };
    });
    check(overflow.docOverflow <= 0, "390px 下页面不横向溢出：" + overflow.docOverflow);
    check(overflow.resultsRight <= 390, "390px 下结果列表不超出视口");
    check(overflow.runVisible, "390px 下运行按钮可见");

    console.log("BATCH SITE TEST UI PASS", { assertions });
  } finally {
    await browser.close();
    server.close();
  }
})().catch((error) => {
  console.error("FAILED:", error.stack || error.message);
  process.exit(1);
});
