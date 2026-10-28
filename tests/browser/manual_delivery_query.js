/*
 * Real-Dashboard regression for manual delivery query context.
 * All preview/submit/detail requests are intercepted; no message is sent.
 *
 * ASTRBOT_URL=http://127.0.0.1:16192 \
 * ASTRBOT_PASSWORD=... ASTRBOT_PLUGIN=astrbot_plugin_60sapi_hub \
 * CHROME_PATH=/path/to/chrome NODE_PATH=<playwright-core> \
 * node tests/browser/manual_delivery_query.js
 */
const assert = require("node:assert/strict");
const { chromium } = require("playwright-core");

const BASE = process.env.ASTRBOT_URL || "http://127.0.0.1:16192";
const PASSWORD = process.env.ASTRBOT_PASSWORD || "";
const PLUGIN = process.env.ASTRBOT_PLUGIN || "astrbot_plugin_60sapi_hub";
const CHROME = process.env.CHROME_PATH;

async function requestJson(path, options = {}) {
  const response = await fetch(`${BASE}${path}`, options);
  return { status: response.status, body: await response.json() };
}

async function login() {
  const response = await fetch(`${BASE}/api/v1/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username: "astrbot", password: PASSWORD }),
  });
  return (await response.json()).data.token;
}

function deferred() {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
}

async function frameOf(page) {
  const iframe = page.locator("iframe").first();
  await iframe.waitFor({ state: "attached" });
  let frame = page.frames().find(
    (item) => item !== page.mainFrame() && item.url().includes("/views/sites/"),
  );
  if (!frame) frame = await (await iframe.elementHandle()).contentFrame();
  await frame.waitForSelector("#tasks-list");
  return frame;
}

async function waitPreviewReady(frame) {
  await frame.waitForFunction(
    () => document.getElementById("delivery-confirm-button")?.textContent === "确认并投递",
  );
}

(async () => {
  assert(PASSWORD, "ASTRBOT_PASSWORD is required");
  const browser = await chromium.launch({
    headless: true, executablePath: CHROME || undefined, args: ["--no-sandbox"],
  });
  const page = await browser.newPage({ viewport: { width: 1300, height: 1200 } });
  try {
    const token = await login();
    const tasksResponse = await requestJson(
      `/api/v1/plugins/extensions/${PLUGIN}/tasks`,
      { headers: { Authorization: `Bearer ${token}` } },
    );
    const taskA = tasksResponse.body.data.tasks[0];
    assert(taskA, "an existing task is required");
    const previewResponse = await requestJson(
      `/api/v1/plugins/extensions/${PLUGIN}/tasks/${taskA.id}/test-preview`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body: "{}",
      },
    );
    const revisions = previewResponse.body.data;
    const historyId = `history-${Date.now()}`;
    const historyCreate = await requestJson(
      `/api/v1/plugins/extensions/${PLUGIN}/tasks/${taskA.id}/test`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify({
          execution_id: historyId,
          expected_task_revision: revisions.task_revision,
          expected_profile_revision: revisions.profile_revision,
          expected_site_revision: revisions.site_revision,
        }),
      },
    );
    assert.equal(historyCreate.status, 200, JSON.stringify(historyCreate.body));
    // Create a second task so history can be closed and a fresh confirmation opened.
    const taskB = (await requestJson(
      `/api/v1/plugins/extensions/${PLUGIN}/tasks`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify({
          name: `查询上下文任务${Date.now()}`,
          profile_id: taskA.profile_id,
          output_format: "text",
          umos: [],
        }),
      },
    )).body.data;

    await page.addInitScript(() => {
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
    await page.goto(`${BASE}/#/plugin-view/${PLUGIN}/sites`, { waitUntil: "domcontentloaded" });
    await page.waitForTimeout(400);
    await page.evaluate(() => {
      for (const node of document.querySelectorAll(".v-overlay__scrim,.v-overlay-container")) {
        node.remove();
      }
    });
    const frame = await frameOf(page);

    let previews = 0;
    const submits = [];
    const detailRequests = [];
    let delayed = null;
    await page.route("**/tasks/*/test-preview", async (route) => {
      previews += 1;
      await route.fulfill({
        status: 200, contentType: "application/json",
        body: JSON.stringify({ status: "ok", data: {
          task_name: "fake", profile_name: "fake", site_name: "fake",
          output_label: "纯文字", task_revision: 1, profile_revision: 1, site_revision: 1,
          targets: [{
            umo: "fake:GroupMessage:123", platform_id: "fake", message_type: "GroupMessage",
            target: "123", isolated: false, valid: true, reason: null,
          }], can_deliver: true,
        }}),
      });
    });
    await page.route("**/tasks/*/test", async (route) => {
      submits.push(route.request().postDataJSON());
      await route.abort("failed");
    });
    await page.route("**/executions/*", async (route) => {
      const id = new URL(route.request().url()).pathname.split("/").pop();
      detailRequests.push(id);
      if (delayed && delayed.id === id) {
        await delayed.promise;
        if (delayed.status >= 500) {
          await route.fulfill({
            status: delayed.status, contentType: "application/json",
            body: '{"status":"error","message":"boom","data":{}}',
          });
        } else {
          await route.fulfill({
            status: delayed.status, contentType: "application/json",
            body: JSON.stringify({ status: "ok", data: {
              id, task_id: "t", task_name: "fake", status: "success",
              content_status: "complete", error_code: null, targets: [], pages: [],
            }}),
          });
        }
        return;
      }
      if (id === historyId) {
        await route.fulfill({
          status: 500, contentType: "application/json",
          body: '{"status":"error","message":"history query failed","data":{}}',
        });
        return;
      }
      await route.fulfill({
        status: 404, contentType: "application/json",
        body: '{"status":"error","message":"not found","data":{}}',
      });
    });

    // History A fails; its retry must GET A, not any global pending.
    await frame.locator("#delivery-refresh-button").click({ force: true });
    await frame.waitForFunction(
      (id) => document.getElementById("delivery-list")?.innerText.includes(id),
      historyId,
    );
    let requestSeen = page.waitForRequest((request) =>
      request.url().endsWith(`/executions/${historyId}`),
    );
    let responseSeen = page.waitForResponse((response) =>
      response.url().endsWith(`/executions/${historyId}`),
    );
    await frame.locator(`button[aria-label="查看执行 ${historyId}"]`).click({ force: true });
    await requestSeen;
    await responseSeen;
    await frame.waitForSelector("#delivery-uncertain:not([hidden])");
    const historyQueriesBefore = detailRequests.length;
    requestSeen = page.waitForRequest((request) =>
      request.url().endsWith(`/executions/${historyId}`),
    );
    responseSeen = page.waitForResponse((response) =>
      response.url().endsWith(`/executions/${historyId}`),
    );
    await frame.locator("#delivery-query-button").click({ force: true });
    await requestSeen;
    await responseSeen;
    await frame.waitForFunction(
      () => document.getElementById("delivery-result")?.hidden === true,
    );
    assert.equal(detailRequests.length, historyQueriesBefore + 1);
    assert.equal(detailRequests.at(-1), historyId);
    await frame.locator("#delivery-cancel-button").click({ force: true });
    await page.locator(".v-overlay__scrim").last().waitFor({ state: "hidden" }).catch(() => {});

    // Fresh submit B; lost response; query must use B.
    await frame.locator("#tasks-list li").filter({ hasText: taskA.name })
      .getByRole("button", { name: "测试投递" }).first().click({ force: true });
    await waitPreviewReady(frame);
    const submitSeen = page.waitForRequest((request) =>
      request.method() === "POST" && request.url().endsWith("/test"),
    );
    await frame.locator("#delivery-confirm-button").click({ force: true });
    const submitRequest = await submitSeen;
    const submittedId = submitRequest.postDataJSON().execution_id;
    await frame.waitForSelector("#delivery-uncertain:not([hidden])");
    assert.equal(submits.length, 1);
    const querySeen = page.waitForRequest((request) =>
      request.url().endsWith(`/executions/${submittedId}`),
    );
    const queryResponseSeen = page.waitForResponse((response) =>
      response.url().endsWith(`/executions/${submittedId}`),
    );
    await frame.locator("#delivery-query-button").click({ force: true });
    await querySeen;
    await queryResponseSeen;
        assert.equal(detailRequests.at(-1), submittedId);
    assert.equal(submits.length, 1);
    await frame.locator("#delivery-cancel-button").click({ force: true });
    await page.locator(".v-overlay__scrim").last().waitFor({ state: "hidden" }).catch(() => {});

    // Delayed old history success: close and switch to fresh confirmation before release.
    await frame.locator("#delivery-refresh-button").click({ force: true });
    await frame.waitForFunction(
      (id) => document.getElementById("delivery-list")?.innerText.includes(id),
      historyId,
    );
    delayed = { id: historyId, status: 200, ...deferred() };
    requestSeen = page.waitForRequest((request) =>
      request.url().endsWith(`/executions/${historyId}`),
    );
    await frame.locator(`button[aria-label="查看执行 ${historyId}"]`).click({ force: true });
    await requestSeen;
    await frame.locator("#delivery-cancel-button").click({ force: true });
    await page.locator(".v-overlay__scrim").last().waitFor({ state: "hidden" }).catch(() => {});
    await frame.locator("#tasks-list li").filter({ hasText: taskB.name })
      .getByRole("button", { name: "测试投递" }).click({ force: true });
    await waitPreviewReady(frame);
    delayed.resolve();
    await page.waitForTimeout(100);
    assert.equal(await frame.locator("#delivery-dialog-title").innerText(), "确认手动测试投递");
    assert.equal(await frame.locator("#delivery-result").isVisible(), false);
    assert.equal(await frame.locator("#delivery-confirm-button").isVisible(), true);
    await frame.locator("#delivery-cancel-button").click({ force: true });
    await page.locator(".v-overlay__scrim").last().waitFor({ state: "hidden" }).catch(() => {});

    // Delayed old history failure must obey the same ownership rule.
    await frame.locator("#delivery-refresh-button").click({ force: true });
    await frame.waitForFunction(
      (id) => document.getElementById("delivery-list")?.innerText.includes(id),
      historyId,
    );
    delayed = { id: historyId, status: 500, ...deferred() };
    requestSeen = page.waitForRequest((request) =>
      request.url().endsWith(`/executions/${historyId}`),
    );
    await frame.locator(`button[aria-label="查看执行 ${historyId}"]`).click({ force: true });
    await requestSeen;
    await frame.locator("#delivery-cancel-button").click({ force: true });
    await page.locator(".v-overlay__scrim").last().waitFor({ state: "hidden" }).catch(() => {});
    await frame.locator("#tasks-list li").filter({ hasText: taskB.name })
      .getByRole("button", { name: "测试投递" }).click({ force: true });
    await waitPreviewReady(frame);
    delayed.resolve();
    await page.waitForTimeout(100);
    assert.equal(await frame.locator("#delivery-dialog-title").innerText(), "确认手动测试投递");
    assert.equal(await frame.locator("#delivery-uncertain").isVisible(), false);
    assert.equal(await frame.locator("#delivery-confirm-button").isVisible(), true);

    console.log("MANUAL DELIVERY QUERY REGRESSION PASSED", {
      previews, submits: submits.length, detailRequests,
    });
  } finally {
    await browser.close();
  }
})().catch((error) => { console.error(error); process.exit(1); });
