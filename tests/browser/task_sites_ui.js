/*
 * 任务候选站点与站点五类测试的页面逻辑回归。
 *
 * 真实 Chromium 加载 views/sites/index.html + app.js，只替换宿主 bridge。
 * 验证：候选站点添加/排序/移除进入保存载荷、能力测试打到独立路由、
 * 站点“测试全部五类”逐一请求五类接口。
 *
 * 这是桩 bridge 的页面逻辑验证，不代表真实 AstrBot 后台/数据库往返，
 * 也不连接真实上游或会话。
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

const SITES = [
  { id: "site-a", name: "站点A", base_url: "https://a.example.test/n",
    enabled: true, timeout_seconds: 10, source_kind: "public",
    has_bearer_token: false, revision: 3, updated_at: "" },
  { id: "site-b", name: "站点B", base_url: "https://b.example.test/n",
    enabled: true, timeout_seconds: 10, source_kind: "public",
    has_bearer_token: false, revision: 4, updated_at: "" },
  { id: "site-c", name: "站点C", base_url: "https://c.example.test/n",
    enabled: false, timeout_seconds: 10, source_kind: "self_built",
    has_bearer_token: true, revision: 1, updated_at: "" },
];

const PROFILES = [{
  id: "profile-1", name: "组合一", site_id: "site-a", site_enabled: true,
  revision: 1, items: [{ content_type: "daily_news", params: {}, limit: 2 }],
  created_at: "", updated_at: "",
}, {
  id: "profile-2", name: "组合二", site_id: "site-b", site_enabled: true,
  revision: 1, items: [{ content_type: "daily_news", params: {}, limit: 2 }],
  created_at: "", updated_at: "",
}];

const TASKS = [{
  id: "task-1", name: "任务一", profile_id: "profile-1", profile_name: "组合一",
  site_id: "site-a", site_enabled: true, output_format: "text",
  output_label: "纯文字", umos: ["aiocqhttp:GroupMessage:g:1"],
  enabled: false, revision: 2, created_at: "", updated_at: "",
  schedule: { kind: "daily", timezone: "Asia/Shanghai", time: "08:30" },
  schedule_raw: { kind: "daily", timezone: "Asia/Shanghai", time: "08:30" },
  sites: [{ id: "site-a", name: "站点A", enabled: true }],
}];

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

async function openPage(browser, port, overrides = {}) {
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  await page.addInitScript((fixtures) => {
    window.__calls = [];
    window.AstrBotPluginView = {
      ready: async () => {
        window.__taskSites = ["site-a"];
        return { pageTitle: "60sAPI Hub" };
      },
      apiGet: async (endpoint) => {
        if (endpoint === "sites") return { sites: fixtures.sites };
        if (endpoint === "profiles") return { profiles: fixtures.profiles };
        if (endpoint === "tasks") {
          const task = { ...fixtures.tasks[0] };
          task.sites = window.__taskSites.map((id) => ({
            id, name: id, enabled: true,
          }));
          return { tasks: [task] };
        }
        if (endpoint === "schedule-runs") return { claims: [], skips: [] };
        if (endpoint === "executions") return { executions: [], total: 0 };
        return {};
      },
      apiPost: async (endpoint, payload) => {
        window.__calls.push({ endpoint, payload });
        if (endpoint.endsWith("/update") && Array.isArray(payload && payload.sites)) {
          window.__taskSites = payload.sites.slice();
        }
        if (endpoint.endsWith("/enable") || endpoint.endsWith("/disable")) {
          return { task_id: "task-1", enabled: true, revision: 3 };
        }
        if (endpoint.endsWith("/enable") || endpoint.endsWith("/disable")) {
          return { task_id: "task-1", enabled: true, revision: 3 };
        }
        if (endpoint.endsWith("/test-preview")) {
          // 全站停用时的手动确认：明确这次只发异常提示。
          return {
            task_id: "task-1", task_name: "任务一", task_revision: 2,
            profile_id: "profile-1", profile_name: "组合一",
            profile_revision: 1, site_id: "site-a", site_name: "站点A",
            site_revision: 3, site_enabled: false, output_format: "text",
            output_label: "纯文字", can_deliver: true, alert_only: true,
            reason: "content_unavailable",
            targets: [{
              umo: "aiocqhttp:GroupMessage:g:1", platform_id: "aiocqhttp",
              message_type: "GroupMessage", target: "g:1", isolated: true,
              valid: true, reason: null,
            }],
            candidate_sites: [
              { id: "site-a", name: "站点A", enabled: false },
              { id: "site-b", name: "站点B", enabled: false },
            ],
            content_policy: "fresh",
          };
        }
        if (endpoint.endsWith("/capability-test") && window.__capabilityGate) {
          await window.__capabilityGate;
        }
        if (endpoint.endsWith("/capability-test")) {
          window.__capabilityDoneCount = (window.__capabilityDoneCount || 0) + 1;
          if (window.__capabilityFail) {
            throw new Error("测试失败，请稍后重试");
          }
          // 宿主 bridge 成功时直接返回业务 data（不再套一层 data）。
          return {
            satisfied: true, site_id: "site-b", site_name: "站点B",
            message: "站点「站点B」可以满足全部配置。",
            truncated: false, checked_count: 2,
            cache: { used: true, ttl_seconds: 300 },
          };
        }
        if (endpoint.includes("/test")) {
          // 与真实 ContentApi 契约一致：result 为 success / empty，
          // 失败走抛出错误（宿主 bridge 对非 2xx 抛错）。
          if (payload && payload.content_type === "zhihu_hot") {
            return {
              result: "empty", message: "连接成功，接口返回合法空内容。",
              elapsed_ms: 8, content_type: "zhihu_hot",
            };
          }
          if (payload && payload.content_type === "douyin_hot") {
            throw new Error("test_timeout");
          }
          return {
            result: "success", message: "连接成功，接口返回内容结构合法。",
            elapsed_ms: 12, content_type: payload && payload.content_type,
          };
        }
        return {};
      },
    };
  }, { sites: SITES, profiles: PROFILES, tasks: overrides.tasks || TASKS });
  await page.goto("http://127.0.0.1:" + port + "/index.html", {
    waitUntil: "domcontentloaded",
  });
  return page;
}

(async () => {
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const port = server.address().port;
  const browser = await chromium.launch({ executablePath: CHROME, headless: true });
  try {
    const page = await openPage(browser, port);
    await page.click('.nav-item[data-view="tasks"]');
    await page.waitForSelector("#tasks-list li", { timeout: 10000 });

    // 手动确认：展示候选顺序，并说明本次只会发送异常提示。
    await page.waitForSelector("#tasks-list li");
    // 页面启用：确认弹窗通过预检后发出启用请求。
    await page.locator("#tasks-list button", { hasText: "启用定时调度" }).first().click();
    await page.waitForSelector("#task-power-confirm-button:not([disabled])");
    await page.click("#task-power-confirm-button");
    await page.waitForFunction(() =>
      window.__calls.some((call) => call.endpoint.endsWith("/enable")));
    check(true, "页面启用应发出启用请求");

    await page.locator("#tasks-list button", { hasText: "测试投递" }).first().click();
    await page.waitForSelector("#delivery-confirm-button");
    const confirmText = await page.textContent("#delivery-confirm-info");
    check(confirmText.includes("候选站点（按尝试顺序）"),
      "确认页应展示候选顺序：" + confirmText.slice(0, 80));
    check(confirmText.includes("1. 站点A") && confirmText.includes("2. 站点B"),
      "确认页候选顺序应正确：" + confirmText.slice(0, 120));
    check(confirmText.includes("不会发送内容"),
      "全站停用应说明只发异常提示：" + confirmText.slice(0, 120));
    const confirmLabel = await page.textContent("#delivery-confirm-button");
    check(confirmLabel.includes("异常提示"), "按钮应写明发送异常提示：" + confirmLabel);
    await page.click("#delivery-cancel-button");


    // 打开任务编辑，候选站点按已保存顺序渲染。
    await page.getByRole("button", { name: "编辑" }).first().click();
    await page.waitForSelector("#task-site-list .task-site-row");
    let rows = await page.$$eval("#task-site-list .task-site-name",
      (nodes) => nodes.map((node) => node.textContent));
    check(rows.length === 1 && rows[0] === "站点A", "已保存候选应渲染：" + rows);

    // 添加站点B，然后上移，形成 B、A 的顺序。
    await page.selectOption("#task-site-picker", "site-b");
    await page.click("#task-site-add-button");
    await page.waitForFunction(
      () => document.querySelectorAll("#task-site-list .task-site-row").length === 2,
    );
    await page.getByRole("button", { name: "上移" }).nth(1).click();
    rows = await page.$$eval("#task-site-list .task-site-name",
      (nodes) => nodes.map((node) => node.textContent));
    check(rows[0] === "站点B" && rows[1] === "站点A", "上移后顺序：" + rows);

    // 保存：载荷带上有序 sites。
    await page.click("#task-save-button");
    await page.waitForFunction(() =>
      window.__calls.some((call) => call.endpoint.endsWith("/update")));
    const update = await page.evaluate(() =>
      window.__calls.filter((call) => call.endpoint.endsWith("/update")).pop());
    check(
      JSON.stringify(update.payload.sites) === JSON.stringify(["site-b", "site-a"]),
      "保存载荷应带有序候选：" + JSON.stringify(update.payload.sites),
    );

    // 能力测试走独立路由，并展示结论。
    await page.waitForSelector("#tasks-list li");
    await page.getByRole("button", { name: "编辑" }).first().click();
    await page.waitForSelector("#task-capability-test-button:not([disabled])");
    await page.click("#task-capability-test-button");
    await page.waitForFunction(() =>
      window.__calls.some((call) => call.endpoint.endsWith("/capability-test")));
    const capability = await page.evaluate(() =>
      window.__calls.find((call) => call.endpoint.endsWith("/capability-test")));
    check(capability.endpoint.endsWith("/capability-test"), "能力测试路由");
    const message = await page.textContent("#task-capability-result");
    check(message.includes("可以满足"), "能力测试结论：" + message);
    check(message.includes("内容缓存"), "应说明缓存时效：" + message);

    // 未保存的候选改动必须让旧结论失效并阻止再次测试。
    await page.selectOption("#task-site-picker", "site-c");
    await page.click("#task-site-add-button");
    await page.waitForFunction(
      () => document.querySelectorAll("#task-site-list .task-site-row").length >= 2,
    );
    const gated = await page.isDisabled("#task-capability-test-button");
    check(gated, "有未保存候选改动时应禁用能力测试");
    const gateText = await page.textContent("#task-capability-result");
    check(gateText.includes("尚未保存"), "应提示先保存：" + gateText);
    await page.locator("#task-site-list button", { hasText: "移除" }).last().click();
    await page.waitForFunction(
      () => !document.querySelector("#task-capability-test-button").disabled,
    );
    check(true, "撤销改动后能力测试恢复可用");

    // 在途更换组合（成功响应）：旧结论不得覆盖“先保存”，按钮保持禁用。
    await page.evaluate(() => {
      window.__capabilityGate = new Promise((resolve) => {
        window.__releaseCapability = resolve;
      });
    });
    let doneBefore = await page.evaluate(
      () => window.__capabilityDoneCount || 0);
    await page.click("#task-capability-test-button");
    await page.waitForFunction(
      (n) => (window.__capabilityDoneCount || 0) === n, doneBefore);
    await page.selectOption("#task-profile", "profile-2");
    await page.evaluate(() => {
      window.__releaseCapability();
      window.__capabilityGate = null;
    });
    await page.waitForFunction(
      (n) => (window.__capabilityDoneCount || 0) > n, doneBefore);
    await page.waitForFunction(() => document
      .querySelector("#task-capability-result").textContent.includes("尚未保存"));
    check(await page.isDisabled("#task-capability-test-button"),
      "在途改组合（成功）后按钮应保持禁用");

    // 恢复为已保存组合后可以再次测试；随后验证失败响应同样被丢弃。
    await page.selectOption("#task-profile", "profile-1");
    await page.waitForFunction(
      () => !document.querySelector("#task-capability-test-button").disabled);
    await page.evaluate(() => {
      window.__capabilityFail = true;
      window.__capabilityGate = new Promise((resolve) => {
        window.__releaseCapability = resolve;
      });
    });
    doneBefore = await page.evaluate(() => window.__capabilityDoneCount || 0);
    await page.click("#task-capability-test-button");
    await page.waitForFunction(
      (n) => (window.__capabilityDoneCount || 0) === n, doneBefore);
    await page.selectOption("#task-profile", "profile-2");
    await page.evaluate(() => {
      window.__releaseCapability();
      window.__capabilityGate = null;
      window.__capabilityFail = false;
    });
    await page.waitForFunction(
      (n) => (window.__capabilityDoneCount || 0) > n, doneBefore);
    await page.waitForFunction(() => document
      .querySelector("#task-capability-result").textContent.includes("尚未保存"));
    check(await page.isDisabled("#task-capability-test-button"),
      "在途改组合（失败）后按钮应保持禁用且提示未被覆盖");

    // 恢复为已保存组合，避免关闭弹窗时触发未保存确认。
    await page.selectOption("#task-profile", "profile-1");
    await page.waitForFunction(() =>
      !document.querySelector("#task-capability-test-button").disabled);

    await page.click("#task-close-dialog-button");


    // 站点一键测试五类：逐一请求五个接口并渲染五行。
    await page.click('.nav-item[data-view="sites"]');
    await page.waitForFunction(
      () => document.querySelectorAll("#sites-list li").length === 3,
    );
    await page.locator("#sites-list button", { hasText: "测试连接" }).first().click();
    await page.waitForSelector("#site-test-all-button");
    await page.click("#site-test-all-button");
    await page.waitForFunction(() =>
      document.querySelectorAll("#site-test-all-results li").length === 5,
    );
    const tests = await page.evaluate(() =>
      window.__calls.filter((call) => call.endpoint.includes("/test")).map(
        (call) => call.payload.content_type));
    check(
      ["daily_news", "bilibili_hot", "zhihu_hot", "douyin_hot", "toutiao_hot"]
        .every((type) => tests.includes(type)),
      "五类测试请求：" + JSON.stringify(tests),
    );
    const labels = await page.$$eval("#site-test-all-results li",
      (nodes) => nodes.map((node) => node.textContent));
    check(labels.length === 5, "五类结果行：" + labels.length);
    check(labels.some((text) => text.includes("空内容")),
      "合法空列表应显示为空内容：" + JSON.stringify(labels));
    check(labels.some((text) => text.includes("不可用")),
      "失败项应显示不可用：" + JSON.stringify(labels));

    // 关闭后重新打开：按钮可用、旧结果清空、可再次测试。
    await page.click("#site-test-close-button");
    await page.waitForFunction(
      () => document.getElementById("site-test-dialog").open === false);
    await page.locator("#sites-list button", { hasText: "测试连接" }).first().click();
    await page.waitForSelector("#site-test-all-button:not([disabled])");
    const reopenedRows = await page.locator("#site-test-all-results li").count();
    check(reopenedRows === 0, "重开弹窗应清空旧结果");
    await page.click("#site-test-all-button");
    await page.waitForFunction(() =>
      document.querySelectorAll("#site-test-all-results li").length === 5);
    check(true, "关闭后重开仍可再次测试五类");


    // 切换到另一个站点：新弹窗不显示上一个站点的结果，按钮可用。
    await page.click("#site-test-close-button");
    await page.waitForFunction(
      () => document.getElementById("site-test-dialog").open === false);
    await page.locator("#sites-list button", { hasText: "测试连接" }).nth(1).click();
    await page.waitForSelector("#site-test-all-button:not([disabled])");
    const switchedRows = await page.locator("#site-test-all-results li").count();
    check(switchedRows === 0, "切换站点后不应残留旧结果");
    await page.click("#site-test-all-button");
    await page.waitForFunction(() =>
      document.querySelectorAll("#site-test-all-results li").length === 5);
    check(true, "切换站点后仍可再次测试五类");

    // A 停用、B 可用：页面仍可确认启用任务。
    const page2 = await openPage(browser, port, {
      tasks: [{
        ...TASKS[0],
        site_enabled: false,
        sites: [
          { id: "site-a", name: "站点A", enabled: false },
          { id: "site-b", name: "站点B", enabled: true },
        ],
      }],
    });
    await page2.click('.nav-item[data-view="tasks"]');
    await page2.waitForSelector("#tasks-list li");
    await page2.locator("#tasks-list button", { hasText: "启用定时调度" }).first().click();
    await page2.waitForSelector("#task-power-confirm-button");
    check(await page2.isEnabled("#task-power-confirm-button"),
      "A 停用 B 可用时页面仍可确认启用");
    await page2.click("#task-power-confirm-button");
    await page2.waitForFunction(() =>
      window.__calls.some((call) => call.endpoint.endsWith("/enable")));
    check(true, "A 停用 B 可用时页面实际发出启用请求");
    await page2.close();
    console.log("TASK SITES UI PASS", { assertions });
  } finally {
    await browser.close();
    server.close();
  }
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
