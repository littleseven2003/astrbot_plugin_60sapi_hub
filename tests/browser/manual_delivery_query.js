/*
 * Real-Dashboard regression for manual delivery query context.
 * Tasks/history are fixtures; preview/submit/detail routes are intercepted.
 * No task test POST reaches the server and no message is sent.
 *
 * ASTRBOT_URL=... ASTRBOT_PASSWORD=... ASTRBOT_PLUGIN=... \
 * CHROME_PATH=... NODE_PATH=<playwright-core> \
 * node tests/browser/manual_delivery_query.js
 */
const assert = require("node:assert/strict");
const { chromium } = require("playwright-core");

const BASE = process.env.ASTRBOT_URL || "http://127.0.0.1:16192";
const PASSWORD = process.env.ASTRBOT_PASSWORD || "";
const PLUGIN = process.env.ASTRBOT_PLUGIN || "astrbot_plugin_60sapi_hub";
const CHROME = process.env.CHROME_PATH;

const TASK_1 = {
  id: "fixture-task-1", name: "夹具任务一", profile_id: "fixture-profile",
  profile_name: "夹具组合", site_id: "fixture-site", site_enabled: true,
  output_format: "text", output_label: "纯文字", target_count: 1,
  enabled: false, can_send: false, revision: 1,
  created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z",
};
const TASK_2 = { ...TASK_1, id: "fixture-task-2", name: "夹具任务二" };
const TASK_3 = { ...TASK_1, id: "fixture-task-3", name: "夹具任务三" };
const HISTORY_ID = "fixture-history-A";

async function login() {
  const response = await fetch(`${BASE}/api/v1/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username: "astrbot", password: PASSWORD }),
  });
  return (await response.json()).data.token;
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

function previewBody(task) {
  return JSON.stringify({ status: "ok", data: {
    task_id: task.id, task_name: task.name, task_revision: 1,
    profile_id: "fixture-profile", profile_name: "夹具组合",
    profile_revision: 1, site_id: "fixture-site", site_name: "夹具站点",
    site_revision: 1, site_enabled: true, output_format: "text",
    output_label: "纯文字", targets: [{
      umo: "fake:GroupMessage:123", platform_id: "fake",
      message_type: "GroupMessage", target: "123",
      isolated: false, valid: true, reason: null,
    }], can_deliver: true, reason: null,
  }});
}

function historySummary(id = HISTORY_ID) {
  return {
    id, task_id: TASK_1.id, task_name: "历史任务A", output_format: "text",
    status: "unknown", content_status: "complete",
    created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z",
    target_count: 1, page_count: 1,
  };
}

async function waitPreview(frame) {
  await frame.waitForFunction(
    () => document.getElementById("delivery-confirm-button")?.textContent === "确认并投递",
  );
}

function deferred() {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
}

async function resetBridgeResponseCounter(frame) {
  await frame.evaluate(() => {
    window.__manualDeliveryBridgeResponses = 0;
    if (!window.__manualDeliveryBridgeListener) {
      window.__manualDeliveryBridgeListener = (event) => {
        if (event.data && event.data.channel && event.data.kind === "response") {
          window.__manualDeliveryBridgeResponses += 1;
        }
      };
      window.addEventListener("message", window.__manualDeliveryBridgeListener);
    }
  });
}

async function waitBridgeResponse(frame, minimum = 1) {
  await frame.waitForFunction(
    (count) => (window.__manualDeliveryBridgeResponses || 0) >= count,
    minimum,
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
    assert(token);
    await page.goto(`${BASE}/`, { waitUntil: "domcontentloaded" });
    await page.fill("input[name=username]", "astrbot");
    await page.fill("input[name=password]", PASSWORD);
    await Promise.all([
      page.waitForURL((url) => !url.toString().includes("/auth/login")),
      page.getByRole("button", { name: "登录", exact: true }).click(),
    ]);

    let historyVisible = false;
    let previews = 0;
    const submits = [];
    const detailRequests = [];
    let delayed = null;
    const delayedSettled = [];
    await page.route("**/tasks", async (route) => {
      if (route.request().method() !== "GET") {
        await route.continue();
        return;
      }
      await route.fulfill({
        status: 200, contentType: "application/json",
        body: JSON.stringify({ status: "ok", data: { tasks: [TASK_1, TASK_2, TASK_3] } }),
      });
    });
    await page.route("**/tasks/*/test-preview", async (route) => {
      previews += 1;
      const id = new URL(route.request().url()).pathname.split("/").at(-2);
      const task = [TASK_1, TASK_2, TASK_3].find((item) => item.id === id) || TASK_1;
      await route.fulfill({
        status: 200, contentType: "application/json", body: previewBody(task),
      });
    });
    await page.route("**/tasks/*/test", async (route) => {
      submits.push(route.request().postDataJSON());
      await route.abort("failed");
    });
    await page.route("**/executions?*", async (route) => {
      await route.fulfill({
        status: 200, contentType: "application/json",
        body: JSON.stringify({ status: "ok", data: {
          executions: historyVisible ? [historySummary()] : [],
          limit: 20, offset: 0, count: historyVisible ? 1 : 0, has_more: false,
        }}),
      });
    });
    await page.route("**/executions/*", async (route) => {
      const id = new URL(route.request().url()).pathname.split("/").pop();
      detailRequests.push(id);
      const localDelayed = delayed && delayed.id === id ? delayed : null;
      if (localDelayed) {
        await localDelayed.promise;
        if (localDelayed.status >= 500) {
          await route.fulfill({
            status: localDelayed.status, contentType: "application/json",
            body: '{"status":"error","message":"delayed failure","data":{}}',
          });
        } else {
          await route.fulfill({
            status: localDelayed.status, contentType: "application/json",
            body: JSON.stringify({ status: "ok", data: {
              id, task_id: TASK_1.id, task_name: "历史任务A",
              output_format: "text", status: "success",
              content_status: "complete", error_code: null,
              targets: [], pages: [],
            }}),
          });
        }
        localDelayed.settled.resolve();
        return;
      }
      if (id === HISTORY_ID) {
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

    await page.goto(`${BASE}/#/plugin-view/${PLUGIN}/sites`, {
      waitUntil: "domcontentloaded",
    });
    await page.waitForTimeout(300);
    await page.evaluate(() => {
      for (const node of document.querySelectorAll(
        ".v-overlay__scrim,.v-overlay-container",
      )) node.remove();
    });
    const frame = await frameOf(page);
    await frame.waitForFunction(() => document.getElementById("tasks-list")?.innerText.includes("夹具任务三"));

    // Scenario 1: first submit B, lost response, only query B.
    await frame.locator("#tasks-list li").filter({ hasText: TASK_1.name })
      .getByRole("button", { name: "测试投递" }).click({ force: true });
    await waitPreview(frame);
    const firstSubmitSeen = page.waitForRequest((request) =>
      request.method() === "POST" && request.url().endsWith("/test"),
    );
    await frame.locator("#delivery-confirm-button").click({ force: true });
    const firstSubmit = await firstSubmitSeen;
    const firstId = firstSubmit.postDataJSON().execution_id;
    await frame.waitForSelector("#delivery-uncertain:not([hidden])");
    let firstResponse = page.waitForResponse((response) =>
      response.url().endsWith(`/executions/${firstId}`),
    );
    let firstRequest = page.waitForRequest((request) =>
      request.url().endsWith(`/executions/${firstId}`),
    );
    await frame.locator("#delivery-query-button").click({ force: true });
    await firstRequest;
    await firstResponse;
    await frame.waitForFunction(
      () => document.getElementById("delivery-query-button")?.dataset.querying !== "1",
    ).catch(() => {});
    assert.equal(detailRequests.at(-1), firstId);
    assert.equal(submits.length, 1);
    await frame.locator("#delivery-cancel-button").click({ force: true });
    await page.locator(".v-overlay__scrim").last().waitFor({ state: "hidden" }).catch(() => {});

    // Scenario 2/3: history A fails, retry still A; then B after history queries B.
    historyVisible = true;
    await frame.locator("#delivery-refresh-button").click({ force: true });
    await frame.waitForFunction(
      (id) => document.getElementById("delivery-list")?.innerText.includes(id),
      HISTORY_ID,
    );
    let historyRequest = page.waitForRequest((request) =>
      request.url().endsWith(`/executions/${HISTORY_ID}`),
    );
    let historyResponse = page.waitForResponse((response) =>
      response.url().endsWith(`/executions/${HISTORY_ID}`),
    );
    await frame.locator(`button[aria-label="查看执行 ${HISTORY_ID}"]`).click({ force: true });
    await historyRequest;
    await historyResponse;
    await frame.waitForSelector("#delivery-uncertain:not([hidden])");
    const beforeRetry = detailRequests.length;
    historyRequest = page.waitForRequest((request) =>
      request.url().endsWith(`/executions/${HISTORY_ID}`),
    );
    historyResponse = page.waitForResponse((response) =>
      response.url().endsWith(`/executions/${HISTORY_ID}`),
    );
    await frame.locator("#delivery-query-button").click({ force: true });
    await historyRequest;
    await historyResponse;
    assert.equal(detailRequests.length, beforeRetry + 1);
    assert.equal(detailRequests.at(-1), HISTORY_ID);
    assert.equal(await frame.locator("#delivery-confirm-button").isVisible(), false);
    await frame.locator("#delivery-cancel-button").click({ force: true });
    await page.locator(".v-overlay__scrim").last().waitFor({ state: "hidden" }).catch(() => {});

    await frame.locator("#tasks-list li").filter({ hasText: TASK_2.name })
      .getByRole("button", { name: "测试投递" }).click({ force: true });
    await waitPreview(frame);
    const secondSubmitSeen = page.waitForRequest((request) =>
      request.method() === "POST" && request.url().endsWith("/test"),
    );
    await frame.locator("#delivery-confirm-button").click({ force: true });
    const secondSubmit = await secondSubmitSeen;
    const secondId = secondSubmit.postDataJSON().execution_id;
    await frame.waitForSelector("#delivery-uncertain:not([hidden])");
    const secondRequest = page.waitForRequest((request) =>
      request.url().endsWith(`/executions/${secondId}`),
    );
    const secondResponse = page.waitForResponse((response) =>
      response.url().endsWith(`/executions/${secondId}`),
    );
    await frame.locator("#delivery-query-button").click({ force: true });
    await secondRequest;
    await secondResponse;
    assert.equal(detailRequests.at(-1), secondId);
    assert.equal(submits.length, 2);
    await frame.locator("#delivery-cancel-button").click({ force: true });
    await page.locator(".v-overlay__scrim").last().waitFor({ state: "hidden" }).catch(() => {});

    // Scenario 4: delayed old success while switching to a fresh confirmation.
    for (const status of [200, 500]) {
      historyVisible = true;
      await frame.locator("#delivery-refresh-button").click({ force: true });
      await frame.waitForFunction(
        (id) => document.getElementById("delivery-list")?.innerText.includes(id),
        HISTORY_ID,
      );
      const releaseGate = deferred();
      const settledGate = deferred();
      delayed = {
        id: HISTORY_ID, status,
        promise: releaseGate.promise,
        resolve: releaseGate.resolve,
        settled: settledGate,
      };
      await resetBridgeResponseCounter(frame);
      const delayedRequest = page.waitForRequest((request) =>
        request.url().endsWith(`/executions/${HISTORY_ID}`),
      );
      await frame.locator(`button[aria-label="查看执行 ${HISTORY_ID}"]`).click({ force: true });
      await delayedRequest;
      await frame.locator("#delivery-cancel-button").click({ force: true });
      await page.locator(".v-overlay__scrim").last().waitFor({ state: "hidden" }).catch(() => {});
      await frame.locator("#tasks-list li").filter({ hasText: TASK_3.name })
        .getByRole("button", { name: "测试投递" }).click({ force: true });
      await waitPreview(frame);
      const local = delayed;
      local.resolve();
      await local.settled.promise;
      await waitBridgeResponse(frame);
      assert.equal(await frame.locator("#delivery-dialog-title").innerText(), "确认手动测试投递");
      assert.equal(await frame.locator("#delivery-result").isVisible(), false);
      assert.equal(await frame.locator("#delivery-uncertain").isVisible(), false);
      assert.equal(await frame.locator("#delivery-confirm-button").isVisible(), true);
      delayed = null;
      await frame.locator("#delivery-cancel-button").click({ force: true });
      await page.locator(".v-overlay__scrim").last().waitFor({ state: "hidden" }).catch(() => {});
    }

    console.log("MANUAL DELIVERY QUERY REGRESSION PASSED", {
      previews, submits: submits.length, detailRequests,
      firstSubmit: firstId, secondSubmit: secondId,
    });
  } finally {
    await browser.close();
  }
})().catch((error) => { console.error(error); process.exit(1); });
