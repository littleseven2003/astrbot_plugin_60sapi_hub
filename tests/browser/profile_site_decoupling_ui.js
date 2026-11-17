/*
 * 任务与站点职责分离的页面回归。
 *
 * 验证：任务只按站点 id 选择候选站点、排序与保存往返一致；站点管理仍是唯一创建/
 * 编辑站点的地方；停用或已删除的站点在候选里显式标注且不阻止保存；候选为空时拦下保存。
 *
 * 桩 bridge 的页面逻辑验证，不代表真实宿主或上游。
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

const SITES = [
  { id: "site-a", name: "站点A", base_url: "https://a.example.test/n", enabled: true,
    timeout_seconds: 10, source_kind: "public", has_bearer_token: false, revision: 2,
    updated_at: "" },
  { id: "site-b", name: "站点B", base_url: "https://b.example.test/n", enabled: true,
    timeout_seconds: 10, source_kind: "public", has_bearer_token: false, revision: 1,
    updated_at: "" },
  { id: "site-c", name: "站点C", base_url: "https://c.example.test/n", enabled: false,
    timeout_seconds: 10, source_kind: "self_built", has_bearer_token: true, revision: 5,
    updated_at: "" },
];

const TASKS = [
  {
    id: "task-a", name: "任务A",
    items: [{ content_type: "daily_news", label: "每日新闻", limit: 2 }],
    output_format: "text", output_label: "纯文字",
    umos: ["aiocqhttp:GroupMessage:g:1"], enabled: true, revision: 3,
    created_at: "", updated_at: "",
    schedule: null, schedule_raw: null, schedule_error: null,
    sites: [{ id: "site-a", name: "站点A", enabled: true },
      { id: "site-c", name: "站点C", enabled: false }],
  },
  {
    id: "task-x", name: "任务X",
    items: [{ content_type: "zhihu_hot", label: "知乎热榜", limit: null }],
    output_format: "text", output_label: "纯文字",
    umos: [], enabled: false, revision: 1, created_at: "", updated_at: "",
    schedule: null, schedule_raw: null, schedule_error: null,
    sites: [{ id: "site-gone", name: "已删站点", enabled: false }],
  },
];

const seal = (value) => JSON.parse(JSON.stringify(value));

async function openPage(browser, port) {
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  await page.addInitScript((fixtures) => {
    window.__calls = [];
    window.__state = {
      sites: JSON.parse(JSON.stringify(fixtures.sites)),
      tasks: JSON.parse(JSON.stringify(fixtures.tasks)),
    };
    window.AstrBotPluginView = {
      ready: async () => ({ pageTitle: "60sAPI Hub" }),
      apiGet: async (endpoint) => {
        if (endpoint === "sites") return { sites: window.__state.sites };
        if (endpoint === "tasks") return { tasks: window.__state.tasks };
        if (endpoint === "schedule-runs") return { claims: [], skips: [] };
        if (endpoint === "executions") return { executions: [], total: 0 };
        return {};
      },
      apiPost: async (endpoint, payload) => {
        window.__calls.push({ endpoint, payload });
        if (endpoint.endsWith("/update")) {
          const id = endpoint.split("/")[1];
          const task = window.__state.tasks.find((entry) => entry.id === id);
          if (!task) throw new Error("任务不存在");
          task.items = (payload.items || []).map((item) => ({
            content_type: item.content_type,
            limit: item.limit === undefined ? null : item.limit,
          }));
          task.sites = (payload.sites || []).map((siteId) => {
            const site = window.__state.sites.find((entry) => entry.id === siteId);
            return { id: siteId, name: site ? site.name : siteId,
              enabled: Boolean(site && site.enabled) };
          });
          task.revision += 1;
          return { task };
        }
        if (endpoint === "tasks") {
          throw new Error("本套件不覆盖新增任务");
        }
        return {};
      },
    };
  }, { sites: SITES, tasks: TASKS });
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

    // 站点创建仍只在站点管理里。
    check(await page.locator("#site-dialog").count() === 1, "站点对话框只存在于站点管理");
    await page.click('.nav-item[data-view="sites"]');
    await page.waitForSelector("#sites-list li");
    check((await page.textContent("#create-button")).trim() === "新增站点",
      "站点视图的主动作是新增站点");

    await page.click('.nav-item[data-view="tasks"]');
    await page.locator('li[data-task-id="task-a"] button', { hasText: "编辑" }).click();
    await page.waitForSelector("#task-dialog[open]");
    check(await page.locator("#task-form #task-profile").count() === 0,
      "任务表单不再选择内容组合");
    check(await page.locator("#task-form input[name=\"base_url\"]").count() === 0,
      "任务表单内没有站点地址字段");
    check(await page.locator("#task-form #site-form").count() === 0,
      "任务表单内没有站点表单");

    const rowNames = await page.$$eval("#task-site-list .task-site-name",
      (nodes) => nodes.map((node) => node.textContent));
    check(rowNames.length === 2 && rowNames[0] === "站点A" &&
      rowNames[1] === "站点C（已停用）",
      "候选站点按保存顺序展示并标注停用：" + rowNames.join(" | "));
    const pickerOptions = await page.$$eval("#task-site-picker option",
      (nodes) => nodes.map((node) => node.textContent));
    check(pickerOptions.length === 1 && pickerOptions[0] === "站点B",
      "下拉只列未添加的站点：" + pickerOptions.join(" | "));

    // 顺序：上移/下移只改候选顺序，不改站点本身。
    await page.selectOption("#task-site-picker", "site-b");
    await page.click("#task-site-add-button");
    await page.waitForFunction(() =>
      document.querySelectorAll("#task-site-list .task-site-row").length === 3);
    await page.locator("#task-site-list button", { hasText: "上移" }).nth(2).click();
    await page.locator("#task-site-list button", { hasText: "下移" }).nth(0).click();
    const reordered = await page.$$eval("#task-site-list .task-site-name",
      (nodes) => nodes.map((node) => node.textContent));
    check(reordered.join(",") === "站点B,站点A,站点C（已停用）",
      "候选顺序随上移/下移改变：" + reordered.join(","));

    const sitesBefore = await page.evaluate(() => JSON.stringify(window.__state.sites));
    await page.click("#task-save-button");
    await page.waitForFunction(() =>
      window.__calls.some((call) => call.endpoint.endsWith("/update")));
    const update = await page.evaluate(() =>
      window.__calls.filter((call) => call.endpoint.endsWith("/update")).pop());
    check(JSON.stringify(update.payload.sites) ===
      JSON.stringify(["site-b", "site-a", "site-c"]),
      "保存载荷只带站点 id 且保持顺序：" + JSON.stringify(update.payload.sites));
    check(update.payload.sites.every((entry) => typeof entry === "string"),
      "候选站点载荷是 id 字符串数组");
    check(!("profile_id" in update.payload), "保存载荷不再带 profile_id");
    const sitesAfter = await page.evaluate(() => JSON.stringify(window.__state.sites));
    check(sitesBefore === sitesAfter, "保存任务不会改写站点数据");
    const siteWrites = await page.evaluate(() =>
      window.__calls.filter((call) => call.endpoint.startsWith("sites/")).length);
    check(siteWrites === 0, "保存任务不发任何站点写请求");

    await page.waitForSelector("#tasks-list li");
    const card = await page.textContent('li[data-task-id="task-a"] .task-card-sites');
    check(card.includes("1. 站点B") && card.includes("3. 站点C（已停用）"),
      "卡片按顺序显示候选站点并标注停用：" + card);

    await page.locator('li[data-task-id="task-a"] button', { hasText: "编辑" }).click();
    await page.waitForSelector("#task-dialog[open]");
    const reopened = await page.$$eval("#task-site-list .task-site-name",
      (nodes) => nodes.map((node) => node.textContent));
    check(reopened.join(",") === "站点B,站点A,站点C（已停用）",
      "重开后候选顺序一致：" + reopened.join(","));
    await page.click("#task-cancel-button");
    if (await page.$("#discard-dialog[open]")) {
      await page.click("#discard-confirm-button");
    }

    // 引用了已删除站点：显式标注，仍可移除后保存。
    await page.locator('li[data-task-id="task-x"] button', { hasText: "编辑" }).click();
    await page.waitForSelector("#task-dialog[open]");
    const gone = await page.textContent("#task-site-list .task-site-name");
    check(gone.endsWith("（已删除）"), "已删除站点在候选里标注：" + gone);
    await page.locator("#task-site-list button", { hasText: "移除" }).click();
    await page.waitForFunction(() =>
      document.querySelectorAll("#task-site-list .task-site-row").length === 0);
    const beforeEmpty = await page.evaluate(() =>
      window.__calls.filter((call) => call.endpoint.endsWith("/update")).length);
    await page.click("#task-save-button");
    await page.waitForSelector("#task-form-error:not([hidden])");
    const emptyError = await page.textContent("#task-form-error");
    check(emptyError.includes("请至少添加一个候选站点"), "候选为空时保存被拦：" + emptyError);
    const afterEmpty = await page.evaluate(() =>
      window.__calls.filter((call) => call.endpoint.endsWith("/update")).length);
    check(afterEmpty === beforeEmpty, "候选为空时没有保存请求");

    console.log("TASK SITE DECOUPLING PASS " + JSON.stringify({ assertions }));
  } finally {
    await browser.close();
    server.close();
  }
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
