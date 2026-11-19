/*
 * 真实宿主回归：未保存草稿的能力测试/预览/出图，与候选站点区块布局。
 * 站点指向本机模拟上游（真实 60s JSON），能力测试、预览、长图都走真实插件后端；
 * 全程不投递、不保存草稿、不改已保存任务。
 *
 * ASTRBOT_URL=... ASTRBOT_PASSWORD=... ASTRBOT_PLUGIN=... \
 * CHROME_PATH=... NODE_PATH=<playwright-core> \
 * SHOT_DIR=/tmp/xxx node tests/browser/task_draft_real_ui.js
 */
const fs = require("node:fs");
const http = require("node:http");
const path = require("node:path");
const { chromium } = require("playwright-core");

const BASE = process.env.ASTRBOT_URL;
const PASSWORD = process.env.ASTRBOT_PASSWORD;
const PLUGIN = process.env.ASTRBOT_PLUGIN || "astrbot_plugin_60sapi_hub";
const CHROME = process.env.CHROME_PATH;
const SHOT_DIR = process.env.SHOT_DIR || "";
if (!BASE || !PASSWORD || !CHROME) {
  console.error("ASTRBOT_URL, ASTRBOT_PASSWORD and CHROME_PATH are required");
  process.exit(2);
}
const WAIT = 60000;
const stamp = Date.now();
let token = null;
let checks = 0;
const problems = [];
const ids = { site: null, longSite: null, task: null };

function check(condition, message) {
  checks += 1;
  if (!condition) throw new Error(message);
}

function newsBody() {
  return {
    code: 200, message: "ok",
    data: {
      date: "2026-11-19",
      news: Array.from({ length: 6 }, (_, i) => "草稿验收新闻 " + (i + 1) + "，用于验证草稿预览内容"),
      tip: "草稿验收微语",
    },
  };
}

function zhihuBody() {
  return {
    code: 200, message: "ok",
    data: Array.from({ length: 3 }, (_, i) => ({
      title: "草稿验收知乎 " + (i + 1) + "，用于验证条数",
      detail: "摘要 " + (i + 1),
      hot_value_desc: "热度 " + (3000 + i),
      hot_value: 3000 + i,
    })),
  };
}

const upstream = http.createServer((request, response) => {
  const body = request.url.endsWith("/v2/60s") ? newsBody() : zhihuBody();
  response.writeHead(200, { "Content-Type": "application/json" });
  response.end(JSON.stringify(body));
});

async function api(method, endpoint, data) {
  const response = await fetch(BASE + endpoint, {
    method,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: "Bearer " + token } : {}),
    },
    body: data ? JSON.stringify(data) : undefined,
  });
  return { status: response.status, body: await response.json().catch(() => ({})) };
}

const ep = (p) => "/api/v1/plugins/extensions/" + PLUGIN + (p || "");

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
        /* 页面还在加载 */
      }
    }
    await page.waitForTimeout(250);
  }
  throw new Error("插件 iframe 未就绪");
}

async function openDashboard(browser, width) {
  const page = await browser.newPage({ viewport: { width, height: 1000 } });
  await page.goto(BASE + "/", { waitUntil: "domcontentloaded" });
  // 登录页由前端路由渲染：先给一点时间，再判断是否需要填表。
  await page.waitForTimeout(1200);
  if (await page.locator("input[name=username]").count() === 0) {
    try {
      await page.waitForSelector("input[name=username]", { timeout: 4000 });
    } catch {
      /* 已登录，无需填表 */
    }
  }
  // 同一浏览器里后续页面可能已带登录 Cookie：只有还停在登录页才需要填表。
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
  let frame = await frameOf(page);
  const deadline = Date.now() + 20000;
  while (Date.now() < deadline) {
    try {
      await frame.locator('.nav-item[data-view="tasks"]').click({ timeout: 3000 });
      await frame.waitForFunction(
        () => document.getElementById("view-tasks")?.hidden === false,
        null, { timeout: 3000 },
      );
      break;
    } catch (error) {
      frame = await frameOf(page);
      if (Date.now() >= deadline) throw error;
    }
  }
  await page.waitForTimeout(200);
  return { page, frame };
}

function shot(page, name) {
  if (!SHOT_DIR) return null;
  fs.mkdirSync(SHOT_DIR, { recursive: true });
  const target = path.join(SHOT_DIR, name + ".png");
  return page.screenshot({ path: target, fullPage: false });
}

async function fillDraft(frame, { name, items, style }) {
  await frame.fill("#task-name", name);
  for (const type of items) {
    await frame.selectOption("#task-item-picker", type);
    await frame.locator("#task-item-add-button").click();
    await frame.waitForFunction(
      (t) => Boolean(document.querySelector('.task-item-row[data-content-type="' + t + '"]')),
      type, { timeout: WAIT },
    );
  }
  await frame.selectOption("#task-output-format", "image");
  if (style) await frame.selectOption("#task-image-style", style);
}

async function waitCapability(frame) {
  await frame.waitForFunction(() => {
    const node = document.getElementById("task-capability-result");
    return node && !node.hidden && /可以满足|无法满足|失败/.test(node.textContent);
  }, null, { timeout: WAIT });
  return frame.textContent("#task-capability-result");
}

async function readPreviewMeta(frame) {
  await frame.waitForFunction(
    () => document.getElementById("task-preview-dialog")?.open === true
      && document.getElementById("task-preview-sections")?.children.length > 0,
    null, { timeout: WAIT },
  );
  return frame.evaluate(() => ({
    meta: document.getElementById("task-preview-meta")?.textContent || "",
    status: document.getElementById("task-preview-status")?.textContent || "",
    sections: document.querySelectorAll("#task-preview-sections .preview-block").length,
    title: document.getElementById("task-preview-title")?.textContent || "",
  }));
}

async function generateImage(frame) {
  await frame.waitForFunction(
    () => !document.getElementById("task-preview-image-button")?.disabled,
    null, { timeout: WAIT },
  );
  await frame.locator("#task-preview-image-button").click();
  await frame.waitForFunction(
    () => document.querySelectorAll("#task-preview-image-pages .preview-image-page").length > 0,
    null, { timeout: WAIT },
  );
  return frame.evaluate(() => ({
    pages: document.querySelectorAll("#task-preview-image-pages .preview-image-page").length,
    captions: [...document.querySelectorAll("#task-preview-image-pages figcaption")]
      .map((node) => node.textContent),
    sources: [...document.querySelectorAll("#task-preview-image-pages img")]
      .map((node) => node.src.slice(0, 30)),
  }));
}

async function closePreview(frame) {
  await frame.locator("#task-preview-close-button").click();
  await frame.waitForFunction(
    () => document.getElementById("task-preview-dialog")?.open !== true,
    null, { timeout: WAIT },
  );
}

async function closeTaskDialog(frame) {
  if (await frame.evaluate(() => document.getElementById("discard-dialog")?.open === true)) {
    await frame.locator("#discard-confirm-button").click();
  }
  await frame.waitForFunction(
    () => !document.getElementById("task-dialog")?.open,
    null, { timeout: WAIT },
  );
}

async function layoutAt(page, frame, width) {
  await page.setViewportSize({ width, height: 1000 });
  await page.waitForTimeout(250);
  return frame.evaluate(() => {
    const picker = document.getElementById("task-site-picker");
    const add = document.getElementById("task-site-add-button");
    const pickerBox = picker.getBoundingClientRect();
    const addBox = add.getBoundingClientRect();
    const list = document.getElementById("task-site-list");
    const row = document.querySelector("#task-site-list .task-site-row");
    const name = row?.querySelector(".task-site-name");
    const actions = row?.querySelector(".task-site-actions");
    const p = picker.getBoundingClientRect();
    const a = add.getBoundingClientRect();
    const r = row.getBoundingClientRect();
    const n = name.getBoundingClientRect();
    const act = actions.getBoundingClientRect();
    const buttons = [...actions.querySelectorAll("button")];
    return {
      sameRow: Math.abs((p.top + p.height / 2) - (a.top + a.height / 2)) <= 2,
      pickerAddSameRow: Math.abs(
        (pickerBox.top + pickerBox.height / 2) - (addBox.top + addBox.height / 2),
      ) <= 2,
      listStyle: getComputedStyle(list).listStyleType,
      rowOverflow: row.scrollWidth - row.clientWidth,
      pageOverflow: document.documentElement.scrollWidth - window.innerWidth,
      actionsBelowName: act.top >= n.bottom - 1,
      actionsRightGap: Math.round(r.right - act.right),
      minButtonHeight: Math.min(...buttons.map((b) => b.getBoundingClientRect().height)),
      nameWidth: Math.round(n.width),
      rowWidth: Math.round(r.width),
    };
  });
}

(async () => {
  let browser = null;
  let workError = null;
  try {
    await new Promise((resolve) => upstream.listen(0, "127.0.0.1", resolve));
    const port = upstream.address().port;
    const login = await api("POST", "/api/v1/auth/login", { username: "astrbot", password: PASSWORD });
    token = login.body.data.token;

    const mkSite = async (name, prefix) => api("POST", ep("/sites"), {
      name,
      base_url: "http://127.0.0.1:" + port + "/" + prefix,
      enabled: true, timeout_seconds: 20,
    });
    const site = await mkSite("草稿验收站点" + stamp, "mock-a");
    check([200, 201].includes(site.status), "创建站点失败：" + JSON.stringify(site.body));
    ids.site = site.body.data.id;
    const longSite = await mkSite(
      "超长候选站点名称用于验证移动端换行与操作组排列" + stamp,
      "mock-b",
    );
    check([200, 201].includes(longSite.status), "创建长名称站点失败");
    ids.longSite = longSite.body.data.id;

    // 已保存任务：编辑态草稿不得改动它。
    const saved = await api("POST", ep("/tasks"), {
      name: "草稿基线任务" + stamp,
      items: [{ content_type: "daily_news", limit: 3 }],
      sites: [ids.site], output_format: "text", umos: [],
      image_style: "default",
    });
    check([200, 201].includes(saved.status), "创建已保存任务失败：" + JSON.stringify(saved.body));
    ids.task = saved.body.data.id;
    const savedRevision = saved.body.data.revision;

    browser = await chromium.launch({ headless: true, executablePath: CHROME, args: ["--no-sandbox"] });
    const { page, frame } = await openDashboard(browser, 1440);

    // 新建表单：未保存草稿直接能力测试 → 预览 → 出图。
    await frame.locator("#create-task-button").click();
    await frame.waitForSelector("#task-dialog[open]", { timeout: WAIT });
    await fillDraft(frame, {
      name: "草稿验收任务" + stamp,
      items: ["daily_news", "zhihu_hot"],
      style: "floral",
    });
    await frame.selectOption("#task-site-picker", ids.site);
    await frame.locator("#task-site-add-button").click();
    await frame.waitForFunction(
      () => document.querySelectorAll("#task-site-list .task-site-row").length === 1,
      null, { timeout: WAIT },
    );
    check(!(await frame.isDisabled("#task-preview-form-button")),
      "未保存草稿的预览按钮应可用");
    await frame.locator("#task-capability-test-button").click();
    const capabilityText = await waitCapability(frame);
    check(capabilityText.includes("可以满足"), "草稿能力测试结论：" + capabilityText);

    await frame.locator("#task-preview-form-button").click();
    const preview = await readPreviewMeta(frame);
    check(preview.sections === 2, "草稿预览应有两个栏目：" + JSON.stringify(preview));
    check(preview.meta.includes("当前表单草稿"), "预览应标注草稿来源：" + preview.meta);
    check(preview.meta.includes("花间"), "预览应显示草稿样式：" + preview.meta);
    const image = await generateImage(frame);
    check(image.pages === 2, "草稿出图应有 2 张：" + JSON.stringify(image));
    check(image.captions.some((text) => text.includes("每日新闻"))
      && image.captions.some((text) => text.includes("知乎热榜")),
    "两张图应分别对应草稿的两个栏目：" + JSON.stringify(image.captions));
    await shot(page, "draft-desktop-preview");
    await closePreview(frame);

    // 候选站点布局：桌面两站（含长名称），390px 换行与无横向溢出。
    await frame.evaluate(() => {
      const picker = document.getElementById("task-site-picker");
      picker.value = "";
    });
    const desktop = await layoutAt(page, frame, 1440);
    check(desktop.sameRow, "桌面选择器与添加按钮应同一行：" + JSON.stringify(desktop));
    check(desktop.listStyle === "none", "候选列表不应有原生圆点：" + desktop.listStyle);
    check(desktop.actionsRightGap <= 16 && desktop.actionsRightGap >= 0,
      "桌面操作组应右对齐：" + desktop.actionsRightGap);
    check(desktop.rowOverflow <= 0, "桌面行内不应溢出：" + desktop.rowOverflow);
    await shot(page, "candidate-sites-desktop");

    await frame.selectOption("#task-site-picker", ids.longSite);
    await frame.locator("#task-site-add-button").click();
    await frame.waitForFunction(
      () => document.querySelectorAll("#task-site-list .task-site-row").length === 2,
      null, { timeout: WAIT },
    );
    const narrow = await layoutAt(page, frame, 390);
    check(narrow.pickerAddSameRow,
      "390px 选择器与添加按钮应同一行：" + JSON.stringify(narrow));
    check(narrow.pageOverflow <= 0, "390px 页面不应横向滚动：" + narrow.pageOverflow);
    check(narrow.rowOverflow <= 0, "390px 行内不应溢出：" + narrow.rowOverflow);
    check(narrow.actionsBelowName, "390px 操作组应下移：" + JSON.stringify(narrow));
    check(narrow.minButtonHeight >= 44, "390px 按钮高度应至少 44px：" + narrow.minButtonHeight);
    await shot(page, "candidate-sites-390");

    // 草稿尚未保存：任务列表里不应出现同名新任务，已保存任务 revision 不变。
    const created = await api("GET", ep("/tasks"));
    check(!(created.body.data.tasks || []).some((task) => task.name === "草稿验收任务" + stamp),
      "草稿不应被保存成任务");
    const still = await api("GET", ep("/tasks/" + ids.task));
    check(still.body.data.revision === savedRevision, "草稿操作不应改动已保存任务");

    // 编辑已保存任务：改动未保存时能力测试与预览直接用草稿（另开一页，避免与新建弹窗状态耦合）。
    const second = await openDashboard(browser, 1440);
    const sframe = second.frame;
    await sframe.waitForFunction(
      (id) => [...document.querySelectorAll("#tasks-list .task-card")]
        .some((node) => node.dataset.taskId === id),
      ids.task, { timeout: WAIT },
    );
    await sframe.locator('#tasks-list .task-card[data-task-id="' + ids.task + '"]')
      .getByRole("button", { name: /编辑推送任务/ }).click();
    await sframe.waitForSelector("#task-dialog[open]", { timeout: WAIT });
    await sframe.fill("#task-item-list .task-item-limit-input", "6");
    await sframe.locator("#task-capability-test-button").click();
    const editCapability = await waitCapability(sframe);
    check(editCapability.includes("可以满足"), "编辑态草稿能力测试：" + editCapability);
    await sframe.locator("#task-preview-form-button").click();
    const editPreview = await readPreviewMeta(sframe);
    check(editPreview.meta.includes("当前表单草稿"),
      "编辑态预览应标注草稿：" + editPreview.meta);
    await closePreview(sframe);
    const untouched = await api("GET", ep("/tasks/" + ids.task));
    check(untouched.body.data.revision === savedRevision, "编辑态草稿预览不应保存任务");

    // 390px 下重复一次编辑态草稿预览，确认移动端交互。
    await second.page.setViewportSize({ width: 390, height: 1000 });
    await second.page.waitForTimeout(300);
    await sframe.locator("#task-preview-form-button").click();
    const mobilePreview = await readPreviewMeta(sframe);
    check(mobilePreview.sections === 1, "390px 编辑态预览栏目数：" + mobilePreview.sections);
    await shot(second.page, "draft-390-preview");
    await closePreview(sframe);
    await second.page.close();

    console.log("TASK DRAFT REAL UI PASS", { checks, taskId: ids.task });
  } catch (error) {
    workError = error;
  } finally {
    if (browser) {
      try { await browser.close(); } catch (error) { problems.push("关闭浏览器失败：" + error.message); }
    }
    try { upstream.close(); } catch (error) { problems.push("关闭模拟上游失败：" + error.message); }
    for (const key of ["task", "site", "longSite"]) {
      if (!ids[key] || !token) continue;
      try {
        const current = key === "task"
          ? await api("GET", ep("/tasks/" + ids[key]))
          : await api("GET", ep("/sites/" + ids[key]));
        if (current.status === 200) {
          if (key === "task") {
            await api("POST", ep("/tasks/" + ids[key] + "/delete"), { expected_revision: current.body.data.revision });
          } else {
            await api("POST", ep("/sites/" + ids[key] + "/delete"), { expected_revision: current.body.data.revision });
          }
        }
      } catch (error) {
        problems.push("清理 " + key + " 失败：" + error.message);
      }
    }
    if (workError) {
      console.error("TASK DRAFT REAL UI FAIL", workError);
      console.error("已通过检查数", checks, problems);
      process.exitCode = 1;
    } else if (problems.length) {
      console.error("清理问题", problems);
      process.exitCode = 1;
    }
  }
})().catch((error) => {
  console.error("TASK DRAFT REAL UI CRASH", error);
  process.exit(1);
});
