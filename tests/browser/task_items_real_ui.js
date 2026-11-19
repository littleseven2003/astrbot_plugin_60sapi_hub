/*
 * 真实宿主回归：任务内栏目的创建/保存/重新打开/排序/条数，以及任务预览。
 * 站点指向本机模拟上游（返回真实 60s JSON），栏目与预览都走真实插件后端。
 * 桌面 1440 与 390px 两档；预览只读：不产生投递、不改任务配置。
 *
 * ASTRBOT_URL=... ASTRBOT_PASSWORD=... ASTRBOT_PLUGIN=... \
 * CHROME_PATH=... NODE_PATH=<playwright-core> \
 * node tests/browser/task_items_real_ui.js
 */
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
const taskName = "栏目验收任务" + stamp;
const siteName = "栏目验收站点" + stamp;
const NEWS_COUNT = 12;
const ZHIHU_COUNT = 9;
const NEWS_MARK = "栏目验收新闻标题";
const ZHIHU_MARK = "栏目验收知乎标题";
let token;
let checks = 0;
const problems = [];
const ids = { site: null, task: null };
const upstreamCalls = [];

function check(condition, message) {
  checks += 1;
  if (!condition) throw new Error(message);
}

function newsBody() {
  return {
    code: 200, message: "ok",
    data: {
      date: "2026-11-06",
      news: Array.from({ length: NEWS_COUNT }, (_, index) =>
        NEWS_MARK + (index + 1) + "，用于验证栏目顺序与条数"),
      tip: "栏目验收微语", link: "https://example.test/60s",
    },
  };
}

function zhihuBody() {
  return {
    code: 200, message: "ok",
    data: Array.from({ length: ZHIHU_COUNT }, (_, index) => ({
      title: ZHIHU_MARK + (index + 1) + "，用于验证条数上限",
      detail: "摘要 " + (index + 1),
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

function ep(path) {
  return "/api/v1/plugins/extensions/" + PLUGIN + (path || "");
}

async function frameOf(page) {
  const iframe = page.locator("iframe.plugin-view-frame").first();
  await iframe.waitFor({ state: "attached", timeout: WAIT });
  for (let attempt = 0; attempt < 60; attempt += 1) {
    let frame = null;
    try {
      frame = await (await iframe.elementHandle()).contentFrame();
    } catch {
      frame = null;
    }
    if (frame) {
      try {
        await frame.waitForFunction(
          () => document.getElementById("sites-list")?.getAttribute("aria-busy") === "false",
          null, { timeout: 2000 },
        );
        return frame;
      } catch {
        /* 页面还在加载，继续等 */
      }
    }
    await page.waitForTimeout(250);
  }
  throw new Error("插件 iframe 在等待窗口内未就绪");
}

async function nav(frame, view) {
  let current = frame;
  const deadline = Date.now() + 15000;
  let lastError = null;
  while (Date.now() < deadline) {
    try {
      await current.locator('.nav-item[data-view="' + view + '"]')
        .click({ timeout: Math.max(1000, Math.min(5000, deadline - Date.now())) });
      await current.waitForFunction(
        (target) => {
          const node = document.getElementById("view-" + target);
          return Boolean(node) && !node.hidden;
        }, view, { timeout: Math.max(500, Math.min(5000, deadline - Date.now())) },
      );
      return current;
    } catch (error) {
      lastError = error;
      if (Date.now() >= deadline) break;
      await current.page().waitForTimeout(200);
    }
  }
  throw new Error("切换到视图 " + view + " 失败：" + (lastError ? lastError.message : "未知错误"));
}

async function openDashboard(browser, width) {
  const page = await browser.newPage({ viewport: { width, height: 1000 } });
  await page.goto(BASE + "/", { waitUntil: "domcontentloaded" });
  // 同一浏览器打开第二档视口时可能复用登录 Cookie：只有登录页才填表。
  await page.waitForTimeout(1200);
  if (await page.locator("input[name=username]").count() === 0) {
    try {
      await page.waitForSelector("input[name=username]", { timeout: 4000 });
    } catch {
      /* 已登录 */
    }
  }
  if (await page.locator("input[name=username]").count() > 0) {
    await page.fill("input[name=username]", "astrbot");
    await page.fill("input[name=password]", PASSWORD);
    await Promise.all([
      page.waitForURL((url) => !url.toString().includes("/auth/login")),
      page.getByRole("button", { name: "登录", exact: true }).click(),
    ]);
  }
  await page.goto(BASE + "/#/plugin-view/" + PLUGIN + "/sites", { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(300);
  await page.evaluate(() => {
    for (const node of document.querySelectorAll(".v-overlay__scrim,.v-overlay-container")) {
      node.remove();
    }
  });
  const frame = await frameOf(page);
  return { page, frame: await nav(frame, "tasks") };
}

function cardOf(frame, taskId) {
  return frame.locator('#tasks-list .task-card[data-task-id="' + taskId + '"]');
}

async function openEditor(frame, taskId) {
  await frame.waitForFunction(
    (id) => [...document.querySelectorAll("#tasks-list .task-card")]
      .some((node) => node.dataset.taskId === id),
    taskId, { timeout: WAIT },
  );
  await cardOf(frame, taskId).getByRole("button", { name: /编辑推送任务/ }).click();
  await frame.waitForSelector("#task-dialog[open]", { timeout: WAIT });
}

async function addItemByPicker(frame, contentType) {
  await frame.selectOption("#task-item-picker", contentType);
  await frame.locator("#task-item-add-button").click();
  await frame.waitForFunction(
    (type) => Boolean(document.querySelector('.task-item-row[data-content-type="' + type + '"]')),
    contentType, { timeout: WAIT },
  );
}

async function readRows(frame) {
  return frame.evaluate(() => [...document.querySelectorAll("#task-item-list .task-item-row")]
    .map((row) => ({
      type: row.dataset.contentType,
      label: row.querySelector(".task-item-label")?.textContent || "",
      order: row.querySelector(".task-item-order")?.textContent || "",
      limit: row.querySelector(".task-item-limit-input")?.value ?? null,
      downDisabled: Boolean(row.querySelector(".task-item-actions button:nth-child(2)")?.disabled),
    })));
}

async function saveTask(frame) {
  await frame.locator("#task-save-button").click();
  await frame.waitForFunction(() => !document.getElementById("task-dialog")?.open, null, { timeout: WAIT });
}

async function readPreview(frame) {
  await frame.waitForFunction(
    () => document.getElementById("task-preview-dialog")?.open === true,
    null, { timeout: WAIT },
  );
  await frame.waitForFunction(
    () => document.getElementById("task-preview-sections")?.children.length > 0,
    null, { timeout: WAIT },
  );
  return frame.evaluate(() => ({
    status: document.getElementById("task-preview-status")?.textContent || "",
    sections: [...document.querySelectorAll("#task-preview-sections .preview-block")].map((block) => ({
      title: block.querySelector("h3")?.textContent || "",
      status: block.dataset.status || "",
      meta: block.querySelector(".site-meta")?.textContent || "",
    })),
    output: document.getElementById("task-preview-output-text")?.value || "",
  }));
}

function metaHas(meta, needle) {
  return typeof meta === "string" && meta.includes(needle);
}

(async () => {
  let browser = null;
  let workError = null;
  try {
    await new Promise((resolve) => upstream.listen(0, "127.0.0.1", resolve));
    const port = upstream.address().port;
    const login = await api("POST", "/api/v1/auth/login", {
      username: "astrbot", password: PASSWORD,
    });
    token = login.body.data.token;
    const site = await api("POST", ep("/sites"), {
      name: siteName,
      base_url: "http://127.0.0.1:" + port + "/mock",
      enabled: true,
      timeout_seconds: 20,
    });
    check([200, 201].includes(site.status), "创建站点失败：" + JSON.stringify(site.body));
    ids.site = site.body.data.id;

    browser = await chromium.launch({ headless: true, executablePath: CHROME, args: ["--no-sandbox"] });
    const desktop = await openDashboard(browser, 1440);
    const frame = desktop.frame;

    /* 1) 创建：先加知乎热榜并限 2 条，再加每日新闻（不限），栏目顺序即推送顺序 */
    await frame.locator("#create-task-button").click();
    await frame.waitForSelector("#task-dialog[open]", { timeout: WAIT });
    await frame.fill("#task-name", taskName);
    await addItemByPicker(frame, "zhihu_hot");
    await addItemByPicker(frame, "daily_news");
    await frame.locator('.task-item-row[data-content-type="zhihu_hot"] .task-item-limit-input').click();
    await frame.page().keyboard.type("2");
    await frame.selectOption("#task-site-picker", ids.site);
    await frame.locator("#task-site-add-button").click();
    await frame.waitForFunction(
      (name) => {
        const rows = [...document.querySelectorAll("#task-site-list .task-site-row")];
        return rows.length === 1 && rows[0].textContent.includes(name);
      },
      siteName, { timeout: WAIT },
    );
    const createdRows = await readRows(frame);
    check(createdRows.length === 2, "新建时应有 2 个栏目，实际 " + JSON.stringify(createdRows));
    check(createdRows[0].type === "zhihu_hot" && createdRows[1].type === "daily_news",
      "新建时栏目顺序应保留添加顺序：" + JSON.stringify(createdRows.map((row) => row.type)));
    check(createdRows[0].label === "知乎热榜" && createdRows[1].label === "每日新闻",
      "栏目标签应与类型对应：" + JSON.stringify(createdRows.map((row) => row.label)));
    check(createdRows[0].limit === "2", "键入了 2 就必须原样保留，实际 " + createdRows[0].limit);
    check(createdRows[1].limit === "", "未填条数的栏目应保持空值（不限），实际 " + createdRows[1].limit);
    check(createdRows[0].downDisabled === false && createdRows[1].downDisabled === true,
      "首行可下移、末行不可下移，实际 " + JSON.stringify(createdRows.map((row) => row.downDisabled)));
    await saveTask(frame);
    await frame.waitForFunction(
      (name) => document.getElementById("tasks-list")?.innerText.includes(name),
      taskName, { timeout: WAIT },
    );
    const listed = await api("GET", ep("/tasks"));
    const listedTask = (listed.body.data.tasks || []).find((task) => task.name === taskName);
    check(Boolean(listedTask), "保存后任务必须出现在列表里");
    ids.task = listedTask.id;
    check(JSON.stringify(listedTask.items.map((item) => [item.content_type, item.limit]))
      === JSON.stringify([["zhihu_hot", 2], ["daily_news", null]]),
    "保存的栏目与条数应与表单一致，实际 " + JSON.stringify(listedTask.items));
    check(listedTask.revision === 1, "首次保存的任务 revision 应为 1，实际 " + listedTask.revision);

    /* 2) 重新打开：编辑器必须回显已保存的顺序与条数 */
    await openEditor(frame, ids.task);
    const reopened = await readRows(frame);
    check(JSON.stringify(reopened.map((row) => [row.type, row.limit]))
      === JSON.stringify([["zhihu_hot", "2"], ["daily_news", ""]]),
    "重新打开后栏目与条数应与已保存配置一致，实际 "
      + JSON.stringify(reopened.map((row) => [row.type, row.limit])));
    check(await frame.locator("#task-preview-form-button").isEnabled(),
      "已保存且未修改时，编辑器内的预览按钮应可用");

    /* 3) 排序：把知乎热榜下移，条数必须跟着行走 */
    await frame.locator('.task-item-row[data-content-type="zhihu_hot"] .task-item-actions button:nth-child(2)').click();
    await frame.waitForFunction(
      () => document.querySelector('.task-item-row[data-content-type="daily_news"] .task-item-order')?.textContent === "1",
      null, { timeout: WAIT },
    );
    const moved = await readRows(frame);
    check(moved[0].type === "daily_news" && moved[1].type === "zhihu_hot",
      "下移后顺序应为每日新闻在前，实际 " + JSON.stringify(moved.map((row) => row.type)));
    check(moved[0].limit === "" && moved[1].limit === "2",
      "排序后条数必须跟随栏目，实际 " + JSON.stringify(moved.map((row) => row.limit)));
    await saveTask(frame);
    const afterMove = await api("GET", ep("/tasks/" + ids.task));
    const moveRevision = afterMove.body.data.revision;
    check(JSON.stringify(afterMove.body.data.items.map((item) => [item.content_type, item.limit]))
      === JSON.stringify([["daily_news", null], ["zhihu_hot", 2]]),
    "排序后的保存应写回新顺序与条数，实际 " + JSON.stringify(afterMove.body.data.items));
    await openEditor(frame, ids.task);
    const reopenedAgain = await readRows(frame);
    check(JSON.stringify(reopenedAgain.map((row) => row.type))
      === JSON.stringify(["daily_news", "zhihu_hot"]),
    "再次打开应保持新顺序，实际 " + JSON.stringify(reopenedAgain.map((row) => row.type)));
    await frame.locator("#task-cancel-button").click();
    await frame.waitForFunction(() => !document.getElementById("task-dialog")?.open, null, { timeout: WAIT });

    /* 4) 预览：栏目顺序=推送顺序，条数上限如实标注，预览不改配置 */
    await cardOf(frame, ids.task).getByRole("button", { name: /预览推送任务/ }).click();
    const preview = await readPreview(frame);
    check(preview.status.includes("预览完成") && preview.status.includes(siteName),
      "预览状态应说明使用的站点，实际 " + preview.status);
    check(preview.sections.length === 2, "预览应包含 2 个栏目，实际 " + preview.sections.length);
    check(JSON.stringify(preview.sections.map((section) => section.title))
      === JSON.stringify(["每日新闻", "知乎热榜"]),
    "预览栏目顺序应与任务栏目顺序一致，实际 "
      + JSON.stringify(preview.sections.map((section) => section.title)));
    check(preview.sections.every((section) => section.status === "ok"),
      "两个栏目都应成功，实际 " + JSON.stringify(preview.sections.map((section) => section.status)));
    check(metaHas(preview.sections[0].meta, "（全部）"),
      "不限条数的栏目应标注全部，实际 " + preview.sections[0].meta);
    check(metaHas(preview.sections[1].meta, "（最多 2）"),
      "限 2 条的栏目应标注上限，实际 " + preview.sections[1].meta);
    check(metaHas(preview.sections[1].meta, "2 / " + ZHIHU_COUNT),
      "限 2 条时展示条数应为 2 / " + ZHIHU_COUNT + "，实际 " + preview.sections[1].meta);
    check(preview.output.includes(ZHIHU_MARK + "1") && preview.output.includes(NEWS_MARK + "1"),
      "预览正文应包含两个栏目的条目");
    check(!preview.output.includes(ZHIHU_MARK + "3"),
      "限 2 条的栏目不应展示第 3 条及以后");
    await frame.locator('#task-preview-dialog input[value="markdown"]').click();
    const markdown = await frame.evaluate(
      () => document.getElementById("task-preview-output-text")?.value || "",
    );
    check(markdown.length > 0 && markdown.includes("每日新闻") && markdown !== preview.output,
      "切换 Markdown 后正文应换成 Markdown 版本");
    await frame.locator("#task-preview-close-button").click();
    await frame.waitForFunction(
      () => document.getElementById("task-preview-dialog")?.open !== true,
      null, { timeout: WAIT },
    );
    const afterPreview = await api("GET", ep("/tasks/" + ids.task));
    check(afterPreview.body.data.revision === moveRevision,
      "预览不得改动任务配置，revision 应保持 " + moveRevision + "，实际 "
        + afterPreview.body.data.revision);
    check(upstreamCalls.length >= 2, "预览必须真的访问模拟上游，实际 " + upstreamCalls.length);
    const executions = await api("GET", ep("/executions?limit=20&task_id=" + encodeURIComponent(ids.task)));
    check(executions.status === 200 && executions.body.data.count === 0,
      "创建、保存与预览都不得产生投递记录，实际 " + JSON.stringify(executions.body));

    /* 5) 390px：编辑与预览对话框都不能横向溢出，栏目仍然可读 */
    const small = await openDashboard(browser, 390);
    const narrow = small.frame;
    await openEditor(narrow, ids.task);
    const narrowRows = await readRows(narrow);
    check(narrowRows.length === 2 && narrowRows[1].type === "zhihu_hot",
      "390px 下编辑器仍应显示 2 个栏目并按顺序排列");
    const narrowLayout = await narrow.evaluate(() => {
      const dialog = document.getElementById("task-dialog");
      return {
        scrollWidth: document.documentElement.scrollWidth,
        innerWidth: window.innerWidth,
        dialogScrollWidth: dialog.scrollWidth,
        dialogClientWidth: dialog.clientWidth,
        hintWidth: dialog.querySelector(".task-item-limit-hint")?.getBoundingClientRect().width ?? 0,
      };
    });
    check(narrowLayout.scrollWidth <= narrowLayout.innerWidth,
      "390px 下页面不应横向滚动，实际 " + JSON.stringify(narrowLayout));
    check(narrowLayout.dialogScrollWidth <= narrowLayout.dialogClientWidth,
      "390px 下任务对话框内容不应溢出，实际 " + JSON.stringify(narrowLayout));
    await narrow.locator("#task-preview-form-button").click();
    const narrowPreview = await readPreview(narrow);
    check(narrowPreview.sections.length === 2, "390px 下预览仍应有 2 个栏目");
    const previewLayout = await narrow.evaluate(() => {
      const dialog = document.getElementById("task-preview-dialog");
      return {
        scrollWidth: document.documentElement.scrollWidth,
        innerWidth: window.innerWidth,
        dialogScrollWidth: dialog.scrollWidth,
        dialogClientWidth: dialog.clientWidth,
      };
    });
    check(previewLayout.scrollWidth <= previewLayout.innerWidth
      && previewLayout.dialogScrollWidth <= previewLayout.dialogClientWidth,
    "390px 下预览对话框不应横向溢出，实际 " + JSON.stringify(previewLayout));
    await narrow.locator("#task-preview-close-button").click();
    await narrow.waitForFunction(
      () => document.getElementById("task-preview-dialog")?.open !== true,
      null, { timeout: WAIT },
    );
    await narrow.locator("#task-cancel-button").click();
    await narrow.waitForFunction(() => !document.getElementById("task-dialog")?.open, null, { timeout: WAIT });
  } catch (error) {
    workError = error;
  } finally {
    if (browser) {
      try {
        await browser.close();
      } catch (error) {
        problems.push("关闭浏览器失败：" + error.message);
      }
    }
    try {
      upstream.close();
    } catch (error) {
      problems.push("关闭模拟上游失败：" + error.message);
    }
    if (ids.task && token) {
      try {
        const current = await api("GET", ep("/tasks/" + ids.task));
        if (current.status === 200) {
          const removed = await api("POST", ep("/tasks/" + ids.task + "/delete"), {
            expected_revision: current.body.data.revision,
          });
          if (removed.status !== 200 || removed.body.status !== "ok") {
            problems.push("删除任务失败：" + JSON.stringify(removed.body));
          }
        }
        const gone = await api("GET", ep("/tasks/" + ids.task));
        if (gone.status !== 404) {
          problems.push("任务仍在：" + gone.status);
        }
      } catch (error) {
        problems.push("清理任务异常：" + error.message);
      }
    }
    if (ids.site && token) {
      try {
        const current = await api("GET", ep("/sites/" + ids.site));
        if (current.status === 200) {
          const removed = await api("POST", ep("/sites/" + ids.site + "/delete"), {
            expected_revision: current.body.data.revision,
          });
          if (removed.status !== 200 || removed.body.status !== "ok") {
            problems.push("删除站点失败：" + JSON.stringify(removed.body));
          }
        }
        const gone = await api("GET", ep("/sites/" + ids.site));
        if (gone.status !== 404) {
          problems.push("站点仍在：" + gone.status);
        }
      } catch (error) {
        problems.push("清理站点异常：" + error.message);
      }
    }
  }

  if (workError) {
    console.error("TASK ITEMS UI FAIL", workError);
  }
  for (const problem of problems) {
    console.error("清理问题：" + problem);
  }
  if (workError || problems.length > 0) {
    console.error("CLEANUP FAILED 或断言未通过（检查数 " + checks + "，清理问题 " + problems.length + "）");
    process.exitCode = 1;
    return;
  }
  console.log("TASK ITEMS UI PASS", { checks, upstreamCalls: upstreamCalls.length, taskId: ids.task });
})().catch((error) => {
  console.error("TASK ITEMS UI FAIL", error);
  process.exitCode = 1;
});
