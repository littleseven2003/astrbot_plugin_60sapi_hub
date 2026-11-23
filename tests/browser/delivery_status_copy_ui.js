/*
 * 投递状态文案回归：image_model 分档、三个数量、跳过原因分解、列表/详情一致。
 * executions 列表与详情用 route fixture 返回冻结契约字段，不发真实投递。
 *
 * ASTRBOT_URL=... ASTRBOT_PASSWORD=... ASTRBOT_PLUGIN=... \
 * CHROME_PATH=... NODE_PATH=<playwright-core> \
 * node tests/browser/delivery_status_copy_ui.js
 */
const assert = require("node:assert/strict");
const { chromium } = require("playwright-core");

const BASE = process.env.ASTRBOT_URL;
const PASSWORD = process.env.ASTRBOT_PASSWORD;
const PLUGIN = process.env.ASTRBOT_PLUGIN || "astrbot_plugin_60sapi_hub";
const CHROME = process.env.CHROME_PATH;
if (!BASE || !PASSWORD || !CHROME) {
  console.error("ASTRBOT_URL, ASTRBOT_PASSWORD and CHROME_PATH are required");
  process.exit(2);
}

const WAIT = 20000;

function detail(caseData) {
  const isSections = caseData.model === "sections";
  return {
    id: caseData.id,
    task_id: "task-1",
    task_name: caseData.taskName,
    items: [{ content_type: "daily_news", label: "每日新闻", limit: null }],
    sites: [{ id: "site-1", name: "夹具站点", enabled: true, revision: 1 }],
    site_id: "site-1", site_revision: 1,
    output_format: caseData.outputFormat || "image",
    content_status: caseData.contentStatus || (caseData.status === "success" ? "complete" : "partial"),
    content_hash: "hash",
    status: caseData.status || "partial",
    error_code: caseData.errorCode || null,
    created_at: "2026-11-06T00:00:00Z", updated_at: "2026-11-06T00:00:00Z",
    image_model: caseData.model,
    image_record_count: caseData.records,
    image_count: caseData.records,
    record_complete: caseData.recordComplete !== false,
    section_slot_count: isSections ? caseData.slots : null,
    generated_image_count: isSections ? caseData.generated : null,
    section_image_count: isSections ? caseData.slots : null,
    failed_section_count: (caseData.failedSections || []).length,
    failed_sections: caseData.failedSections || [],
    image_partial: caseData.imagePartial === true,
    delivered_image_count: caseData.delivered,
    skipped_image_count: caseData.skipped,
    skipped_section_image_failed_count: caseData.skippedSectionFailed || 0,
    skipped_config_changed_count: caseData.skippedConfig || 0,
    skipped_other_count: caseData.skippedOther || 0,
    targets: caseData.targets || [],
  };
}

function summary(caseData) {
  const payload = detail(caseData);
  return {
    id: payload.id, task_id: payload.task_id, task_name: payload.task_name,
    output_format: payload.output_format, status: payload.status,
    content_status: payload.content_status,
    created_at: payload.created_at, updated_at: payload.updated_at,
    target_count: (caseData.targets || []).length,
    image_model: payload.image_model,
    image_record_count: payload.image_record_count,
    image_count: payload.image_count,
    record_complete: payload.record_complete,
    section_slot_count: payload.section_slot_count,
    generated_image_count: payload.generated_image_count,
    section_image_count: payload.section_image_count,
    failed_section_count: payload.failed_section_count,
    image_partial: payload.image_partial,
    delivered_image_count: payload.delivered_image_count,
    skipped_image_count: payload.skipped_image_count,
    skipped_section_image_failed_count: payload.skipped_section_image_failed_count,
    skipped_config_changed_count: payload.skipped_config_changed_count,
    skipped_other_count: payload.skipped_other_count,
  };
}

function imageTarget(position, images) {
  return {
    position,
    umo: "fake_qq_01:GroupMessage:g" + position,
    status: "partial",
    error_code: "section_image_failed",
    delivered_image_count: images.filter((item) => item.status === "success").length,
    skipped_image_count: images.filter((item) => item.status !== "success").length,
    section_image_count: images.length,
    images,
  };
}

const SECTION_IMAGES = [
  { image_index: 1, status: "success", label: "每日新闻" },
  { image_index: 2, status: "success", label: "知乎热榜" },
  { image_index: 3, status: "success", label: "Bilibili 热搜" },
];

const CASES = {
  "copy-all-success": {
    id: "copy-all-success", taskName: "全部成功", model: "sections",
    status: "success", imagePartial: false,
    slots: 3, generated: 3, records: 6, delivered: 6, skipped: 0,
    failedSections: [],
    targets: [imageTarget(0, SECTION_IMAGES), imageTarget(1, SECTION_IMAGES)],
  },
  "copy-section-failed": {
    id: "copy-section-failed", taskName: "栏目未生成", model: "sections",
    slots: 3, generated: 2, records: 6, delivered: 4, skipped: 2,
    imagePartial: true, skippedSectionFailed: 2,
    failedSections: [{ label: "知乎热榜", reason: "image_failed", image_index: 2 }],
    targets: [imageTarget(0, [
      { image_index: 1, status: "success", label: "每日新闻" },
      { image_index: 2, status: "skipped", error_code: "section_image_failed", label: "知乎热榜" },
      { image_index: 3, status: "success", label: "Bilibili 热搜" },
    ]), imageTarget(1, [
      { image_index: 1, status: "success", label: "每日新闻" },
      { image_index: 2, status: "skipped", error_code: "section_image_failed", label: "知乎热榜" },
      { image_index: 3, status: "success", label: "Bilibili 热搜" },
    ])],
  },
  "copy-config-changed": {
    id: "copy-config-changed", taskName: "配置变化", model: "sections",
    slots: 3, generated: 3, records: 6, delivered: 3, skipped: 3,
    imagePartial: true, skippedConfig: 2, skippedOther: 1,
    failedSections: [],
    targets: [imageTarget(0, [
      { image_index: 1, status: "success", label: "每日新闻" },
      { image_index: 2, status: "success", label: "知乎热榜" },
      { image_index: 3, status: "skipped", error_code: "config_changed", label: "Bilibili 热搜" },
    ]), imageTarget(1, [
      { image_index: 1, status: "success", label: "每日新闻" },
      { image_index: 2, status: "skipped", error_code: "config_changed" },
      { image_index: 3, status: "skipped", error_code: "unknown" },
    ])],
  },
  "copy-mixed": {
    id: "copy-mixed", taskName: "混合原因", model: "sections",
    slots: 3, generated: 2, records: 6, delivered: 2, skipped: 4,
    imagePartial: true, skippedSectionFailed: 2, skippedConfig: 1, skippedOther: 1,
    failedSections: [{ label: "知乎热榜", reason: "image_failed", image_index: 2 }],
    targets: [imageTarget(0, [
      { image_index: 1, status: "success", label: "每日新闻" },
      { image_index: 2, status: "skipped", error_code: "section_image_failed", label: "知乎热榜" },
      { image_index: 3, status: "success", label: "Bilibili 热搜" },
    ]), imageTarget(1, [
      { image_index: 1, status: "skipped", error_code: "config_changed", label: "每日新闻" },
      { image_index: 2, status: "skipped", error_code: "section_image_failed", label: "知乎热榜" },
      { image_index: 3, status: "skipped", error_code: "interrupted", label: "Bilibili 热搜" },
    ])],
  },
  "copy-legacy-pages": {
    id: "copy-legacy-pages", taskName: "旧版分页记录", model: "unknown",
    slots: null, generated: null, records: 6, delivered: 4, skipped: 2,
    imagePartial: true, skippedConfig: 1, skippedOther: 1,
    failedSections: [],
    targets: [{
      position: 0, umo: "fake_qq_01:GroupMessage:g0", status: "partial",
      error_code: null, delivered_image_count: 2, skipped_image_count: 1,
      section_image_count: null,
      images: [
        { image_index: 1, status: "success" },
        { image_index: 2, status: "success" },
        { image_index: 3, status: "skipped", error_code: "config_changed" },
      ],
    }, {
      position: 1, umo: "fake_qq_01:GroupMessage:g1", status: "partial",
      error_code: null, delivered_image_count: 2, skipped_image_count: 1,
      section_image_count: null,
      images: [
        { image_index: 1, status: "success" },
        { image_index: 2, status: "success" },
        { image_index: 3, status: "skipped", error_code: "unknown" },
      ],
    }],
  },
  "copy-never-started": {
    id: "copy-never-started", taskName: "恢复后未开始", model: "sections",
    slots: 3, generated: 1, records: 6, delivered: 1, skipped: 5,
    imagePartial: true, skippedOther: 5,
    failedSections: [],
    targets: [imageTarget(0, [
      { image_index: 1, status: "success", label: "每日新闻" },
      { image_index: 2, status: "skipped", error_code: "never_started", label: "知乎热榜" },
      { image_index: 3, status: "skipped", error_code: "never_started", label: "Bilibili 热搜" },
    ]), imageTarget(1, [
      { image_index: 1, status: "skipped", error_code: "never_started", label: "每日新闻" },
      { image_index: 2, status: "skipped", error_code: "never_started", label: "知乎热榜" },
      { image_index: 3, status: "skipped", error_code: "never_started", label: "Bilibili 热搜" },
    ])],
  },
  "copy-unknown-count": {
    id: "copy-unknown-count", taskName: "记录不完整", model: "sections",
    recordComplete: false, slots: null, generated: null,
    records: 6, delivered: 2, skipped: 2,
    imagePartial: true, skippedConfig: 1, skippedOther: 1,
    failedSections: [],
    targets: [imageTarget(0, [
      { image_index: 1, status: "success" },
      { image_index: 2, status: "success" },
      { image_index: 3, status: "skipped", error_code: "config_changed" },
    ])],
  },
  // 发送中断：第一张已调用发送器后超时，后续目标预算耗尽（未调用），
  // 每个目标剩余槽位因前一条结果未知而跳过。
  "copy-send-interrupted": {
    id: "copy-send-interrupted", taskName: "发送中断记录", model: "sections",
    status: "unknown", contentStatus: "complete", errorCode: "sender_timeout",
    slots: 3, generated: 3, records: 4, delivered: 0, skipped: 2,
    imagePartial: false,
    failedSections: [],
    targets: [{
      position: 0, umo: "fake_qq_01:GroupMessage:g0", status: "unknown",
      error_code: "sender_timeout", delivered_image_count: 0,
      skipped_image_count: 1, section_image_count: 2,
      images: [
        { image_index: 1, status: "unknown", error_code: "sender_timeout", label: "每日新闻" },
        { image_index: 2, status: "skipped", error_code: "skipped_after_send_unknown", label: "知乎热榜" },
      ],
    }, {
      position: 1, umo: "fake_qq_01:GroupMessage:g1", status: "unknown",
      error_code: "sender_budget_exhausted", delivered_image_count: 0,
      skipped_image_count: 1, section_image_count: 2,
      images: [
        { image_index: 1, status: "unknown", error_code: "sender_budget_exhausted", label: "每日新闻" },
        { image_index: 2, status: "skipped", error_code: "skipped_after_send_unknown", label: "知乎热榜" },
      ],
    }],
  },
  // 发送成功但写投递记录失败：执行级错误码要译成中文，目标本身仍是成功。
  "copy-record-failed": {
    id: "copy-record-failed", taskName: "记录写入失败记录", model: "sections",
    status: "unknown", contentStatus: "complete", errorCode: "record_after_send_failed",
    slots: 1, generated: 1, records: 1, delivered: 1, skipped: 0,
    imagePartial: false,
    failedSections: [],
    targets: [{
      position: 0, umo: "fake_qq_01:GroupMessage:g0", status: "sending",
      error_code: null, delivered_image_count: 0,
      skipped_image_count: 0, section_image_count: 1,
      images: [
        { image_index: 1, status: "sending", label: "每日新闻" },
      ],
    }],
  },
  "copy-text-output": {
    id: "copy-text-output", taskName: "文字历史记录", model: "unknown",
    outputFormat: "text", status: "success", imagePartial: false,
    slots: null, generated: null, records: 2, delivered: 2, skipped: 0,
    failedSections: [],
    targets: [{
      position: 0, umo: "fake_qq_01:GroupMessage:g0", status: "success",
      error_code: null, delivered_image_count: 1, skipped_image_count: 0,
      section_image_count: null,
      images: [{ image_index: 1, status: "success" }],
    }, {
      position: 1, umo: "fake_qq_01:GroupMessage:g1", status: "success",
      error_code: null, delivered_image_count: 1, skipped_image_count: 0,
      section_image_count: null,
      images: [{ image_index: 1, status: "success" }],
    }],
  },
};

async function frameOf(page) {
  const iframe = page.locator("iframe.plugin-view-frame").first();
  await iframe.waitFor({ state: "visible" });
  const frame = await (await iframe.elementHandle()).contentFrame();
  assert.ok(frame, "plugin iframe must load");
  await frame.waitForFunction(() => document.getElementById("sites-list"), null, { timeout: WAIT });
  return frame;
}

(async () => {
  const browser = await chromium.launch({
    headless: true, executablePath: CHROME, args: ["--no-sandbox"],
  });
  let assertions = 0;
  const check = (condition, message) => {
    assertions += 1;
    assert.ok(condition, message);
  };
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  try {
    await page.route("**/executions**", async (route) => {
      const url = route.request().url();
      const matched = Object.values(CASES).find((item) => url.includes(item.id));
      if (matched) {
        await route.fulfill({
          status: 200, contentType: "application/json",
          body: JSON.stringify({ status: "ok", data: detail(matched) }),
        });
        return;
      }
      const summaries = Object.values(CASES).map(summary);
      await route.fulfill({
        status: 200, contentType: "application/json",
        body: JSON.stringify({
          status: "ok", data: { count: summaries.length, executions: summaries },
        }),
      });
    });

    await page.goto(BASE + "/", { waitUntil: "domcontentloaded" });
    await page.fill("input[name=username]", "astrbot");
    await page.fill("input[name=password]", PASSWORD);
    await Promise.all([
      page.waitForURL((url) => !url.toString().includes("/auth/login")),
      page.getByRole("button", { name: "登录", exact: true }).click(),
    ]);
    await page.goto(BASE + "/#/plugin-view/" + PLUGIN + "/sites", { waitUntil: "domcontentloaded" });
    await page.waitForTimeout(300);
    await page.evaluate(() => {
      for (const node of document.querySelectorAll(".v-overlay__scrim,.v-overlay-container")) {
        node.remove();
      }
    });
    const frame = await frameOf(page);
    await frame.evaluate(() => {
      if (document.getElementById("view-executions")?.hidden !== false) {
        window.location.hash = "#executions";
      }
    });
    await frame.waitForFunction(
      () => document.getElementById("view-executions")?.hidden === false,
      null, { timeout: WAIT },
    );
    await frame.evaluate(() => document.getElementById("delivery-refresh-button").click());
    await frame.waitForFunction(() =>
      document.getElementById("delivery-list")?.innerText.includes("copy-all-success"),
    null, { timeout: WAIT });

    const cardText = async (id) => frame.evaluate((executionId) => {
      const card = [...document.querySelectorAll("#delivery-list li")]
        .find((node) => node.innerText.includes(executionId));
      return card ? card.innerText : "";
    }, id);
    const listText = await frame.evaluate(() => document.getElementById("delivery-list").innerText);

    // 1) 多目标多栏目全部成功：栏目图数按槽位计，记录数另给
    const success = await cardText("copy-all-success");
    check(success.includes("3 张栏目图"), "全部成功应展示栏目图数: " + success);
    check(success.includes("6 条图片记录"), "全部成功应展示记录数: " + success);
    check(!success.includes("未送达"), "全部成功不应出现未送达: " + success);

    // 2) 新格式部分栏目未生成：槽位数 / 实际生成数 / 记录数 三者可区分
    const sectionFailed = await cardText("copy-section-failed");
    check(sectionFailed.includes("3 个栏目（槽位）"), "要给出栏目槽位数: " + sectionFailed);
    check(sectionFailed.includes("实际生成 2 张图"), "要给出实际生成数: " + sectionFailed);
    check(sectionFailed.includes("6 条图片记录"), "要给出记录数: " + sectionFailed);
    check(sectionFailed.includes("1 个栏目未生成图片"), "要说明有栏目没出图: " + sectionFailed);
    check(sectionFailed.includes("2 条发送记录未送达"), "记录维度要单独给数: " + sectionFailed);
    check(sectionFailed.includes("2 条因栏目图片生成失败"), "原因要分解: " + sectionFailed);

    // 3) 配置变化 + 其他原因：不得说成栏目未生成，也不得出现「发送前终止」
    const configChanged = await cardText("copy-config-changed");
    check(configChanged.includes("3 张栏目图"), "配置变化场景栏目数不变: " + configChanged);
    check(configChanged.includes("3 条发送记录未送达"), "配置变化按记录计数: " + configChanged);
    check(configChanged.includes("2 条因配置变化"), "配置变化原因单列: " + configChanged);
    check(configChanged.includes("1 条因其他或未知原因"), "其它原因单列: " + configChanged);
    check(!configChanged.includes("栏目未生成图片"), "无栏目失败时不得声称栏目未生成: " + configChanged);
    check(!listText.includes("发送前终止"), "不得再出现发送前终止: " + configChanged);

    // 4) 混合原因：栏目数与记录数、三个原因分别有数
    const mixed = await cardText("copy-mixed");
    check(mixed.includes("3 个栏目（槽位）"), "混合场景槽位数: " + mixed);
    check(mixed.includes("实际生成 2 张图"), "混合场景实际生成数: " + mixed);
    check(mixed.includes("1 个栏目未生成图片"), "混合场景栏目失败数: " + mixed);
    check(mixed.includes("4 条发送记录未送达"), "混合场景记录数: " + mixed);
    check(mixed.includes("2 条因栏目图片生成失败"), "混合原因一: " + mixed);
    check(mixed.includes("1 条因配置变化"), "混合原因二: " + mixed);
    check(mixed.includes("1 条因其他或未知原因"), "混合原因三: " + mixed);

    // 5) 旧版分页记录：只说图片记录，全文不得出现栏目相关词
    const legacy = await cardText("copy-legacy-pages");
    check(legacy.includes("6 条图片记录"), "旧记录只给中性记录数: " + legacy);
    check(!legacy.includes("栏目"), "旧记录不得出现栏目字样: " + legacy);
    check(!legacy.includes("张栏目图"), "旧记录不得出现张栏目图: " + legacy);
    check(!legacy.includes("槽位"), "旧记录不得出现槽位: " + legacy);
    check(legacy.includes("2 条发送记录未送达"), "旧记录仍要给出未送达记录数: " + legacy);
    check(legacy.includes("1 条因配置变化"), "旧记录原因一: " + legacy);
    check(legacy.includes("1 条因其他或未知原因"), "旧记录原因二: " + legacy);

    // 6) 文字/Markdown 历史记录：条消息，无栏目词
    const textOutput = await cardText("copy-text-output");
    check(textOutput.includes("2 条消息"), "文字输出按条消息计数: " + textOutput);
    check(!textOutput.includes("张栏目图"), "文字输出不得出现张栏目图: " + textOutput);
    check(!textOutput.includes("栏目"), "文字输出不得出现栏目字样: " + textOutput);

    // 6b) 恢复后 never_started 槽位：不得算成已生成图片
    const neverStarted = await cardText("copy-never-started");
    check(neverStarted.includes("3 个栏目（槽位）"), "恢复记录要给槽位数: " + neverStarted);
    check(neverStarted.includes("实际生成 1 张图"), "只有真产出的图才算已生成: " + neverStarted);
    check(!neverStarted.includes("3 张栏目图"), "不得把未开始槽位算成已生成: " + neverStarted);
    check(!neverStarted.includes("图片数量未知"), "记录完整时不应说未知: " + neverStarted);

    // 6c) 记录不完整 / generated 为 null：必须给未知表述且不给确定数字
    const unknownCount = await cardText("copy-unknown-count");
    check(unknownCount.includes("图片数量未知（记录不完整）"), "记录不完整要明确未知: " + unknownCount);
    check(!unknownCount.includes("张栏目图"), "未知时不得给栏目图数字: " + unknownCount);
    check(!unknownCount.includes("实际生成"), "未知时不得给实际生成数字: " + unknownCount);
    check(!unknownCount.includes("个栏目（槽位）"), "未知时不得给槽位数字: " + unknownCount);
    check(unknownCount.includes("6 条图片记录"), "未知时仍可给中性记录数: " + unknownCount);

    // 7) 列表与详情使用同一句摘要
    await frame.evaluate(() => {
      const button = document.querySelector('button[aria-label="查看执行 copy-mixed"]');
      if (button) button.click();
    });
    await frame.waitForFunction(
      () => document.getElementById("delivery-result")?.hidden === false,
      null, { timeout: WAIT },
    );
    const detailText = await frame.evaluate(() =>
      document.getElementById("delivery-result").innerText);
    check(detailText.includes("部分成功"), "详情标题应为部分成功: " + detailText);
    for (const sentence of [
      "1 个栏目未生成图片",
      "4 条发送记录未送达",
      "2 条因栏目图片生成失败",
      "1 条因配置变化",
      "1 条因其他或未知原因",
    ]) {
      check(detailText.includes(sentence), "详情缺少列表同款文案 " + sentence + ": " + detailText);
      check(mixed.includes(sentence), "列表缺少文案 " + sentence + ": " + mixed);
    }
    check(detailText.includes("第 2 张 未送达（知乎热榜）"), "槽位文案要带 label: " + detailText);
    check(detailText.includes("栏目图片生成失败"), "错误码要译成可读文案: " + detailText);
    check(!detailText.includes("重发") && !detailText.includes("补发"),
      "不得出现重发/补发入口: " + detailText);

    // 8) 旧记录详情同样不得出现栏目字样
    await frame.evaluate(() => {
      const button = document.querySelector('button[aria-label="查看执行 copy-legacy-pages"]');
      if (button) button.click();
    });
    await frame.waitForFunction(
      () => document.getElementById("delivery-result")?.innerText.includes("2 条发送记录未送达"),
      null, { timeout: WAIT },
    );
    const legacyDetail = await frame.evaluate(() =>
      document.getElementById("delivery-result").innerText);
    check(!legacyDetail.includes("栏目"), "旧记录详情不得出现栏目字样: " + legacyDetail);
    check(!legacyDetail.includes("张栏目图"), "旧记录详情不得出现张栏目图: " + legacyDetail);

    // 9) 发送中断：执行级 / 目标级 / 槽位都译成中文，
    //    并区分「已调用发送器后超时」与「预算耗尽未调用发送器」。
    await frame.evaluate(() => {
      const button = document.querySelector('button[aria-label="查看执行 copy-send-interrupted"]');
      if (button) button.click();
    });
    await frame.waitForFunction(
      () => document.getElementById("delivery-result")?.innerText.includes("copy-send-interrupted"),
      null, { timeout: WAIT },
    );
    const interruptedDetail = await frame.evaluate(() =>
      document.getElementById("delivery-result").innerText);
    check(interruptedDetail.includes("错误码：已调用发送器后超时，结果未知"),
      "执行级错误码要译成中文: " + interruptedDetail);
    check(interruptedDetail.includes("结果未知（已调用发送器后超时，结果未知）"),
      "目标级要说明已调用发送器后超时: " + interruptedDetail);
    check(interruptedDetail.includes("结果未知（发送预算已耗尽，未调用发送器）"),
      "目标级要说明预算耗尽未调用发送器: " + interruptedDetail);
    check(interruptedDetail.includes("第 1 张 结果未知（每日新闻）（已调用发送器后超时，结果未知）"),
      "槽位要带栏目与中文原因: " + interruptedDetail);
    check(interruptedDetail.includes("第 2 张 未送达（知乎热榜）（因前一条结果未知未尝试）"),
      "后续槽位要说明为何未尝试: " + interruptedDetail);
    check(!interruptedDetail.includes("sender_timeout"),
      "不得再暴露发送错误原码: " + interruptedDetail);
    check(!interruptedDetail.includes("sender_budget_exhausted"),
      "预算耗尽不得暴露原码: " + interruptedDetail);
    check(!interruptedDetail.includes("skipped_after_send_unknown"),
      "跳过原因不得暴露原码: " + interruptedDetail);

    // 10) 发送成功但写投递记录失败：执行级译成中文，目标仍为成功
    await frame.evaluate(() => {
      const button = document.querySelector('button[aria-label="查看执行 copy-record-failed"]');
      if (button) button.click();
    });
    await frame.waitForFunction(
      () => document.getElementById("delivery-result")?.innerText.includes("copy-record-failed"),
      null, { timeout: WAIT },
    );
    const recordFailedDetail = await frame.evaluate(() =>
      document.getElementById("delivery-result").innerText);
    check(recordFailedDetail.includes("错误码：发送器返回成功，但写入投递记录失败，请核对实际收件"),
      "记录写入失败要按真实语义译成中文: " + recordFailedDetail);
    check(!recordFailedDetail.includes("record_after_send_failed"),
      "记录写入失败不得暴露原码: " + recordFailedDetail);
    check(recordFailedDetail.includes("发送中 · 第 1 张 发送中（每日新闻）"),
      "目标与图片应显示真实的未写回状态: " + recordFailedDetail);
    check(!recordFailedDetail.includes("sending"),
      "发送中不得显示英文原值: " + recordFailedDetail);

    console.log("DELIVERY STATUS COPY PASS", { assertions });
  } finally {
    await page.unrouteAll({ behavior: "ignoreErrors" });
    await page.close();
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
