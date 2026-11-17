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

    // ⑦ 有未保存改动时点预览只提示保存，零请求。
    const previewCount = await page.evaluate(() =>
      window.__calls.filter((call) => call.endpoint.endsWith("/preview")).length);
    await page.locator('li[data-task-id="task-a"] button', { hasText: "编辑" }).click();
    await page.waitForSelector("#task-dialog[open]");
    await page.fill("#task-item-list .task-item-row:nth-child(1) .task-item-limit-input", "9");
    await page.click("#task-preview-form-button");
    await page.waitForSelector("#task-form-error:not([hidden])");
    const gateError = await page.textContent("#task-form-error");
    check(gateError.includes("请先保存任务修改后再预览"), "未保存预览提示：" + gateError);
    const previewAfter = await page.evaluate(() =>
      window.__calls.filter((call) => call.endpoint.endsWith("/preview")).length);
    check(previewAfter === previewCount, "未保存改动时不发预览请求");
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

    console.log("TASK ITEMS UI PASS " + JSON.stringify({ assertions }));
  } finally {
    await browser.close();
    server.close();
  }
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
