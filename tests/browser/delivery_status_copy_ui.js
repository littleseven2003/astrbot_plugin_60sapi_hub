/*
 * 投递状态文案回归：栏目图数量、未送达原因分解、列表与详情一致性。
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

function detail(fields) {
  return {
    id: fields.id, task_id: "task-1", task_name: fields.taskName || "文案任务",
    profile_id: "profile-1", profile_revision: 1, site_id: "site-1", site_revision: 1,
    output_format: fields.outputFormat || "image",
    content_status: fields.contentStatus || "partial",
    content_hash: "hash", status: fields.status || "partial", error_code: null,
    created_at: "2026-11-06T00:00:00Z", updated_at: "2026-11-06T00:00:00Z",
    failed_sections: fields.failedSections || [],
    image_partial: fields.imagePartial !== false,
    delivered_image_count: fields.delivered || 0,
    skipped_image_count: fields.skipped || 0,
    section_image_count: fields.sectionImages,
    skipped_section_image_failed_count: fields.skippedSectionFailed || 0,
    skipped_config_changed_count: fields.skippedConfig || 0,
    skipped_other_count: fields.skippedOther || 0,
    targets: fields.targets || [],
  };
}

function target(position, images) {
  return {
    position,
    umo: "fake_qq_01:GroupMessage:g" + position,
    status: "partial",
    error_code: "section_image_failed",
    delivered_image_count: images.filter((image) => image.status === "success").length,
    skipped_image_count: images.filter((image) => image.status !== "success").length,
    section_image_count: images.length,
    images,
  };
}

const CASES = {
  "all-success": {
    id: "copy-all-success", taskName: "全部成功",
    status: "success", contentStatus: "complete", imagePartial: false,
    delivered: 6, skipped: 0, sectionImages: 3,
    failedSections: [],
    targets: [target(0, [
      { image_index: 1, status: "success", label: "每日新闻" },
      { image_index: 2, status: "success", label: "知乎热榜" },
      { image_index: 3, status: "success", label: "Bilibili 热搜" },
    ]), target(1, [
      { image_index: 1, status: "success", label: "每日新闻" },
      { image_index: 2, status: "success", label: "知乎热榜" },
      { image_index: 3, status: "success", label: "Bilibili 热搜" },
    ])],
  },
  "section-failed": {
    id: "copy-section-failed", taskName: "栏目生成失败",
    delivered: 4, skipped: 2, sectionImages: 3,
    failedSections: [
      { label: "知乎热榜", reason: "image_failed", image_index: 2 },
    ],
    skippedSectionFailed: 2, skippedConfig: 0, skippedOther: 0,
    targets: [target(0, [
      { image_index: 1, status: "success", label: "每日新闻" },
      { image_index: 2, status: "skipped", error_code: "section_image_failed", label: "知乎热榜" },
      { image_index: 3, status: "success", label: "Bilibili 热搜" },
    ]), target(1, [
      { image_index: 1, status: "success", label: "每日新闻" },
      { image_index: 2, status: "skipped", error_code: "section_image_failed", label: "知乎热榜" },
      { image_index: 3, status: "success", label: "Bilibili 热搜" },
    ])],
  },
  "config-changed": {
    id: "copy-config-changed", taskName: "配置变化",
    delivered: 3, skipped: 3, sectionImages: 3,
    failedSections: [],
    skippedSectionFailed: 0, skippedConfig: 2, skippedOther: 1,
    targets: [target(0, [
      { image_index: 1, status: "success", label: "每日新闻" },
      { image_index: 2, status: "success", label: "知乎热榜" },
      { image_index: 3, status: "skipped", error_code: "config_changed", label: "Bilibili 热搜" },
    ]), target(1, [
      { image_index: 1, status: "success", label: "每日新闻" },
      { image_index: 2, status: "skipped", error_code: "config_changed" },
      { image_index: 3, status: "skipped", error_code: "config_changed" },
    ])],
  },
  mixed: {
    id: "copy-mixed", taskName: "混合原因",
    delivered: 2, skipped: 4, sectionImages: 3,
    failedSections: [
      { label: "知乎热榜", reason: "image_failed", image_index: 2 },
    ],
    skippedSectionFailed: 2, skippedConfig: 1, skippedOther: 1,
    targets: [target(0, [
      { image_index: 1, status: "success", label: "每日新闻" },
      { image_index: 2, status: "skipped", error_code: "section_image_failed", label: "知乎热榜" },
      { image_index: 3, status: "success", label: "Bilibili 热搜" },
    ]), target(1, [
      { image_index: 1, status: "skipped", error_code: "config_changed", label: "每日新闻" },
      { image_index: 2, status: "skipped", error_code: "section_image_failed", label: "知乎热榜" },
      { image_index: 3, status: "skipped", error_code: "interrupted", label: "Bilibili 热搜" },
    ])],
  },
  "text-output": {
    id: "copy-text-output", taskName: "文字任务",
    outputFormat: "text", status: "success", contentStatus: "complete",
    imagePartial: false, delivered: 2, skipped: 0, sectionImages: undefined,
    failedSections: [],
    targets: [{
      position: 0, umo: "fake_qq_01:GroupMessage:g0", status: "success",
      error_code: null, delivered_image_count: 1, skipped_image_count: 0,
      images: [{ image_index: 1, status: "success" }],
    }, {
      position: 1, umo: "fake_qq_01:GroupMessage:g1", status: "success",
      error_code: null, delivered_image_count: 1, skipped_image_count: 0,
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
      const summaries = Object.values(CASES).map((item) => ({
        id: item.id, task_id: "task-1", task_name: item.taskName,
        output_format: item.outputFormat || "image",
        status: item.status || "partial",
        content_status: item.contentStatus || "partial",
        created_at: "2026-11-06T00:00:00Z", updated_at: "2026-11-06T00:00:00Z",
        target_count: (item.targets || []).length,
        image_count: (item.delivered || 0) + (item.skipped || 0),
        delivered_image_count: item.delivered || 0,
        skipped_image_count: item.skipped || 0,
        section_image_count: item.sectionImages,
        failed_section_count: (item.failedSections || []).length,
        image_partial: item.imagePartial !== false,
        skipped_section_image_failed_count: item.skippedSectionFailed || 0,
        skipped_config_changed_count: item.skippedConfig || 0,
        skipped_other_count: item.skippedOther || 0,
      }));
      await route.fulfill({
        status: 200, contentType: "application/json",
        body: JSON.stringify({ status: "ok", data: { count: summaries.length, executions: summaries } }),
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

    const listText = await frame.evaluate(() => document.getElementById("delivery-list").innerText);
    const cardText = async (id) => frame.evaluate((executionId) => {
      const card = [...document.querySelectorAll("#delivery-list li")]
        .find((node) => node.innerText.includes(executionId));
      return card ? card.innerText : "";
    }, id);

    // 1) 多目标多栏目全部成功：栏目图数按去重栏目算，不出现「未送达」
    const success = await cardText("copy-all-success");
    check(success.includes("3 张栏目图"), "全部成功应按栏目数展示: " + success);
    check(!success.includes("未送达"), "全部成功不应出现未送达: " + success);
    check(!success.includes("记录"), "全部成功不应出现记录数: " + success);

    // 2) 栏目生成失败：栏目数 + 记录数 + 原因分别给数
    const sectionFailed = await cardText("copy-section-failed");
    check(sectionFailed.includes("1 个栏目未生成图片"), "栏目失败要按栏目计数: " + sectionFailed);
    check(sectionFailed.includes("2 条发送记录未送达"), "记录数要按记录计数: " + sectionFailed);
    check(sectionFailed.includes("2 因栏目图片生成失败"), "记录原因要分解: " + sectionFailed);
    check(!sectionFailed.includes("页"), "不应出现分页字样");

    // 3) 配置变化：原因里必须出现配置变化，且不能说成栏目未生成
    const configChanged = await cardText("copy-config-changed");
    check(configChanged.includes("3 条发送记录未送达"), "配置变化按记录计数: " + configChanged);
    check(configChanged.includes("2 因配置变化"), "配置变化原因要单列: " + configChanged);
    check(configChanged.includes("1 因发送前终止"), "其它原因要单列: " + configChanged);
    check(!configChanged.includes("栏目未生成图片"), "无栏目失败时不得声称栏目未生成: " + configChanged);

    // 4) 混合：两种原因同时出现且分别有数
    const mixed = await cardText("copy-mixed");
    check(mixed.includes("1 个栏目未生成图片"), "混合场景栏目数: " + mixed);
    check(mixed.includes("4 条发送记录未送达"), "混合场景记录数: " + mixed);
    check(mixed.includes("2 因栏目图片生成失败"), "混合场景原因一: " + mixed);
    check(mixed.includes("1 因配置变化"), "混合场景原因二: " + mixed);
    check(mixed.includes("1 因发送前终止"), "混合场景原因三: " + mixed);

    // 5) 文字/Markdown 输出不得出现「张栏目图」
    const textOutput = await cardText("copy-text-output");
    check(!textOutput.includes("张栏目图"), "文字输出不得出现张栏目图: " + textOutput);
    check(textOutput.includes("条消息"), "文字输出按条消息计数: " + textOutput);

    // 详情：列表与详情必须使用同一句摘要（此时列表已渲染完成）
    await frame.evaluate(() => {
      const button = document.querySelector('button[aria-label="查看执行 copy-mixed"]');
      if (button) button.click();
    });
    await frame.waitForFunction(() =>
      document.getElementById("delivery-result")?.hidden === false,
    null, { timeout: WAIT });
    const detailText = await frame.evaluate(() =>
      document.getElementById("delivery-result").innerText);
    check(detailText.includes("部分成功"), "详情标题应为部分成功: " + detailText);
    check(detailText.includes("1 个栏目未生成图片"), "详情栏目数与列表一致: " + detailText);
    check(detailText.includes("4 条发送记录未送达"), "详情记录数与列表一致: " + detailText);
    check(detailText.includes("2 因栏目图片生成失败"), "详情原因与列表一致: " + detailText);
    check(detailText.includes("第 2 张 未送达（知乎热榜）"), "槽位文案带 label: " + detailText);
    check(detailText.includes("栏目图片生成失败"), "错误码要翻译成可读文案: " + detailText);
    check(!detailText.includes("重发") && !detailText.includes("补发"),
      "不得出现重发/补发入口: " + detailText);

    check(listText.includes("copy-all-success"), "列表必须加载夹具执行记录");
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
