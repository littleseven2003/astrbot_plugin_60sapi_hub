/*
 * 任务内容栏目（items）编辑、任务卡片摘要与“预览内容”的页面逻辑回归。
 *
 * 真实 Chromium 加载 views/sites/index.html + app.js，只替换宿主 bridge。
 * 覆盖：栏目增删/排序/条数往返一致、栏目只影响本任务、空集合与重复类型被拦、
 * 卡片可读摘要、预览走 POST tasks/<id>/preview 且不带栏目、未保存时不发请求、
 * 两种预览错误文案、390px 无横向溢出。
 *
 * 桩 bridge 的页面逻辑验证，不代表真实 AstrBot 后台/数据库往返，也不连接真实上游或会话。
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

function baseTask(id, name, items, outputFormat, sites) {
  return {
    id, name, items,
    output_format: outputFormat,
    output_label: outputFormat === "markdown" ? "Markdown" : "纯文字",
    umos: ["aiocqhttp:GroupMessage:g:1"],
    enabled: id === "task-a",
    revision: 3, created_at: "", updated_at: "",
    schedule: { kind: "daily", timezone: "Asia/Shanghai", time: "08:30" },
    schedule_raw: { kind: "daily", timezone: "Asia/Shanghai", time: "08:30" },
    schedule_error: null,
    sites,
  };
}

const TASKS = [
  baseTask("task-a", "任务A", [
    { content_type: "daily_news", label: LABELS.daily_news, limit: 2 },
    { content_type: "zhihu_hot", label: LABELS.zhihu_hot, limit: null },
  ], "markdown", [
    { id: "site-a", name: "站点A", enabled: true },
    { id: "site-c", name: "站点C", enabled: false },
  ]),
  baseTask("task-b", "任务B", [
    { content_type: "bilibili_hot", label: LABELS.bilibili_hot, limit: 4 },
  ], "text", [{ id: "site-b", name: "站点B", enabled: true }]),
];

const PNG_BASE64 =
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==";

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

function previewPayload() {
  return {
    task_id: "task-a", task_name: "任务A", task_revision: 3,
    items: TASKS[0].items.map((item) => ({ ...item })),
    site_id: "site-a", site_name: "站点A",
    success_count: 2, failure_count: 0,
    sections: [
      {
        content_type: "daily_news", label: "每日新闻", endpoint: "/60s?format=json",
        items: [{ title: "预览新闻标题一" }],
        limit: 2, item_count: 1, total_item_count: 1, displayed_count: 1,
        cache: { hit: false, expires_at: null, ttl_seconds: 300 },
        status: "success", source_display: "a.example.test", source_kind: "public",
      },
      {
        content_type: "zhihu_hot", label: "知乎热榜", endpoint: "/zhihu?format=json",
        items: [{ title: "预览热榜标题一" }],
        limit: null, item_count: 1, total_item_count: 1, displayed_count: 1,
        cache: { hit: true, expires_at: null, ttl_seconds: 300 },
        status: "success", source_display: "自建 60s 服务", source_kind: "self_built",
      },
    ],
    failed_sections: [],
    status: "complete",
    outputs: { text: "预览正文：今日要闻", markdown: "**预览正文**" },
    image_snapshot: { id: "snap-1", expires_at: "2026-01-01T00:00:00Z" },
  };
}

async function openPage(browser, port, viewport) {
  const page = await browser.newPage({ viewport: viewport || { width: 1440, height: 900 } });
  await page.addInitScript((fixtures) => {
    window.__calls = [];
    window.__state = { tasks: fixtures.tasks.map((task) => JSON.parse(JSON.stringify(task))) };
    window.__previewMode = "ok";
    window.__createdCount = 0;
    const labelOf = (type) => fixtures.labels[type] || type;
    const decorate = (items, sites) => ({
      items: (items || []).map((item) => ({
        content_type: item.content_type,
        label: labelOf(item.content_type),
        limit: item.limit === null || item.limit === undefined ? null : item.limit,
      })),
      sites: (sites || []).map((id) => {
        const site = fixtures.sites.find((entry) => entry.id === id);
        return {
          id,
          name: site ? site.name : id,
          enabled: Boolean(site && site.enabled),
        };
      }),
    });
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
        if (endpoint === "tasks") {
          window.__createdCount += 1;
          const created = Object.assign(
            {},
            fixtures.tasks[0],
            {
              id: "task-new-" + window.__createdCount,
              name: payload.name,
              revision: 1,
              enabled: false,
              output_format: payload.output_format,
              output_label: payload.output_format === "markdown" ? "Markdown" : "纯文字",
              umos: payload.umos || [],
              schedule: payload.schedule || null,
            },
            decorate(payload.items, payload.sites),
          );
          window.__state.tasks.push(created);
          return { task: created };
        }
        if (endpoint.endsWith("/update")) {
          const id = endpoint.split("/")[1];
          const target = window.__state.tasks.find((task) => task.id === id);
          if (!target) throw new Error("任务不存在");
          Object.assign(target, decorate(payload.items, payload.sites), {
            name: payload.name, revision: target.revision + 1,
          });
          return { task: target };
        }
        if (endpoint.endsWith("/preview")) {
          if (window.__previewMode === "content_unavailable") {
            const error = new Error("没有候选站点能同时提供全部栏目，请调整栏目或候选站点。");
            error.code = "content_unavailable";
            error.data = { code: "content_unavailable" };
            throw error;
          }
          if (window.__previewMode === "revision") {
            const error = new Error("任务配置在获取期间发生变化，本次预览已取消，请重试。");
            error.code = "preview_revision_changed";
            error.data = { code: "preview_revision_changed" };
            throw error;
          }
          if (window.__previewMode === "boom") {
            throw new Error("upstream_500");
          }
          return fixtures.preview;
        }
        if (endpoint.endsWith("/image")) {
          return {
            images: [{
              index: 1, content_type: "daily_news", label: "每日新闻",
              status: "success", png_base64: fixtures.png,
              width: 200, height: 300, error: null,
            }],
          };
        }
        return {};
      },
    };
  }, { sites: SITES, tasks: TASKS, labels: LABELS, preview: previewPayload(), png: PNG_BASE64 });
  await page.goto("http://127.0.0.1:" + port + "/index.html", {
    waitUntil: "domcontentloaded",
  });
  return page;
}

function calls(page) {
  return page.evaluate(() => window.__calls.map((call) => call.endpoint));
}

async function addItem(page, type) {
  const before = await page.$$eval("#task-item-list .task-item-row", (nodes) => nodes.length);
  await page.selectOption("#task-item-picker", type);
  await page.click("#task-item-add-button");
  await page.waitForFunction(
    (count) => document.querySelectorAll("#task-item-list .task-item-row").length === count,
    before + 1,
  );
}

async function addSite(page, id) {
  const before = await page.$$eval("#task-site-list .task-site-row", (nodes) => nodes.length);
  await page.selectOption("#task-site-picker", id);
  await page.click("#task-site-add-button");
  await page.waitForFunction(
    (count) => document.querySelectorAll("#task-site-list .task-site-row").length === count,
    before + 1,
  );
}

async function itemSnapshot(page) {
  return page.evaluate(() => {
    const rows = Array.from(document.querySelectorAll("#task-item-list .task-item-row"));
    return rows.map((row) => ({
      type: row.dataset.contentType,
      label: row.querySelector(".task-item-label").textContent,
      limit: row.querySelector(".task-item-limit-input").value,
    }));
  });
}

async function cancelTaskDialog(page) {
  await page.click("#task-cancel-button");
  if (await page.$("#discard-dialog[open]")) {
    await page.click("#discard-confirm-button");
  }
  await page.waitForFunction(() => !document.getElementById("task-dialog").open);
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

    // ① 新建任务：三个栏目、设置条数、上移一次，保存后重开一致。
    await page.click("#create-task-button");
    await page.waitForSelector("#task-dialog[open]");
    await page.fill("#task-name", "新任务");
    await addItem(page, "daily_news");
    await addItem(page, "bilibili_hot");
    await addItem(page, "zhihu_hot");
    check((await itemSnapshot(page)).map((row) => row.type).join(",") ===
      "daily_news,bilibili_hot,zhihu_hot", "栏目按添加顺序排列");
    await page.fill("#task-item-list .task-item-row:nth-child(1) .task-item-limit-input", "5");
    await page.fill("#task-item-list .task-item-row:nth-child(3) .task-item-limit-input", "3");
    await page.locator("#task-item-list button", { hasText: "上移" }).nth(2).click();
    const moved = await itemSnapshot(page);
    check(moved.map((row) => row.type).join(",") === "daily_news,zhihu_hot,bilibili_hot",
      "上移后顺序：" + moved.map((row) => row.label).join(","));
    check(moved[0].limit === "5" && moved[1].limit === "3" && moved[2].limit === "",
      "上移后条数随栏目移动：" + JSON.stringify(moved.map((row) => row.limit)));
    await addSite(page, "site-a");
    await page.fill("#task-umos", "aiocqhttp:GroupMessage:g:1");
    await page.click("#task-save-button");
    await page.waitForFunction(() => window.__calls.some((call) => call.endpoint === "tasks"));
    const create = await page.evaluate(() =>
      window.__calls.find((call) => call.endpoint === "tasks"));
    check(JSON.stringify(create.payload.items) === JSON.stringify([
      { content_type: "daily_news", limit: 5 },
      { content_type: "zhihu_hot", limit: 3 },
      { content_type: "bilibili_hot", limit: null },
    ]), "保存载荷栏目顺序与条数：" + JSON.stringify(create.payload.items));
    check(!("profile_id" in create.payload), "保存载荷不再带 profile_id");
    await page.waitForSelector('li[data-task-id="task-new-1"]');
    await page.locator('li[data-task-id="task-new-1"] button', { hasText: "编辑" }).click();
    await page.waitForSelector("#task-dialog[open]");
    const reopened = await itemSnapshot(page);
    check(reopened.map((row) => row.type).join(",") === "daily_news,zhihu_hot,bilibili_hot",
      "重开后栏目顺序一致：" + reopened.map((row) => row.label).join(","));
    check(reopened.map((row) => row.limit).join("|") === "5|3|",
      "重开后条数一致：" + JSON.stringify(reopened.map((row) => row.limit)));

    // ② 编辑任务A的栏目不影响任务B（桩里两个任务）。
    const beforeB = await page.evaluate(() =>
      JSON.stringify(window.__state.tasks.find((task) => task.id === "task-b").items));
    await page.fill("#task-item-list .task-item-row:nth-child(1) .task-item-limit-input", "7");
    await addItem(page, "douyin_hot");
    await page.click("#task-save-button");
    await page.waitForFunction(() =>
      window.__calls.some((call) => call.endpoint.endsWith("/update")));
    const update = await page.evaluate(() =>
      window.__calls.filter((call) => call.endpoint.endsWith("/update")).pop());
    check(JSON.stringify(update.payload.items) === JSON.stringify([
      { content_type: "daily_news", limit: 7 },
      { content_type: "zhihu_hot", limit: 3 },
      { content_type: "bilibili_hot", limit: null },
      { content_type: "douyin_hot", limit: null },
    ]), "另一分支保存载荷：" + JSON.stringify(update.payload.items));
    const afterB = await page.evaluate(() =>
      JSON.stringify(window.__state.tasks.find((task) => task.id === "task-b").items));
    check(beforeB === afterB, "任务B的栏目未被A的编辑改动：" + afterB);
    await page.waitForSelector("#tasks-list li");
    const cardB = await page.textContent('li[data-task-id="task-b"] .task-card-items');
    check(cardB.includes("哔哩哔哩热门 最多 4 条"), "任务B卡片栏目摘要不变：" + cardB);

    // ③ 栏目为空时保存被拦：错误文案可见且没有 POST /tasks。
    const createCount = await page.evaluate(() =>
      window.__calls.filter((call) => call.endpoint === "tasks").length);
    await page.click("#create-task-button");
    await page.waitForSelector("#task-dialog[open]");
    await page.fill("#task-name", "空栏目任务");
    await addSite(page, "site-a");
    await page.click("#task-save-button");
    await page.waitForSelector("#task-form-error:not([hidden])");
    const emptyError = await page.textContent("#task-form-error");
    check(emptyError.includes("请至少添加一个内容栏目"), "空栏目错误文案：" + emptyError);
    const createAfter = await page.evaluate(() =>
      window.__calls.filter((call) => call.endpoint === "tasks").length);
    check(createAfter === createCount, "空栏目时不应发出新增请求");

    // ④ 重复类型与非法条数被拦。
    await addItem(page, "daily_news");
    const options = await page.$$eval("#task-item-picker option", (nodes) =>
      nodes.map((node) => node.value));
    check(!options.includes("daily_news"), "已添加的栏目不再出现在可选列表：" + options);
    await page.evaluate(() => {
      const picker = document.getElementById("task-item-picker");
      const option = document.createElement("option");
      option.value = "daily_news";
      option.textContent = "每日新闻";
      picker.append(option);
      picker.value = "daily_news";
    });
    await page.click("#task-item-add-button");
    await page.waitForTimeout(150);
    const dupRows = await page.$$eval("#task-item-list .task-item-row", (nodes) => nodes.length);
    check(dupRows === 1, "重复类型不会被加成第二行：" + dupRows);
    const postsBefore = await page.evaluate(() =>
      window.__calls.filter((call) => call.endpoint === "tasks").length);
    await page.fill("#task-item-list .task-item-row:nth-child(1) .task-item-limit-input", "0");
    await page.click("#task-save-button");
    await page.waitForTimeout(200);
    const invalidBlocked = await page.evaluate((before) => ({
      posts: window.__calls.filter((call) => call.endpoint === "tasks").length,
      invalid: Boolean(document.querySelector(
        "#task-item-list .task-item-row:nth-child(1) .task-item-limit-input:invalid")),
      open: document.getElementById("task-dialog").open,
    }), postsBefore);
    check(invalidBlocked.posts === postsBefore, "非法条数不发保存请求：" + invalidBlocked.posts);
    check(invalidBlocked.invalid, "非法条数被输入约束标为无效");
    check(invalidBlocked.open, "非法条数时表单不关闭");
    await page.fill("#task-item-list .task-item-row:nth-child(1) .task-item-limit-input", "");
    await cancelTaskDialog(page);
    const taskCount = await page.$$eval("#tasks-list li", (nodes) => nodes.length);
    check(taskCount === 3, "被拦下的表单没有新增任务：" + taskCount);

    // ⑤ 任务卡片不展开即可读懂。
    const cardA = await page.textContent('li[data-task-id="task-a"]');
    check(cardA.includes("栏目：每日新闻 最多 2 条 · 知乎热榜 不限条数"),
      "卡片栏目与条数：" + cardA.slice(0, 120));
    check(cardA.includes("输出：Markdown"), "卡片显示输出方式");
    check(cardA.includes("Asia/Shanghai"), "卡片显示计划与时区");
    check(cardA.includes("候选站点：1. 站点A → 2. 站点C（已停用）"),
      "卡片显示候选顺序与停用标记");
    check(cardA.includes("已启用 · 定时调度"), "卡片显示启用状态");
    check(cardA.includes("目标数量：1 个 UMO"), "卡片显示 UMO 数量");
    const cardButtons = await page.$$eval('li[data-task-id="task-a"] .task-card-actions button',
      (nodes) => nodes.map((node) => node.textContent.trim()));
    check(cardButtons.includes("预览内容") && cardButtons.includes("测试投递") &&
      cardButtons.includes("编辑") && cardButtons.includes("删除"),
      "卡片操作按钮：" + cardButtons.join(","));

    // ⑥ 预览内容：POST tasks/<id>/preview，载荷不带栏目；显示站点、文字与长图；不发投递请求。
    await page.locator('li[data-task-id="task-a"] button', { hasText: "预览内容" }).click();
    await page.waitForSelector("#task-preview-dialog[open]");
    await page.waitForFunction(() =>
      window.__calls.some((call) => call.endpoint === "tasks/task-a/preview"));
    const previewCall = await page.evaluate(() =>
      window.__calls.find((call) => call.endpoint === "tasks/task-a/preview"));
    check(JSON.stringify(previewCall.payload) === JSON.stringify({ force_refresh: false }),
      "预览载荷只用已保存配置：" + JSON.stringify(previewCall.payload));
    await page.waitForSelector("#task-preview-meta:not([hidden])");
    const meta = await page.textContent("#task-preview-meta");
    check(meta.includes("站点A"), "预览元信息显示实际站点：" + meta);
    check(meta.includes("a.example.test"), "预览元信息显示公开来源域名：" + meta);
    check(meta.includes("成功 2 个栏目"), "预览元信息显示成功栏目数：" + meta);
    const outputText = await page.inputValue("#task-preview-output-text");
    check(outputText.includes("预览正文"), "预览展示文字输出：" + outputText);
    await page.check('input[name="task-preview-output-format"][value="markdown"]');
    const outputMarkdown = await page.inputValue("#task-preview-output-text");
    check(outputMarkdown.includes("**预览正文**"), "预览可切换到 Markdown：" + outputMarkdown);
    await page.check('input[name="task-preview-output-format"][value="text"]');
    const sectionTitles = await page.$$eval("#task-preview-sections h3",
      (nodes) => nodes.map((node) => node.textContent));
    check(sectionTitles.join(",") === "每日新闻,知乎热榜", "预览按栏目展示：" + sectionTitles);
    await page.click("#task-preview-image-button");
    await page.waitForFunction(() =>
      window.__calls.some((call) => call.endpoint === "tasks/task-a/image"));
    const imageCall = await page.evaluate(() =>
      window.__calls.find((call) => call.endpoint === "tasks/task-a/image"));
    check(JSON.stringify(imageCall.payload) === JSON.stringify({ snapshot_id: "snap-1" }),
      "长图请求用快照 id：" + JSON.stringify(imageCall.payload));
    await page.waitForSelector("#task-preview-image-pages .preview-image-page img");
    const imageSrc = await page.getAttribute("#task-preview-image-pages .preview-image-page img",
      "src");
    check(imageSrc.startsWith("data:image/png;base64,"), "栏目长图按快照绘制");
    const imageState = await page.textContent("#task-preview-image-state");
    check(imageState.includes("已生成 1 张栏目长图"), "长图状态：" + imageState);
    await page.locator("#task-preview-image-pages button", { hasText: "查看大图" }).click();
    await page.waitForSelector("#preview-image-viewer[open]");
    check(true, "长图查看器可打开");
    await page.click("#preview-image-viewer-close");
    const endpoints = await calls(page);
    check(!endpoints.some((endpoint) => /\/test$/.test(endpoint)),
      "预览不触发投递请求：" + endpoints.join(","));
    await page.click("#task-preview-done-button");
    await page.waitForFunction(() => !document.getElementById("task-preview-dialog").open);

    // ⑦ 有未保存改动时预览与能力测试入口一起置灰并给出共用提示，零请求；撤销后恢复。
    const previewCount = await page.evaluate(() =>
      window.__calls.filter((call) => call.endpoint.endsWith("/preview")).length);
    const testCount = await page.evaluate(() =>
      window.__calls.filter((call) => call.endpoint.endsWith("/capability-test")).length);
    await page.locator('li[data-task-id="task-a"] button', { hasText: "编辑" }).click();
    await page.waitForSelector("#task-dialog[open]");
    const cleanGate = await page.evaluate(() => ({
      preview: document.getElementById("task-preview-form-button").disabled,
      test: document.getElementById("task-capability-test-button").disabled,
      hintHidden: document.getElementById("task-capability-result").hidden,
    }));
    check(!cleanGate.preview && !cleanGate.test && cleanGate.hintHidden,
      "未改动时两个入口都可用且没有提示：" + JSON.stringify(cleanGate));
    await page.fill("#task-item-list .task-item-row:nth-child(1) .task-item-limit-input", "9");
    const dirtyGate = await page.evaluate(() => ({
      preview: document.getElementById("task-preview-form-button").disabled,
      test: document.getElementById("task-capability-test-button").disabled,
      hint: document.getElementById("task-capability-result").textContent,
      hintHidden: document.getElementById("task-capability-result").hidden,
    }));
    check(dirtyGate.preview && dirtyGate.test,
      "未保存改动时预览与能力测试都置灰：" + JSON.stringify(dirtyGate));
    check(!dirtyGate.hintHidden && dirtyGate.hint.includes("预览与能力测试都使用已保存的任务配置"),
      "未保存改动时给出共用提示：" + dirtyGate.hint);
    const previewAfter = await page.evaluate(() =>
      window.__calls.filter((call) => call.endpoint.endsWith("/preview")).length);
    check(previewAfter === previewCount, "未保存改动时不发预览请求");
    const testAfter = await page.evaluate(() =>
      window.__calls.filter((call) => call.endpoint.endsWith("/capability-test")).length);
    check(testAfter === testCount, "未保存改动时不发能力测试请求");
    await page.fill("#task-item-list .task-item-row:nth-child(1) .task-item-limit-input", "2");
    const restoredGate = await page.evaluate(() => ({
      preview: document.getElementById("task-preview-form-button").disabled,
      test: document.getElementById("task-capability-test-button").disabled,
      hintHidden: document.getElementById("task-capability-result").hidden,
    }));
    check(!restoredGate.preview && !restoredGate.test && restoredGate.hintHidden,
      "撤销修改后入口恢复且提示消失：" + JSON.stringify(restoredGate));
    await cancelTaskDialog(page);

    // ⑧ 两种预览错误各有可读文案。
    await page.evaluate(() => { window.__previewMode = "content_unavailable"; });
    await page.locator('li[data-task-id="task-a"] button', { hasText: "预览内容" }).click();
    await page.waitForFunction(() => {
      const node = document.getElementById("task-preview-status");
      return node && !node.hidden && node.textContent.includes("没有候选站点");
    });
    const unavailable = await page.textContent("#task-preview-status");
    check(unavailable === "没有候选站点能同时提供全部栏目，请调整栏目或候选站点后再预览。",
      "content_unavailable 文案：" + unavailable);
    await page.evaluate(() => { window.__previewMode = "revision"; });
    await page.click("#task-preview-refresh-button");
    await page.waitForFunction(() => {
      const node = document.getElementById("task-preview-status");
      return node && node.textContent.includes("任务配置在获取期间发生变化");
    });
    const changed = await page.textContent("#task-preview-status");
    check(changed === "任务配置在获取期间发生变化，本次预览已取消，请重试。",
      "preview_revision_changed 文案：" + changed);
    await page.evaluate(() => { window.__previewMode = "ok"; });
    await page.click("#task-preview-done-button");

    // ⑨ 390x844 无横向溢出，主要按钮可见。
    const narrow = await openPage(browser, port, { width: 390, height: 844 });
    await narrow.click('.nav-item[data-view="tasks"]');
    await narrow.waitForSelector("#tasks-list li");
    await narrow.click("#create-task-button");
    await narrow.waitForSelector("#task-dialog[open]");
    await addItem(narrow, "daily_news");
    await addItem(narrow, "bilibili_hot");
    await addItem(narrow, "zhihu_hot");
    await addSite(narrow, "site-a");
    await narrow.fill("#task-item-list .task-item-row:nth-child(1) .task-item-limit-input", "12");
    const metrics = await narrow.evaluate(() => {
      const doc = document.documentElement;
      const overflow = (selector) => Array.from(document.querySelectorAll(selector))
        .map((node) => node.scrollWidth - node.clientWidth);
      const rect = (id) => document.getElementById(id).getBoundingClientRect().toJSON();
      const list = document.getElementById("task-item-list");
      return {
        doc: doc.scrollWidth - doc.clientWidth,
        body: document.body.scrollWidth - doc.clientWidth,
        itemRows: overflow("#task-item-list .task-item-row"),
        siteRows: overflow("#task-site-list .task-site-row"),
        itemList: list.scrollWidth - list.clientWidth,
        dialog: document.getElementById("task-dialog").getBoundingClientRect().width,
        save: rect("task-save-button"),
        preview: rect("task-preview-form-button"),
        picker: rect("task-item-picker"),
      };
    });
    check(metrics.doc <= 1, "390px 页面无横向溢出：" + metrics.doc);
    check(metrics.body <= 1, "390px body 无横向溢出：" + metrics.body);
    check(metrics.itemList <= 1, "390px 栏目列表无横向溢出：" + metrics.itemList);
    check(metrics.itemRows.every((value) => value <= 1),
      "390px 栏目行无横向溢出：" + metrics.itemRows.join(","));
    check(metrics.siteRows.every((value) => value <= 1),
      "390px 候选行无横向溢出：" + metrics.siteRows.join(","));
    check(metrics.dialog <= 390, "390px 对话框不超出视口：" + metrics.dialog);
    check(metrics.picker.width > 120 && metrics.picker.width + metrics.picker.left <= 390,
      "390px 栏目下拉可见：" + JSON.stringify(metrics.picker));
    for (const [name, box] of [["保存", metrics.save], ["预览内容", metrics.preview]]) {
      check(box.width > 40 && box.left >= 0 && box.left + box.width <= 390,
        "390px " + name + "按钮完整可见：" + JSON.stringify(box));
    }
    await narrow.locator("#task-save-button").scrollIntoViewIfNeeded();
    const hit = await narrow.evaluate(() => {
      const button = document.getElementById("task-save-button");
      const box = button.getBoundingClientRect();
      const node = document.elementFromPoint(
        box.left + box.width / 2, box.top + box.height / 2);
      return {
        top: box.top,
        bottom: box.bottom,
        hit: Boolean(node) && (node === button || button.contains(node)),
      };
    });
    check(hit.hit, "390px 保存按钮未被遮挡");
    check(hit.top >= 0 && hit.bottom <= 844, "390px 保存按钮在视口内：" + JSON.stringify(hit));

    // 非法提示在 390px 也不能把行或页面撑出横向溢出。
    await narrow.click("#task-item-list .task-item-row:nth-child(1) .task-item-limit-input");
    for (let index = 0; index < 2; index += 1) {
      await narrow.keyboard.press("Backspace");
    }
    await narrow.keyboard.type("1e");
    const hintMetrics = await narrow.evaluate(() => {
      const row = document.querySelector("#task-item-list .task-item-row");
      const hint = row.querySelector(".task-item-limit-hint");
      const input = row.querySelector(".task-item-limit-input");
      return {
        hidden: hint.hidden,
        text: hint.textContent.trim(),
        ariaInvalid: input.getAttribute("aria-invalid"),
        rowOverflow: row.scrollWidth - row.clientWidth,
        rowWidth: row.getBoundingClientRect().width,
        hintWidth: hint.getBoundingClientRect().width,
        doc: document.documentElement.scrollWidth - document.documentElement.clientWidth,
      };
    });
    check(hintMetrics.hidden === false && hintMetrics.text.includes("1-1000") &&
      hintMetrics.ariaInvalid === "true" && hintMetrics.rowOverflow <= 1 &&
      hintMetrics.doc <= 1 && hintMetrics.hintWidth <= hintMetrics.rowWidth + 1,
    "390px 非法条数提示可见且不撑破行：" + JSON.stringify(hintMetrics));

    // ⑩ 真实键盘输入条数：正常键入按数字保存；未完成的数字（badInput，value 为空串）
    // 不许当成「不限」发出去，也不许改写输入框。
    // 任务A的第二行是已保存的「不限」（zhihu_hot）。
    const unlimitedInput = "#task-item-list .task-item-row:nth-child(2) .task-item-limit-input";
    await page.locator('li[data-task-id="task-a"] button', { hasText: "编辑" }).click();
    await page.waitForSelector("#task-dialog[open]");
    await page.click(unlimitedInput);
    for (let index = 0; index < 6; index += 1) {
      await page.keyboard.press("Backspace");
    }
    await page.keyboard.type("6");
    await page.click("#task-save-button");
    await page.waitForFunction(() => window.__calls.some((call) =>
      call.endpoint.endsWith("/update") && call.payload.items.some((item) => item.limit === 6)));
    const typedUpdate = await page.evaluate(() =>
      window.__calls.filter((call) => call.endpoint.endsWith("/update")).pop());
    check(JSON.stringify(typedUpdate.payload.items) === JSON.stringify([
      { content_type: "daily_news", limit: 2 },
      { content_type: "zhihu_hot", limit: 6 },
    ]), "键盘键入的条数按数字保存：" + JSON.stringify(typedUpdate.payload.items));
    await page.waitForFunction(() => !document.getElementById("task-dialog").open);

    await page.locator('li[data-task-id="task-a"] button', { hasText: "编辑" }).click();
    await page.waitForSelector("#task-dialog[open]");
    await page.click(unlimitedInput);
    for (let index = 0; index < 6; index += 1) {
      await page.keyboard.press("Backspace");
    }
    await page.keyboard.type("1e");
    const badTyped = await page.evaluate((selector) => {
      const input = document.querySelector(selector);
      return {
        value: input.value,
        badInput: input.validity.badInput,
        valid: input.validity.valid,
        ariaInvalid: input.getAttribute("aria-invalid"),
        message: input.validationMessage,
      };
    }, unlimitedInput);
    check(badTyped.value === "" && badTyped.badInput === true,
      "键盘键入 1e：input.value 为空但 validity.badInput 为真 " + JSON.stringify(badTyped));
    check(badTyped.valid === false && badTyped.ariaInvalid === "true" &&
      badTyped.message.includes("知乎热榜的条数需为 1-1000 的整数"),
      "键盘键入 1e：中文校验信息与 aria-invalid " + JSON.stringify(badTyped));
    const requestsBeforeBadSave = await page.evaluate(() => window.__calls.length);
    await page.click("#task-save-button");
    await page.waitForSelector("#task-form-error:not([hidden])");
    const badSaveBlocked = await page.evaluate((before) => ({
      requests: window.__calls.length - before,
      error: document.getElementById("task-form-error").textContent,
      dialog: document.getElementById("task-dialog").open,
      value: document.querySelector(
        "#task-item-list .task-item-row:nth-child(2) .task-item-limit-input").value,
    }), requestsBeforeBadSave);
    check(badSaveBlocked.requests === 0 && badSaveBlocked.dialog,
      "键盘坏输入不发出任何请求 " + JSON.stringify(badSaveBlocked));
    check(badSaveBlocked.error.includes("条数需为 1-1000 的整数") &&
      badSaveBlocked.error.includes("知乎热榜"),
      "键盘坏输入显示中文错误 " + badSaveBlocked.error);
    check(badSaveBlocked.value === "", "键盘坏输入不被改写成不限 " + badSaveBlocked.value);

    // ⑪ 坏输入是用户的输入事实，不是某个 DOM 元素的寿命：行被重建之后仍然非法，
    // 只有显式清空（退格）或键入数字才算解决。
    const limitRow = (index) => page.evaluate((rowIndex) => {
      const row = document.querySelectorAll("#task-item-list .task-item-row")[rowIndex];
      if (!row) return null;
      const input = row.querySelector(".task-item-limit-input");
      const hint = row.querySelector(".task-item-limit-hint");
      return {
        type: row.dataset.contentType,
        value: input.value,
        badInput: input.validity.badInput,
        valid: input.validity.valid,
        ariaInvalid: input.getAttribute("aria-invalid"),
        placeholder: input.placeholder,
        hint: hint && !hint.hidden ? hint.textContent.trim() : "",
      };
    }, index);
    const badRow = await limitRow(1);
    check(badRow.hint.includes("1-1000"), "非法行里有可见提示：" + JSON.stringify(badRow));
    await page.locator("#task-item-list .task-item-row:nth-child(2) button",
      { hasText: "上移" }).click();
    const movedBadRow = await limitRow(0);
    check(movedBadRow.type === "zhihu_hot" && movedBadRow.value === "" &&
      movedBadRow.badInput === false && movedBadRow.valid === false &&
      movedBadRow.ariaInvalid === "true" &&
      movedBadRow.placeholder === "请输入 1-1000 的整数" && movedBadRow.hint.includes("1-1000"),
    "上移重建后该行仍然非法：" + JSON.stringify(movedBadRow));
    // 预览请求也会带合法的不限（limit:null），所以只比较点击前后 update 载荷里的不限条数。
    const nullUpdates = () => page.evaluate(() => window.__calls.filter((call) =>
      call.endpoint.endsWith("/update") && call.payload && Array.isArray(call.payload.items) &&
      call.payload.items.some((item) => item.limit === null)).length);
    const stickyBefore = await page.evaluate(() => window.__calls.length);
    const nullUpdatesBefore = await nullUpdates();
    await page.click("#task-save-button");
    const stickySave = await page.evaluate((before) => ({
      requests: window.__calls.length - before,
      error: document.getElementById("task-form-error").textContent,
      dialog: document.getElementById("task-dialog").open,
    }), stickyBefore);
    check(stickySave.requests === 0 && (await nullUpdates()) === nullUpdatesBefore &&
      stickySave.dialog && stickySave.error.includes("知乎热榜"),
    "重建后点保存仍被拦且没有 limit:null 载荷：" +
      JSON.stringify({ ...stickySave, nullUpdatesBefore }));

    // Escape 不承担清空：它只走对话框关闭守卫。
    await page.click("#task-item-list .task-item-row:nth-child(1) .task-item-limit-input");
    await page.keyboard.press("Escape");
    await page.waitForSelector("#discard-dialog[open]");
    const afterEscape = await page.evaluate(() => {
      const input = document.querySelector(
        "#task-item-list .task-item-row:nth-child(1) .task-item-limit-input");
      return {
        taskOpen: document.getElementById("task-dialog").open,
        discardOpen: document.getElementById("discard-dialog").open,
        value: input.value,
        badInput: input.validity.badInput,
        ariaInvalid: input.getAttribute("aria-invalid"),
      };
    });
    check(afterEscape.taskOpen && afterEscape.discardOpen && afterEscape.value === "" &&
      afterEscape.badInput === false && afterEscape.ariaInvalid === "true",
    "Escape 不清空条数、只弹放弃确认：" + JSON.stringify(afterEscape));
    await page.click("#discard-cancel-button");
    await page.waitForFunction(() => !document.getElementById("discard-dialog").open);
    const keptEditing = await limitRow(0);
    check(keptEditing.valid === false && keptEditing.ariaInvalid === "true",
      "继续编辑后坏输入仍在：" + JSON.stringify(keptEditing));

    // 删掉坏行本身：它的提示与顶部红字立刻消失，其它行的条数不受影响。
    await page.locator("#task-item-list .task-item-row:nth-child(1) button",
      { hasText: "移除" }).click();
    const afterRemove = await page.evaluate(() => {
      const rows = Array.from(document.querySelectorAll("#task-item-list .task-item-row"));
      return {
        types: rows.map((row) => row.dataset.contentType),
        values: rows.map((row) => row.querySelector(".task-item-limit-input").value),
        hints: rows.filter((row) => {
          const hint = row.querySelector(".task-item-limit-hint");
          return hint && !hint.hidden;
        }).length,
        errorHidden: document.getElementById("task-form-error").hidden,
      };
    });
    check(JSON.stringify(afterRemove.types) === JSON.stringify(["daily_news"]) &&
      afterRemove.values[0] === "2" && afterRemove.hints === 0 && afterRemove.errorHidden,
    "删掉坏行后提示消失、其它行条数不丢：" + JSON.stringify(afterRemove));

    await cancelTaskDialog(page);
    await page.waitForFunction(() => !document.getElementById("task-dialog").open);
    await page.locator('li[data-task-id="task-a"] button', { hasText: "编辑" }).click();
    await page.waitForSelector("#task-dialog[open]");
    const reopenedValues = await page.evaluate(() => Array.from(document.querySelectorAll(
      "#task-item-list .task-item-row .task-item-limit-input")).map((input) => input.value));
    check(JSON.stringify(reopenedValues) === JSON.stringify(["2", "6"]),
      "放弃删行后重开是已保存的条数：" + JSON.stringify(reopenedValues));

    // 退格清空后按键盘语义恢复成「不限」，保存载荷是 null 而不是坏值。
    await page.click(unlimitedInput);
    await page.keyboard.press("Backspace");
    await page.keyboard.press("Backspace");
    await page.click("#task-save-button");
    await page.waitForFunction(() => window.__calls.some((call) =>
      call.endpoint.endsWith("/update") && call.payload.items.some((item) => item.limit === null)));
    const clearedUpdate = await page.evaluate(() =>
      window.__calls.filter((call) => call.endpoint.endsWith("/update")).pop());
    check(JSON.stringify(clearedUpdate.payload.items) === JSON.stringify([
      { content_type: "daily_news", limit: 2 },
      { content_type: "zhihu_hot", limit: null },
    ]), "退格清空后按不限保存：" + JSON.stringify(clearedUpdate.payload.items));
    await page.waitForFunction(() => !document.getElementById("task-dialog").open);
    const reopenedLimits = await page.evaluate(() => window.__state.tasks
      .find((task) => task.id === "task-a").items.map((item) => item.limit).join("|"));
    check(reopenedLimits === "2|", "任务A保存后桩数据里第二行是不限：" + reopenedLimits);

    console.log("TASK ITEMS UI PASS " + JSON.stringify({ assertions }));
  } finally {
    await browser.close();
    server.close();
  }
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
