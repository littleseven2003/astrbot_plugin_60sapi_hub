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

const upstream = http.createServer((req, res) => {
  upstreamCalls.push(req.url);
  const delay = req.url.includes("/v2/zhihu") ? 1200
    : req.url.includes("/v2/douyin") ? 2000 : 0;
  const body = { code: 200, message: "ok", data: [] };
  if (req.url.endsWith("/v2/60s")) body.data = { news: [] };
  setTimeout(() => {
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(JSON.stringify(body));
  }, delay);
});

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

    // In-flight switch must not display the old result.
    await runType(frame, "zhihu_hot");
    await frame.waitForFunction(() =>
      document.getElementById("site-test-run-button").disabled);
    await frame.selectOption("#site-test-content-type", "daily_news");
    await frame.waitForFunction(() =>
      document.getElementById("site-test-result").hidden);
    await frame.waitForTimeout(1500);
    assert.equal(await frame.locator("#site-test-result").isHidden(), true);

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

    await page.setViewportSize({ width: 390, height: 900 });
    const overflow = await frame.evaluate(() => {
      const dialog = document.getElementById("site-test-dialog");
      return document.documentElement.scrollWidth > document.documentElement.clientWidth
        || dialog.scrollWidth > dialog.clientWidth;
    });
    if (overflow) console.log("overflow", await frame.evaluate(() => ({
      doc: [document.documentElement.scrollWidth, document.documentElement.clientWidth],
      dialog: [document.getElementById("site-test-dialog").scrollWidth,
        document.getElementById("site-test-dialog").clientWidth],
      wide: [...document.querySelectorAll("*")].filter((el) =>
        el.scrollWidth > document.documentElement.clientWidth)
        .slice(0, 8).map((el) => [el.tagName, el.id, el.className, el.scrollWidth]),
    })));
    assert.equal(overflow, false);
    await frame.locator("#site-test-close-button").click();
    await page.screenshot({ path: "/tmp/site-connection-390.png", fullPage: true });
    await page.close();

    ({ page, frame } = await openPage(browser, 390));
    await frame.waitForFunction(
      (text) => document.getElementById("sites-list")?.innerText.includes(text),
      name,
    );
    await testButton(frame).click();
    await frame.waitForSelector("#site-test-dialog[open]");
    assert.equal(await frame.locator("#site-test-content-type").isVisible(), true);
    await frame.locator("#site-test-close-button").click();
    await page.waitForFunction(() => !document.getElementById("site-test-dialog")?.open);
    console.log("SITE CONNECTION UI PASS", { name, upstreamCalls: upstreamCalls.length });
  } catch (error) {
    failure = error;
    throw error;
  } finally {
    try {
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
