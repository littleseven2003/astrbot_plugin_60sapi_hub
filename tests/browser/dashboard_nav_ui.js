const assert = require("assert/strict");
const http = require("http");
const { chromium } = require("playwright-core");

// 后台导航与视觉重设计的浏览器回归：验证 DOM 契约、导航零上游请求、视图内容持久化、
// 对话框关闭时的未保存表单守卫与移动端无横向溢出。只读断言，不点击任何"发送"。

const BASE = process.env.ASTRBOT_URL;
const PW = process.env.ASTRBOT_PASSWORD;
const CHROME = process.env.CHROME_PATH;
const PLUGIN = process.env.ASTRBOT_PLUGIN || "astrbot_plugin_60sapi_hub";
if (!BASE || !PW || !CHROME) {
  console.error("ASTRBOT_URL, ASTRBOT_PASSWORD and CHROME_PATH are required");
  process.exit(2);
}

const WAIT_TIMEOUT = Number(process.env.DASH_NAV_TIMEOUT_MS || 15000);
const TOTAL_TIMEOUT_MS = Number(process.env.DASH_NAV_TOTAL_TIMEOUT_MS || 600000);
const VIEWS = ["sites", "preview", "profiles", "tasks", "executions"];
const VIEW_TITLES = {
  sites: "站点管理",
  preview: "内容预览",
  profiles: "内容组合",
  tasks: "推送任务",
  executions: "执行记录",
};
const MOBILE_WIDTH = 390;
const DESKTOP_WIDTH = 1400;

let assertionCount = 0;
function check(condition, message) {
  assertionCount += 1;
  assert.ok(condition, message);
}
function checkEqual(actual, expected, message) {
  assertionCount += 1;
  assert.equal(actual, expected, message);
}
function checkMatch(actual, pattern, message) {
  assertionCount += 1;
  assert.match(String(actual), pattern, message);
}

// ---------------------------------------------------------------- 本地上游
const upstreamCalls = [];
const upstream = http.createServer((req, res) => {
  upstreamCalls.push({ url: req.url, method: req.method, at: Date.now() });
  const body = { code: 200, message: "ok", data: [] };
  if (req.url.endsWith("/v2/60s")) body.data = { news: [] };
  res.writeHead(200, { "Content-Type": "application/json" });
  res.end(JSON.stringify(body));
});
let upstreamOrigin = "";

// ------------------------------------------------------------ 请求观测
const observed = [];
let phase = "boot";
let deliverySends = [];
function isDeliverySend(request) {
  if (request.method() !== "POST") return false;
  const url = request.url();
  return /\/plugins\/extensions\/[^/]+\/tasks\/[^/]+\/(test|send)/.test(url)
    || /\/plugins\/extensions\/[^/]+\/executions\/?$/.test(url);
}
function markPhase(name) {
  phase = name;
  return observed.length;
}
function requestsSince(mark) {
  return observed.slice(mark);
}
function assertNoUpstreamSince(mark, label) {
  const hits = requestsSince(mark).filter((entry) =>
    upstreamOrigin && entry.url.startsWith(upstreamOrigin));
  check(hits.length === 0,
    label + " 期间出现上游站点请求：" + JSON.stringify(hits.slice(0, 3)));
}
function assertNoDeliverySendSince(mark, label) {
  const hits = requestsSince(mark).filter((entry) => entry.deliverySend);
  check(hits.length === 0,
    label + " 期间出现投递发送 POST：" + JSON.stringify(hits.slice(0, 3)));
}

function assertNoSiteWriteSince(mark, label) {
  const hits = requestsSince(mark).filter((entry) =>
    entry.method === "POST"
    && /\/plugins\/extensions\/[^/]+\/sites\/?$/.test(entry.url));
  check(hits.length === 0,
    label + " 期间出现站点写入 POST：" + JSON.stringify(hits.slice(0, 3)));
}

// ---------------------------------------------------------------- 宿主 API
let token;
let siteId;
const siteName = "导航回归站点" + Date.now();
let createdSiteNames = [];

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

async function closeHostFirstPrompt(page) {
  const dialog = page.locator(".account-dialog");
  if (await dialog.isVisible().catch(() => false)) {
    const inputs = dialog.locator("input");
    if (await inputs.count() >= 3) {
      const next = process.env.ASTRBOT_NEW_PASSWORD || PW;
      await inputs.nth(0).fill(PW);
      await inputs.nth(1).fill(next);
      await inputs.nth(2).fill(next);
      const save = dialog.getByRole("button", { name: "保存修改" });
      await save.waitFor({ state: "visible" });
      await page.waitForFunction(() => {
        const button = document.querySelector(".account-dialog button");
        return button && !button.disabled;
      }, null, { timeout: WAIT_TIMEOUT });
      await save.click();
      await dialog.waitFor({ state: "hidden", timeout: WAIT_TIMEOUT });
    }
  }
}

async function closeHostPrompts(page) {
  await closeHostFirstPrompt(page);
  await page.waitForFunction(() => {
    if (localStorage.getItem("astrbot:first_notice_seen:v1") === "1") return true;
    return [...document.querySelectorAll("button")].some((button) =>
      button.offsetParent && button.innerText.trim() === "关闭");
  }, null, { timeout: WAIT_TIMEOUT });
  if (await page.evaluate(() =>
    localStorage.getItem("astrbot:first_notice_seen:v1") === "1")) return;
  const close = page.getByRole("button", { name: "关闭", exact: true });
  await close.click();
  await close.waitFor({ state: "hidden", timeout: WAIT_TIMEOUT });
  await page.waitForFunction(() =>
    localStorage.getItem("astrbot:first_notice_seen:v1") === "1",
  null, { timeout: WAIT_TIMEOUT });
}

async function openPage(browser, width, label) {
  const page = await browser.newPage({ viewport: { width, height: 900 } });
  page.on("request", (request) => {
    const entry = {
      phase,
      method: request.method(),
      url: request.url(),
      deliverySend: isDeliverySend(request),
    };
    observed.push(entry);
    if (entry.deliverySend) deliverySends.push(entry);
  });
  await page.goto(BASE + "/", { waitUntil: "domcontentloaded" });
  await page.fill("input[name=username]", "astrbot");
  await page.fill("input[name=password]", PW);
  await Promise.all([
    page.waitForURL((url) => !url.toString().includes("/auth/login")),
    page.getByRole("button", { name: "登录", exact: true }).click(),
  ]);
  await closeHostPrompts(page);
  await page.goto(BASE + "/#/plugin-view/" + PLUGIN + "/sites", {
    waitUntil: "domcontentloaded",
  });
  const iframe = page.locator("iframe.plugin-view-frame").first();
  await iframe.waitFor({ state: "visible", timeout: WAIT_TIMEOUT });
  const frame = await (await iframe.elementHandle()).contentFrame();
  check(Boolean(frame), label + " 插件 iframe 必须加载");
  try {
    await frame.waitForFunction(() => document.getElementById("sites-list"),
      null, { timeout: WAIT_TIMEOUT });
  } catch (error) {
    throw new Error(label + " 插件页在 " + WAIT_TIMEOUT + "ms 内未渲染 #sites-list："
      + error.message);
  }
  markPhase(label + ":open");
  return { page, frame };
}

// ------------------------------------------------------------- DOM 契约读取
async function readNavState(frame) {
  return frame.evaluate(() => {
    const views = [...document.querySelectorAll("section.view")].map((section) => ({
      id: section.id,
      dataView: section.getAttribute("data-view"),
      hidden: section.hasAttribute("hidden"),
      h1: (section.querySelector("h1") ? section.querySelector("h1").textContent : "")
        .trim(),
    }));
    const navItems = [...document.querySelectorAll(".rail-nav .nav-item")].map((button) => ({
      dataView: button.getAttribute("data-view"),
      type: button.getAttribute("type"),
      isActive: button.classList.contains("is-active"),
      ariaCurrent: button.getAttribute("aria-current"),
    }));
    const nav = document.querySelector("nav.rail-nav");
    return {
      navExists: Boolean(nav),
      navLabel: nav ? nav.getAttribute("aria-label") : null,
      navItems,
      views,
      visibleViews: views.filter((view) => !view.hidden).map((view) => view.dataView),
      breadcrumb: (() => {
        const node = document.getElementById("breadcrumb-view");
        return node ? node.textContent.trim() : null;
      })(),
    };
  });
}

function failWith(message, state) {
  throw new Error(message + "；实际状态：" + JSON.stringify(state));
}

async function assertNavContract(frame, label) {
  const state = await readNavState(frame);
  if (!state.navExists) {
    failWith(label + " 缺少 <nav class=\"rail-nav\" aria-label=\"工作区导航\">", state);
  }
  checkEqual(state.navLabel, "工作区导航", label + " 导航 aria-label 必须是 工作区导航");
  checkEqual(state.navItems.length, VIEWS.length,
    label + " 导航项数量应为 " + VIEWS.length);
  checkEqual(JSON.stringify(state.navItems.map((item) => item.dataView)),
    JSON.stringify(VIEWS), label + " 导航 data-view 顺序/取值不符合契约");
  for (const item of state.navItems) {
    checkEqual(item.type, "button", label + " 导航项必须是 button type=button");
  }
  checkEqual(state.views.length, VIEWS.length, label + " section.view 数量应为 5");
  checkEqual(JSON.stringify(state.views.map((view) => view.dataView)),
    JSON.stringify(VIEWS), label + " section.view data-view 不符合契约");
  for (const view of state.views) {
    checkEqual(view.id, "view-" + view.dataView,
      label + " 视图 id 必须为 view-" + view.dataView);
    check(Boolean(view.h1), label + " 视图 " + view.dataView + " 内缺少 h1");
  }
  checkEqual(state.visibleViews.length, 1,
    label + " 同一时刻只能有一个视图不带 hidden");
  check(state.breadcrumb !== null, label + " 缺少 #breadcrumb-view");
  return state;
}

async function waitForActiveView(frame, view, label) {
  try {
    await frame.waitForFunction((wanted) => {
      const target = document.getElementById("view-" + wanted);
      if (!target || target.hasAttribute("hidden")) return false;
      const others = [...document.querySelectorAll("section.view")]
        .filter((section) => section !== target && !section.hasAttribute("hidden"));
      return others.length === 0;
    }, view, { timeout: WAIT_TIMEOUT });
  } catch (error) {
    const state = await readNavState(frame).catch(() => null);
    throw new Error(label + " 等待视图 " + view + " 单独可见超时（"
      + WAIT_TIMEOUT + "ms）；实际状态：" + JSON.stringify(state));
  }
}

function assertActiveView(state, view, label) {
  const title = VIEW_TITLES[view];
  checkEqual(JSON.stringify(state.visibleViews), JSON.stringify([view]),
    label + " 激活视图应为 " + view + " 且唯一");
  const activeNav = state.navItems.filter((item) => item.dataView === view);
  checkEqual(activeNav.length, 1, label + " 导航项 " + view + " 应唯一");
  check(activeNav[0].isActive, label + " 导航项 " + view + " 缺少 is-active");
  checkEqual(activeNav[0].ariaCurrent, "page",
    label + " 导航项 " + view + " 需要 aria-current=page");
  for (const item of state.navItems) {
    if (item.dataView === view) continue;
    check(!item.isActive, label + " 非激活导航项 " + item.dataView + " 不应带 is-active");
    check(item.ariaCurrent === null,
      label + " 非激活导航项 " + item.dataView + " 不应有 aria-current");
  }
  checkMatch(state.breadcrumb, new RegExp("工作区\\s*/\\s*" + title),
    label + " 面包屑应随视图更新为「工作区 / " + title + "」");
  const activeHeading = state.views.find((entry) => entry.dataView === view).h1;
  checkEqual(activeHeading, title, label + " 视图 " + view + " 的 h1 应为 " + title);
}

async function switchView(frame, view, label) {
  const button = frame.locator('.rail-nav .nav-item[data-view="' + view + '"]');
  const count = await button.count();
  checkEqual(count, 1, label + " 导航项 data-view=" + view + " 应唯一");
  const mark = markPhase(label + ":nav:" + view);
  await button.click({ timeout: WAIT_TIMEOUT });
  await waitForActiveView(frame, view, label);
  const state = await readNavState(frame);
  assertActiveView(state, view, label);
  assertNoUpstreamSince(mark, label + " 切换到 " + view);
  assertNoDeliverySendSince(mark, label + " 切换到 " + view);
  return state;
}

// -------------------------------------------------------------- 断言分组
async function runNavCycle(frame, label) {
  const initial = await assertNavContract(frame, label);
  checkMatch(initial.breadcrumb, /工作区\s*\/\s*站点管理/,
    label + " 首屏面包屑应为「工作区 / 站点管理」");
  const upstreamBeforeCycle = upstreamCalls.length;
  for (const view of VIEWS) {
    await switchView(frame, view, label);
    const state = await readNavState(frame);
    assertActiveView(state, view, label);
  }
  for (const view of [...VIEWS].reverse()) {
    await switchView(frame, view, label);
  }
  checkEqual(upstreamCalls.length, upstreamBeforeCycle,
    label + " 导航往返期间上游请求数必须为 0（实际新增 "
    + (upstreamCalls.length - upstreamBeforeCycle) + "）");
}

async function assertNoHorizontalOverflow(page, frame, label) {
  const frameBox = await frame.evaluate(() => {
    const documentElement = document.documentElement;
    const body = document.body;
    return {
      scrollWidth: Math.max(documentElement.scrollWidth, body.scrollWidth),
      clientWidth: Math.max(documentElement.clientWidth, body.clientWidth),
      overflowElements: [...document.querySelectorAll("body *")]
        .filter((node) => node.getBoundingClientRect().right > documentElement.clientWidth + 1)
        .slice(0, 5)
        .map((node) => (node.id ? "#" + node.id : node.className || node.tagName)),
    };
  });
  check(frameBox.scrollWidth <= frameBox.clientWidth,
    label + " 插件页出现横向溢出：" + JSON.stringify(frameBox));
  const pageBox = await page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    clientWidth: document.documentElement.clientWidth,
  }));
  check(pageBox.scrollWidth <= pageBox.clientWidth,
    label + " 宿主页出现横向溢出：" + JSON.stringify(pageBox));
}

async function runPreviewPersistence(page, frame, label) {
  await switchView(frame, "preview", label);
  const select = frame.locator("#preview-site");
  await select.waitFor({ state: "visible", timeout: WAIT_TIMEOUT });
  try {
    await frame.waitForFunction(() => {
      const node = document.getElementById("preview-site");
      return node && !node.disabled && node.options.length > 0;
    }, null, { timeout: WAIT_TIMEOUT });
  } catch (error) {
    const options = await frame.evaluate(() => {
      const node = document.getElementById("preview-site");
      return node
        ? { disabled: node.disabled, options: [...node.options].map((o) => [o.value, o.text]) }
        : null;
    });
    throw new Error(label + " 预览站点下拉未就绪：" + JSON.stringify(options));
  }
  const siteOptions = await frame.evaluate(() => {
    const node = document.getElementById("preview-site");
    return [...node.options].map((option) => ({ value: option.value, text: option.text }));
  });
  const targetOption = siteOptions.find((option) => option.value === siteId)
    || siteOptions.find((option) => option.text.includes(siteName));
  check(Boolean(targetOption),
    label + " 预览下拉未包含测试站点：" + JSON.stringify(siteOptions));
  await select.selectOption(targetOption.value);
  await frame.selectOption("#preview-content-type", "daily_news");
  const upstreamBeforePreview = upstreamCalls.length;
  await frame.locator("#preview-button").click({ timeout: WAIT_TIMEOUT });
  try {
    await frame.waitForFunction(() => {
      const result = document.getElementById("preview-result");
      return result && !result.hidden;
    }, null, { timeout: WAIT_TIMEOUT });
  } catch (error) {
    const status = await frame.evaluate(() => {
      const result = document.getElementById("preview-result");
      const node = document.getElementById("preview-status");
      return {
        resultHidden: result ? result.hidden : null,
        status: node ? node.textContent.trim() : null,
      };
    });
    throw new Error(label + " 预览结果未渲染：" + JSON.stringify(status));
  }
  // 上游结果有缓存：第二条流程可能命中缓存而不打上游，此时用「刷新上游」强制一次真实请求，
  // 保证后续"切走切回零上游请求"的对比有真实基线。
  let cacheBypassed = false;
  if (upstreamCalls.length === upstreamBeforePreview) {
    cacheBypassed = true;
    await frame.locator("#preview-refresh-button").click({ timeout: WAIT_TIMEOUT });
    const deadline = Date.now() + WAIT_TIMEOUT;
    while (upstreamCalls.length === upstreamBeforePreview && Date.now() < deadline) {
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
    try {
      await frame.waitForFunction(() => {
        const result = document.getElementById("preview-result");
        const refresh = document.getElementById("preview-refresh-button");
        return Boolean(result && !result.hidden && refresh && !refresh.disabled);
      }, null, { timeout: WAIT_TIMEOUT });
    } catch (error) {
      throw new Error(label + " 强制刷新上游后预览结果未就绪：" + JSON.stringify(
        await frame.evaluate(() => ({
          resultHidden: (document.getElementById("preview-result") || {}).hidden ?? null,
          refreshDisabled: (document.getElementById("preview-refresh-button") || {}).disabled ?? null,
          status: (document.getElementById("preview-status") || { textContent: null }).textContent,
        })),
      ));
    }
  }
  check(upstreamCalls.length > upstreamBeforePreview,
    label + " 预览（含强制刷新）应真实请求本地上游（用于后续零请求对比）：cacheBypassed="
    + cacheBypassed + " upstreamDelta=" + (upstreamCalls.length - upstreamBeforePreview));
  const snapshot = await frame.evaluate(() => ({
    fetchedAt: (document.getElementById("preview-fetched-at") || {}).textContent || "",
    itemCount: (document.getElementById("preview-item-count") || {}).textContent || "",
    source: (document.getElementById("preview-source") || {}).textContent || "",
    sitesList: [...document.querySelectorAll("#sites-list li")]
      .map((item) => item.textContent.replace(/\s+/g, " ").trim()),
  }));
  const upstreamAfterPreview = upstreamCalls.length;
  const mark = markPhase(label + ":persistence");
  await switchView(frame, "tasks", label);
  await switchView(frame, "executions", label);
  await switchView(frame, "preview", label);
  await switchView(frame, "sites", label);
  const restored = await frame.evaluate(() => {
    const result = document.getElementById("preview-result");
    const sitesList = document.getElementById("sites-list");
    return {
      resultHidden: result ? result.hidden : null,
      fetchedAt: (document.getElementById("preview-fetched-at") || {}).textContent || "",
      itemCount: (document.getElementById("preview-item-count") || {}).textContent || "",
      source: (document.getElementById("preview-source") || {}).textContent || "",
      sitesList: [...document.querySelectorAll("#sites-list li")]
        .map((item) => item.textContent.replace(/\s+/g, " ").trim()),
    };
  });
  checkEqual(restored.resultHidden, false,
    label + " 切回预览视图后此前结果被清空");
  checkEqual(restored.fetchedAt, snapshot.fetchedAt,
    label + " 切回预览视图后抓取时间发生变化，说明被重新请求");
  checkEqual(restored.itemCount, snapshot.itemCount,
    label + " 切回预览视图后条目数发生变化");
  checkEqual(restored.source, snapshot.source,
    label + " 切回预览视图后来源发生变化");
  checkEqual(upstreamCalls.length, upstreamAfterPreview,
    label + " 切回预览视图不应重新请求上游");
  assertNoUpstreamSince(mark, label + " 预览持久化往返");
  assertNoDeliverySendSince(mark, label + " 预览持久化往返");
  checkEqual(JSON.stringify(restored.sitesList), JSON.stringify(snapshot.sitesList),
    label + " 站点列表内容在视图往返后发生变化：actual="
    + JSON.stringify(restored.sitesList)
    + " expected=" + JSON.stringify(snapshot.sitesList));
}

// ------------------------------------------------- 未保存表单守卫（对话框关闭）
async function dialogStates(frame) {
  return frame.evaluate(() => {
    const read = (id) => {
      const node = document.getElementById(id);
      return node ? node.open === true : null;
    };
    return {
      siteDialogOpen: read("site-dialog"),
      discardDialogOpen: read("discard-dialog"),
      nameValue: (document.getElementById("name") || {}).value ?? null,
      sitesList: [...document.querySelectorAll("#sites-list li")]
        .map((item) => item.textContent.replace(/\s+/g, " ").trim()),
    };
  });
}

async function waitForDialogOpen(frame, dialogId, label) {
  try {
    await frame.waitForFunction((id) => {
      const node = document.getElementById(id);
      return Boolean(node && node.open === true);
    }, dialogId, { timeout: WAIT_TIMEOUT });
  } catch (error) {
    throw new Error(label + " 等待 #" + dialogId + " 打开超时（" + WAIT_TIMEOUT
      + "ms）；实际状态：" + JSON.stringify(await dialogStates(frame)));
  }
}

async function waitForDialogClosed(frame, dialogId, label) {
  try {
    await frame.waitForFunction((id) => {
      const node = document.getElementById(id);
      return !node || node.open !== true;
    }, dialogId, { timeout: WAIT_TIMEOUT });
  } catch (error) {
    throw new Error(label + " 等待 #" + dialogId + " 关闭超时（" + WAIT_TIMEOUT
      + "ms）；实际状态：" + JSON.stringify(await dialogStates(frame)));
  }
}

// 反向断言窗口：未改动内容时关闭表单，确认框在观测窗口内始终不得出现。
async function assertDiscardStaysClosed(frame, label, windowMs = 1500) {
  const deadline = Date.now() + windowMs;
  let observedOpen = null;
  while (Date.now() < deadline) {
    observedOpen = (await dialogStates(frame)).discardDialogOpen;
    if (observedOpen === true) break;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  check(observedOpen === false,
    label + " 未改动内容时不应弹出 #discard-dialog（观测 " + windowMs
    + "ms，实际 #discard-dialog.open=" + JSON.stringify(observedOpen) + "）");
}

async function openSiteDialog(frame, label) {
  const states = await dialogStates(frame);
  if (states.siteDialogOpen === true) return;
  await frame.locator("#create-button").click({ timeout: WAIT_TIMEOUT });
  await waitForDialogOpen(frame, "site-dialog", label);
}

async function runDiscardGuard(frame, label) {
  const contract = await frame.evaluate(() => {
    const dialog = document.getElementById("discard-dialog");
    const confirmButton = document.getElementById("discard-confirm-button");
    const cancelButton = document.getElementById("discard-cancel-button");
    return {
      dialog: Boolean(dialog),
      role: dialog ? dialog.getAttribute("role") : null,
      text: dialog ? dialog.innerText.trim() : null,
      confirm: confirmButton ? confirmButton.textContent.trim() : null,
      cancel: cancelButton ? cancelButton.textContent.trim() : null,
    };
  });
  if (!contract.dialog) {
    failWith(label + " 缺少未保存守卫确认框 <dialog id=\"discard-dialog\" role=\"alertdialog\">",
      contract);
  }
  checkEqual(contract.role, "alertdialog",
    label + " #discard-dialog 需要 role=alertdialog");
  checkMatch(contract.text, /放弃未保存的修改？/, label + " #discard-dialog 标题文案不符");
  checkMatch(contract.cancel, /继续编辑/, label + " #discard-cancel-button 文案应为「继续编辑」");
  checkMatch(contract.confirm, /放弃修改并关闭/,
    label + " #discard-confirm-button 文案应为「放弃修改并关闭」");

  const typed = "未保存守卫" + Date.now();
  const mark = markPhase(label + ":discard-guard");
  const upstreamBeforeGuard = upstreamCalls.length;
  const listBeforeGuard = (await dialogStates(frame)).sitesList;

  // A) 无改动点「取消」：直接关闭，不弹确认框。
  await openSiteDialog(frame, label + " 无改动打开表单");
  await frame.locator("#cancel-button").click({ timeout: WAIT_TIMEOUT });
  await waitForDialogClosed(frame, "site-dialog", label + " 无改动取消");
  await assertDiscardStaysClosed(frame, label + " 无改动取消");

  // B) 有未保存输入按 Escape：弹确认框；选「继续编辑」则留在原对话框且内容保留。
  await openSiteDialog(frame, label + " 有改动打开表单");
  await frame.locator("#name").fill(typed);
  await frame.locator("#name").press("Escape");
  await waitForDialogOpen(frame, "discard-dialog", label + " Escape 未保存关闭");
  const escapeState = await dialogStates(frame);
  checkEqual(escapeState.siteDialogOpen, true,
    label + " 弹出确认框时原表单对话框应保持打开：" + JSON.stringify(escapeState));
  const discardText = await frame.evaluate(() => {
    const node = document.getElementById("discard-dialog");
    return node ? node.innerText.trim() : null;
  });
  checkMatch(discardText, /放弃未保存的修改？/, label + " 确认框文案不符");
  await frame.locator("#discard-cancel-button").click({ timeout: WAIT_TIMEOUT });
  await waitForDialogClosed(frame, "discard-dialog", label + " 继续编辑关闭确认框");
  const afterStay = await dialogStates(frame);
  checkEqual(afterStay.siteDialogOpen, true,
    label + " 选择「继续编辑」后原表单对话框必须仍然打开：" + JSON.stringify(afterStay));
  checkEqual(afterStay.nameValue, typed,
    label + " 选择「继续编辑」后表单输入内容必须保留");

  // C) 再点「取消」→ 选「放弃修改并关闭」：两个对话框都关闭，站点列表不变，零请求。
  await frame.locator("#cancel-button").click({ timeout: WAIT_TIMEOUT });
  await waitForDialogOpen(frame, "discard-dialog", label + " 取消未保存关闭");
  await frame.locator("#discard-confirm-button").click({ timeout: WAIT_TIMEOUT });
  await waitForDialogClosed(frame, "discard-dialog", label + " 放弃修改关闭确认框");
  await waitForDialogClosed(frame, "site-dialog", label + " 放弃修改关闭表单");
  const afterDiscard = await dialogStates(frame);
  checkEqual(afterDiscard.siteDialogOpen, false,
    label + " 选择「放弃修改并关闭」后 #site-dialog 必须关闭：" + JSON.stringify(afterDiscard));
  checkEqual(afterDiscard.discardDialogOpen, false,
    label + " 选择「放弃修改并关闭」后 #discard-dialog 必须关闭：" + JSON.stringify(afterDiscard));
  checkEqual(JSON.stringify(afterDiscard.sitesList), JSON.stringify(listBeforeGuard),
    label + " 放弃修改过程不得改动站点列表：actual="
    + JSON.stringify(afterDiscard.sitesList)
    + " expected=" + JSON.stringify(listBeforeGuard));
  checkEqual(upstreamCalls.length, upstreamBeforeGuard,
    label + " 守卫过程不得请求上游（新增 " + (upstreamCalls.length - upstreamBeforeGuard) + " 次）");
  assertNoUpstreamSince(mark, label + " 未保存守卫");
  assertNoDeliverySendSince(mark, label + " 未保存守卫");
  assertNoSiteWriteSince(mark, label + " 未保存守卫");
}

// -------------------------------------------------------------------- 主流程
async function readFrameViewport(page, frame) {
  const inner = await frame.evaluate(() => ({
    width: window.innerWidth,
    height: window.innerHeight,
  }));
  const host = await page.evaluate(() => ({
    width: window.innerWidth,
    height: window.innerHeight,
  }));
  return {
    frameWidth: inner.width,
    frameHeight: inner.height,
    pageWidth: host.width,
    pageHeight: host.height,
  };
}

async function runFlow(browser, width, label) {
  const { page, frame } = await openPage(browser, width, label);
  try {
    await assertNavContract(frame, label);
    await runNavCycle(frame, label);
    await runPreviewPersistence(page, frame, label);
    await runDiscardGuard(frame, label);
    if (width <= MOBILE_WIDTH) {
      await switchView(frame, "sites", label);
      await assertNoHorizontalOverflow(page, frame, label);
    }
    const viewport = await readFrameViewport(page, frame);
    console.log("FLOW PASS", {
      label,
      requestedWidth: width,
      frameViewport: { w: viewport.frameWidth, h: viewport.frameHeight },
      pageViewport: { w: viewport.pageWidth, h: viewport.pageHeight },
      assertions: assertionCount,
    });
  } finally {
    await page.close().catch(() => {});
  }
}

(async () => {
  await new Promise((resolve) => upstream.listen(0, "127.0.0.1", resolve));
  upstreamOrigin = "http://127.0.0.1:" + upstream.address().port;
  const browser = await chromium.launch({
    headless: true, executablePath: CHROME, args: ["--no-sandbox"],
  });
  const watchdog = setTimeout(() => {
    console.error("DASHBOARD NAV UI TIMEOUT: 超过 " + TOTAL_TIMEOUT_MS
      + "ms 未结束；临时站点可能未清理");
    process.exit(1);
  }, TOTAL_TIMEOUT_MS);
  watchdog.unref?.();
  try {
    let response = await api("POST", "/api/v1/auth/login", {
      username: "astrbot", password: PW,
    });
    token = response.body.data.token;
    response = await api("POST", "/api/v1/plugins/extensions/" + PLUGIN + "/sites", {
      name: siteName,
      base_url: upstreamOrigin + "/proxy",
      enabled: true,
      timeout_seconds: 5,
    });
    checkEqual(response.status, 201, "创建回归站点失败：" + JSON.stringify(response.body));
    siteId = response.body.data.id;
    createdSiteNames.push(siteName);

    await runFlow(browser, DESKTOP_WIDTH, "桌面1400");
    await runFlow(browser, MOBILE_WIDTH, "移动390");

    checkEqual(upstreamCalls.length > 0, true, "整轮流程应至少发生一次上游请求（预览）");
    checkEqual(deliverySends.length, 0,
      "整轮流程不得触发任何投递发送 POST：" + JSON.stringify(deliverySends.slice(0, 3)));
    console.log("DASHBOARD NAV UI PASS", {
      assertions: assertionCount,
      upstreamCalls: upstreamCalls.length,
      deliverySends: deliverySends.length,
      siteId,
    });
  } finally {
    try {
      if (siteId && token) {
        const current = await api("GET",
          "/api/v1/plugins/extensions/" + PLUGIN + "/sites/" + siteId);
        if (current.status === 200) {
          await api("POST",
            "/api/v1/plugins/extensions/" + PLUGIN + "/sites/" + siteId + "/delete",
            { expected_revision: current.body.data.revision });
        }
      }
    } finally {
      clearTimeout(watchdog);
      await browser.close();
      upstream.close();
      upstream.closeAllConnections?.();
    }
  }
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
