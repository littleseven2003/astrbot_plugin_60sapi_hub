/*
 * 投递记录错误码文案回归：执行级 / 目标级 / 图片槽位都译成中文。
 *
 * 真实 Chromium 加载 views/sites/index.html + app.js，只替换宿主 bridge，
 * 用冻结的执行记录夹具检查：已调用发送器后超时与预算耗尽未调用可区分、
 * 前一条失败/未知后跳过的原因可读、未登记的错误码仍回退显示原码。
 *
 * 桩 bridge 的页面逻辑验证，不代表真实 AstrBot 后台/数据库往返，也不发送消息。
 */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const http = require("node:http");
const path = require("node:path");
const { chromium } = require("playwright-core");

function resolveChrome() {
  const configured = process.env.CHROME_PATH;
  if (configured) return configured;
  try {
    return chromium.executablePath();
  } catch {
    return "";
  }
}

const CHROME = resolveChrome();
if (!CHROME || !fs.existsSync(CHROME)) {
  console.error("找不到可用的 Chromium：请设置 CHROME_PATH。");
  process.exit(2);
}

const VIEWS_DIR = path.resolve(__dirname, "..", "..", "views", "sites");
const MIME = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
};

const UMO_A = "aiocqhttp:GroupMessage:g-a:1";
const UMO_B = "aiocqhttp:GroupMessage:g-b:1";

function page_(index, status, errorCode) {
  const value = { image_index: index, status, label: index === 1 ? "每日新闻" : "知乎热榜" };
  if (errorCode) value.error_code = errorCode;
  return value;
}

function target(position, umo, status, errorCode, images) {
  return {
    position,
    umo,
    status,
    error_code: errorCode || null,
    delivered_image_count: images.filter((item) => item.status === "success").length,
    skipped_image_count: images.filter((item) => item.status !== "success").length,
    section_image_count: images.length,
    images,
  };
}

function execution(id, taskName, fields, targets) {
  return {
    id,
    task_id: "task-1",
    task_name: taskName,
    output_format: "image",
    content_status: fields.contentStatus || "complete",
    status: fields.status,
    error_code: fields.errorCode || null,
    created_at: "2026-11-06T00:00:00Z",
    updated_at: "2026-11-06T00:00:00Z",
    image_model: "sections",
    image_record_count: targets.reduce((sum, item) => sum + item.images.length, 0),
    image_count: targets.reduce((sum, item) => sum + item.images.length, 0),
    record_complete: true,
    section_slot_count: 2,
    generated_image_count: 2,
    section_image_count: 2,
    failed_section_count: 0,
    failed_sections: [],
    image_partial: false,
    delivered_image_count: 0,
    skipped_image_count: 0,
    skipped_section_image_failed_count: 0,
    skipped_config_changed_count: 0,
    skipped_other_count: 0,
    targets,
  };
}

// 已调用发送器后超时（第一张）→ 后续目标预算耗尽（未调用发送器）→ 剩余槽位跳过。
const TIMEOUT = execution("exec-timeout", "发送中断记录", {
  status: "unknown", contentStatus: "complete", errorCode: "sender_timeout",
}, [
  target(0, UMO_A, "unknown", "sender_timeout", [
    page_(1, "unknown", "sender_timeout"),
    page_(2, "skipped", "skipped_after_send_unknown"),
  ]),
  target(1, UMO_B, "unknown", "sender_budget_exhausted", [
    page_(1, "unknown", "sender_budget_exhausted"),
    page_(2, "skipped", "skipped_after_send_unknown"),
  ]),
]);

// 明确失败：发送通道拒绝，同目标后续槽位因前一条失败跳过，另一目标仍成功。
const FAILED = execution("exec-failed", "明确失败记录", {
  status: "partial", contentStatus: "complete",
}, [
  target(0, UMO_A, "failed", "sender_route_rejected", [
    page_(1, "failed", "sender_route_rejected"),
    page_(2, "skipped", "skipped_after_send_failed"),
  ]),
  target(1, UMO_B, "success", null, [
    page_(1, "success"),
    page_(2, "success"),
  ]),
]);

// 未登记的错误码：执行级与槽位都必须回退显示原码，不能变成空串或 undefined。
const UNKNOWN_CODE = execution("exec-unknown-code", "未知错误码记录", {
  status: "failed", contentStatus: "failed", errorCode: "mystery_failure_code",
}, [
  target(0, UMO_A, "failed", "mystery_failure_code", [
    page_(1, "failed", "mystery_failure_code"),
  ]),
]);

// 发送器已返回成功但写投递记录失败：执行级 unknown，目标与图片停在 sending，
// 与真实库中记录一致（尚未写回 success）。
const RECORD_FAILED = execution("exec-record-failed", "记录写入失败记录", {
  status: "unknown", contentStatus: "complete", errorCode: "record_after_send_failed",
}, [
  target(0, UMO_A, "sending", null, [page_(1, "sending")]),
]);

const DETAILS = {
  "exec-timeout": TIMEOUT,
  "exec-failed": FAILED,
  "exec-unknown-code": UNKNOWN_CODE,
  "exec-record-failed": RECORD_FAILED,
};

function summary(item) {
  return {
    id: item.id, task_id: item.task_id, task_name: item.task_name,
    output_format: item.output_format, status: item.status,
    content_status: item.content_status,
    created_at: item.created_at, updated_at: item.updated_at,
    target_count: item.targets.length,
    image_model: item.image_model,
    image_record_count: item.image_record_count,
    image_count: item.image_count,
    record_complete: item.record_complete,
    section_slot_count: item.section_slot_count,
    generated_image_count: item.generated_image_count,
    section_image_count: item.section_image_count,
    failed_section_count: item.failed_section_count,
    image_partial: item.image_partial,
    delivered_image_count: item.delivered_image_count,
    skipped_image_count: item.skipped_image_count,
    skipped_section_image_failed_count: 0,
    skipped_config_changed_count: 0,
    skipped_other_count: 0,
  };
}

const server = http.createServer((request, response) => {
  const url = new URL(request.url, "http://127.0.0.1");
  const relative = url.pathname === "/" ? "/index.html" : url.pathname;
  const file = path.join(VIEWS_DIR, relative);
  if (!file.startsWith(VIEWS_DIR) || !fs.existsSync(file)) {
    response.writeHead(404).end("not found");
    return;
  }
  response.writeHead(200, {
    "Content-Type": MIME[path.extname(file)] || "application/octet-stream",
  });
  response.end(fs.readFileSync(file));
});

let assertions = 0;
function check(condition, message) {
  assertions += 1;
  assert.ok(condition, message);
}

function countOf(text, needle) {
  return text.split(needle).length - 1;
}

(async () => {
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const port = server.address().port;
  const browser = await chromium.launch({
    headless: true, executablePath: CHROME, args: ["--no-sandbox"],
  });
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  try {
    await page.addInitScript((fixtures) => {
      window.__deliveryCalls = [];
      window.AstrBotPluginView = {
        ready: async () => ({ pageTitle: "60sAPI Hub" }),
        apiGet: async (endpoint) => {
          window.__deliveryCalls.push(endpoint);
          if (endpoint === "sites") return { sites: [] };
          if (endpoint === "tasks") return { tasks: [] };
          if (endpoint === "schedule-runs") return { claims: [], skips: [] };
          if (endpoint === "executions") {
            return {
              executions: fixtures.summaries,
              has_more: false,
              limit: 20,
            };
          }
          if (endpoint.startsWith("executions/")) {
            const id = decodeURIComponent(endpoint.slice("executions/".length));
            const detail = fixtures.details[id];
            if (!detail) throw new Error("执行记录不存在");
            return detail;
          }
          return {};
        },
        apiPost: async (endpoint) => {
          window.__deliveryCalls.push("POST " + endpoint);
          return {};
        },
      };
    }, {
      summaries: [
        summary(TIMEOUT), summary(FAILED), summary(UNKNOWN_CODE), summary(RECORD_FAILED),
      ],
      details: DETAILS,
    });

    await page.goto("http://127.0.0.1:" + port + "/index.html", {
      waitUntil: "domcontentloaded",
    });
    await page.click('.nav-item[data-view="executions"]');
    await page.waitForFunction(
      () => document.getElementById("view-executions")?.hidden === false,
      null, { timeout: 15000 },
    );
    await page.waitForFunction(
      () => document.getElementById("delivery-list")?.innerText.includes("发送中断记录"),
      null, { timeout: 15000 },
    );

    const openDetail = async (id) => {
      await page.click('button[aria-label="查看执行 ' + id + '"]');
      await page.waitForFunction(
        (executionId) => {
          const result = document.getElementById("delivery-result");
          return result && result.hidden === false &&
            result.innerText.includes(executionId);
        },
        id, { timeout: 15000 },
      );
      return page.evaluate(() => ({
        text: document.getElementById("delivery-result").innerText,
        rows: [...document.querySelectorAll("#delivery-result .delivery-target")]
          .map((row) => row.innerText),
      }));
    };
    const closeDialog = async () => {
      await page.evaluate(() => document.getElementById("delivery-dialog")?.close());
    };

    // 1) 执行级：已调用发送器后超时译成中文，且不再暴露原码
    const timeout = await openDetail("exec-timeout");
    check(timeout.text.includes("错误码：已调用发送器后超时，结果未知"),
      "执行级要译成中文: " + timeout.text);
    check(!timeout.text.includes("sender_timeout"), "映射后不得再出现原码: " + timeout.text);
    check(!timeout.text.includes("sender_budget_exhausted"), "预算耗尽也要译成中文: " + timeout.text);
    check(!timeout.text.includes("undefined") && !timeout.text.includes("null"),
      "不得出现 undefined/null: " + timeout.text);

    // 2) 目标级：同一次执行里两种终态必须可区分
    check(timeout.rows.length === 2, "应有两条目标记录: " + JSON.stringify(timeout.rows));
    check(timeout.rows[0].includes("结果未知（已调用发送器后超时，结果未知）"),
      "已调用后超时要在目标行体现: " + timeout.rows[0]);
    check(timeout.rows[1].includes("结果未知（发送预算已耗尽，未调用发送器）"),
      "预算耗尽未调用要在目标行体现: " + timeout.rows[1]);
    check(countOf(timeout.text, "已调用发送器后超时，结果未知") >= 2,
      "执行级与目标级都要说明已调用后超时: " + timeout.text);

    // 3) 图片槽位：失败/未知之后跳过的槽位保留可读原因
    check(timeout.text.includes("第 1 张 结果未知（每日新闻）（已调用发送器后超时，结果未知）"),
      "槽位要带 label 与中文原因: " + timeout.text);
    check(timeout.text.includes("第 2 张 未送达（知乎热榜）（因前一条结果未知未尝试）"),
      "后续槽位要说明为何未尝试: " + timeout.text);
    check(timeout.text.includes("第 1 张 结果未知（每日新闻）（发送预算已耗尽，未调用发送器）"),
      "预算耗尽的槽位文案: " + timeout.text);

    // 4) 明确失败：原因与原码映射一致，成功目标不带多余括号
    await closeDialog();
    const failed = await openDetail("exec-failed");
    check(failed.text.includes("失败（发送通道拒绝了本次投递）"),
      "目标失败原因要译成中文: " + failed.text);
    check(failed.text.includes("第 1 张 失败（每日新闻）（发送通道拒绝了本次投递）"),
      "失败槽位文案: " + failed.text);
    check(failed.text.includes("第 2 张 未送达（知乎热榜）（因前一条发送失败未尝试）"),
      "失败后的槽位说明: " + failed.text);
    check(failed.rows[1].includes("成功 · 第 1 张 成功"),
      "成功目标不应带错误码括号: " + failed.rows[1]);
    check(failed.rows[1].includes(
      "成功 · 第 1 张 成功（每日新闻）；第 2 张 成功（知乎热榜）"),
    "成功目标行文案应稳定: " + JSON.stringify(failed.rows[1]));
    check(!failed.rows[1].includes("（发送"),
      "成功目标行不应带错误码文案: " + JSON.stringify(failed.rows[1]));
    check(!failed.text.includes("sender_route_rejected"),
      "明确失败也不得暴露原码: " + failed.text);
    check(failed.text.includes("无执行级错误"),
      "无执行级错误仍要明确说明: " + failed.text);

    // 5) 未登记的错误码：回退显示原码而不是清空
    await closeDialog();
    const unknown = await openDetail("exec-unknown-code");
    check(unknown.text.includes("错误码：mystery_failure_code"),
      "未登记的执行级错误码要回退显示原码: " + unknown.text);
    check(unknown.text.includes("失败（mystery_failure_code）"),
      "未登记的目标级错误码要回退显示原码: " + unknown.text);
    check(unknown.text.includes("第 1 张 失败（每日新闻）（mystery_failure_code）"),
      "未登记的槽位错误码要回退显示原码: " + unknown.text);
    check(!unknown.text.includes("undefined"), "回退时不得出现 undefined: " + unknown.text);

    // 6) 发送器返回成功但写投递记录失败：执行级译成中文；目标和图片仍是
    //    真实库里未写回的状态 sending，不能伪造成成功。
    await closeDialog();
    const recordFailed = await openDetail("exec-record-failed");
    check(recordFailed.text.includes("错误码：发送器返回成功，但写入投递记录失败，请核对实际收件"),
      "记录写入失败要按真实语义译成中文: " + recordFailed.text);
    check(!recordFailed.text.includes("record_after_send_failed"),
      "映射后不得再出现原码: " + recordFailed.text);
    check(recordFailed.rows.length === 1
      && recordFailed.rows[0].includes("发送中 · 第 1 张 发送中（每日新闻）"),
    "目标与图片应显示真实的未写回状态发送中: " + JSON.stringify(recordFailed.rows));
    check(!recordFailed.text.includes("sending"),
      "发送中不得显示英文原值: " + recordFailed.text);

    // 7) 打开详情不写投递：只读 executions
    const calls = await page.evaluate(() => window.__deliveryCalls.slice());
    check(!calls.some((item) => String(item).startsWith("POST")),
      "打开执行记录不得发起写请求: " + calls.join(","));
    check(calls.filter((item) => item === "executions").length >= 1,
      "列表应读取 executions: " + calls.join(","));

    console.log("DELIVERY ERROR LABELS PASS", { assertions });
  } finally {
    await page.close();
    await browser.close();
    server.close();
  }
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
