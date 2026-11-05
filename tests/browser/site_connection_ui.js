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
  const gateKey = req.url.includes("/a/") ? "a" : req.url.includes("/b/") ? "b" : null;
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
    for (const [siteName, prefix] of [[aName, "a"], [bName, "b"]]) {
      const created = await api("POST",
        `/api/v1/plugins/extensions/${PLUGIN}/sites`, {
          name: siteName, base_url: `http://127.0.0.1:${port}/${prefix}`,
          enabled: false, timeout_seconds: 5,
        });
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
    await dailyEntered.promise;
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
    await siteEntered.a.promise;
    await frame.locator("#site-test-close-button").click();
    await siteButton(frame, bName).click();
    await frame.waitForSelector("#site-test-dialog[open]");
    await runType(frame, "bilibili_hot");
    await siteEntered.b.promise;
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
    console.log("SITE CONNECTION UI PASS", { name, upstreamCalls: upstreamCalls.length });
  } catch (error) {
    failure = error;
    throw error;
  } finally {
    try {
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
      await browser.close();
      upstream.close();
    }
  }
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
