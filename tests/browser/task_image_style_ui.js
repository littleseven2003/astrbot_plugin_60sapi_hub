/*
 * 任务级图片样式的后台回归：真实隔离 AstrBot + 真实 Dashboard iframe。
 * 覆盖注册表选项、新建/改选/保存/重开、未保存判定与任务列表接口。
 *
 * ASTRBOT_URL=... ASTRBOT_PASSWORD=... CHROME_PATH=...  * NODE_PATH=<playwright-core> node tests/browser/task_image_style_ui.js
 */
const { chromium } = require("playwright-core");
const BASE = process.env.ASTRBOT_URL;
const PASSWORD = process.env.ASTRBOT_PASSWORD;
const PLUGIN = "astrbot_plugin_60sapi_hub";
const CHROME = process.env.CHROME_PATH;
const WAIT = 30000;
if (!BASE || !PASSWORD || !CHROME) {
  console.error("ASTRBOT_URL, ASTRBOT_PASSWORD and CHROME_PATH are required");
  process.exit(2);
}
let token = null;
async function api(method, path, data) {
  const r = await fetch(BASE + path, {
    method,
    headers: { "Content-Type": "application/json", ...(token ? { Authorization: "Bearer " + token } : {}) },
    body: data ? JSON.stringify(data) : undefined,
  });
  return { status: r.status, body: await r.json().catch(() => ({})) };
}
const ep = (p) => "/api/v1/plugins/extensions/" + PLUGIN + (p || "");
function check(ok, msg) { if (!ok) throw new Error(msg); }
(async () => {
  const stamp = Date.now();
  const login = await api("POST", "/api/v1/auth/login", { username: "astrbot", password: PASSWORD });
  token = login.body.data.token;
  const site = await api("POST", ep("/sites"), { name: "样式UI站点" + stamp, base_url: "http://127.0.0.1:9/mock", enabled: true, timeout_seconds: 5 });
  const siteId = site.body.data.id;
  const task = await api("POST", ep("/tasks"), {
    name: "样式UI任务" + stamp,
    items: [{ content_type: "daily_news", limit: 3 }],
    sites: [siteId], output_format: "image", umos: [], image_style: "mist",
  });
  check([200, 201].includes(task.status), "创建任务失败 " + JSON.stringify(task.body));
  const taskId = task.body.data.id;
  check(task.body.data.image_style === "mist", "API 未返回 mist: " + JSON.stringify(task.body.data));
  const browser = await chromium.launch({ headless: true, executablePath: CHROME, args: ["--no-sandbox"] });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  try {
    await page.goto(BASE + "/", { waitUntil: "domcontentloaded" });
    await page.fill("input[name=username]", "astrbot");
    await page.fill("input[name=password]", PASSWORD);
    await Promise.all([
      page.waitForURL((u) => !u.toString().includes("/auth/login")),
      page.getByRole("button", { name: "登录", exact: true }).click(),
    ]);
    await page.goto(BASE + "/#/plugin-view/" + PLUGIN + "/sites", { waitUntil: "domcontentloaded" });
    await page.waitForTimeout(300);
    await page.evaluate(() => { for (const n of document.querySelectorAll(".v-overlay__scrim,.v-overlay-container")) n.remove(); });
    const iframe = page.locator("iframe.plugin-view-frame").first();
    await iframe.waitFor({ state: "visible" });
    const frame = await (await iframe.elementHandle()).contentFrame();
    await frame.waitForFunction(() => document.getElementById("sites-list")?.getAttribute("aria-busy") === "false", null, { timeout: WAIT });
    await frame.evaluate(() => { if (document.getElementById("view-tasks")?.hidden !== false) window.location.hash = "#tasks"; });
    await frame.waitForFunction(() => document.getElementById("view-tasks")?.hidden === false, null, { timeout: WAIT });
    await frame.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))));
    await page.waitForTimeout(400);
    await frame.waitForFunction((id) => [...document.querySelectorAll("#tasks-list .task-card")].some((n) => n.dataset.taskId === id), taskId, { timeout: WAIT });
    const openEditor = async () => {
      const button = frame.locator(`#tasks-list .task-card[data-task-id="${taskId}"]`).getByRole("button", { name: /编辑推送任务/ });
      for (let attempt = 0; attempt < 2; attempt += 1) {
        await button.click();
        try {
          await frame.waitForFunction(() => document.getElementById("task-dialog")?.open === true, null, { timeout: 4000 });
          return;
        } catch (error) {
          if (attempt === 1) throw error;
          await page.waitForTimeout(300);
        }
      }
    };
    await openEditor();
    const selectInfo = await frame.evaluate(() => {
      const select = document.getElementById("task-image-style");
      return { value: select.value, options: [...select.options].map((o) => [o.value, o.textContent]) };
    });
    check(selectInfo.value === "mist", "编辑器未回填 mist: " + JSON.stringify(selectInfo));
    const ids = selectInfo.options.map((o) => o[0]);
    check(JSON.stringify(ids) === JSON.stringify(["default", "mist", "floral", "bold_studio"]),
      "样式选项顺序不对: " + JSON.stringify(selectInfo.options));
    // 改选并保存
    await frame.selectOption("#task-image-style", "bold_studio");
    await frame.locator("#task-save-button").click();
    await frame.waitForFunction(() => !document.getElementById("task-dialog")?.open, null, { timeout: WAIT });
    const after = await api("GET", ep("/tasks/" + taskId));
    check(after.body.data.image_style === "bold_studio", "保存后未生效: " + JSON.stringify(after.body.data));
    // 重开回填
    await openEditor();
    const reopened = await frame.evaluate(() => document.getElementById("task-image-style").value);
    check(reopened === "bold_studio", "重开未回填: " + reopened);
    // 改选未保存样式不再置灰预览：预览与能力测试改用当前表单草稿。
    const previewButton = frame.locator("#task-preview-form-button");
    check(await previewButton.isEnabled(), "重开后预览按钮应可用");
    await frame.selectOption("#task-image-style", "floral");
    await frame.waitForTimeout(300);
    check(await previewButton.isEnabled(), "改选未保存样式后预览按钮仍应可用");
    await frame.selectOption("#task-image-style", "bold_studio");
    await frame.waitForTimeout(300);
    check(await previewButton.isEnabled(), "改回已保存样式后预览按钮仍应可用");
    // 未保存判定：改选后取消应弹出放弃修改
    await frame.selectOption("#task-image-style", "floral");
    await frame.locator("#task-cancel-button").click();
    await frame.waitForFunction(() => document.getElementById("discard-dialog")?.open === true, null, { timeout: WAIT });
    await frame.locator("#discard-cancel-button").click();
    await frame.waitForFunction(() => document.getElementById("discard-dialog")?.open !== true, null, { timeout: WAIT });
    // 取消放弃后仍是编辑态，直接取消并确认放弃
    await frame.locator("#task-cancel-button").click();
    await frame.waitForFunction(() => document.getElementById("discard-dialog")?.open === true, null, { timeout: WAIT });
    await frame.locator("#discard-confirm-button").click();
    await frame.waitForFunction(() => !document.getElementById("task-dialog")?.open, null, { timeout: WAIT });
    const listed = await api("GET", ep("/tasks"));
    check(JSON.stringify((listed.body.data.image_styles || []).map((s) => s.id)) === JSON.stringify(["default","mist","floral","bold_studio"]),
      "任务列表未附带样式注册表: " + JSON.stringify(listed.body.data.image_styles));
    console.log("STYLE UI PASS");
  } finally {
    await browser.close();
    const live = await api("GET", ep("/tasks/" + taskId));
    if (live.status === 200) await api("POST", ep("/tasks/" + taskId + "/delete"), { expected_revision: live.body.data.revision });
    const s = await api("GET", ep("/sites/" + siteId));
    if (s.status === 200) await api("POST", ep("/sites/" + siteId + "/delete"), { expected_revision: s.body.data.revision });
  }
})().catch((e) => { console.error("STYLE UI FAIL", e); process.exit(1); });
