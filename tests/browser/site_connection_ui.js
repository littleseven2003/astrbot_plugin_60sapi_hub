const assert = require("assert/strict");
const http = require("http");
const { chromium } = require("playwright-core");

const BASE = process.env.ASTRBOT_URL;
const PW = process.env.ASTRBOT_PASSWORD;
const CHROME = process.env.CHROME_PATH;
const PLUGIN = process.env.ASTRBOT_PLUGIN || "astrbot_plugin_60sapi_hub";
if (!BASE || !PW || !CHROME) {
  console.error("ASTRBOT_URL, ASTRBOT_PASSWORD and CHROME_PATH are required");
  process.exit(2);
}

let token;
let siteId;
const name = `站点连接回归${Date.now()}`;
const upstreamCalls = [];
let failOnce = true;
const secondName = `${name}B`;
let secondSiteId;
let aName;
let bName;
let dailyGate = null;
let dailyEntered = null;
const siteGates = {};
const siteEntered = {};
const extraSiteIds = [];
const siteIds = {};
const cName = name + "C";
const dName = name + "D";
const WAIT_TIMEOUT = Number(process.env.SITE_TEST_TIMEOUT_MS || 15000);
const TOTAL_TIMEOUT_MS = Number(process.env.SITE_TEST_TOTAL_TIMEOUT_MS || 600000);
const PLUGIN_TOGGLE_SPECS = [
  {
    method: "PATCH",
    path: process.env.ASTRBOT_PLUGIN_TOGGLE_PATH || "/api/v1/plugins/enabled",
    body: (active) => ({ plugin_id: PLUGIN, enabled: active }),
  },
  {
    method: "PATCH",
    path: "/api/v1/plugins/" + PLUGIN + "/enabled",
    body: (active) => ({ enabled: active }),
  },
];
let pluginDeactivated = false;
function deferred() {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
}
function armDailyGate() {
  dailyGate = deferred();
  dailyEntered = deferred();
}
const upstream = http.createServer((req, res) => {
  upstreamCalls.push(req.url);
  const delay = req.url.includes("/v2/zhihu") ? 1200
    : req.url.includes("/v2/douyin") ? 2000 : 0;
  const body = { code: 200, message: "ok", data: [] };
  if (req.url.endsWith("/v2/60s")) body.data = { news: [] };
  const gatePrefix = req.url.split("/")[1];
  const gateKey = siteGates[gatePrefix] ? gatePrefix : null;
  if (gateKey && req.url.endsWith("/v2/bili")) {
    siteEntered[gateKey]?.resolve();
    return siteGates[gateKey].promise.then(() => {
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify(body));
    });
  }
  if (req.url.includes("/fail/") && failOnce) {
    failOnce = false;
    res.writeHead(500, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ message: "secret-token reflected" }));
    return;
  }
  setTimeout(async () => {
    if (req.url.endsWith("/v2/60s") && dailyGate) {
      dailyEntered.resolve();
      await dailyGate.promise;
    }
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(JSON.stringify(body));
  }, delay);
});

function armSite(key) {
  siteGates[key] = deferred();
  siteEntered[key] = deferred();
}
function siteButton(frame, siteName) {
  return frame.getByRole("button", {
    name: new RegExp(`测试站点 ${siteName} 连接`),
  });
}
function rejectAfter(ms, label) {
  let timer;
  const promise = new Promise((_, reject) => {
    timer = setTimeout(() => reject(new Error("等待超时：" + label)), ms);
    timer.unref?.();
  });
  return { promise, cancel: () => clearTimeout(timer) };
}

async function withTimeout(promise, ms, label) {
  const timer = rejectAfter(ms, label);
  try {
    return await Promise.race([promise, timer.promise]);
  } finally {
    timer.cancel();
  }
}

function releaseAllGates() {
  dailyGate?.resolve?.();
  for (const gate of Object.values(siteGates)) gate?.resolve?.();
}

async function resetBridgeTracking(page, frame) {
  await page.evaluate(() => {
    window.__siteBridgeRequests = [];
    if (!window.__siteBridgeRequestListener) {
      window.__siteBridgeRequestListener = (event) => {
        const data = event.data;
        if (data && data.channel === "astrbot-plugin-page" && data.kind === "request") {
          window.__siteBridgeRequests.push({
            requestId: data.requestId,
            action: data.action,
            endpoint: data.endpoint,
          });
        }
      };
      window.addEventListener("message", window.__siteBridgeRequestListener);
    }
  });
  await frame.evaluate(() => {
    window.__siteBridgeResponses = {};
    if (!window.__siteBridgeResponseListener) {
      window.__siteBridgeResponseListener = (event) => {
        const data = event.data;
        if (data && data.channel === "astrbot-plugin-page" && data.kind === "response") {
          window.__siteBridgeResponses[data.requestId] = {
            ok: data.ok === true,
            error: typeof data.error === "string" ? data.error : null,
          };
        }
      };
      window.addEventListener("message", window.__siteBridgeResponseListener);
    }
  });
}

async function waitBridgeRequest(page, endpoint) {
  try {
    const handle = await page.waitForFunction((wanted) => {
      const found = (window.__siteBridgeRequests || []).find((item) => {
        const seen = typeof item.endpoint === "string" ? item.endpoint : "";
        return seen === wanted || seen.endsWith("/" + wanted);
      });
      return found ? found.requestId : false;
    }, endpoint, { timeout: WAIT_TIMEOUT });
    return await handle.jsonValue();
  } catch {
    const seen = await page.evaluate(() => window.__siteBridgeRequests || []);
    throw new Error("未捕获 bridge 请求 " + endpoint + "：" + JSON.stringify(seen));
  }
}

async function waitBridgeResponse(frame, requestId) {
  try {
    await frame.waitForFunction((id) => Object.prototype.hasOwnProperty.call(
      window.__siteBridgeResponses || {}, id,
    ), requestId, { timeout: WAIT_TIMEOUT });
  } catch {
    const seen = await frame.evaluate(() => window.__siteBridgeResponses || {});
    throw new Error("bridge 响应未回到 iframe " + requestId + "：" + JSON.stringify(seen));
  }
}

function captureTestResponse(page, targetSiteId) {
  const pending = page.waitForResponse((response) => {
    try {
      return new URL(response.url()).pathname
        .endsWith("/sites/" + targetSiteId + "/test");
    } catch {
      return false;
    }
  }, { timeout: WAIT_TIMEOUT });
  pending.catch(() => {});
  return pending;
}

async function runTypeCaptured(page, frame, type, targetSiteId) {
  await frame.selectOption("#site-test-content-type", type);
  const pending = captureTestResponse(page, targetSiteId);
  await frame.locator("#site-test-run-button").click();
  return withTimeout(pending, WAIT_TIMEOUT, "测试响应 " + type);
}

async function refreshSites(page, frame) {
  await frame.waitForFunction(
    () => document.getElementById("refresh-button")?.disabled === false,
    null, { timeout: WAIT_TIMEOUT },
  );
  await resetBridgeTracking(page, frame);
  await frame.locator("#refresh-button").click();
  const requestId = await waitBridgeRequest(page, "sites");
  await waitBridgeResponse(frame, requestId);
  await frame.waitForFunction(
    () => document.getElementById("sites-list")?.getAttribute("aria-busy") === "false",
    null, { timeout: WAIT_TIMEOUT },
  );
}

async function describeTestState(frame) {
  return frame.evaluate(() => {
    const result = document.getElementById("site-test-result");
    const run = document.getElementById("site-test-run-button");
    return {
      tone: result?.dataset.tone ?? null,
      text: result?.textContent ?? null,
      hidden: result?.hidden ?? null,
      runDisabled: run?.disabled ?? null,
      runText: run?.textContent ?? null,
      dialogOpen: document.getElementById("site-test-dialog")?.open === true,
      contentType: document.getElementById("site-test-content-type")?.value ?? null,
      responses: window.__siteBridgeResponses || {},
    };
  });
}

async function siteRecord(targetSiteId) {
  return api("GET", "/api/v1/plugins/extensions/" + PLUGIN + "/sites/" + targetSiteId);
}

async function closeTestDialog(frame) {
  await frame.locator("#site-test-close-button").click();
  await frame.waitForFunction(
    () => document.getElementById("site-test-dialog")?.open !== true,
    null, { timeout: WAIT_TIMEOUT },
  );
}

function headerRevision(text) {
  const match = /revision (\d+)/.exec(text || "");
  return match ? Number(match[1]) : null;
}

async function setPluginActive(active) {
  const attempts = [];
  for (const spec of PLUGIN_TOGGLE_SPECS) {
    let result;
    try {
      result = await api(spec.method, spec.path, spec.body(active));
    } catch (error) {
      attempts.push({ path: spec.path, error: String(error && error.message) });
      continue;
    }
    const body = result.body || {};
    const accepted = result.status === 200 && body.status === "ok";
    attempts.push({
      path: spec.path,
      status: result.status,
      bodyStatus: body.status,
      message: body.message,
    });
    if (accepted) return attempts;
  }
  throw new Error("无法" + (active ? "启用" : "停用")
    + "插件：" + JSON.stringify(attempts));
}

async function waitExtensionSitesStatus(expected, ms) {
  const deadline = Date.now() + ms;
  let last = null;
  for (;;) {
    const result = await api("GET", "/api/v1/plugins/extensions/" + PLUGIN + "/sites");
    last = result.status;
    if (result.status === expected || Date.now() >= deadline) return last;
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
}

async function api(method, path, data) {
  const response = await fetch(BASE + path, {
    method,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
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
      }, null, { timeout: 10000 });
      await save.click();
      await dialog.waitFor({ state: "hidden", timeout: 15000 });
    }
  }
}

async function closeHostPrompts(page) {
  await closeHostFirstPrompt(page);
  await page.waitForFunction(() => {
    if (localStorage.getItem("astrbot:first_notice_seen:v1") === "1") return true;
    return [...document.querySelectorAll("button")].some((button) =>
      button.offsetParent && button.innerText.trim() === "关闭");
  }, null, { timeout: 15000 });
  if (await page.evaluate(() =>
    localStorage.getItem("astrbot:first_notice_seen:v1") === "1")) return;
  const close = page.getByRole("button", { name: "关闭", exact: true });
  await close.click();
  await close.waitFor({ state: "hidden", timeout: 15000 });
  await page.waitForFunction(() =>
    localStorage.getItem("astrbot:first_notice_seen:v1") === "1",
  null, { timeout: 15000 });
}

async function openPage(browser, width) {
  const page = await browser.newPage({ viewport: { width, height: 900 } });
  await page.goto(BASE + "/", { waitUntil: "domcontentloaded" });
  await page.fill("input[name=username]", "astrbot");
  await page.fill("input[name=password]", PW);
  await Promise.all([
    page.waitForURL((url) => !url.toString().includes("/auth/login")),
    page.getByRole("button", { name: "登录", exact: true }).click(),
  ]);
  await closeHostPrompts(page);
  await page.goto(`${BASE}/#/plugin-view/${PLUGIN}/sites`, {
    waitUntil: "domcontentloaded",
  });
  const iframe = page.locator("iframe.plugin-view-frame").first();
  await iframe.waitFor({ state: "visible" });
  const frame = await (await iframe.elementHandle()).contentFrame();
  assert.ok(frame, "plugin iframe must load");
  await frame.waitForFunction(() => document.getElementById("sites-list"));
  await resetBridgeTracking(page, frame);
  return { page, frame };
}

function testButton(frame) {
  return frame.getByRole("button", {
    name: new RegExp(`测试站点 ${name} 连接`),
  });
}

async function runType(frame, type) {
  await frame.selectOption("#site-test-content-type", type);
  await frame.locator("#site-test-run-button").click();
}

(async () => {
  await new Promise((resolve) => upstream.listen(0, "127.0.0.1", resolve));
  const port = upstream.address().port;
  const browser = await chromium.launch({
    headless: true, executablePath: CHROME, args: ["--no-sandbox"],
  });
  let failure;
  const watchdog = setTimeout(() => {
    console.error("SITE CONNECTION UI TIMEOUT: 超过 "
      + TOTAL_TIMEOUT_MS + "ms 未结束；临时站点与插件启停状态可能未清理");
    process.exit(1);
  }, TOTAL_TIMEOUT_MS);
  watchdog.unref?.();
  try {
    let response = await api("POST", "/api/v1/auth/login", {
      username: "astrbot", password: PW,
    });
    token = response.body.data.token;
    response = await api("POST", `/api/v1/plugins/extensions/${PLUGIN}/sites`, {
      name, base_url: `http://127.0.0.1:${port}/proxy`,
      enabled: false, timeout_seconds: 5,
    });
    siteId = response.body.data.id;
    response = await api("POST", `/api/v1/plugins/extensions/${PLUGIN}/sites`, {
      name: secondName, base_url: `http://127.0.0.1:${port}/fail`,
      enabled: false, timeout_seconds: 5,
    });
    secondSiteId = response.body.data.id;
    aName = `${name}A`;
    bName = `${name}B2`;
    for (const [key, siteName] of [
      ["a", aName], ["b", bName], ["c", cName], ["d", dName],
    ]) {
      const created = await api("POST",
        `/api/v1/plugins/extensions/${PLUGIN}/sites`, {
          name: siteName, base_url: `http://127.0.0.1:${port}/${key}`,
          enabled: false, timeout_seconds: 5,
        });
      siteIds[key] = created.body.data.id;
      extraSiteIds.push([created.body.data.id, siteName]);
    }

    let { page, frame } = await openPage(browser, 1400);
    await frame.waitForFunction(
      (text) => document.getElementById("sites-list")?.innerText.includes(text),
      name,
    );
    assert.equal(upstreamCalls.length, 0, "page open must not request upstream");

    await testButton(frame).click();
    await frame.waitForSelector("#site-test-dialog[open]");
    assert.equal(await frame.locator("#site-test-result").isHidden(), true);
    await frame.selectOption("#site-test-content-type", "bilibili_hot");
    assert.equal(upstreamCalls.length, 0, "type switch must not request upstream");
    await frame.locator("#site-test-run-button").click();
    await frame.waitForFunction(() =>
      document.getElementById("site-test-result")?.dataset.tone === "success");
    assert.match(await frame.locator("#site-test-result").innerText(), /连接成功/);
    assert.equal(upstreamCalls.length, 1);

    for (const type of ["daily_news", "zhihu_hot", "douyin_hot", "toutiao_hot"]) {
      await runType(frame, type);
      await frame.waitForFunction(() =>
        document.getElementById("site-test-result")?.dataset.tone === "success");
      assert.match(await frame.locator("#site-test-result").innerText(),
        /连接成功/);
    }

    // In-flight switch: hold daily upstream, switch type, release explicitly.
    armDailyGate();
    await runType(frame, "daily_news");
    await withTimeout(dailyEntered.promise, WAIT_TIMEOUT, "daily 测试进入上游");
    await frame.selectOption("#site-test-content-type", "zhihu_hot");
    await frame.waitForFunction(() =>
      document.getElementById("site-test-result").hidden);
    dailyGate.resolve();
    await frame.waitForFunction(() =>
      !document.getElementById("site-test-run-button").disabled);
    assert.equal(await frame.locator("#site-test-result").isHidden(), true);
    assert.equal(await frame.locator("#site-test-content-type").inputValue(),
      "zhihu_hot");

    // Close/reopen during request keeps busy, then clears busy after finish.
    await runType(frame, "douyin_hot");
    await frame.waitForFunction(() =>
      document.getElementById("site-test-run-button").disabled);
    await frame.locator("#site-test-close-button").click();
    await testButton(frame).click();
    await frame.waitForSelector("#site-test-dialog[open]");
    assert.equal(await frame.locator("#site-test-run-button").isDisabled(), true);
    await frame.waitForFunction(() =>
      !document.getElementById("site-test-run-button").disabled);
    assert.doesNotMatch(
      await frame.locator("#site-test-result").innerText(), /正在请求/,
    );

    // Escape must close inside plugin iframe.
    await page.keyboard.press("Escape");
    await frame.waitForFunction(() =>
      !document.getElementById("site-test-dialog")?.open);

    // Upstream failure is safe and manual retry succeeds.
    await frame.waitForFunction(
      (text) => document.getElementById("sites-list")?.innerText.includes(text),
      secondName,
    );
    await frame.getByRole("button", {
      name: new RegExp(`测试站点 ${secondName} 连接`),
    }).click();
    await frame.waitForSelector("#site-test-dialog[open]");
    await runType(frame, "bilibili_hot");
    await frame.waitForFunction(() =>
      document.getElementById("site-test-result")?.dataset.tone === "danger");
    const failureText = await frame.locator("#site-test-result").innerText();
    assert.doesNotMatch(failureText, /secret-token/);
    assert.match(failureText, /测试失败|失败状态/);
    await frame.locator("#site-test-run-button").click();
    await frame.waitForFunction(() =>
      document.getElementById("site-test-result")?.dataset.tone === "success");
    await frame.locator("#site-test-close-button").click();

    // A and B concurrent: A completion must not clear B busy state.
    aName = `${name}A`;
    bName = `${name}B2`;
    await frame.waitForFunction(
      ([a, b]) => {
        const text = document.getElementById("sites-list")?.innerText || "";
        return text.includes(a) && text.includes(b);
      }, [aName, bName],
    );
    armSite("a");
    armSite("b");
    await siteButton(frame, aName).click();
    await frame.waitForSelector("#site-test-dialog[open]");
    await runType(frame, "bilibili_hot");
    await withTimeout(siteEntered.a.promise, WAIT_TIMEOUT, "A 测试进入上游");
    await frame.locator("#site-test-close-button").click();
    await siteButton(frame, bName).click();
    await frame.waitForSelector("#site-test-dialog[open]");
    await runType(frame, "bilibili_hot");
    await withTimeout(siteEntered.b.promise, WAIT_TIMEOUT, "B 测试进入上游");
    siteGates.a.resolve();
    await frame.waitForFunction(() =>
      document.getElementById("site-test-result")?.textContent
        .includes("正在请求所选接口"));
    assert.equal(await frame.locator("#site-test-run-button").isDisabled(), true);
    assert.doesNotMatch(await frame.locator("#site-test-result").innerText(),
      /连接成功/);
    siteGates.b.resolve();
    await frame.waitForFunction(() =>
      document.getElementById("site-test-result")?.dataset.tone === "success");
    await frame.locator("#site-test-close-button").click();

    // 409 displays the config-change warning through the real bridge.
    const current = await api("GET",
      `/api/v1/plugins/extensions/${PLUGIN}/sites/${siteId}`);
    await api("POST", `/api/v1/plugins/extensions/${PLUGIN}/sites/${siteId}/update`, {
      name, base_url: `http://127.0.0.1:${port}/proxy`,
      enabled: false, timeout_seconds: 5,
      expected_revision: current.body.data.revision,
    });
    await testButton(frame).click();
    await frame.waitForSelector("#site-test-dialog[open]");
    await runType(frame, "bilibili_hot");
    await frame.waitForFunction(() =>
      document.getElementById("site-test-result")?.dataset.tone === "warning");
    assert.match(await frame.locator("#site-test-result").innerText(), /配置已变化/);

    // 刷新后旧结果失效：刷新列表必须带来最新 revision，重开弹窗不得残留旧结果。
    const staleRevision = headerRevision(
      await frame.locator("#site-test-site").innerText(),
    );
    assert.notEqual(staleRevision, null, "弹窗必须显示 revision");
    const liveSite = await siteRecord(siteId);
    assert.equal(liveSite.status, 200);
    assert.ok(liveSite.body.data.revision > staleRevision,
      "刷新前弹窗应持有旧 revision");
    await closeTestDialog(frame);
    await refreshSites(page, frame);
    await testButton(frame).click();
    await frame.waitForSelector("#site-test-dialog[open]", { timeout: WAIT_TIMEOUT });
    assert.equal(
      headerRevision(await frame.locator("#site-test-site").innerText()),
      liveSite.body.data.revision,
      "刷新后必须使用最新 revision",
    );
    assert.equal(await frame.locator("#site-test-result").isHidden(), true,
      "刷新重开后不得显示旧结果");

    console.log("SCENARIO refresh-invalidation PASS");

    // 429 繁忙：两个在途测试占满并发预算，页面给出繁忙提示并恢复按钮。
    const heldRevisions = {};
    for (const key of ["a", "b"]) {
      const record = await siteRecord(siteIds[key]);
      assert.equal(record.status, 200);
      heldRevisions[key] = record.body.data.revision;
    }
    armSite("a");
    armSite("b");
    const heldTests = [
      api("POST", "/api/v1/plugins/extensions/" + PLUGIN + "/sites/"
        + siteIds.a + "/test", {
        expected_revision: heldRevisions.a, content_type: "bilibili_hot",
      }),
      api("POST", "/api/v1/plugins/extensions/" + PLUGIN + "/sites/"
        + siteIds.b + "/test", {
        expected_revision: heldRevisions.b, content_type: "bilibili_hot",
      }),
    ];
    heldTests.forEach((pending) => pending.catch(() => {}));
    await withTimeout(Promise.all([siteEntered.a.promise, siteEntered.b.promise]),
      WAIT_TIMEOUT, "两个在途测试进入上游");
    const busyResponse = await runTypeCaptured(page, frame, "bilibili_hot", siteId);
    assert.equal(busyResponse.status(), 429, "第三个并发测试必须被拒绝");
    try {
      await frame.waitForFunction(() => {
        const result = document.getElementById("site-test-result");
        const run = document.getElementById("site-test-run-button");
        return run?.disabled === false && result?.hidden === false
          && result.dataset.tone !== "info"
          && !result.textContent.includes("正在请求");
      }, null, { timeout: WAIT_TIMEOUT });
    } catch (error) {
      throw new Error("桌面 429 繁忙态未收敛："
        + JSON.stringify(await describeTestState(frame)));
    }
    const busyUi = await describeTestState(frame);
    assert.equal(busyUi.dialogOpen, true, "繁忙后测试弹窗必须保持打开");
    assert.equal(busyUi.runDisabled, false, "繁忙提示后按钮必须恢复可点");
    assert.doesNotMatch(busyUi.text, /连接成功|正在请求/);
    if (/同时进行的连接测试过多/.test(busyUi.text)) {
      assert.equal(busyUi.tone, "warning", "保留插件消息时必须是繁忙警告态");
    } else {
      // 已知宿局限：AstrBot 4.29.1 的全局 axios 拦截器对 429 直接 reject 字符串消息,
      // plugin-view bridge 只能回退到 "Plugin bridge request failed.",
      // 页面因此落到通用失败提示；繁忙专用文案在该宿主不可达（见报告未验证项）。
      assert.equal(busyUi.tone, "danger", "宿主流失 429 消息时必须给出通用失败提示");
      assert.match(busyUi.text, /测试失败，请手动重试/);
    }
    siteGates.a.resolve();
    siteGates.b.resolve();
    const heldResults = await withTimeout(Promise.all(heldTests), WAIT_TIMEOUT,
      "两个在途测试返回");
    assert.deepEqual(heldResults.map((item) => item.status), [200, 200],
      "在途测试本身必须成功");
    await runTypeCaptured(page, frame, "bilibili_hot", siteId);
    await frame.waitForFunction(() =>
      document.getElementById("site-test-result")?.dataset.tone === "success",
    null, { timeout: WAIT_TIMEOUT });
    assert.match(await frame.locator("#site-test-result").innerText(), /连接成功/);
    await closeTestDialog(frame);

    console.log("SCENARIO 429-busy desktop PASS", { status: 429 });

    // A 的旧结果不得写入 B 弹窗：A 的结果回到 iframe 时 B 必须仍在请求。
    await resetBridgeTracking(page, frame);
    armSite("a");
    armSite("b");
    await siteButton(frame, aName).click();
    await frame.waitForSelector("#site-test-dialog[open]", { timeout: WAIT_TIMEOUT });
    await runType(frame, "bilibili_hot");
    const aRequestId = await waitBridgeRequest(page, "sites/" + siteIds.a + "/test");
    await withTimeout(siteEntered.a.promise, WAIT_TIMEOUT, "A 测试进入上游");
    await closeTestDialog(frame);
    await siteButton(frame, bName).click();
    await frame.waitForSelector("#site-test-dialog[open]", { timeout: WAIT_TIMEOUT });
    assert.match(await frame.locator("#site-test-site").innerText(),
      new RegExp("站点：" + bName));
    await runType(frame, "bilibili_hot");
    await withTimeout(siteEntered.b.promise, WAIT_TIMEOUT, "B 测试进入上游");
    siteGates.a.resolve();
    await waitBridgeResponse(frame, aRequestId);
    const afterA = await frame.evaluate(() => {
      const result = document.getElementById("site-test-result");
      return {
        text: result.textContent,
        tone: result.dataset.tone,
        hidden: result.hidden,
        header: document.getElementById("site-test-site").textContent,
        runDisabled: document.getElementById("site-test-run-button").disabled,
      };
    });
    assert.match(afterA.text, /正在请求所选接口/, "A 的结果不得覆盖 B 弹窗");
    assert.equal(afterA.tone, "info");
    assert.equal(afterA.hidden, false);
    assert.equal(afterA.runDisabled, true, "A 完成后 B 仍在请求，按钮必须保持禁用");
    assert.match(afterA.header, new RegExp(bName));
    assert.doesNotMatch(afterA.text, /连接成功/);
    siteGates.b.resolve();
    await frame.waitForFunction(() =>
      document.getElementById("site-test-result")?.dataset.tone === "success",
    null, { timeout: WAIT_TIMEOUT });
    assert.match(await frame.locator("#site-test-result").innerText(), /连接成功/);
    await closeTestDialog(frame);

    console.log("SCENARIO a-b-isolation PASS");

    // 编辑后旧结果失效：在途测试的旧快照不得显示为成功。
    await frame.waitForFunction(
      (text) => document.getElementById("sites-list")?.innerText.includes(text),
      cName, { timeout: WAIT_TIMEOUT },
    );
    await siteButton(frame, cName).click();
    await frame.waitForSelector("#site-test-dialog[open]", { timeout: WAIT_TIMEOUT });
    armSite("c");
    const editResponse = captureTestResponse(page, siteIds.c);
    await runType(frame, "bilibili_hot");
    await withTimeout(siteEntered.c.promise, WAIT_TIMEOUT, "C 测试进入上游");
    const cRecord = await siteRecord(siteIds.c);
    assert.equal(cRecord.status, 200);
    const cEdited = await api("POST", "/api/v1/plugins/extensions/" + PLUGIN
      + "/sites/" + siteIds.c + "/update", {
      name: cName + "改", base_url: "http://127.0.0.1:" + port + "/c",
      enabled: false, timeout_seconds: 5,
      expected_revision: cRecord.body.data.revision,
    });
    assert.equal(cEdited.status, 200, JSON.stringify(cEdited.body));
    siteGates.c.resolve();
    const editedResult = await withTimeout(editResponse, WAIT_TIMEOUT, "C 测试响应");
    assert.equal(editedResult.status(), 409, "编辑后旧快照必须返回 409");
    await frame.waitForFunction(() =>
      document.getElementById("site-test-result")?.dataset.tone === "warning",
    null, { timeout: WAIT_TIMEOUT });
    const editedText = await frame.locator("#site-test-result").innerText();
    assert.match(editedText, /配置已变化/);
    assert.doesNotMatch(editedText, /连接成功/);
    await closeTestDialog(frame);
    await refreshSites(page, frame);
    await frame.waitForFunction(
      (text) => document.getElementById("sites-list")?.innerText.includes(text),
      cName + "改", { timeout: WAIT_TIMEOUT },
    );
    await siteButton(frame, cName + "改").click();
    await frame.waitForSelector("#site-test-dialog[open]", { timeout: WAIT_TIMEOUT });
    assert.equal(await frame.locator("#site-test-result").isHidden(), true,
      "编辑后重开不得显示旧结果");
    await runType(frame, "bilibili_hot");
    await frame.waitForFunction(() =>
      document.getElementById("site-test-result")?.dataset.tone === "success",
    null, { timeout: WAIT_TIMEOUT });
    await closeTestDialog(frame);

    console.log("SCENARIO edit-invalidation PASS");

    // 删除后旧结果失效：在途测试的站点被删除后不得显示成功。
    await frame.waitForFunction(
      (text) => document.getElementById("sites-list")?.innerText.includes(text),
      dName, { timeout: WAIT_TIMEOUT },
    );
    await siteButton(frame, dName).click();
    await frame.waitForSelector("#site-test-dialog[open]", { timeout: WAIT_TIMEOUT });
    armSite("d");
    const deleteResponse = captureTestResponse(page, siteIds.d);
    await runType(frame, "bilibili_hot");
    await withTimeout(siteEntered.d.promise, WAIT_TIMEOUT, "D 测试进入上游");
    const dRecord = await siteRecord(siteIds.d);
    assert.equal(dRecord.status, 200);
    const dDeleted = await api("POST", "/api/v1/plugins/extensions/" + PLUGIN
      + "/sites/" + siteIds.d + "/delete", {
      expected_revision: dRecord.body.data.revision,
    });
    assert.equal(dDeleted.status, 200, JSON.stringify(dDeleted.body));
    siteGates.d.resolve();
    const deletedResult = await withTimeout(deleteResponse, WAIT_TIMEOUT, "D 测试响应");
    assert.equal(deletedResult.status(), 404, "删除后旧快照必须返回 404");
    await frame.waitForFunction(() => {
      const result = document.getElementById("site-test-result");
      return result?.dataset.tone === "danger"
        && result.textContent.includes("站点不存在或已被删除");
    }, null, { timeout: WAIT_TIMEOUT });
    assert.doesNotMatch(await frame.locator("#site-test-result").innerText(),
      /连接成功/);
    await closeTestDialog(frame);
    await refreshSites(page, frame);
    await frame.waitForFunction(
      (text) => !(document.getElementById("sites-list")?.innerText || "")
        .includes(text),
      dName, { timeout: WAIT_TIMEOUT },
    );
    assert.equal(await siteButton(frame, dName).count(), 0, "删除后列表不得保留该站点");

    console.log("SCENARIO delete-invalidation PASS");

    await page.close();
    ({ page, frame } = await openPage(browser, 390));
    await frame.waitForFunction(
      (text) => document.getElementById("sites-list")?.innerText.includes(text),
      name,
    );
    const layout = await page.evaluate(() => {
      const iframe = document.querySelector("iframe.plugin-view-frame");
      const sidebar = document.querySelector(".v-navigation-drawer");
      return {
        viewport: window.innerWidth,
        body: document.body.scrollWidth,
        sidebar: sidebar ? sidebar.getBoundingClientRect().x : null,
        iframe: iframe ? iframe.getBoundingClientRect().width : 0,
      };
    });
    assert.equal(layout.viewport, 390);
    assert.equal(layout.body, 390);
    assert.ok(layout.iframe >= 380, `iframe must fill mobile viewport: ${layout.iframe}`);
    await testButton(frame).click();
    await frame.waitForSelector("#site-test-dialog[open]");
    assert.equal(await frame.locator("#site-test-content-type").isVisible(), true);
    await runType(frame, "bilibili_hot");
    await frame.waitForFunction(() =>
      document.getElementById("site-test-result")?.dataset.tone === "success");
    const overflow = await frame.evaluate(() => {
      const dialog = document.getElementById("site-test-dialog");
      return {
        doc: [document.documentElement.scrollWidth, document.documentElement.clientWidth],
        dialog: [dialog.scrollWidth, dialog.clientWidth],
        bad: document.documentElement.scrollWidth > document.documentElement.clientWidth
          || dialog.scrollWidth > dialog.clientWidth,
      };
    });
    assert.equal(overflow.bad, false, JSON.stringify({ layout, overflow }));
    await page.screenshot({ path: process.env.SITE_TEST_SCREENSHOT
      || "/tmp/site-connection-390.png", fullPage: true });
    await page.keyboard.press("Escape");
    await frame.waitForFunction(() =>
      !document.getElementById("site-test-dialog")?.open);

    // 390px 429 繁忙：同样的真实并发预算，窄屏也要给出繁忙提示并恢复按钮。
    await testButton(frame).click();
    await frame.waitForSelector("#site-test-dialog[open]", { timeout: WAIT_TIMEOUT });
    const mobileSite = await siteRecord(siteId);
    assert.equal(mobileSite.status, 200);
    assert.equal(
      headerRevision(await frame.locator("#site-test-site").innerText()),
      mobileSite.body.data.revision,
      "390px 弹窗必须使用最新 revision",
    );
    const mobileRevisions = {};
    for (const key of ["a", "b"]) {
      const record = await siteRecord(siteIds[key]);
      assert.equal(record.status, 200);
      mobileRevisions[key] = record.body.data.revision;
    }
    armSite("a");
    armSite("b");
    const mobileHeld = [
      api("POST", "/api/v1/plugins/extensions/" + PLUGIN + "/sites/"
        + siteIds.a + "/test", {
        expected_revision: mobileRevisions.a, content_type: "bilibili_hot",
      }),
      api("POST", "/api/v1/plugins/extensions/" + PLUGIN + "/sites/"
        + siteIds.b + "/test", {
        expected_revision: mobileRevisions.b, content_type: "bilibili_hot",
      }),
    ];
    mobileHeld.forEach((pending) => pending.catch(() => {}));
    await withTimeout(Promise.all([siteEntered.a.promise, siteEntered.b.promise]),
      WAIT_TIMEOUT, "390px 两个在途测试进入上游");
    const mobileBusy = await runTypeCaptured(page, frame, "bilibili_hot", siteId);
    assert.equal(mobileBusy.status(), 429, "390px 第三个并发测试必须被拒绝");
    try {
      await frame.waitForFunction(() => {
        const result = document.getElementById("site-test-result");
        const run = document.getElementById("site-test-run-button");
        return run?.disabled === false && result?.hidden === false
          && result.dataset.tone !== "info"
          && !result.textContent.includes("正在请求");
      }, null, { timeout: WAIT_TIMEOUT });
    } catch (error) {
      throw new Error("390px 429 繁忙态未收敛："
        + JSON.stringify(await describeTestState(frame)));
    }
    const mobileBusyUi = await describeTestState(frame);
    assert.equal(mobileBusyUi.dialogOpen, true, "繁忙后测试弹窗必须保持打开");
    assert.equal(mobileBusyUi.runDisabled, false, "繁忙提示后按钮必须恢复可点");
    assert.doesNotMatch(mobileBusyUi.text, /连接成功|正在请求/);
    if (/同时进行的连接测试过多/.test(mobileBusyUi.text)) {
      assert.equal(mobileBusyUi.tone, "warning", "保留插件消息时必须是繁忙警告态");
    } else {
      // 已知宿局限：AstrBot 4.29.1 的全局 axios 拦截器对 429 直接 reject 字符串消息,
      // plugin-view bridge 只能回退到 "Plugin bridge request failed.",
      // 页面因此落到通用失败提示；繁忙专用文案在该宿主不可达（见报告未验证项）。
      assert.equal(mobileBusyUi.tone, "danger",
        "宿主流失 429 消息时必须给出通用失败提示");
      assert.match(mobileBusyUi.text, /测试失败，请手动重试/);
    }
    siteGates.a.resolve();
    siteGates.b.resolve();
    await withTimeout(Promise.all(mobileHeld), WAIT_TIMEOUT, "390px 在途测试返回");
    await runTypeCaptured(page, frame, "bilibili_hot", siteId);
    await frame.waitForFunction(() =>
      document.getElementById("site-test-result")?.dataset.tone === "success",
    null, { timeout: WAIT_TIMEOUT });

    console.log("SCENARIO 429-busy 390px PASS", { status: 429 });

    // 插件停用：测试必须显示不可用提示；真实启用后重测成功。
    assert.equal(
      (await api("GET", "/api/v1/plugins/extensions/" + PLUGIN + "/sites")).status,
      200, "停用插件前扩展 API 必须可用",
    );
    pluginDeactivated = true;
    await setPluginActive(false);
    const offlineStatus = await waitExtensionSitesStatus(503, 20000);
    assert.equal(offlineStatus, 503,
      "停用插件后扩展 API 必须返回 503，实际 " + offlineStatus);
    const unavailable = await runTypeCaptured(page, frame, "bilibili_hot", siteId);
    assert.equal(unavailable.status(), 503, "停用插件后测试必须返回 503");
    await frame.waitForFunction(() => {
      const result = document.getElementById("site-test-result");
      return result?.dataset.tone === "danger"
        && result.textContent.includes("插件当前未启用");
    }, null, { timeout: WAIT_TIMEOUT });
    assert.doesNotMatch(await frame.locator("#site-test-result").innerText(),
      /连接成功/);
    await setPluginActive(true);
    const restoredStatus = await waitExtensionSitesStatus(200, 30000);
    assert.equal(restoredStatus, 200, "启用插件后扩展 API 必须恢复 200");
    pluginDeactivated = false;
    await runTypeCaptured(page, frame, "bilibili_hot", siteId);
    await frame.waitForFunction(() =>
      document.getElementById("site-test-result")?.dataset.tone === "success",
    null, { timeout: WAIT_TIMEOUT });
    assert.match(await frame.locator("#site-test-result").innerText(), /连接成功/);

    console.log("SCENARIO plugin-503-recovery PASS");
    console.log("SITE CONNECTION UI PASS", { name, upstreamCalls: upstreamCalls.length });
  } catch (error) {
    failure = error;
    throw error;
  } finally {
    try {
      releaseAllGates();
      if (pluginDeactivated && token) {
        let restored = false;
        try {
          await setPluginActive(true);
          restored = (await waitExtensionSitesStatus(200, 30000)) === 200;
        } catch (error) {
          console.error("插件恢复启用失败：", error);
        }
        if (restored) {
          pluginDeactivated = false;
        } else {
          console.error(
            "SITE CONNECTION UI HOST DIRTY: 插件可能仍处于停用状态，"
            + "请手动 PATCH /api/v1/plugins/enabled 恢复。",
          );
          process.exitCode = 1;
        }
      }
      for (const [id, siteName] of extraSiteIds) {
        const current = await api("GET",
          `/api/v1/plugins/extensions/${PLUGIN}/sites/${id}`);
        if (current.status === 200) {
          await api("POST",
            `/api/v1/plugins/extensions/${PLUGIN}/sites/${id}/delete`,
            { expected_revision: current.body.data.revision });
        }
      }
      if (secondSiteId && token) {
        const current = await api("GET",
          `/api/v1/plugins/extensions/${PLUGIN}/sites/${secondSiteId}`);
        if (current.status === 200) {
          await api("POST",
            `/api/v1/plugins/extensions/${PLUGIN}/sites/${secondSiteId}/delete`,
            { expected_revision: current.body.data.revision });
        }
      }
      if (siteId && token) {
        const current = await api("GET",
          `/api/v1/plugins/extensions/${PLUGIN}/sites/${siteId}`);
        if (current.status === 200) {
          await api("POST",
            `/api/v1/plugins/extensions/${PLUGIN}/sites/${siteId}/delete`,
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
