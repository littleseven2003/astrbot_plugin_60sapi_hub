/*
 * 已保存站点列表的真实页面逻辑回归：勾选、单站与批量五类测试、批量删除。
 *
 * 用真实 Chromium 加载 views/sites/index.html + app.js，只把宿主 bridge 换成
 * 记录型桩，验证：行内五类状态、全选/全不选/反选、单站与批量复用同一接口、
 * 停用站点可显式测试且不改配置、部分失败逐站标注、失败文案安全、在途禁用、
 * 刷新后旧结果作废、超过 20 站分块、批量删除部分成功保留失败项、窄屏不溢出。
 *
 * 这是「桩 bridge 的页面逻辑验证」：不连接真实 AstrBot 后台、不访问任何上游。
 * 真实宿主验收需在具备 WebUI 的环境单独完成。
 */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const http = require("node:http");
const path = require("node:path");
const { chromium } = require("playwright-core");

function resolveChrome() {
  const configured = process.env.CHROME_PATH;
  if (configured) {
    return configured;
  }
  try {
    return chromium.executablePath();
  } catch {
    return "";
  }
}

const CHROME = resolveChrome();
if (!CHROME || !fs.existsSync(CHROME)) {
  console.error(
    "找不到可用的 Chromium：请设置 CHROME_PATH，或安装 playwright 的浏览器。",
  );
  process.exit(2);
}
const VIEWS_DIR = path.resolve(__dirname, "..", "..", "views", "sites");
const MIME = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
};

const FIVE_TYPES = [
  "daily_news", "bilibili_hot", "zhihu_hot", "douyin_hot", "toutiao_hot",
];
const FIVE_LABELS = [
  "每日新闻", "Bilibili 热榜", "知乎热榜", "抖音热搜", "今日头条",
];

const SITES = [
  {
    id: "site-ok", name: "公开站点甲", base_url: "https://a.example.test/60s/api",
    enabled: true, timeout_seconds: 10, source_kind: "public",
    has_bearer_token: false, revision: 1, updated_at: "",
  },
  {
    id: "site-partial", name: "自建站点乙",
    base_url: "https://b.example.test:8443/60s/api",
    enabled: true, timeout_seconds: 8, source_kind: "self_built",
    has_bearer_token: true, revision: 2, updated_at: "",
  },
  {
    id: "site-disabled", name: "已停用站点丙", base_url: "https://c.example.test/n",
    enabled: false, timeout_seconds: 10, source_kind: "public",
    has_bearer_token: false, revision: 5, updated_at: "",
  },
];

const server = http.createServer((request, response) => {
  const name = (request.url || "/").split("?")[0] === "/"
    ? "index.html"
    : (request.url || "/").split("?")[0].replace(/^\//, "");
  const file = path.join(VIEWS_DIR, name);
  if (!file.startsWith(VIEWS_DIR) || !fs.existsSync(file)) {
    response.writeHead(404);
    response.end("not found");
    return;
  }
  response.writeHead(200, { "content-type": MIME[path.extname(file)] || "text/plain" });
  response.end(fs.readFileSync(file));
});

function check(passed, label) {
  assert.ok(passed, label);
  console.log("  ✓ " + label);
}

async function openPage(browser, port, options) {
  const settings = options || {};
  const sites = settings.sites || SITES;
  const page = await browser.newPage({ viewport: settings.viewport || { width: 1440, height: 900 } });
  await page.addInitScript((fixtures) => {
    window.__siteFixtures = fixtures.map((site) => ({ ...site }));
    window.__calls = [];
    window.__batchMode = "ok";
    window.__batchGate = null;
    window.__batchReject = false;
    window.__deleteFails = {};
    window.__deleteDrops = {};
    window.__sitesReject = false;
    window.__staleSites = {};
    window.__driftSites = {};
    window.AstrBotPluginView = {
      ready: async () => ({ pageTitle: "60sAPI Hub" }),
      apiGet: async (endpoint) => {
        if (endpoint === "sites") {
          if (window.__sitesReject) {
            throw new Error("站点存储不可用。");
          }
          return { sites: window.__siteFixtures.map((site) => ({ ...site })) };
        }
        if (endpoint === "profiles") {
          return { profiles: [] };
        }
        if (endpoint === "tasks") {
          return { tasks: [] };
        }
        if (endpoint === "schedule-runs") {
          return { claims: [], skips: [] };
        }
        if (endpoint === "executions") {
          return { executions: [], total: 0 };
        }
        return {};
      },
      apiPost: async (endpoint, payload) => {
        window.__calls.push({
          seq: window.__calls.length + 1, endpoint, payload,
        });
        if (endpoint === "sites/test-batch") {
          if (window.__batchGate) {
            window.__batchStarted = true;
            await window.__batchGate;
          }
          if (window.__batchReject) {
            const error = new Error("插件当前不可用。");
            error.status = 503;
            throw error;
          }
          return { results: window.__makeResults(payload), failed: 0 };
        }
        const match = /^sites\/(.+)\/delete$/.exec(endpoint);
        if (match) {
          const siteId = decodeURIComponent(match[1]);
          const failure = window.__deleteFails[siteId];
          if (failure) {
            const error = new Error(failure);
            error.status = 409;
            throw error;
          }
          // 响应丢失：页面拿不到结论，只能靠重读列表判断结果。
          const drop = window.__deleteDrops[siteId];
          if (drop) {
            if (drop === "removed") {
              window.__siteFixtures = window.__siteFixtures.filter(
                (site) => site.id !== siteId,
              );
            }
            throw new Error("网络连接中断。");
          }
          window.__siteFixtures = window.__siteFixtures.filter(
            (site) => site.id !== siteId,
          );
          return { deleted_id: siteId };
        }
        return {};
      },
    };
  }, sites);
  await page.goto("http://127.0.0.1:" + port + "/index.html", {
    waitUntil: "domcontentloaded",
  });
  await page.waitForFunction(
    (count) => document.getElementById("sites-list")?.getAttribute("aria-busy") === "false"
      && document.querySelectorAll("#sites-list > li").length === count,
    sites.length, { timeout: 30000 },
  );
  await page.evaluate(() => {
    window.__fixtureRevision = (siteId) => {
      const site = window.__siteFixtures.find((item) => item.id === siteId);
      return site ? site.revision : 0;
    };
    window.__makeResults = (payload) => {
      const results = [];
      for (const siteId of payload.site_ids) {
        for (const contentType of payload.content_types) {
          let result = "success";
          let message = "连接成功";
          if (window.__batchMode === "mixed" && siteId === "site-partial"
              && contentType === "zhihu_hot") {
            result = "error";
            message = "上游返回了失败状态。";
          }
          if (window.__batchMode === "mixed" && siteId === "site-disabled"
              && contentType === "douyin_hot") {
            result = "empty";
            message = "接口返回空列表。";
          }
          if (window.__batchMode === "unsafe" && siteId === "site-ok"
              && contentType === "daily_news") {
            result = "error";
            message = "上游把 token=abc 回显了";
          }
          const expected = payload.expected_revisions || {};
          let siteRevision = Object.prototype.hasOwnProperty.call(expected, siteId)
            ? expected[siteId]
            : window.__fixtureRevision(siteId);
          if (window.__staleSites[siteId]) {
            result = "stale";
            message = "站点配置已变化，请刷新后重新测试。";
          }
          if (window.__driftSites[siteId]) {
            siteRevision = siteRevision + 1;
          }
          results.push({
            site_id: siteId, content_type: contentType, result, message,
            elapsed_ms: 12, site_revision: siteRevision,
          });
        }
      }
      return results;
    };
  });
  return page;
}

async function calls(page, endpoint) {
  return page.evaluate((name) => window.__calls.filter(
    (call) => call.endpoint === name), endpoint);
}

async function chips(page, siteId) {
  return page.evaluate((id) => {
    const row = document.querySelector('#sites-list > li[data-site-id="' + id + '"]');
    if (!row) {
      return null;
    }
    return Array.from(row.querySelectorAll(".site-test-chip")).map((chip) => ({
      type: chip.dataset.type,
      state: chip.dataset.state,
      text: chip.querySelector(".site-test-text").textContent,
    }));
  }, siteId);
}

const allState = (list, name) => list.every((chip) => chip.state === name);

(async () => {
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const port = server.address().port;
  const browser = await chromium.launch({
    headless: true, executablePath: CHROME, args: ["--no-sandbox"],
  });
  try {
    const page = await openPage(browser, port);

    // 旧弹窗与旧批量测试面板都已移除，列表自带全部能力。
    check(await page.locator("#site-test-dialog").count() === 0, "站点测试弹窗已移除");
    check(await page.locator("#batch-test-run").count() === 0, "批量测试面板已移除");
    check(await page.locator("#delete-results").count() === 1, "删除弹窗带有逐站结果区");

    // 工具条默认：3 个站点、未选任何站点、批量按钮禁用。
    check(await page.isVisible("#site-selection"), "选择工具条可见");
    check((await page.textContent("#site-selection-count")).trim() === "已选 0 个",
      "初始已选 0 个");
    check(await page.isDisabled("#site-test-selected"), "未选站点时批量测试禁用");
    check(await page.isDisabled("#site-delete-selected"), "未选站点时批量删除禁用");

    // 每行内容：勾选框、来源药丸、名称、完整地址、五类状态、三个图标按钮。
    check(await page.locator("#sites-list > li").count() === 3, "列表有 3 行");
    const firstRow = await page.evaluate(() => {
      const row = document.querySelector('#sites-list > li[data-site-id="site-ok"]');
      return {
        badge: row.querySelector(".badge-source")?.textContent.trim(),
        kind: row.querySelector(".badge-source")?.dataset.kind,
        name: row.querySelector(".site-name")?.textContent.trim(),
        url: row.querySelector(".site-url")?.textContent.trim(),
        urlTitle: row.querySelector(".site-url")?.title,
        disabledBadge: Boolean(row.querySelector(".badge-disabled")),
        selectLabel: row.querySelector(".site-select")?.getAttribute("aria-label"),
        actions: Array.from(row.querySelectorAll(".site-actions button")).map((node) => ({
          label: node.getAttribute("aria-label"),
          text: node.textContent.trim(),
        })),
      };
    });
    check(firstRow.badge === "公开" && firstRow.kind === "public", "公开站点显示公开药丸");
    check(firstRow.name === "公开站点甲", "行内显示站点名称");
    check(firstRow.url === "https://a.example.test/60s/api", "行内显示站点地址");
    check(firstRow.urlTitle === "https://a.example.test/60s/api",
      "地址 title 保留完整值");
    check(firstRow.disabledBadge === false, "启用站点不显示已停用");
    check(firstRow.selectLabel === "选择站点 公开站点甲", "勾选框有可读名称");
    check(firstRow.actions.length === 3, "每行三个操作按钮");
    check(firstRow.actions[0].text === "" && firstRow.actions[0].label === "测试站点 公开站点甲 的五类接口",
      "测试按钮是图标且有可读名称：" + firstRow.actions[0].label);
    check(firstRow.actions[1].label === "编辑站点 公开站点甲", "编辑按钮有可读名称");
    check(firstRow.actions[2].label === "删除站点 公开站点甲", "删除按钮有可读名称");
    check((await page.getAttribute('#sites-list > li[data-site-id="site-partial"] .badge-source', "data-kind")) === "self_built",
      "自建站点显示自建药丸");
    check((await page.textContent('#sites-list > li[data-site-id="site-partial"] .badge-source')).trim() === "自建",
      "自建药丸文案为自建");
    check((await page.textContent('#sites-list > li[data-site-id="site-disabled"] .badge-disabled')).trim() === "已停用",
      "停用站点标注已停用");

    // 五类状态初始都是未测试，图标与文字一起表达。
    const initial = await chips(page, "site-ok");
    check(initial.length === 5, "每行五个类型状态");
    check(JSON.stringify(initial.map((chip) => chip.type)) === JSON.stringify(FIVE_TYPES),
      "五个类型按固定顺序：" + initial.map((chip) => chip.type).join(","));
    check(allState(initial, "idle"), "初始状态都是未测试");
    check(initial.every((chip, index) => chip.text === FIVE_LABELS[index] + " 未测试"),
      "每格同时有类型名与状态文字：" + initial[0].text);
    const iconText = await page.textContent('#sites-list > li[data-site-id="site-ok"] .site-test-chip .site-test-icon');
    check(iconText === "○", "未测试状态有图标：" + iconText);

    // 全选 / 全不选 / 反选只影响勾选，不改启用状态。
    await page.click("#site-select-all");
    check((await page.textContent("#site-selection-count")).trim() === "已选 3 个",
      "全选后已选 3 个");
    check(await page.isEnabled("#site-test-selected"), "全选后批量测试可用");
    check(await page.isEnabled("#site-delete-selected"), "全选后批量删除可用");
    check(await page.$$eval("#sites-list .site-select", (nodes) => nodes.every((node) => node.checked)),
      "全选后勾选框都选中");
    check(await page.$$eval("#sites-list > li", (nodes) => nodes.every((node) => node.dataset.selected === "true")),
      "选中行有 data-selected 标记");
    check(await page.$$eval("#sites-list > li", (nodes) => nodes.map((node) => node.dataset.enabled).join(","))
      === "true,true,false", "勾选不改启用状态");
    await page.click("#site-select-none");
    check((await page.textContent("#site-selection-count")).trim() === "已选 0 个",
      "全不选后已选 0 个");
    check(await page.isDisabled("#site-test-selected"), "全不选后批量测试禁用");
    await page.click("#site-select-invert");
    check((await page.textContent("#site-selection-count")).trim() === "已选 3 个",
      "空选择时反选等于全选");
    await page.click("#site-select-invert");
    check((await page.textContent("#site-selection-count")).trim() === "已选 0 个",
      "全选后反选等于全不选");

    // 单站测试：点该行的图标按钮，只测这个站点、固定五类。
    await page.click('#sites-list > li[data-site-id="site-ok"] .site-icon-button[aria-label="测试站点 公开站点甲 的五类接口"]');
    await page.waitForFunction(() => document
      .querySelector('#sites-list > li[data-site-id="site-ok"] .site-test-chip[data-type="toutiao_hot"]')
      ?.dataset.state === "success", null, { timeout: 10000 });
    let testCalls = await calls(page, "sites/test-batch");
    check(testCalls.length === 1, "单站测试只发一次请求");
    check(JSON.stringify(testCalls[0].payload.site_ids) === JSON.stringify(["site-ok"]),
      "单站测试只提交该站点：" + JSON.stringify(testCalls[0].payload.site_ids));
    check(JSON.stringify(testCalls[0].payload.content_types) === JSON.stringify(FIVE_TYPES),
      "单站测试固定提交五类");
    const single = await chips(page, "site-ok");
    check(allState(single, "success"), "单站测试后五类都成功");
    check(single[0].text === "每日新闻 成功", "成功状态文字可读：" + single[0].text);
    check((await page.textContent("#site-test-summary")).includes("测试完成：1 个站点 × 5 类；成功 5 项"),
      "单站测试汇总：" + (await page.textContent("#site-test-summary")).trim());
    check((await chips(page, "site-partial")).every((chip) => chip.state === "idle"),
      "单站测试不影响其他站点");

    // 未选站点不发请求；停用站点可显式勾选并测试，且不启用站点。
    await page.click('#sites-list > li[data-site-id="site-disabled"] .site-select');
    check((await page.textContent("#site-selection-count")).trim() === "已选 1 个",
      "可只勾选停用站点");
    await page.evaluate(() => { window.__batchMode = "mixed"; });
    await page.click("#site-test-selected");
    await page.waitForFunction(() => document
      .querySelector('#sites-list > li[data-site-id="site-disabled"] .site-test-chip[data-type="toutiao_hot"]')
      ?.dataset.state === "success", null, { timeout: 10000 });
    testCalls = await calls(page, "sites/test-batch");
    check(testCalls.length === 2, "批量测试仍复用同一接口");
    check(JSON.stringify(testCalls[1].payload.site_ids) === JSON.stringify(["site-disabled"]),
      "只提交勾选的停用站点");
    const disabledChips = await chips(page, "site-disabled");
    check(disabledChips.filter((chip) => chip.state === "empty").length === 1,
      "合法空内容单独标注");
    check(disabledChips.find((chip) => chip.state === "empty").text === "抖音热搜 合法空内容",
      "空内容文字：" + disabledChips.find((chip) => chip.state === "empty").text);
    check((await page.textContent('#sites-list > li[data-site-id="site-disabled"] .site-test-note'))
      .includes("接口返回空列表。"), "空内容附带后端说明");
    check(await page.getAttribute('#sites-list > li[data-site-id="site-disabled"]', "data-enabled") === "false",
      "测试停用站点不会启用它");
    const writes = await page.evaluate(() => window.__calls.filter(
      (call) => call.endpoint !== "sites/test-batch"));
    check(writes.length === 0, "测试不会改动站点配置：" + JSON.stringify(writes.map((call) => call.endpoint)));

    // 多站批量：一次提交全部勾选站点，部分失败逐站标注、不阻断其他项。
    await page.click("#site-select-all");
    await page.click("#site-test-selected");
    await page.waitForFunction(() => document
      .querySelector('#sites-list > li[data-site-id="site-partial"] .site-test-chip[data-type="toutiao_hot"]')
      ?.dataset.state === "success", null, { timeout: 10000 });
    testCalls = await calls(page, "sites/test-batch");
    check(JSON.stringify(testCalls[2].payload.site_ids) === JSON.stringify(["site-ok", "site-partial", "site-disabled"]),
      "批量测试一次提交全部勾选站点（含停用）：" + JSON.stringify(testCalls[2].payload.site_ids));
    const partialChips = await chips(page, "site-partial");
    check(partialChips.find((chip) => chip.type === "zhihu_hot").state === "error",
      "失败类型标注为失败");
    check(partialChips.filter((chip) => chip.state === "success").length === 4,
      "同一站点的其他类型仍显示成功");
    check((await page.textContent('#sites-list > li[data-site-id="site-partial"] .site-test-note'))
      .includes("上游返回了失败状态。"), "失败项显示后端安全说明");
    check((await page.textContent("#site-test-summary")).includes("成功 13 项，合法空内容 1 项，失败 1 项"),
      "批量汇总逐项计数：" + (await page.textContent("#site-test-summary")).trim());

    // 失败说明可展开：不依赖悬停，手机点按与键盘都能读全。
    const detailBox = await page.evaluate(() => {
      const node = document.querySelector(
        '#sites-list > li[data-site-id="site-partial"] .site-test-details');
      if (!node) {
        return null;
      }
      return {
        tag: node.tagName,
        open: node.open,
        summary: node.querySelector("summary")?.textContent || "",
        items: Array.from(node.querySelectorAll(".site-test-detail-list li"))
          .map((item) => item.textContent),
      };
    });
    check(detailBox !== null && detailBox.tag === "DETAILS" && detailBox.open === false,
      "失败说明默认收起且可展开");
    check(detailBox.summary === "查看说明（1 项）",
      "展开入口标明说明条数：" + detailBox.summary);
    check(detailBox.items.length === 1
      && detailBox.items[0].includes("知乎热榜：上游返回了失败状态。"),
      "展开内容逐项写明接口与原因：" + JSON.stringify(detailBox.items));
    const partialSummary = '#sites-list > li[data-site-id="site-partial"] .site-test-details > summary';
    await page.click(partialSummary);
    check(await page.locator('#sites-list > li[data-site-id="site-partial"] .site-test-details')
      .evaluate((node) => node.open), "点按可展开说明");
    await page.focus(partialSummary);
    await page.keyboard.press("Enter");
    check(!(await page.locator('#sites-list > li[data-site-id="site-partial"] .site-test-details')
      .evaluate((node) => node.open)), "键盘回车可收起说明");

    // 未知失败文案不回显上游正文或凭据。
    await page.evaluate(() => { window.__batchMode = "unsafe"; });
    await page.click("#site-select-none");
    await page.click('#sites-list > li[data-site-id="site-ok"] .site-select');
    await page.click("#site-test-selected");
    await page.waitForFunction(() => document
      .querySelector('#sites-list > li[data-site-id="site-ok"] .site-test-chip[data-type="daily_news"]')
      ?.dataset.state === "error", null, { timeout: 10000 });
    const unsafeNote = await page.textContent('#sites-list > li[data-site-id="site-ok"] .site-test-note');
    check(unsafeNote.includes("测试失败，无法完成连接验证。"), "未知失败回落安全文案：" + unsafeNote);
    check(!unsafeNote.includes("token"), "不回显上游正文或凭据");

    // 结果对应的站点版本与列表不同：标注需重新测试，不当作成功或失败。
    await page.evaluate(() => {
      window.__batchMode = "ok";
      window.__driftSites = { "site-ok": true };
    });
    await page.click("#site-select-none");
    await page.click('#sites-list > li[data-site-id="site-ok"] .site-select');
    await page.click("#site-test-selected");
    await page.waitForFunction(() => document
      .querySelector('#sites-list > li[data-site-id="site-ok"] .site-test-chip[data-type="daily_news"]')
      ?.dataset.state === "stale", null, { timeout: 10000 });
    const drifted = await chips(page, "site-ok");
    check(allState(drifted, "stale"), "版本不符的五类都标注需重新测试");
    check(drifted[0].text === "每日新闻 需重新测试", "版本不符状态文字：" + drifted[0].text);
    const driftCalls = await calls(page, "sites/test-batch");
    const driftPayload = driftCalls[driftCalls.length - 1].payload;
    check(JSON.stringify(driftPayload.expected_revisions) === JSON.stringify({ "site-ok": 1 }),
      "测试请求带上发起时看到的版本：" + JSON.stringify(driftPayload.expected_revisions));
    check((await page.textContent("#site-test-summary")).includes("因站点配置已变化未采用"),
      "汇总提示版本不符需重新测试：" + (await page.textContent("#site-test-summary")).trim());
    check((await page.textContent('#sites-list > li[data-site-id="site-ok"] .site-test-note'))
      .includes("站点配置已变化，请刷新后重新测试。"), "版本不符给出可操作说明");

    // 后端直接判为 stale（配置在测试中被改动）时行为一致。
    await page.evaluate(() => {
      window.__driftSites = {};
      window.__staleSites = { "site-ok": true };
    });
    await page.click("#site-test-selected");
    await page.waitForFunction(() => document
      .querySelector('#sites-list > li[data-site-id="site-ok"] .site-test-chip[data-type="toutiao_hot"]')
      ?.dataset.state === "stale", null, { timeout: 10000 });
    check(allState(await chips(page, "site-ok"), "stale"), "后端 stale 结果同样标注");
    await page.evaluate(() => { window.__staleSites = {}; });

    // 在途状态：按钮禁用并显示测试中，五类状态为测试中。
    await page.evaluate(() => {
      window.__batchMode = "ok";
      window.__batchGate = new Promise((resolve) => { window.__release = resolve; });
    });
    await page.click("#site-select-all");
    await page.click("#site-test-selected");
    await page.waitForFunction(() => Boolean(window.__batchStarted), null, { timeout: 5000 });
    check(await page.isDisabled("#site-test-selected"), "测试在途时批量测试禁用");
    check((await page.textContent("#site-test-selected")).trim() === "测试中…",
      "测试在途时按钮文字为测试中");
    const running = await chips(page, "site-ok");
    check(allState(running, "running"), "在途时五类显示测试中");
    check(running[0].text === "每日新闻 测试中", "测试中文字：" + running[0].text);
    await page.evaluate(() => { window.__release(); window.__batchGate = null; });
    await page.waitForFunction(() => !document.getElementById("site-test-selected").disabled,
      null, { timeout: 10000 });
    check(allState(await chips(page, "site-ok"), "success"), "在途结束后结果落回行内");

    // 测试期间站点被修改并刷新：旧配置的结果不得贴到新配置上。
    await page.evaluate(() => {
      window.__batchGate = new Promise((resolve) => { window.__release = resolve; });
    });
    await page.click("#site-select-none");
    await page.click('#sites-list > li[data-site-id="site-ok"] .site-select');
    await page.click("#site-test-selected");
    await page.waitForFunction(() => Boolean(window.__batchStarted), null, { timeout: 5000 });
    await page.evaluate(() => {
      window.__batchStarted = false;
      window.__siteFixtures = window.__siteFixtures.map((site) => (
        site.id === "site-ok" ? { ...site, revision: 9 } : site));
    });
    await page.click("#refresh-button");
    await page.waitForFunction(() => document
      .querySelector('#sites-list > li[data-site-id="site-ok"]')
      ?.dataset.siteId === "site-ok" && document
      .querySelector('#sites-list > li[data-site-id="site-ok"] .site-test-chip[data-type="daily_news"]')
      ?.dataset.state === "idle", null, { timeout: 10000 });
    check(true, "刷新后行内状态回到未测试");
    await page.evaluate(() => { window.__release(); window.__batchGate = null; });
    await page.waitForFunction(() => !document.getElementById("site-test-selected").disabled,
      null, { timeout: 10000 });
    check(allState(await chips(page, "site-ok"), "idle"),
      "配置变化后在途结果被丢弃，不会显示到新配置上");
    await page.evaluate(() => {
      window.__siteFixtures = window.__siteFixtures.map((site) => (
        site.id === "site-ok" ? { ...site, revision: 1 } : site));
    });
    await page.click("#refresh-button");
    await page.waitForFunction(() => document
      .querySelector('#sites-list > li[data-site-id="site-ok"]') !== null,
    null, { timeout: 10000 });

    // 后端整体失败：逐站标注失败、不阻断，按钮恢复。
    await page.evaluate(() => { window.__batchReject = true; });
    check((await page.textContent("#site-selection-count")).trim() === "已选 1 个",
      "刷新列表后仍保持勾选");
    await page.click("#site-test-selected");
    await page.waitForFunction(() => document
      .querySelector('#sites-list > li[data-site-id="site-ok"] .site-test-chip[data-type="daily_news"]')
      ?.dataset.state === "error", null, { timeout: 10000 });
    const failedNote = await page.textContent('#sites-list > li[data-site-id="site-ok"] .site-test-note');
    check(failedNote.includes("插件当前不可用，请关闭后重新打开页面。"),
      "后端不可用给出可操作提示：" + failedNote);
    check(await page.isEnabled("#site-test-selected"), "失败后批量测试按钮恢复可用");
    await page.evaluate(() => { window.__batchReject = false; });

    // 批量删除：逐站结果、部分成功时保留失败项、不把部分成功当全部成功。
    await page.click('#sites-list > li[data-site-id="site-partial"] .site-select');
    await page.evaluate(() => {
      window.__deleteFails = { "site-partial": "站点已被其他操作修改，请刷新后重试。" };
    });
    await page.click("#site-delete-selected");
    await page.waitForSelector("#delete-dialog[open]");
    check((await page.textContent("#delete-title")).trim() === "批量删除站点",
      "批量删除标题：" + (await page.textContent("#delete-title")).trim());
    check((await page.textContent("#delete-message")).includes("确定删除选中的 2 个站点吗？"),
      "确认文案说明选中数量：" + (await page.textContent("#delete-message")).trim());
    check(await page.locator("#delete-selection li").count() === 2, "列出待删除站点");
    await page.click("#delete-confirm-button");
    await page.waitForFunction(() => document.querySelectorAll("#delete-results li.delete-result").length === 2,
      null, { timeout: 10000 });
    const deletePayloads = await page.evaluate(() => window.__calls
      .filter((call) => call.endpoint.endsWith("/delete"))
      .map((call) => ({ endpoint: call.endpoint, payload: call.payload })));
    check(deletePayloads.length === 2, "逐站请求删除");
    check(deletePayloads[0].payload.expected_revision === 1
      && deletePayloads[1].payload.expected_revision === 2, "删除带上各自 revision");
    check((await page.textContent("#delete-results li.delete-result-summary")).trim()
      === "共 2 个：已删除 1 个，未删除 1 个。", "汇总如实报告部分成功："
      + (await page.textContent("#delete-results li.delete-result-summary")).trim());
    const resultStates = await page.$$eval("#delete-results li.delete-result",
      (nodes) => nodes.map((node) => ({ state: node.dataset.state, text: node.textContent })));
    check(resultStates[0].state === "deleted" && resultStates[0].text.includes("已删除"),
      "成功项标记已删除");
    check(resultStates[1].state === "changed" && resultStates[1].text.includes("站点已被其他操作修改"),
      "冲突项标记未删除并给出原因：" + resultStates[1].text);
    check(await page.locator("#delete-dialog").evaluate((node) => node.open),
      "有失败时对话框保持打开");
    check((await page.textContent("#delete-message")).includes("未删除的 1 个站点仍保留在列表中"),
      "失败项说明仍保留：" + (await page.textContent("#delete-message")).trim());
    check(await page.locator("#delete-selection li").count() === 1
      && (await page.textContent("#delete-selection li")).includes("自建站点乙"),
      "失败站点重新列为待处理项");
    check(await page.locator("#sites-list > li").count() === 2, "已删除站点从列表移除");
    check((await page.textContent("#site-selection-count")).trim() === "已选 1 个",
      "失败站点保持勾选：" + (await page.textContent("#site-selection-count")).trim());
    check((await page.textContent("#status")).includes("1 个未删除"),
      "状态栏提示未删除数量：" + (await page.textContent("#status")).trim());
    await page.click("#delete-cancel-button");

    // 删除响应丢失：重读列表能确认站点仍在 → 如实标注未删除，可修正后重试。
    const keptPage = await openPage(browser, port, { sites: SITES.map((site) => ({ ...site })) });
    await keptPage.click("#site-select-all");
    await keptPage.evaluate(() => {
      window.__deleteDrops = { "site-partial": "kept" };
    });
    await keptPage.click("#site-delete-selected");
    await keptPage.waitForSelector("#delete-dialog[open]");
    await keptPage.click("#delete-confirm-button");
    await keptPage.waitForFunction(
      () => document.querySelectorAll("#delete-results li.delete-result").length === 3,
      null, { timeout: 10000 },
    );
    const keptStates = await keptPage.$$eval("#delete-results li.delete-result",
      (nodes) => nodes.map((node) => ({ state: node.dataset.state, text: node.textContent })));
    const keptItem = keptStates.find((item) => item.state === "kept");
    check(Boolean(keptItem), "响应丢失后重读列表给出结论："
      + JSON.stringify(keptStates.map((item) => item.state)));
    check(keptItem.text.includes("列表确认该站点仍在，未执行删除。"),
      "确认仍在时如实标注未删除：" + keptItem.text);
    check(await keptPage.locator("#delete-selection li").count() === 1,
      "确认未删除的站点保留为可重试项");
    const keptCalls = await keptPage.evaluate(() => window.__calls
      .filter((call) => call.endpoint.endsWith("/delete")).length);
    check(keptCalls === 3, "响应丢失的站点没有被重复提交：" + keptCalls);
    await keptPage.click("#delete-cancel-button");
    await keptPage.close();

    // 删除响应丢失但站点确实已删除：重读列表后按已删除计数，不留在待处理项。
    const gonePage = await openPage(browser, port, { sites: SITES.map((site) => ({ ...site })) });
    await gonePage.click("#site-select-all");
    await gonePage.evaluate(() => {
      window.__deleteDrops = { "site-ok": "removed" };
    });
    await gonePage.click("#site-delete-selected");
    await gonePage.waitForSelector("#delete-dialog[open]");
    await gonePage.click("#delete-confirm-button");
    await gonePage.waitForFunction(
      () => !document.getElementById("delete-dialog").open, null, { timeout: 10000 },
    );
    check((await gonePage.textContent("#status")).includes("已删除 3/3 个站点"),
      "确认已删除后按成功收场：" + (await gonePage.textContent("#status")).trim());
    await gonePage.waitForFunction(
      () => document.querySelectorAll("#delete-results li.delete-result").length === 0,
      null, { timeout: 5000 },
    );
    check(true, "全部确认成功后对话框关闭并清空逐站结果");
    await gonePage.close();

    // 列表刷新失败时保持“结果待确认”，移出重试目标并提示刷新。
    const blindPage = await openPage(browser, port, { sites: SITES.map((site) => ({ ...site })) });
    await blindPage.click('#sites-list > li[data-site-id="site-ok"] .site-select');
    await blindPage.evaluate(() => {
      window.__deleteDrops = { "site-ok": "kept" };
      // 响应丢失的同时列表也无法重读：无法确认时不能猜结果。
      window.__sitesReject = true;
    });
    await blindPage.click("#site-delete-selected");
    await blindPage.waitForSelector("#delete-dialog[open]");
    await blindPage.click("#delete-confirm-button");
    await blindPage.waitForFunction(
      () => document.getElementById("delete-message").textContent.includes("结果待确认"),
      null, { timeout: 10000 },
    );
    const blindMessage = await blindPage.textContent("#delete-message");
    check(blindMessage.includes("结果待确认"), "无法确认时明确提示：" + blindMessage.trim());
    check(await blindPage.locator("#delete-selection li").count() === 0,
      "结果待确认的站点被移出重试目标");
    await blindPage.close();

    // 超过后端单次上限（20）时按块顺序请求。
    const many = [];
    for (let index = 0; index < 22; index += 1) {
      many.push({
        id: "site-bulk-" + index, name: "批量站点 " + index,
        base_url: "https://bulk" + index + ".example.test/60s/api",
        enabled: index % 5 !== 0, timeout_seconds: 10, source_kind: "public",
        has_bearer_token: false, revision: 1, updated_at: "",
      });
    }
    const bulk = await openPage(browser, port, { sites: many });
    await bulk.click("#site-select-all");
    await bulk.click("#site-test-selected");
    await bulk.waitForFunction(() => document.getElementById("site-test-summary")
      .textContent.includes("测试完成：22 个站点"), null, { timeout: 20000 });
    const bulkCalls = await calls(bulk, "sites/test-batch");
    check(bulkCalls.length === 2, "22 个站点分成两次请求：" + bulkCalls.length);
    check(bulkCalls[0].payload.site_ids.length === 20, "第一块 20 个站点");
    check(bulkCalls[1].payload.site_ids.length === 2, "第二块 2 个站点");
    check(bulkCalls[0].payload.site_ids[0] === "site-bulk-0"
      && bulkCalls[1].payload.site_ids[1] === "site-bulk-21", "分块保持列表顺序");
    check((await bulk.textContent("#site-test-summary")).includes("测试完成：22 个站点 × 5 类"),
      "分块后汇总按全部站点计数");
    await bulk.close();

    // 键盘：图标按钮有可读名称、键盘焦点可见，回车可触发测试。
    const keyboard = await openPage(browser, port);
    await keyboard.focus('#sites-list > li[data-site-id="site-ok"] .site-select');
    await keyboard.keyboard.press("Tab");
    const kbTest = await keyboard.evaluate(() => {
      const node = document.activeElement;
      const style = window.getComputedStyle(node);
      return {
        label: node.getAttribute("aria-label"),
        focusVisible: node.matches(":focus-visible"),
        outlineStyle: style.outlineStyle,
        outlineWidth: parseFloat(style.outlineWidth),
      };
    });
    check(kbTest.label === "测试站点 公开站点甲 的五类接口",
      "Tab 进入测试按钮：" + kbTest.label);
    check(kbTest.focusVisible && kbTest.outlineStyle !== "none"
      && kbTest.outlineWidth >= 2,
      "键盘焦点有可见描边：" + kbTest.outlineStyle + " " + kbTest.outlineWidth);
    await keyboard.keyboard.press("Tab");
    const kbEdit = await keyboard.evaluate(() => document.activeElement.getAttribute("aria-label"));
    await keyboard.keyboard.press("Tab");
    const kbDelete = await keyboard.evaluate(() => document.activeElement.getAttribute("aria-label"));
    check(kbEdit === "编辑站点 公开站点甲" && kbDelete === "删除站点 公开站点甲",
      "三个图标按钮依次可获得焦点：" + kbEdit + " / " + kbDelete);
    await keyboard.keyboard.press("Shift+Tab");
    await keyboard.keyboard.press("Shift+Tab");
    await keyboard.keyboard.press("Enter");
    await keyboard.waitForFunction(() => document.getElementById("site-test-summary")
      .textContent.includes("测试完成：1 个站点"), null, { timeout: 20000 });
    const kbCalls = await calls(keyboard, "sites/test-batch");
    check(kbCalls.length === 1 && kbCalls[0].payload.site_ids.length === 1
      && kbCalls[0].payload.site_ids[0] === "site-ok", "回车触发单站测试");
    await keyboard.close();

    // 390px：不横向溢出，图标按钮与五类状态仍可用。
    const narrow = await openPage(browser, port, { viewport: { width: 390, height: 844 } });
    const metrics = await narrow.evaluate(() => {
      const row = document.querySelector('#sites-list > li[data-site-id="site-partial"]');
      return {
        page: document.documentElement.scrollWidth - document.documentElement.clientWidth,
        row: row.scrollWidth - row.clientWidth,
        chips: row.querySelectorAll(".site-test-chip").length,
        buttons: row.querySelectorAll(".site-icon-button").length,
      };
    });
    check(metrics.page <= 1, "390px 页面不横向溢出：" + metrics.page);
    check(metrics.row <= 1, "390px 站点行不横向溢出：" + metrics.row);
    check(metrics.chips === 5, "390px 仍显示五类状态");
    check(metrics.buttons === 3, "390px 仍显示三个图标按钮");
    await narrow.click('#sites-list > li[data-site-id="site-partial"] .site-select');
    check((await narrow.textContent("#site-selection-count")).trim() === "已选 1 个",
      "390px 可勾选站点");
    await narrow.close();

    console.log("\n站点列表：全部断言通过");
  } finally {
    await browser.close();
    server.close();
  }
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
