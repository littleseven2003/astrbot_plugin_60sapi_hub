/*
 * Optional real-Dashboard regression for manual delivery query context.
 * Requires playwright-core and a running isolated AstrBot with the plugin.
 * All preview/submit/detail network paths are intercepted; no real message is sent.
 *
 * ASTRBOT_URL=http://127.0.0.1:16192 \
 * ASTRBOT_PASSWORD=... ASTRBOT_PLUGIN=astrbot_plugin_60sapi_hub \
 * CHROME_PATH=/path/to/chrome node tests/browser/manual_delivery_query.js
 */
const assert = require("node:assert/strict");
const { chromium } = require("playwright-core");

const BASE = process.env.ASTRBOT_URL || "http://127.0.0.1:16192";
const PASSWORD = process.env.ASTRBOT_PASSWORD || "";
const PLUGIN = process.env.ASTRBOT_PLUGIN || "astrbot_plugin_60sapi_hub";
const CHROME = process.env.CHROME_PATH;

async function login() {
  const response = await fetch(`${BASE}/api/v1/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username: "astrbot", password: PASSWORD }),
  });
  return (await response.json()).data.token;
}

(async () => {
  assert(PASSWORD, "ASTRBOT_PASSWORD is required");
  const browser = await chromium.launch({
    headless: true,
    executablePath: CHROME || undefined,
    args: ["--no-sandbox"],
  });
  const page = await browser.newPage({ viewport: { width: 1300, height: 1200 } });
  try {
    const token = await login();
    const response = await fetch(`${BASE}/api/v1/plugins/extensions/${PLUGIN}/tasks`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    const task = (await response.json()).data.tasks[0];
    assert(task, "an existing task is required");

    await page.addInitScript(() => {
      // Simulate the iframe refusing plugin storage without breaking auth storage.
      const get = Storage.prototype.getItem;
      const set = Storage.prototype.setItem;
      const remove = Storage.prototype.removeItem;
      const blocked = (key) => String(key).startsWith("60sapi");
      Storage.prototype.getItem = function (key) {
        if (blocked(key)) throw new DOMException("blocked", "SecurityError");
        return get.call(this, key);
      };
      Storage.prototype.setItem = function (key, value) {
        if (blocked(key)) throw new DOMException("blocked", "SecurityError");
        return set.call(this, key, value);
      };
      Storage.prototype.removeItem = function (key) {
        if (blocked(key)) throw new DOMException("blocked", "SecurityError");
        return remove.call(this, key);
      };
    });

    await page.goto(`${BASE}/`, { waitUntil: "domcontentloaded" });
    await page.fill("input[name=username]", "astrbot");
    await page.fill("input[name=password]", PASSWORD);
    await Promise.all([
      page.waitForURL((url) => !url.toString().includes("/auth/login")),
      page.getByRole("button", { name: "登录", exact: true }).click(),
    ]);
    await page.goto(`${BASE}/#/plugin-view/${PLUGIN}/sites`, {
      waitUntil: "domcontentloaded",
    });
    await page.waitForTimeout(500);
    await page.evaluate(() => {
      for (const node of document.querySelectorAll(
        ".v-overlay__scrim,.v-overlay-container",
      )) node.remove();
    });
    const iframe = page.locator("iframe").first();
    await iframe.waitFor({ state: "attached" });
    let frame = page.frames().find(
      (item) => item !== page.mainFrame() && item.url().includes("/views/sites/"),
    );
    if (!frame) frame = await (await iframe.elementHandle()).contentFrame();
    await frame.waitForFunction(() => document.getElementById("tasks-list")?.innerText.length > 0);

    let previews = 0;
    let submits = 0;
    let queries = 0;
    await page.route("**/tasks/*/test-preview", async (route) => {
      previews += 1;
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          status: "ok",
          data: {
            task_name: task.name, profile_name: "fake",
            site_name: "fake", output_label: "纯文字",
            task_revision: 1, profile_revision: 1, site_revision: 1,
            targets: [{
              umo: "fake:GroupMessage:123", platform_id: "fake",
              message_type: "GroupMessage", target: "123",
              isolated: false, valid: true, reason: null,
            }],
            can_deliver: true,
          },
        }),
      });
    });
    await page.route("**/tasks/*/test", async (route) => {
      submits += 1;
      await route.abort("failed");
    });
    await page.route("**/executions/*", async (route) => {
      queries += 1;
      await route.fulfill({
        status: 404,
        contentType: "application/json",
        body: '{"status":"error","message":"not found","data":{}}',
      });
    });

    await frame.locator("#tasks-list li").first()
      .getByRole("button", { name: "测试投递" }).click({ force: true });
    await frame.waitForFunction(
      () => document.getElementById("delivery-confirm-button")?.textContent === "确认并投递",
    );
    const executionId = await frame.evaluate(() => {
      const original = globalThis.crypto.randomUUID;
      return original ? original() : null;
    });
    void executionId;
    await frame.locator("#delivery-confirm-button").click({ force: true });
    await frame.waitForSelector("#delivery-uncertain:not([hidden])");
    assert.match(await frame.locator("#delivery-pending-text").innerText(), /禁止本地存储/);
    const queryBefore = queries;
    await frame.locator("#delivery-query-button").click({ force: true });
    await page.waitForTimeout(200);
    assert.equal(submits, 1, "response loss must not create a second POST");
    assert.equal(previews, 1, "reopen/query must not fetch preview again");
    assert.equal(queries, queryBefore + 1, "query button must GET the current attempt");
    console.log("MANUAL DELIVERY QUERY REGRESSION PASSED", {
      previews, submits, queries,
    });
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
