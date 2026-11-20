/*
 * 任务表单「未保存修改」判断的页面回归。
 *
 * 真实 Chromium 加载 views/sites/index.html + app.js，只替换宿主 bridge。
 * 覆盖：名称/栏目/条数/栏目顺序/候选站点/输出方式/计划/UMO 改动都能被识别，
 * 预览与能力测试入口置灰并共用同一条提示；条数三态（空=不限、1-1000=该值、
 * 其他=非法且不改写输入）；撤销后恢复；在途能力测试与预览的旧响应被丢弃。
 *
 * 桩 bridge 的页面逻辑验证，不代表真实 AstrBot 后台/数据库往返。
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

const LABELS = {
  daily_news: "每日新闻",
  bilibili_hot: "哔哩哔哩热门",
  zhihu_hot: "知乎热榜",
  douyin_hot: "抖音热榜",
  toutiao_hot: "今日头条热榜",
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

// task-a 是后面所有编辑用例的对象；task-b 只用于「切换任务」那条。
const TASKS = [
  {
    id: "task-a", name: "任务A",
    items: [
      { content_type: "daily_news", label: LABELS.daily_news, limit: 2 },
      { content_type: "zhihu_hot", label: LABELS.zhihu_hot, limit: null },
    ],
    output_format: "markdown", output_label: "Markdown",
    umos: ["aiocqhttp:GroupMessage:g:1"],
    enabled: true, revision: 3, created_at: "", updated_at: "",
    schedule: { kind: "daily", timezone: "Asia/Shanghai", time: "08:30" },
    schedule_raw: { kind: "daily", timezone: "Asia/Shanghai", time: "08:30" },
    schedule_error: null,
    sites: [
      { id: "site-a", name: "站点A", enabled: true },
      { id: "site-c", name: "站点C", enabled: false },
    ],
  },
  {
    id: "task-b", name: "任务B",
    items: [{ content_type: "bilibili_hot", label: LABELS.bilibili_hot, limit: 4 }],
    output_format: "text", output_label: "纯文字",
    umos: ["aiocqhttp:GroupMessage:g:2"],
    enabled: false, revision: 1, created_at: "", updated_at: "",
    schedule: null, schedule_raw: null, schedule_error: null,
    sites: [{ id: "site-b", name: "站点B", enabled: true }],
  },
  {
    // 五种栏目 + 三个站点全用上：下拉无项可加，首尾行按钮由渲染期决定。
    id: "task-c", name: "任务C",
    items: [
      { content_type: "daily_news", label: LABELS.daily_news, limit: 1 },
      { content_type: "bilibili_hot", label: LABELS.bilibili_hot, limit: 2 },
      { content_type: "zhihu_hot", label: LABELS.zhihu_hot, limit: 3 },
      { content_type: "douyin_hot", label: LABELS.douyin_hot, limit: null },
      { content_type: "toutiao_hot", label: LABELS.toutiao_hot, limit: 4 },
    ],
    output_format: "text", output_label: "纯文字",
    umos: [],
    enabled: false, revision: 2, created_at: "", updated_at: "",
    schedule: null, schedule_raw: null, schedule_error: null,
    sites: [
      { id: "site-a", name: "站点A", enabled: true },
      { id: "site-b", name: "站点B", enabled: true },
      { id: "site-c", name: "站点C", enabled: false },
    ],
  },
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

const DIRTY_HINT =
  "当前修改尚未保存；预览与能力测试都使用已保存的任务配置，请先保存后再试。";
const CAPABILITY_MESSAGE = "候选站点全部可用（桩）。";

async function openPage(browser, port) {
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  await page.addInitScript((fixtures) => {
    window.__calls = [];
    window.__state = { tasks: fixtures.tasks.map((task) => JSON.parse(JSON.stringify(task))) };
    window.__holdMode = { preview: false, test: false };
    window.__holds = { preview: [], test: [] };
    // 每次 capability-test 按调用顺序取一条计划：{kind:"ok"|"fail", message}。
    window.__capabilityPlan = [];
    window.__awaitHold = (kind) => new Promise((resolve) => {
      window.__holds[kind].push(resolve);
    });
    const host = "aiocqhttp";
    const previewFor = (taskId) => {
      const task = window.__state.tasks.find((entry) => entry.id === taskId);
      return {
        task_id: taskId,
        task_name: task ? task.name : taskId,
        task_revision: task ? task.revision : 1,
        items: task ? task.items.map((item) => ({ ...item })) : [],
        site_id: "site-a", site_name: "站点A " + taskId,
        success_count: 1, failure_count: 0,
        sections: [{
          content_type: "daily_news", label: fixtures.labels.daily_news,
          endpoint: "/60s?format=json",
          items: [{ title: "标题-" + taskId }],
          limit: 2, item_count: 1, total_item_count: 1, displayed_count: 1,
          cache: { hit: false, expires_at: null, ttl_seconds: 300 },
          status: "success", source_display: "a.example.test", source_kind: "public",
        }],
        failed_sections: [], status: "complete",
        outputs: { text: "正文-" + taskId, markdown: "**正文-" + taskId + "**" },
        image_snapshot: { id: "snap-1", expires_at: "2026-01-01T00:00:00Z" },
      };
    };
    window.AstrBotPluginView = {
      ready: async () => ({ pageTitle: "60sAPI Hub" }),
      apiGet: async (endpoint) => {
        if (endpoint === "sites") return { sites: fixtures.sites };
        if (endpoint === "tasks") return { tasks: window.__state.tasks };
        if (endpoint === "schedule-runs") return { claims: [], skips: [] };
        if (endpoint === "executions") return { executions: [], total: 0 };
        return {};
      },
      apiPost: async (endpoint, payload) => {
        window.__calls.push({ endpoint, payload });
        if (endpoint.endsWith("/capability-test")) {
          const plan = window.__capabilityPlan.shift() || { kind: "ok" };
          if (window.__holdMode.test) await window.__awaitHold("test");
          if (plan.kind === "fail") throw new Error(plan.message || "桩失败");
          return {
            message: plan.message || fixtures.capabilityMessage,
            satisfied: true, checked_count: 2, truncated: false, cache: null,
          };
        }
        if (endpoint.endsWith("/preview")) {
          if (window.__holdMode.preview) await window.__awaitHold("preview");
          return previewFor(endpoint.split("/")[1]);
        }
        if (endpoint.endsWith("/update")) {
          const id = endpoint.split("/")[1];
          const target = window.__state.tasks.find((task) => task.id === id);
          if (target) {
            // 把真实载荷写回夹具：重开读到的必须是「已保存」的值，而不是原样。
            target.name = payload.name;
            target.items = (payload.items || []).map((item) => ({
              content_type: item.content_type,
              label: fixtures.labels[item.content_type],
              limit: item.limit === undefined ? null : item.limit,
            }));
            target.sites = (payload.sites || []).map((siteId) => {
              const site = fixtures.sites.find((entry) => entry.id === siteId) || {};
              return {
                id: siteId,
                name: site.name || siteId,
                enabled: Boolean(site.enabled),
              };
            });
            target.output_format = payload.output_format;
            target.output_label = payload.output_format === "markdown" ? "Markdown" : "纯文字";
            target.umos = (payload.umos || []).slice();
            if (payload.schedule !== undefined) {
              target.schedule = payload.schedule;
              target.schedule_raw = payload.schedule;
            }
            target.revision += 1;
          }
          return { task: target || null };
        }
        return { host };
      },
    };
  }, {
    sites: SITES, tasks: TASKS, labels: LABELS,
    capabilityMessage: CAPABILITY_MESSAGE,
  });
  await page.goto("http://127.0.0.1:" + port + "/index.html", {
    waitUntil: "domcontentloaded",
  });
  return page;
}

function gate(page) {
  return page.evaluate(() => {
    const hint = document.getElementById("task-capability-result");
    return {
      previewDisabled: document.getElementById("task-preview-form-button").disabled,
      testDisabled: document.getElementById("task-capability-test-button").disabled,
      hintText: hint.textContent,
      hintHidden: hint.hidden,
    };
  });
}

async function expectDirty(page, label) {
  const snapshot = await gate(page);
  check(snapshot.previewDisabled && snapshot.testDisabled,
    label + "：预览与能力测试都置灰 " + JSON.stringify(snapshot));
  check(!snapshot.hintHidden && snapshot.hintText === DIRTY_HINT,
    label + "：共用「未保存」提示 " + snapshot.hintText);
}

async function expectClean(page, label) {
  const snapshot = await gate(page);
  check(!snapshot.previewDisabled && !snapshot.testDisabled,
    label + "：两个入口都恢复可用 " + JSON.stringify(snapshot));
  check(snapshot.hintHidden, label + "：提示消失 " + JSON.stringify(snapshot));
}

async function expectInvalid(page, label, itemName, rowIndex, expectedValue) {
  const snapshot = await gate(page);
  check(snapshot.previewDisabled && snapshot.testDisabled,
    label + "：非法条数时两个入口都置灰 " + JSON.stringify(snapshot));
  check(!snapshot.hintHidden &&
    snapshot.hintText.includes("栏目条数不合法：" + itemName) &&
    snapshot.hintText.includes("修正后才能预览或进行能力测试"),
    label + "：非法条数提示 " + snapshot.hintText);
  check(!snapshot.hintText.includes("当前修改尚未保存"),
    label + "：非法条数不能算成普通的未保存 " + snapshot.hintText);
  const rows = await page.$$eval("#task-item-list .task-item-row", (nodes) =>
    nodes.map((node) => ({
      type: node.dataset.contentType,
      limit: node.querySelector(".task-item-limit-input").value,
    })));
  const row = rows[rowIndex];
  check(row && row.limit === expectedValue,
    label + "：输入框保留原样 " + JSON.stringify(row));
}

async function cancelTaskDialog(page) {
  await page.click("#task-cancel-button");
  if (await page.$("#discard-dialog[open]")) {
    await page.click("#discard-confirm-button");
  }
  await page.waitForFunction(() => !document.getElementById("task-dialog").open);
}

async function setHold(page, kind, on) {
  await page.evaluate(([name, value]) => { window.__holdMode[name] = value; }, [kind, on]);
}

async function waitHold(page, kind, count) {
  await page.waitForFunction(([name, size]) => window.__holds[name].length >= size,
    [kind, count]);
}

async function releaseHold(page, kind) {
  await page.evaluate((name) => {
    const resolve = window.__holds[name].shift();
    if (resolve) resolve();
  }, kind);
}

// 已保存为「不限」的那一行：清空后应当回到干净状态，而不是变成未保存的修改。
const LIMIT_UNLIMITED_ROW =
  "#task-item-list .task-item-row:nth-child(2) .task-item-limit-input";

// 真实键盘输入：先退格清空，再逐字键入，拿到浏览器自己的值/校验态。
async function typeLimit(page, selector, keys) {
  await page.click(selector);
  for (let index = 0; index < 6; index += 1) {
    await page.keyboard.press("Backspace");
  }
  await page.keyboard.type(keys);
}

function limitState(page, selector) {
  return page.evaluate((target) => {
    const input = document.querySelector(target);
    return {
      value: input.value,
      badInput: input.validity.badInput,
      valid: input.validity.valid,
      ariaInvalid: input.getAttribute("aria-invalid"),
      message: input.validationMessage,
    };
  }, selector);
}

// 一行条数输入框的完整状态：值、原生校验、placeholder、aria-invalid。
function rowLimitState(page, rowIndex) {
  return page.evaluate((index) => {
    const row = document.querySelectorAll("#task-item-list .task-item-row")[index];
    if (!row) return null;
    const input = row.querySelector(".task-item-limit-input");
    return {
      type: row.dataset.contentType,
      value: input.value,
      badInput: input.validity.badInput,
      valid: input.validity.valid,
      ariaInvalid: input.getAttribute("aria-invalid"),
      placeholder: input.placeholder,
      message: input.validationMessage,
    };
  }, rowIndex);
}

// 行被重建后仍然是非法：坏的是「用户键入的数字还没成形」，不是那个 DOM 元素。
async function expectStickyInvalidRow(page, label, rowIndex) {
  const state = await rowLimitState(page, rowIndex);
  check(state !== null && state.value === "" && state.badInput === false &&
    state.valid === false && state.ariaInvalid === "true" &&
    state.placeholder === "请输入 1-1000 的整数" &&
    state.message.includes("条数需为 1-1000 的整数"),
    label + "：重绘后该行仍非法（空值 + aria-invalid + 明确 placeholder） " +
    JSON.stringify(state));
  const hints = await page.$$eval("#task-item-list .task-item-row .task-item-limit-hint",
    (nodes) => nodes.filter((node) => !node.hidden).map((node) => node.textContent.trim()));
  check(hints.length === 1 && hints[0].includes("请输入 1-1000 的整数"),
    label + "：非法行有一条可见提示 " + JSON.stringify(hints));
  const snapshot = await gate(page);
  check(snapshot.previewDisabled && snapshot.testDisabled,
    label + "：预览与能力测试都锁着 " + JSON.stringify(snapshot));
}

// 能力测试请求自己记的结算序号：等到旧请求的 finally 真跑过，不靠固定时长。
function capabilitySettled(page) {
  return page.evaluate(() =>
    document.getElementById("task-dialog").dataset.capabilitySettled || "");
}

async function waitCapabilitySettled(page, previous) {
  await page.waitForFunction((prev) => {
    const value = document.getElementById("task-dialog").dataset.capabilitySettled;
    return Boolean(value) && value !== prev;
  }, previous);
}

(async () => {
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const port = server.address().port;
  const browser = await chromium.launch({ executablePath: CHROME, headless: true });
  try {
    const page = await openPage(browser, port);
    page.on("pageerror", (error) => console.error("PAGEERROR", error.message));
    await page.click('.nav-item[data-view="tasks"]');
    await page.waitForSelector("#tasks-list li", { timeout: 10000 });

    // ① 打开编辑：干净表单与已保存状态一致，两个入口都可用。
    await page.locator('li[data-task-id="task-a"] button', { hasText: "编辑" }).click();
    await page.waitForSelector("#task-dialog[open]");
    await expectClean(page, "刚打开编辑");

    // ② 每个字段单独改动都算「未保存修改」，撤销后回到干净状态。
    await page.fill("#task-name", "任务A改");
    await expectDirty(page, "改名");
    await page.fill("#task-name", "任务A");
    await expectClean(page, "撤销改名");

    await page.selectOption("#task-output-format", "text");
    await expectDirty(page, "改输出方式");
    await page.selectOption("#task-output-format", "markdown");
    await expectClean(page, "撤销输出方式");

    await page.fill("#task-umos",
      "aiocqhttp:GroupMessage:g:1\naiocqhttp:FriendMessage:u:2");
    await expectDirty(page, "改 UMO");
    await page.fill("#task-umos", "aiocqhttp:GroupMessage:g:1");
    await expectClean(page, "撤销 UMO");

    await page.fill("#task-item-list .task-item-row:nth-child(1) .task-item-limit-input", "9");
    await expectDirty(page, "调条数");
    await page.fill("#task-item-list .task-item-row:nth-child(1) .task-item-limit-input", "2");
    await expectClean(page, "撤销条数");

    await page.selectOption("#task-item-picker", "bilibili_hot");
    await page.click("#task-item-add-button");
    await page.waitForFunction(() =>
      document.querySelectorAll("#task-item-list .task-item-row").length === 3);
    await expectDirty(page, "加栏目");
    await page.locator("#task-item-list .task-item-row:nth-child(3) button",
      { hasText: "移除" }).click();
    await page.waitForFunction(() =>
      document.querySelectorAll("#task-item-list .task-item-row").length === 2);
    await expectClean(page, "撤销加栏目");

    await page.locator("#task-item-list .task-item-row:nth-child(2) button",
      { hasText: "上移" }).click();
    await expectDirty(page, "调整栏目顺序");
    await page.locator("#task-item-list .task-item-row:nth-child(1) button",
      { hasText: "下移" }).click();
    await expectClean(page, "撤销栏目顺序");

    await page.selectOption("#task-site-picker", "site-b");
    await page.click("#task-site-add-button");
    await page.waitForFunction(() =>
      document.querySelectorAll("#task-site-list .task-site-row").length === 3);
    await expectDirty(page, "加候选站点");
    await page.locator("#task-site-list .task-site-row:nth-child(3) button",
      { hasText: "移除" }).click();
    await page.waitForFunction(() =>
      document.querySelectorAll("#task-site-list .task-site-row").length === 2);
    await expectClean(page, "撤销加候选站点");

    await page.locator("#task-site-list .task-site-row:nth-child(1) button",
      { hasText: "下移" }).click();
    await expectDirty(page, "调整候选顺序");
    await page.locator("#task-site-list .task-site-row:nth-child(2) button",
      { hasText: "上移" }).click();
    await expectClean(page, "撤销候选顺序");

    await page.selectOption("#task-schedule-hour", "09");
    await page.selectOption("#task-schedule-minute", "00");
    await expectDirty(page, "改计划时间");
    await page.selectOption("#task-schedule-hour", "08");
    await page.selectOption("#task-schedule-minute", "30");
    await expectClean(page, "撤销计划时间");

    await page.check("#task-schedule-weekly");
    await expectDirty(page, "改计划 kind");
    await page.check("#task-schedule-daily");
    await expectClean(page, "撤销计划 kind");
    await page.selectOption("#task-schedule-timezone", "UTC");
    await expectDirty(page, "改计划时区");
    await page.selectOption("#task-schedule-timezone", "Asia/Shanghai");
    await expectClean(page, "撤销计划时区");

    // 间隔计划：起点和间隔分钟数都参与判断。
    await page.check("#task-schedule-interval");
    await page.fill("#task-schedule-start-at", "2026-01-01T09:00");
    await page.fill("#task-schedule-interval-minutes", "60");
    await expectDirty(page, "改间隔计划起点与分钟数");
    await page.fill("#task-schedule-start-at", "");
    await page.fill("#task-schedule-interval-minutes", "");
    await expectDirty(page, "间隔计划字段不全");
    // Cron 表达式同样参与判断（空白折叠后比较）。
    await page.check("#task-schedule-cron");
    await page.fill("#task-schedule-expression", "0 8 * * *");
    await expectDirty(page, "改 Cron 表达式");
    await page.check("#task-schedule-daily");
    await page.selectOption("#task-schedule-hour", "08");
    await page.selectOption("#task-schedule-minute", "30");
    await page.selectOption("#task-schedule-timezone", "Asia/Shanghai");
    await expectClean(page, "计划改回已保存的每天 08:30");

    // ③ 条数三态：空=不限，1-1000=该值，其余非法且不改写输入。
    // 已保存「不限」的栏目填 1.5：旧实现会归一成「不限」而误判为未修改。
    await page.fill("#task-item-list .task-item-row:nth-child(2) .task-item-limit-input", "1.5");
    await expectInvalid(page, "不限栏目填 1.5", "知乎热榜", 1, "1.5");
    await page.fill("#task-item-list .task-item-row:nth-child(2) .task-item-limit-input", "");
    await expectClean(page, "不限栏目恢复留空");

    for (const [value, label] of [["0", "0"], ["1001", "1001"], ["1e2", "1e2"], ["-1", "-1"]]) {
      await page.fill("#task-item-list .task-item-row:nth-child(1) .task-item-limit-input", value);
      await expectInvalid(page, "条数 " + label, "每日新闻", 0, value);
    }
    // number 输入框不保留字母；临时换成 text 验证同一判断对非数字文本的态度。
    await page.evaluate(() => {
      const input = document.querySelector(
        "#task-item-list .task-item-row:nth-child(1) .task-item-limit-input");
      input.type = "text";
      input.value = "abc";
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await expectInvalid(page, "条数 abc", "每日新闻", 0, "abc");
    await page.evaluate(() => {
      const input = document.querySelector(
        "#task-item-list .task-item-row:nth-child(1) .task-item-limit-input");
      input.value = "1000";
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    const boundary = await gate(page);
    check(!boundary.hintText.includes("不合法") && boundary.previewDisabled,
      "条数 1000 属于合法值，只算未保存：" + boundary.hintText);

    // ④ 非法条数仍然走既有保存校验，不发请求也不改写输入。
    // 用 1e2 而不是 1.5：1e2 能通过 input[type=number] 的原生校验（=100，整数、在范围内），
    // 所以被拦下只能来自我们自己的条数判断。
    const updatesBefore = await page.evaluate(() =>
      window.__calls.filter((call) => call.endpoint.endsWith("/update")).length);
    await page.evaluate(() => {
      const input = document.querySelector(
        "#task-item-list .task-item-row:nth-child(1) .task-item-limit-input");
      input.type = "number";
      input.value = "1e2";
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await page.click("#task-save-button");
    await page.waitForSelector("#task-form-error:not([hidden])");
    const saveBlocked = await page.evaluate((before) => ({
      updates: window.__calls.filter((call) => call.endpoint.endsWith("/update")).length,
      open: document.getElementById("task-dialog").open,
      error: document.getElementById("task-form-error").textContent,
      value: document.querySelector(
        "#task-item-list .task-item-row:nth-child(1) .task-item-limit-input").value,
    }), updatesBefore);
    check(saveBlocked.updates === updatesBefore && saveBlocked.open,
      "非法条数不保存：" + JSON.stringify(saveBlocked));
    check(saveBlocked.error.includes("条数需为 1-1000 的整数"), "保存前提示条数写法：" + saveBlocked.error);
    check(saveBlocked.value === "1e2", "保存被拦后输入框仍保留 1e2：" + saveBlocked.value);
    await page.fill("#task-item-list .task-item-row:nth-child(1) .task-item-limit-input", "2");
    await expectClean(page, "非法条数改回已保存值");

    // ⑤ 脏表单下点击预览入口不会发出预览请求。
    const previewsBefore = await page.evaluate(() =>
      window.__calls.filter((call) => call.endpoint.endsWith("/preview")).length);
    await page.fill("#task-name", "任务A改");
    await page.evaluate(() => document.getElementById("task-preview-form-button").click());
    await page.waitForTimeout(200);
    const previewsAfter = await page.evaluate(() =>
      window.__calls.filter((call) => call.endpoint.endsWith("/preview")).length);
    check(previewsAfter === previewsBefore, "脏表单不发预览请求：" + previewsAfter);
    await page.fill("#task-name", "任务A");
    await expectClean(page, "预览入口用例撤销改名");

    // ⑥ 在途能力测试：改表单后旧响应（成功）不得写入结果区。
    await setHold(page, "test", true);
    await page.click("#task-capability-test-button");
    await waitHold(page, "test", 1);
    await page.fill("#task-name", "任务A在途");
    const settledBeforeStaleOk = await capabilitySettled(page);
    await releaseHold(page, "test");
    await waitCapabilitySettled(page, settledBeforeStaleOk);
    const capabilityAfter = await page.evaluate(() => ({
      text: document.getElementById("task-capability-result").textContent,
      hidden: document.getElementById("task-capability-result").hidden,
    }));
    check(!capabilityAfter.text.includes("候选站点全部可用"),
      "在途能力测试响应被丢弃：" + capabilityAfter.text);
    check(!capabilityAfter.hidden && capabilityAfter.text === DIRTY_HINT,
      "改表单后的提示不被旧响应覆盖：" + capabilityAfter.text);
    await page.fill("#task-name", "任务A");
    await expectClean(page, "能力测试用例撤销改名");
    await setHold(page, "test", false);
    await page.click("#task-capability-test-button");
    await page.waitForFunction((expected) =>
      document.getElementById("task-capability-result").textContent === expected,
    CAPABILITY_MESSAGE);
    check(true, "干净表单下能力测试正常显示结果");

    // ⑦ 在途预览：改动表单后旧响应（正文）不得写进预览面板。
    await setHold(page, "preview", true);
    await page.click("#task-preview-form-button");
    await waitHold(page, "preview", 1);
    await page.waitForSelector("#task-preview-dialog[open]");
    check(true, "干净表单下预览入口可发起请求");
    await page.evaluate(() => {
      const input = document.getElementById("task-name");
      input.value = "任务A在途预览";
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    const previewStatusMid = await page.textContent("#task-preview-status");
    check(previewStatusMid.includes("已作废"), "改表单后立即作废在途预览：" + previewStatusMid);
    await releaseHold(page, "preview");
    await page.waitForTimeout(300);
    const previewAfterDirty = await page.evaluate(() => ({
      text: document.getElementById("task-preview-output-text").value,
      status: document.getElementById("task-preview-status").textContent,
      metaHidden: document.getElementById("task-preview-meta").hidden,
    }));
    check(!previewAfterDirty.text.includes("正文-task-a"),
      "旧预览响应没有写入正文：" + JSON.stringify(previewAfterDirty));
    check(previewAfterDirty.status.includes("已作废") && previewAfterDirty.metaHidden,
      "旧预览响应没有覆盖状态：" + JSON.stringify(previewAfterDirty));
    await page.click("#task-preview-done-button");
    await page.waitForFunction(() => !document.getElementById("task-preview-dialog").open);
    await setHold(page, "preview", false);

    // ⑧ 关闭编辑弹窗时在途能力测试作废。
    await page.fill("#task-name", "任务A");
    await expectClean(page, "关闭弹窗用例前恢复表单");
    await setHold(page, "test", true);
    await page.click("#task-capability-test-button");
    await waitHold(page, "test", 1);
    await page.fill("#task-name", "任务A待关闭");
    await cancelTaskDialog(page);
    const settledBeforeClose = await capabilitySettled(page);
    await releaseHold(page, "test");
    await waitCapabilitySettled(page, settledBeforeClose);
    const afterClose = await page.evaluate(() => ({
      text: document.getElementById("task-capability-result").textContent,
      dialog: document.getElementById("task-dialog").open,
    }));
    check(!afterClose.dialog, "关闭弹窗后不再打开");
    check(!afterClose.text.includes("候选站点全部可用"),
      "关闭弹窗后旧能力测试响应被丢弃：" + afterClose.text);
    await setHold(page, "test", false);

    // ⑨ 切换任务后，前一个任务的在途预览不得写进新任务的预览面板。
    await setHold(page, "preview", true);
    await page.locator('li[data-task-id="task-a"] button', { hasText: "预览内容" }).click();
    await waitHold(page, "preview", 1);
    await page.click("#task-preview-done-button");
    await page.waitForFunction(() => !document.getElementById("task-preview-dialog").open);
    await page.locator('li[data-task-id="task-b"] button', { hasText: "预览内容" }).click();
    await waitHold(page, "preview", 2);
    await releaseHold(page, "preview");
    await page.waitForTimeout(300);
    const switched = await page.evaluate(() => ({
      text: document.getElementById("task-preview-output-text").value,
      metaHidden: document.getElementById("task-preview-meta").hidden,
    }));
    check(!switched.text.includes("正文-task-a"),
      "切换任务后旧预览响应被丢弃：" + JSON.stringify(switched));
    await releaseHold(page, "preview");
    await page.waitForFunction(() =>
      document.getElementById("task-preview-output-text").value.includes("正文-task-b"));
    check(true, "当前任务的预览正常显示");
    await page.click("#task-preview-done-button");
    await page.waitForFunction(() => !document.getElementById("task-preview-dialog").open);
    await setHold(page, "preview", false);

    // ⑩ 真实键盘输入：number 框里未完成的数字是 badInput（value 为空串），
    // 不能因此当成「不限」。逐字键入，断言浏览器自己的值与校验态。
    await page.locator('li[data-task-id="task-a"] button', { hasText: "编辑" }).click();
    await page.waitForSelector("#task-dialog[open]");
    await expectClean(page, "键盘用例打开编辑");
    const unlimitedStart = await limitState(page, LIMIT_UNLIMITED_ROW);
    check(unlimitedStart.value === "" && unlimitedStart.badInput === false,
      "键盘用例起点是已保存的「不限」 " + JSON.stringify(unlimitedStart));
    await expectClean(page, "键盘用例起点为已保存的不限");

    for (const keys of ["1e", "-", "e"]) {
      await typeLimit(page, LIMIT_UNLIMITED_ROW, keys);
      const typed = await limitState(page, LIMIT_UNLIMITED_ROW);
      check(typed.value === "" && typed.badInput === true,
        "键盘键入 " + keys + "：input.value 是空串但 validity.badInput 为真 " + JSON.stringify(typed));
      check(typed.valid === false && typed.ariaInvalid === "true",
        "键盘键入 " + keys + "：浏览器判定非法且标了 aria-invalid " + JSON.stringify(typed));
      check(typed.message.includes("条数需为 1-1000 的整数") && typed.message.includes("知乎热榜"),
        "键盘键入 " + keys + "：原生校验信息是中文 " + typed.message);
      await expectInvalid(page, "键盘键入 " + keys, "知乎热榜", 1, "");
    }

    const postsBeforeBlocked = await page.evaluate(() => window.__calls.length);
    await page.click("#task-save-button");
    await page.waitForSelector("#task-form-error:not([hidden])");
    const blockedKeyboardSave = await page.evaluate((before) => ({
      requests: window.__calls.length - before,
      updates: window.__calls.filter((call) => call.endpoint.endsWith("/update")).length,
      error: document.getElementById("task-form-error").textContent,
      dialog: document.getElementById("task-dialog").open,
      value: document.querySelector(
        "#task-item-list .task-item-row:nth-child(2) .task-item-limit-input").value,
    }), postsBeforeBlocked);
    check(blockedKeyboardSave.requests === 0 && blockedKeyboardSave.dialog,
      "键盘坏输入点保存零请求且弹窗不关 " + JSON.stringify(blockedKeyboardSave));
    check(blockedKeyboardSave.error.includes("条数需为 1-1000 的整数") &&
      blockedKeyboardSave.error.includes("知乎热榜"),
      "键盘坏输入点保存显示中文错误 " + blockedKeyboardSave.error);
    check(blockedKeyboardSave.value === "",
      "键盘坏输入点保存不改写输入框 " + JSON.stringify(blockedKeyboardSave.value));

    await page.click(LIMIT_UNLIMITED_ROW);
    await page.keyboard.press("Backspace");
    await page.keyboard.press("Backspace");
    const clearedByKeyboard = await limitState(page, LIMIT_UNLIMITED_ROW);
    check(clearedByKeyboard.value === "" && clearedByKeyboard.badInput === false &&
      clearedByKeyboard.valid && clearedByKeyboard.ariaInvalid === null,
      "键盘退格清空后字段恢复合法 " + JSON.stringify(clearedByKeyboard));
    check(await page.evaluate(() => document.getElementById("task-form-error").hidden),
      "键盘退格清空后中文错误收回");
    await expectClean(page, "键盘退格清空后");

    await typeLimit(page, LIMIT_UNLIMITED_ROW, "0");
    const typedZero = await limitState(page, LIMIT_UNLIMITED_ROW);
    check(typedZero.value === "0" && typedZero.badInput === false && typedZero.valid === false,
      "键盘键入 0 仍是非法值 " + JSON.stringify(typedZero));
    await expectInvalid(page, "键盘键入 0", "知乎热榜", 1, "0");
    await typeLimit(page, LIMIT_UNLIMITED_ROW, "1001");
    await expectInvalid(page, "键盘键入 1001", "知乎热榜", 1, "1001");
    await typeLimit(page, LIMIT_UNLIMITED_ROW, "");
    await expectClean(page, "键盘退格清回已保存的不限");

    // ⑪ 两道路由门闩：A 在途 → 改字段作废 A → 改回 → 起点 B → 放行 A。
    // 只有最新一次请求结束才能解除忙状态，旧请求的结果文案也不得落到当前提示。
    const busyText = "正在检查候选站点…";
    await page.evaluate(() => {
      window.__capabilityPlan.push(
        { kind: "ok", message: "旧请求 A 的结果" },
        { kind: "ok", message: "新请求 B 的结果" },
      );
    });
    await setHold(page, "test", true);
    await page.click("#task-capability-test-button");
    await waitHold(page, "test", 1);
    const busyA = await gate(page);
    check(busyA.testDisabled && busyA.hintText === busyText,
      "请求 A 在途：按钮置灰且提示检查中 " + JSON.stringify(busyA));

    await page.fill("#task-name", "任务A在途B");
    await page.fill("#task-name", "任务A");
    await expectClean(page, "作废 A 后表单改回已保存");
    await page.click("#task-capability-test-button");
    await waitHold(page, "test", 2);
    const busyB = await gate(page);
    check(busyB.testDisabled && busyB.hintText === busyText,
      "请求 B 在途：按钮置灰且提示检查中 " + JSON.stringify(busyB));

    const settledBeforeA = await capabilitySettled(page);
    await releaseHold(page, "test");
    await waitCapabilitySettled(page, settledBeforeA);
    const afterStaleA = await gate(page);
    check(afterStaleA.testDisabled === true,
      "旧请求 A 成功后新请求 B 仍在途，按钮必须保持置灰（忙状态没被提前解除）" +
      JSON.stringify(afterStaleA));
    check(afterStaleA.hintText === busyText,
      "旧请求 A 的结果文案不得覆盖 B 的检查中提示 " + JSON.stringify(afterStaleA));
    check(!afterStaleA.hintText.includes("旧请求 A 的结果"),
      "旧请求 A 的结果文案没有出现 " + afterStaleA.hintText);

    await releaseHold(page, "test");
    await page.waitForFunction((expected) =>
      document.getElementById("task-capability-result").textContent === expected,
    "新请求 B 的结果");
    const afterB = await gate(page);
    check(afterB.testDisabled === false && afterB.previewDisabled === false,
      "B 结束后按钮按表单状态恢复可用 " + JSON.stringify(afterB));
    check(afterB.hintText === "新请求 B 的结果", "B 的结果文案落地 " + afterB.hintText);

    // 旧请求以失败结束同样不得解除新请求的忙状态。
    await page.evaluate(() => {
      window.__capabilityPlan.push(
        { kind: "fail", message: "旧请求 A 失败" },
        { kind: "ok", message: "新请求 B2 的结果" },
      );
    });
    await page.click("#task-capability-test-button");
    await waitHold(page, "test", 1);
    await page.fill("#task-name", "任务A在途B2");
    await page.fill("#task-name", "任务A");
    await expectClean(page, "作废失败的 A 后表单改回已保存");
    await page.click("#task-capability-test-button");
    await waitHold(page, "test", 2);
    const settledBeforeFailedA = await capabilitySettled(page);
    await releaseHold(page, "test");
    await waitCapabilitySettled(page, settledBeforeFailedA);
    const afterFailedA = await gate(page);
    check(afterFailedA.testDisabled === true,
      "旧请求 A 以失败结束时也不解除 B2 的忙状态 " + JSON.stringify(afterFailedA));
    check(afterFailedA.hintText === busyText && !afterFailedA.hintText.includes("旧请求 A 失败"),
      "失败的旧请求不得覆盖 B2 的检查中提示 " + afterFailedA.hintText);
    await releaseHold(page, "test");
    await page.waitForFunction((expected) =>
      document.getElementById("task-capability-result").textContent === expected,
    "新请求 B2 的结果");
    const afterB2 = await gate(page);
    check(afterB2.testDisabled === false && afterB2.hintText === "新请求 B2 的结果",
      "B2 结束后按钮恢复可用且显示 B2 的结果 " + JSON.stringify(afterB2));
    await setHold(page, "test", false);
    // ⑫ 原生 badInput（键入 "1e" 得到 value="" + badInput 真）记录的是用户的输入，
    // 不是某个 DOM 元素的一次寿命：上移/下移/增删栏目重建行之后仍然是非法条数。
    // 只有「显式清空（Backspace/Delete）」或「键入合法数字」才算解决。
    await page.fill("#task-name", "任务A刷");
    await page.fill("#task-name", "任务A");
    await expectClean(page, "重建用例前恢复干净表单");

    // 上移：被重建的知乎热榜行仍然非法，保存入口锁死且不产生请求。
    await typeLimit(page, LIMIT_UNLIMITED_ROW, "1e");
    const typedForUp = await rowLimitState(page, 1);
    check(typedForUp.value === "" && typedForUp.badInput === true &&
      typedForUp.placeholder === "请输入 1-1000 的整数",
      "上移：键入 1e 后是空串 + badInput，placeholder 不是「不限」 " + JSON.stringify(typedForUp));
    await page.locator("#task-item-list .task-item-row:nth-child(2) button",
      { hasText: "上移" }).click();
    await expectStickyInvalidRow(page, "上移", 0);
    const movedUpGate = await gate(page);
    check(movedUpGate.hintText.includes("栏目条数不合法：知乎热榜"),
      "上移：重绘后仍然报条数非法 " + movedUpGate.hintText);
    await page.click("#task-save-button");
    await page.waitForSelector("#task-form-error:not([hidden])");
    const blockedAfterUp = await page.evaluate(() => ({
      updates: window.__calls.filter((call) => call.endpoint.endsWith("/update")).length,
      nullLimit: window.__calls.some((call) => call.endpoint.endsWith("/update") &&
        Array.isArray(call.payload.items) &&
        call.payload.items.some((item) => item.content_type === "zhihu_hot" && item.limit === null)),
      dialog: document.getElementById("task-dialog").open,
      error: document.getElementById("task-form-error").textContent,
    }));
    check(blockedAfterUp.updates === 0 && !blockedAfterUp.nullLimit && blockedAfterUp.dialog,
      "上移：非法条数点保存零请求，且没有任何 limit:null 载荷 " + JSON.stringify(blockedAfterUp));
    check(blockedAfterUp.error.includes("条数需为 1-1000 的整数") &&
      blockedAfterUp.error.includes("知乎热榜"),
      "上移：保存被拦并给出可读提示 " + blockedAfterUp.error);
    // 退格才是明确的清空；清完顺序仍是被改过的，所以只剩「未保存」。
    await page.click("#task-item-list .task-item-row:nth-child(1) .task-item-limit-input");
    await page.keyboard.press("Backspace");
    const clearedAfterUp = await rowLimitState(page, 0);
    check(clearedAfterUp.value === "" && clearedAfterUp.badInput === false &&
      clearedAfterUp.valid === true && clearedAfterUp.ariaInvalid === null &&
      clearedAfterUp.placeholder === "不限",
      "上移：Backspace 是明确清空 → 合法不限 " + JSON.stringify(clearedAfterUp));
    check(await page.evaluate(() => document.getElementById("task-form-error").hidden),
      "上移：改正后过期红字立刻收回");
    await expectDirty(page, "上移后顺序仍与已保存不同");
    await page.locator("#task-item-list .task-item-row:nth-child(1) button",
      { hasText: "下移" }).click();
    await expectClean(page, "上移后移回原位");

    // 下移：换个方向重建，坏状态同样跟着走。
    await typeLimit(page, "#task-item-list .task-item-row:nth-child(1) .task-item-limit-input", "1e");
    const typedForDown = await rowLimitState(page, 0);
    check(typedForDown.value === "" && typedForDown.badInput === true,
      "下移：键入 1e 后字段是空串 + badInput " + JSON.stringify(typedForDown));
    await page.locator("#task-item-list .task-item-row:nth-child(1) button",
      { hasText: "下移" }).click();
    await expectStickyInvalidRow(page, "下移", 1);
    check((await rowLimitState(page, 1)).type === "daily_news",
      "下移：被重建的是每日新闻行 " + JSON.stringify(await rowLimitState(page, 1)));
    await page.locator("#task-item-list .task-item-row:nth-child(2) button",
      { hasText: "上移" }).click();
    await typeLimit(page, "#task-item-list .task-item-row:nth-child(1) .task-item-limit-input", "2");
    await expectClean(page, "下移用例复原为已保存的 2");

    // 新增另一个栏目：坏输入行与新增行分开判断，坏的那行保持非法。
    await typeLimit(page, LIMIT_UNLIMITED_ROW, "1e");
    await page.selectOption("#task-item-picker", "bilibili_hot");
    await page.click("#task-item-add-button");
    await page.waitForFunction(() =>
      document.querySelectorAll("#task-item-list .task-item-row").length === 3);
    await expectStickyInvalidRow(page, "新增栏目", 1);
    const addedRow = await rowLimitState(page, 2);
    check(addedRow.type === "bilibili_hot" && addedRow.value === "" && addedRow.badInput === false &&
      addedRow.valid === true && addedRow.ariaInvalid === null && addedRow.placeholder === "不限",
      "新增栏目：新增行是干净的「空 = 不限」 " + JSON.stringify(addedRow));

    // 删除另一个栏目：又一次重建，坏行依旧非法。
    await page.locator("#task-item-list .task-item-row:nth-child(3) button",
      { hasText: "移除" }).click();
    await page.waitForFunction(() =>
      document.querySelectorAll("#task-item-list .task-item-row").length === 2);
    await expectStickyInvalidRow(page, "删除栏目", 1);

    // 删掉坏行本身：它带来的错误提示必须立刻消失，不能挂在表单顶部。
    await page.click("#task-save-button");
    await page.waitForSelector("#task-form-error:not([hidden])");
    await page.locator("#task-item-list .task-item-row:nth-child(2) button",
      { hasText: "移除" }).click();
    await page.waitForFunction(() =>
      document.querySelectorAll("#task-item-list .task-item-row").length === 1);
    check(await page.evaluate(() => document.getElementById("task-form-error").hidden),
      "删除非法行后顶部红字立刻消失");
    const afterRemoveGate = await gate(page);
    check(!afterRemoveGate.hintText.includes("栏目条数不合法"),
      "删除非法行后不再报条数非法 " + afterRemoveGate.hintText);

    // 放弃这次删行：重开回到已保存的两栏。
    await page.click("#task-cancel-button");
    await page.waitForSelector("#discard-dialog[open]");
    await page.click("#discard-confirm-button");
    await page.waitForFunction(() => !document.getElementById("task-dialog").open);
    await page.locator('li[data-task-id="task-a"] button', { hasText: "编辑" }).click();
    await page.waitForSelector("#task-dialog[open]");
    const reopenedRows = await page.$$eval("#task-item-list .task-item-row",
      (nodes) => nodes.map((node) => node.dataset.contentType));
    check(JSON.stringify(reopenedRows) === JSON.stringify(["daily_news", "zhihu_hot"]),
      "放弃删行后重开是已保存的两栏 " + JSON.stringify(reopenedRows));
    await expectClean(page, "放弃后重开");

    // ⑬ Escape 只承担关闭对话框：不清空条数，并按未保存守则弹放弃确认。
    // 这里的输入框值和已保存的一样都是空串，只有整表签名能看出它其实是非法态。
    await page.click(LIMIT_UNLIMITED_ROW);
    await page.keyboard.press("Backspace");
    await page.keyboard.type("1e");
    const beforeEscape = await rowLimitState(page, 1);
    check(beforeEscape.value === "" && beforeEscape.badInput === true,
      "Escape 用例：键入 1e 后是空串 + badInput " + JSON.stringify(beforeEscape));
    await page.keyboard.press("Escape");
    await page.waitForSelector("#discard-dialog[open]");
    const escaped = await page.evaluate(() => ({
      taskOpen: document.getElementById("task-dialog").open,
      discardOpen: document.getElementById("discard-dialog").open,
    }));
    check(escaped.taskOpen && escaped.discardOpen,
      "Escape：只弹出放弃修改确认，任务对话框不关 " + JSON.stringify(escaped));
    const afterEscape = await rowLimitState(page, 1);
    check(afterEscape.value === "" && afterEscape.badInput === true &&
      afterEscape.ariaInvalid === "true" && afterEscape.placeholder === "请输入 1-1000 的整数",
      "Escape：条数没有被清成合法不限 " + JSON.stringify(afterEscape));
    await page.click("#discard-cancel-button");
    await page.waitForFunction(() => !document.getElementById("discard-dialog").open);
    check(await page.evaluate(() => document.getElementById("task-dialog").open),
      "放弃确认里选「继续编辑」后仍留在任务表单");
    const stillInvalid = await rowLimitState(page, 1);
    check(stillInvalid.ariaInvalid === "true" && stillInvalid.value === "",
      "继续编辑后坏输入仍在（Escape 没有顺手抹掉它） " + JSON.stringify(stillInvalid));
    await page.click(LIMIT_UNLIMITED_ROW);
    await page.keyboard.press("Backspace");
    await expectClean(page, "退格清空后表单回到已保存状态");

    // ⑭ 只调顺序也必须是未保存修改：候选站点顺序不在任何控件值里，
    // 只比控件快照会静默丢掉用户的改动。
    await page.locator("#task-site-list .task-site-row:nth-child(1) button",
      { hasText: "下移" }).click();
    await expectDirty(page, "只调候选站点顺序");
    await page.click("#task-cancel-button");
    await page.waitForSelector("#discard-dialog[open]");
    check(await page.evaluate(() => document.getElementById("task-dialog").open),
      "只调候选顺序：取消关闭先弹放弃确认，任务对话框不关");
    await page.click("#discard-cancel-button");
    await page.waitForFunction(() => !document.getElementById("discard-dialog").open);
    const keptSites = await page.$$eval("#task-site-list .task-site-row .task-site-name",
      (nodes) => nodes.map((node) => node.textContent.trim().split("（")[0]));
    check(JSON.stringify(keptSites) === JSON.stringify(["站点C", "站点A"]),
      "选「继续编辑」保留当前候选顺序 " + JSON.stringify(keptSites));
    await page.click("#task-cancel-button");
    await page.waitForSelector("#discard-dialog[open]");
    await page.click("#discard-confirm-button");
    await page.waitForFunction(() => !document.getElementById("task-dialog").open);
    await page.locator('li[data-task-id="task-a"] button', { hasText: "编辑" }).click();
    await page.waitForSelector("#task-dialog[open]");
    const restoredSites = await page.$$eval("#task-site-list .task-site-row .task-site-name",
      (nodes) => nodes.map((node) => node.textContent.trim().split("（")[0]));
    check(JSON.stringify(restoredSites) === JSON.stringify(["站点A", "站点C"]),
      "放弃后重开是已保存的候选顺序 " + JSON.stringify(restoredSites));
    await expectClean(page, "放弃后重开候选顺序干净");

    // 栏目顺序同理。
    await page.locator("#task-item-list .task-item-row:nth-child(1) button",
      { hasText: "下移" }).click();
    await expectDirty(page, "只调栏目顺序");
    await page.click("#task-cancel-button");
    await page.waitForSelector("#discard-dialog[open]");
    await page.click("#discard-cancel-button");
    await page.waitForFunction(() => !document.getElementById("discard-dialog").open);
    const keptItems = await page.$$eval("#task-item-list .task-item-row",
      (nodes) => nodes.map((node) => node.dataset.contentType));
    check(JSON.stringify(keptItems) === JSON.stringify(["zhihu_hot", "daily_news"]),
      "选「继续编辑」保留当前栏目顺序 " + JSON.stringify(keptItems));
    await page.click("#task-cancel-button");
    await page.waitForSelector("#discard-dialog[open]");
    await page.click("#discard-confirm-button");
    await page.waitForFunction(() => !document.getElementById("task-dialog").open);
    await page.locator('li[data-task-id="task-a"] button', { hasText: "编辑" }).click();
    await page.waitForSelector("#task-dialog[open]");
    const restoredItems = await page.$$eval("#task-item-list .task-item-row",
      (nodes) => nodes.map((node) => node.dataset.contentType));
    check(JSON.stringify(restoredItems) === JSON.stringify(["daily_news", "zhihu_hot"]),
      "放弃后重开是已保存的栏目顺序 " + JSON.stringify(restoredItems));

    // ⑮ 保存后重开读到的是真实载荷：有限条数、两种顺序。
    await typeLimit(page, LIMIT_UNLIMITED_ROW, "5");
    await page.locator("#task-item-list .task-item-row:nth-child(1) button",
      { hasText: "下移" }).click();
    await page.locator("#task-site-list .task-site-row:nth-child(1) button",
      { hasText: "下移" }).click();
    await page.click("#task-save-button");
    await page.waitForFunction(() => !document.getElementById("task-dialog").open);
    const savedPayload = await page.evaluate(() => {
      const calls = window.__calls.filter((call) => call.endpoint.endsWith("/update"));
      return calls.length > 0 ? calls[calls.length - 1].payload : null;
    });
    check(savedPayload && Array.isArray(savedPayload.items) &&
      JSON.stringify(savedPayload.items.map((item) => item.content_type)) ===
        JSON.stringify(["zhihu_hot", "daily_news"]) &&
      savedPayload.items[0].limit === 5 && savedPayload.items[1].limit === 2,
      "保存：载荷带真实栏目顺序与有限条数 " + JSON.stringify(savedPayload.items));
    check(savedPayload && JSON.stringify(savedPayload.sites) ===
      JSON.stringify(["site-c", "site-a"]),
      "保存：载荷带真实候选顺序 " + JSON.stringify(savedPayload.sites));

    await page.locator('li[data-task-id="task-a"] button', { hasText: "编辑" }).click();
    await page.waitForSelector("#task-dialog[open]");
    const reopenRows = await page.$$eval("#task-item-list .task-item-row", (nodes) =>
      nodes.map((node) => ({
        type: node.dataset.contentType,
        value: node.querySelector(".task-item-limit-input").value,
      })));
    check(JSON.stringify(reopenRows.map((row) => row.type)) ===
      JSON.stringify(["zhihu_hot", "daily_news"]) &&
      reopenRows[0].value === "5" && reopenRows[1].value === "2",
      "重开：栏目顺序与有限条数都来自保存的值 " + JSON.stringify(reopenRows));
    const reopenSites = await page.$$eval("#task-site-list .task-site-row .task-site-name",
      (nodes) => nodes.map((node) => node.textContent.trim().split("（")[0]));
    check(JSON.stringify(reopenSites) === JSON.stringify(["站点C", "站点A"]),
      "重开：候选顺序来自保存的值 " + JSON.stringify(reopenSites));
    await expectClean(page, "保存后重开是干净的已保存状态");

    // 显式清空 → 保存 → 重开为「不限」（空值与用户清空是同一件事，但必须真的存下来）。
    await typeLimit(page, "#task-item-list .task-item-row:nth-child(1) .task-item-limit-input", "");
    await page.click("#task-save-button");
    await page.waitForFunction(() => !document.getElementById("task-dialog").open);
    const clearedPayload = await page.evaluate(() => {
      const calls = window.__calls.filter((call) => call.endpoint.endsWith("/update"));
      return calls.length > 0 ? calls[calls.length - 1].payload : null;
    });
    const clearedZhihu = clearedPayload && clearedPayload.items
      .find((item) => item.content_type === "zhihu_hot");
    check(clearedZhihu && clearedZhihu.limit === null,
      "显式清空保存后载荷是不限（limit:null，且是用户主动清空的） " +
      JSON.stringify(clearedZhihu));
    await page.locator('li[data-task-id="task-a"] button', { hasText: "编辑" }).click();
    await page.waitForSelector("#task-dialog[open]");
    const unlimitedReopen = await rowLimitState(page, 0);
    check(unlimitedReopen.type === "zhihu_hot" && unlimitedReopen.value === "" &&
      unlimitedReopen.valid === true && unlimitedReopen.ariaInvalid === null &&
      unlimitedReopen.placeholder === "不限",
      "清空保存后重开是不限 " + JSON.stringify(unlimitedReopen));
    await expectClean(page, "清空保存后重开干净");
    await page.click("#task-cancel-button");
    await page.waitForFunction(() => !document.getElementById("task-dialog").open);
    check(!(await page.$("#discard-dialog[open]")),
      "改回原样并保存后再关闭不提示放弃修改");

    // ⑯ 打开任务后渲染期算出的禁用态不能被整体启停抹掉（真实点击验证）。
    await page.locator('li[data-task-id="task-c"] button', { hasText: "编辑" }).click();
    await page.waitForSelector("#task-dialog[open]");
    const navState = await page.evaluate(() => {
      const rows = Array.from(document.querySelectorAll("#task-item-list .task-item-row"));
      const siteRows = Array.from(document.querySelectorAll("#task-site-list .task-site-row"));
      const first = rows[0].querySelectorAll("button");
      const last = rows[rows.length - 1].querySelectorAll("button");
      const siteFirst = siteRows[0].querySelectorAll("button");
      const siteLast = siteRows[siteRows.length - 1].querySelectorAll("button");
      return {
        rows: rows.length, sites: siteRows.length,
        firstUp: first[0].disabled, firstDown: first[1].disabled,
        lastUp: last[0].disabled, lastDown: last[1].disabled,
        siteFirstUp: siteFirst[0].disabled, siteFirstDown: siteFirst[1].disabled,
        siteLastUp: siteLast[0].disabled, siteLastDown: siteLast[1].disabled,
        itemPicker: document.getElementById("task-item-picker").disabled,
        itemAdd: document.getElementById("task-item-add-button").disabled,
        sitePicker: document.getElementById("task-site-picker").disabled,
        siteAdd: document.getElementById("task-site-add-button").disabled,
      };
    });
    check(navState.rows === 5 && navState.firstUp && !navState.firstDown &&
      !navState.lastUp && navState.lastDown,
      "满栏目任务：首行只能下移、末行只能上移 " + JSON.stringify(navState));
    check(navState.itemPicker && navState.itemAdd,
      "满栏目任务：没有可添加的栏目时下拉与添加按钮保持禁用 " + JSON.stringify(navState));
    check(navState.sites === 3 && navState.siteFirstUp && !navState.siteFirstDown &&
      !navState.siteLastUp && navState.siteLastDown,
      "满站点任务：首尾候选行的上移/下移按位置禁用 " + JSON.stringify(navState));
    check(navState.sitePicker && navState.siteAdd,
      "满站点任务：没有可添加的站点时下拉与添加按钮保持禁用 " + JSON.stringify(navState));
    // 真点一下被禁用的按钮：顺序不能动。
    await page.evaluate(() => document.querySelector(
      "#task-item-list .task-item-row:nth-child(1) .task-item-actions button").click());
    const afterDisabledClick = await page.$$eval("#task-item-list .task-item-row",
      (nodes) => nodes.map((node) => node.dataset.contentType));
    check(afterDisabledClick[0] === "daily_news",
      "点被禁用的首行上移没有改变顺序 " + JSON.stringify(afterDisabledClick));
    await page.click("#task-cancel-button");
    await page.waitForFunction(() => !document.getElementById("task-dialog").open);

    console.log("TASK FORM DIRTY UI PASS " + JSON.stringify({ assertions }));
  } finally {
    await browser.close();
    server.close();
  }
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
