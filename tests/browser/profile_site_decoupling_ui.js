/*
 * 组合与站点职责分离的页面回归。
 *
 * 真实 Chromium 加载 views/sites/index.html + app.js，只替换宿主 bridge。
 * 验证：组合表单不再选择站点、保存载荷不带站点；预览必须显式选站并随站点失效；
 * 任务候选为空时不能保存；候选顺序保存后重开仍保持；没有站点也能建组合；
 * 390px 下候选与长名称不横向溢出。
 *
 * 这是桩 bridge 的页面逻辑验证，不代表真实 AstrBot 后台/数据库往返。
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

const LONG_NAME = "超长站点名称用于检查候选行在窄屏下是否会撑破容器边界";
const LONG_SITES = [
  { id: "site-long-a", name: LONG_NAME + "甲", base_url: "https://long.example.test/n",
    enabled: true, timeout_seconds: 10, source_kind: "public",
    has_bearer_token: false, revision: 1, updated_at: "" },
  { id: "site-long-b", name: LONG_NAME + "乙", base_url: "https://long.example.test/m",
    enabled: true, timeout_seconds: 10, source_kind: "public",
    has_bearer_token: false, revision: 1, updated_at: "" },
];

const PROFILES = [{
  id: "profile-1", name: "组合一", revision: 1,
  items: [{ content_type: "daily_news", params: {}, limit: 2 }],
  created_at: "", updated_at: "",
}];

const TASKS = [{
  id: "task-1", name: "任务一", profile_id: "profile-1", profile_name: "组合一",
  site_id: null, site_enabled: true, output_format: "text",
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

async function openPage(browser, port, options = {}) {
  const fixtures = {
    sites: options.sites || SITES,
    profiles: options.profiles || PROFILES,
    tasks: options.tasks || TASKS,
    taskSites: options.taskSites || ["site-a", "site-b"],
  };
  const page = await browser.newPage({
    viewport: options.viewport || { width: 1440, height: 900 },
  });
  await page.addInitScript((data) => {
    window.__state = {
      calls: [],
      sites: data.sites,
      profiles: data.profiles.slice(),
      tasks: data.tasks,
      taskSites: data.taskSites.slice(),
    };
    window.AstrBotPluginView = {
      ready: async () => ({ pageTitle: "60sAPI Hub" }),
      apiGet: async (endpoint) => {
        if (endpoint === "sites") return { sites: window.__state.sites };
        if (endpoint === "profiles") {
          return { profiles: window.__state.profiles };
        }
        if (endpoint === "tasks") {
          const task = { ...window.__state.tasks[0] };
          task.sites = window.__state.taskSites.map((id) => {
            const site = window.__state.sites.find((item) => item.id === id);
            return { id, name: site ? site.name : id, enabled: site ? site.enabled : true };
          });
          return { tasks: [task] };
        }
        if (endpoint === "schedule-runs") return { claims: [], skips: [] };
        if (endpoint === "executions") return { executions: [], total: 0 };
        return {};
      },
      apiPost: async (endpoint, payload) => {
        window.__state.calls.push({ endpoint, payload });
        if (endpoint === "profiles") {
          const created = {
            id: "profile-new", name: payload.name, revision: 1,
            items: payload.items, created_at: "", updated_at: "",
          };
          window.__state.profiles.push(created);
          return created;
        }
        if (endpoint.endsWith("/preview")) {
          const site = window.__state.sites.find(
            (item) => item.id === payload.site_id);
          return {
            profile_id: "profile-1", profile_revision: 1, profile_name: "组合一",
            site_id: payload.site_id, site_name: site ? site.name : "未知站点",
            status: "complete", success_count: 1, failure_count: 0,
            sections: [{
              content_type: "daily_news", status: "ok", label: "每日60秒",
              endpoint: "/60s", limit: 2, total_item_count: 1, displayed_count: 1,
              items: [{ title: "标题一", description: "摘要一" }],
              source_url: "https://a.example.test/n/60s", source_kind: "public",
            }],
            outputs: { text: "组合正文", markdown: "**组合正文**" },
            image_snapshot: null,
          };
        }
        if (endpoint.endsWith("/update") && Array.isArray(payload && payload.sites)) {
          window.__state.taskSites = payload.sites.slice();
          return { task_id: "task-1", revision: 3, enabled: false };
        }
        if (endpoint === "tasks") {
          return { task_id: "task-new", revision: 1, enabled: false };
        }
        return {};
      },
    };
  }, fixtures);
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
    await page.click('.nav-item[data-view="profiles"]');
    await page.waitForSelector("#profiles-list li");

    check(await page.locator("#profile-site").count() === 0,
      "组合表单不应再包含站点选择");
    const previewSite = await page.$eval("#profile-preview-site", (node) => ({
      value: node.value,
      disabled: node.disabled,
      options: Array.from(node.options).map((option) => option.value),
    }));
    check(previewSite.options.join(",") === "site-a,site-b,site-c",
      "预览站点下拉应列出全部站点：" + JSON.stringify(previewSite.options));
    check(previewSite.value === "site-a",
      "预览站点默认选中第一个启用站点：" + previewSite.value);
    check(previewSite.disabled === false, "有站点时预览站点下拉应可用");

    // 组合不再绑定站点：只填名称即可保存，载荷不含 site_id。
    await page.click("#create-profile-button");
    await page.waitForFunction(() => document.getElementById("profile-dialog").open);
    check(await page.isEnabled("#profile-save-button"),
      "没有站点选择也能保存组合");
    check(await page.locator("#profile-form #profile-site").count() === 0,
      "组合对话框内不应有站点字段");
    await page.fill("#profile-name", "新组合");
    await page.click("#profile-save-button");
    await page.waitForFunction(() => window.__state.calls.some(
      (call) => call.endpoint === "profiles"));
    const createCall = await page.evaluate(() => window.__state.calls
      .find((call) => call.endpoint === "profiles"));
    check(Object.keys(createCall.payload).sort().join(",") === "items,name",
      "组合保存载荷只应包含 name/items：" + JSON.stringify(createCall.payload));

    // 预览必须显式站点，并把站点带进请求。
    await page.selectOption("#profile-preview-select", "profile-1");
    await page.waitForSelector("#profile-preview-button:not([disabled])");
    await page.click("#profile-preview-button");
    await page.waitForSelector("#profile-preview-result:not([hidden])");
    const firstPreview = await page.evaluate(() => window.__state.calls
      .filter((call) => call.endpoint === "profiles/profile-1/preview").at(-1));
    check(firstPreview.payload.site_id === "site-a",
      "预览请求应带所选站点：" + JSON.stringify(firstPreview.payload));
    check(await page.textContent("#profile-preview-source") === "站点A",
      "预览结果应显示实际使用的站点");

    // 换站点使旧结果失效，下一次预览用新站点。
    await page.selectOption("#profile-preview-site", "site-b");
    await page.waitForFunction(
      () => document.getElementById("profile-preview-result").hidden);
    check(true, "切换预览站点后旧结果应被清空");
    await page.click("#profile-preview-button");
    await page.waitForFunction(() => window.__state.calls
      .filter((call) => call.endpoint === "profiles/profile-1/preview").length === 2);
    await page.waitForSelector("#profile-preview-result:not([hidden])");
    const secondPreview = await page.evaluate(() => window.__state.calls
      .filter((call) => call.endpoint === "profiles/profile-1/preview").at(-1));
    check(secondPreview.payload.site_id === "site-b",
      "切换后预览应使用新站点：" + JSON.stringify(secondPreview.payload));
    check(await page.textContent("#profile-preview-source") === "站点B",
      "切换后预览结果应显示新站点");

    // 任务：组合选项不再携带站点说明。
    await page.click('.nav-item[data-view="tasks"]');
    await page.waitForSelector("#tasks-list li");
    await page.getByRole("button", { name: "编辑" }).first().click();
    await page.waitForFunction(() => document.getElementById("task-dialog").open);
    check(await page.$eval("#task-profile option", (node) => node.textContent) === "组合一",
      "任务组合选项只显示组合名");

    // 清空候选：保存被拦，且不发请求。
    while (await page.locator("#task-site-list .task-site-row").count() > 0) {
      await page.locator("#task-site-list button", { hasText: "移除" }).first().click();
    }
    check(await page.locator("#task-site-list .task-site-empty").count() === 1,
      "清空候选后应提示至少添加一个");
    const updatesBefore = await page.evaluate(() => window.__state.calls
      .filter((call) => call.endpoint.endsWith("/update")).length);
    await page.click("#task-save-button");
    await page.waitForFunction(() => {
      const node = document.getElementById("task-form-error");
      return node.hidden === false;
    });
    check((await page.textContent("#task-form-error"))
      .includes("请至少添加一个候选站点后再保存。"), "空候选应给出明确错误");
    check(await page.evaluate(() => window.__state.calls
      .filter((call) => call.endpoint.endsWith("/update")).length) === updatesBefore,
      "空候选不应发出保存请求");

    // 候选添加与排序进入载荷，保存后重开保持顺序。
    await page.selectOption("#task-site-picker", "site-b");
    await page.click("#task-site-add-button");
    await page.selectOption("#task-site-picker", "site-a");
    await page.click("#task-site-add-button");
    const addedOrder = await page.$$eval(
      "#task-site-list .task-site-row .task-site-name",
      (nodes) => nodes.map((node) => node.textContent));
    check(addedOrder.join(",") === "站点B,站点A",
      "按添加顺序渲染候选：" + JSON.stringify(addedOrder));
    await page.locator("#task-site-list .task-site-row").nth(1)
      .locator("button", { hasText: "上移" }).click();
    const movedOrder = await page.$$eval(
      "#task-site-list .task-site-row .task-site-name",
      (nodes) => nodes.map((node) => node.textContent));
    check(movedOrder.join(",") === "站点A,站点B",
      "上移应改变候选顺序：" + JSON.stringify(movedOrder));
    await page.click("#task-save-button");
    await page.waitForFunction(() => window.__state.calls.some(
      (call) => call.endpoint.endsWith("/update") && Array.isArray(call.payload.sites)));
    const updateCall = await page.evaluate(() => window.__state.calls
      .filter((call) => call.endpoint.endsWith("/update")).at(-1));
    check(updateCall.payload.sites.join(",") === "site-a,site-b",
      "保存载荷按界面顺序提交候选：" + JSON.stringify(updateCall.payload.sites));
    await page.waitForFunction(() => document.getElementById("task-dialog").open === false);
    await page.waitForSelector("#tasks-list li");
    await page.getByRole("button", { name: "编辑" }).first().click();
    await page.waitForFunction(() => document.getElementById("task-dialog").open);
    const reopened = await page.$$eval(
      "#task-site-list .task-site-row .task-site-name",
      (nodes) => nodes.map((node) => node.textContent));
    check(reopened.join(",") === "站点A,站点B",
      "保存后重开应保持候选顺序：" + JSON.stringify(reopened));
    await page.close();

    // 一个站点都没有时：仍可建组合，预览明确提示先选站点。
    const empty = await openPage(browser, port, { sites: [], taskSites: [] });
    await empty.click('.nav-item[data-view="profiles"]');
    await empty.waitForSelector("#profiles-list li");
    check(await empty.$eval("#profile-preview-site", (node) => node.disabled),
      "没有站点时预览站点下拉应禁用");
    await empty.selectOption("#profile-preview-select", "profile-1");
    await empty.click("#profile-preview-button");
    await empty.waitForFunction(() => {
      const node = document.getElementById("profile-preview-status");
      return node.hidden === false && node.textContent.includes("请先选择本次预览使用的站点");
    });
    check(true, "没有站点时预览应提示先选站点");
    check(await empty.evaluate(() => window.__state.calls
      .every((call) => !call.endpoint.endsWith("/preview"))),
      "没有站点时不应发出预览请求");
    await empty.click("#create-profile-button");
    await empty.waitForFunction(() => document.getElementById("profile-dialog").open);
    await empty.fill("#profile-name", "无站点组合");
    await empty.click("#profile-save-button");
    await empty.waitForFunction(() => window.__state.calls.some(
      (call) => call.endpoint === "profiles"));
    check(true, "一个站点都没有时仍可保存组合");
    await empty.close();

    // 390px：长名称候选行与页面都不横向溢出。
    const narrow = await openPage(browser, port, {
      viewport: { width: 390, height: 844 },
      sites: LONG_SITES,
      taskSites: ["site-long-a", "site-long-b"],
    });
    await narrow.click('.nav-item[data-view="tasks"]');
    await narrow.waitForSelector("#tasks-list li");
    const pageOverflow = await narrow.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    check(pageOverflow <= 1, "390px 下页面不应横向溢出：" + pageOverflow);
    await narrow.getByRole("button", { name: "编辑" }).first().click();
    await narrow.waitForFunction(() => document.getElementById("task-dialog").open);
    const rowOverflow = await narrow.evaluate(() => {
      const list = document.getElementById("task-site-list");
      const rows = Array.from(list.querySelectorAll(".task-site-row"));
      return {
        list: list.scrollWidth - list.clientWidth,
        rows: rows.map((row) => row.scrollWidth - row.clientWidth),
        count: rows.length,
      };
    });
    check(rowOverflow.count === 2,
      "窄屏应渲染两条候选：" + JSON.stringify(rowOverflow));
    check(rowOverflow.list <= 1,
      "390px 下候选列表不应横向溢出：" + rowOverflow.list);
    check(rowOverflow.rows.every((value) => value <= 1),
      "390px 下长名称候选行不应溢出：" + JSON.stringify(rowOverflow.rows));
    await narrow.close();

    console.log("PROFILE SITE DECOUPLING UI PASS", { assertions });
  } finally {
    await browser.close();
    server.close();
  }
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
