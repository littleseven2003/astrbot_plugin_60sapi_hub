/*
 * 站点来源类型的真实页面逻辑回归。
 *
 * 用真实 Chromium 加载 views/sites/index.html + app.js，只把宿主 bridge
 * 换成记录型桩，用页面自己的表单和事件流验证：
 * 新建默认公开、已知类型往返、缺失/未知类型不被当成公开、也不会被写回。
 *
 * 这是“桩 bridge 的页面逻辑验证”：宿主 bridge 由本脚本替换，因此它不验证
 * 真实 AstrBot 后台、数据库往返（例如未知来源类型能否真的被后端保存），
 * 也不连接任何真实上游或会话。真实宿主验收需在具备 WebUI 的环境单独完成。
 *
 * 浏览器可用 CHROME_PATH 指定；未指定时回退到 playwright-core 解析到的
 * Chromium。Node 与 playwright-core 由 NODE_PATH / 安装位置决定，不含任何
 * 开发者本机绝对路径。
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

const SITES = [
  {
    id: "site-public", name: "公开站点", base_url: "https://a.example.test/n",
    enabled: true, timeout_seconds: 10, source_kind: "public",
    has_bearer_token: false, revision: 1, updated_at: "",
  },
  {
    id: "site-token", name: "带凭据站点", base_url: "https://t.example.test/n",
    enabled: true, timeout_seconds: 10, source_kind: "public",
    has_bearer_token: true, revision: 2, updated_at: "",
  },
  {
    id: "site-built", name: "自建站点", base_url: "http://10.0.0.5:8080/p",
    enabled: true, timeout_seconds: 10, source_kind: "self_built",
    has_bearer_token: false, revision: 1, updated_at: "",
  },
  {
    id: "site-missing", name: "缺字段站点", base_url: "https://b.example.test/n",
    enabled: true, timeout_seconds: 10,
    has_bearer_token: false, revision: 1, updated_at: "",
  },
  {
    id: "site-unknown", name: "未知类型站点", base_url: "https://c.example.test/n",
    enabled: true, timeout_seconds: 10, source_kind: "community",
    has_bearer_token: false, revision: 1, updated_at: "",
  },
];

const server = http.createServer((request, response) => {
  const url = new URL(request.url, "http://127.0.0.1");
  const relative = url.pathname === "/" ? "/index.html" : url.pathname;
  const target = path.join(VIEWS_DIR, relative);
  if (!target.startsWith(VIEWS_DIR) || !fs.existsSync(target)) {
    response.writeHead(404).end("not found");
    return;
  }
  response.writeHead(200, {
    "Content-Type": MIME[path.extname(target)] || "application/octet-stream",
  });
  response.end(fs.readFileSync(target));
});

let assertions = 0;
function check(condition, message) {
  assertions += 1;
  assert.ok(condition, message);
}

async function openPage(browser, port) {
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  await page.addInitScript((sites) => {
    window.__siteFixtures = sites;
    window.__calls = [];
    window.AstrBotPluginView = {
      ready: async () => ({ pageTitle: "60sAPI Hub" }),
      apiGet: async (endpoint) => {
        window.__calls.push({
          seq: window.__calls.length + 1, method: "GET", endpoint,
        });
        if (endpoint === "sites") {
          return { sites: window.__siteFixtures };
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
          seq: window.__calls.length + 1, method: "POST", endpoint, payload,
        });
        return {};
      },
    };
  }, SITES);
  await page.goto("http://127.0.0.1:" + port + "/index.html", {
    waitUntil: "domcontentloaded",
  });
  await page.waitForFunction(
    () => document.getElementById("sites-list")?.getAttribute("aria-busy") === "false"
      && document.querySelectorAll("#sites-list li").length === 5,
    null, { timeout: 30000 },
  );
  return page;
}

async function openEditFor(page, name) {
  await page.getByRole("button", { name: "编辑站点 " + name }).click();
  await page.waitForFunction(
    () => document.getElementById("site-dialog")?.open === true,
    null, { timeout: 10000 },
  );
}

// 同一 endpoint 可能被多次提交（例如未知类型先不选择、随后显式选择）。
// 这里以点击前的最大序号为基准，只接受本次操作之后产生的新 POST。
async function submitAndCapture(page, endpoint) {
  const baseline = await page.evaluate((target) => {
    const posts = window.__calls.filter(
      (call) => call.method === "POST" && call.endpoint === target,
    );
    return posts.length ? posts[posts.length - 1].seq : 0;
  }, endpoint);
  await page.click("#save-button");
  await page.waitForFunction(
    ({ target, after }) => window.__calls.some(
      (call) => call.method === "POST"
        && call.endpoint === target
        && call.seq > after,
    ),
    { target: endpoint, after: baseline },
    { timeout: 10000 },
  );
  return page.evaluate(({ target, after }) => {
    const posts = window.__calls.filter(
      (call) => call.method === "POST"
        && call.endpoint === target
        && call.seq > after,
    );
    return posts[0].payload;
  }, { target: endpoint, after: baseline });
}

(async () => {
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const port = server.address().port;
  const browser = await chromium.launch({
    headless: true, executablePath: CHROME, args: ["--no-sandbox"],
  });
  try {
    const page = await openPage(browser, port);

    // 列表展示：缺失/未知类型不能被显示成“公开服务”。
    const metas = await page.$$eval("#sites-list li .site-meta", (nodes) =>
      nodes.map((node) => node.textContent));
    check(metas[0].includes("公开服务"), "公开站点显示公开服务");
    check(metas[1].includes("公开服务"), "带凭据站点同为公开服务");
    check(metas[2].includes("自建服务"), "自建站点显示自建服务");
    check(metas[3].includes("来源类型未知"), "缺失类型显示未知");
    check(!metas[3].includes("公开服务"), "缺失类型不得显示公开服务");
    check(!metas[3].includes("自建服务"), "缺失类型不得显示自建服务");
    check(metas[4].includes("来源类型未知"), "未知类型显示未知");
    check(!metas[4].includes("公开服务"), "未知类型不得显示公开服务");

    // 新建默认公开。
    await page.click("#create-button");
    await page.waitForFunction(
      () => document.getElementById("site-dialog")?.open === true,
      null, { timeout: 10000 },
    );
    check(
      await page.inputValue("#source-kind") === "public",
      "新建表单默认公开",
    );
    check(await page.isChecked("#instance-type-public"), "新建默认选中公开实例");
    check(
      await page.inputValue("#instance-preset") === "custom",
      "新建默认自定义地址",
    );
    check(!(await page.isHidden("#base-url-field")), "自定义时显示基础地址输入框");
    check(await page.isHidden("#preset-field") === false, "公开实例显示预设列表");
    check(
      await page.isHidden("#source-kind-hint"),
      "新建时不显示未知提示",
    );
    await page.fill("#name", "新建站点");
    await page.fill("#base-url", "https://new.example.test/news");
    const created = await submitAndCapture(page, "sites");
    check(created.source_kind === "public", "新建保存发送公开类型");

    // 已有公开站点：往返保持。
    await openEditFor(page, "公开站点");
    check(await page.inputValue("#source-kind") === "public", "编辑公开站点回填公开");
    check(await page.isHidden("#source-kind-hint"), "公开站点不显示未知提示");
    await page.fill("#name", "公开站点改名");
    const publicPayload = await submitAndCapture(
      page, "sites/site-public/update",
    );
    check(publicPayload.source_kind === "public", "公开站点保存仍为公开");

    // 已有自建站点：往返保持。
    await openEditFor(page, "自建站点");
    check(
      await page.inputValue("#source-kind") === "self_built",
      "编辑自建站点回填自建",
    );
    await page.fill("#name", "自建站点改名");
    const builtPayload = await submitAndCapture(
      page, "sites/site-built/update",
    );
    check(builtPayload.source_kind === "self_built", "自建站点保存仍为自建");

    // 缺失类型：不能默认公开，也不能写回 public。
    await openEditFor(page, "缺字段站点");
    check(
      await page.inputValue("#source-kind") === "",
      "缺失类型不预选公开",
    );
    check(!(await page.isHidden("#source-kind-hint")), "缺失类型显示未知提示");
    await page.fill("#name", "缺字段站点改名");
    const missingPayload = await submitAndCapture(
      page, "sites/site-missing/update",
    );
    check(
      !Object.prototype.hasOwnProperty.call(missingPayload, "source_kind"),
      "缺失类型只改名称时不发送来源类型",
    );
    check(missingPayload.source_kind !== "public", "缺失类型不写回公开");

    // 未知类型：同样保守。
    await openEditFor(page, "未知类型站点");
    check(await page.inputValue("#source-kind") === "", "未知类型不预选公开");
    check(!(await page.isHidden("#source-kind-hint")), "未知类型显示未知提示");
    await page.fill("#name", "未知类型站点改名");
    const unknownPayload = await submitAndCapture(
      page, "sites/site-unknown/update",
    );
    check(
      !Object.prototype.hasOwnProperty.call(unknownPayload, "source_kind"),
      "未知类型只改名称时不发送来源类型",
    );

    // 管理员明确选择后按选择保存，且未知提示随即消失。
    await openEditFor(page, "未知类型站点");
    check(
      !(await page.isHidden("#source-kind-hint")),
      "未知类型编辑时提示可见",
    );
    await page.click("#instance-type-self");
    check(await page.isHidden("#source-kind-hint"), "明确选择后提示消失");
    await page.fill("#name", "未知类型站点显式选择");
    const explicitPayload = await submitAndCapture(
      page, "sites/site-unknown/update",
    );
    check(
      explicitPayload.source_kind === "self_built",
      "显式选择自建后保存发送自建",
    );

    // 编辑既有站点时不应重复新建。
    const createdCount = await page.evaluate(() =>
      window.__calls.filter(
        (call) => call.method === "POST" && call.endpoint === "sites",
      ).length);
    check(createdCount === 1, "编辑流程没有额外新建站点");


    // 公开预设：保留完整基础地址与路径前缀，隐藏输入框。
    await page.click("#create-button");
    await page.waitForFunction(
      () => document.getElementById("site-dialog")?.open === true);
    await page.fill("#name", "预设站点");
    await page.selectOption("#instance-preset", "https://api.cczo.cc/60s");
    check(await page.isHidden("#base-url-field"), "选预设后隐藏基础地址输入框");
    check(
      await page.inputValue("#base-url") === "https://api.cczo.cc/60s",
      "预设地址保留路径前缀",
    );
    const presetPayload = await submitAndCapture(page, "sites");
    await page.waitForFunction(() => document.getElementById("site-dialog")?.open !== true);
    check(
      presetPayload.base_url === "https://api.cczo.cc/60s",
      "预设保存为完整基础地址：" + presetPayload.base_url,
    );
    check(presetPayload.source_kind === "public", "预设保存为公开实例");

    // 自建实例：不显示预设列表，直接填地址。
    await page.click("#create-button");
    await page.waitForFunction(
      () => document.getElementById("site-dialog")?.open === true);
    await page.fill("#name", "自建新站点");
    await page.click("#instance-type-self");
    check(await page.isHidden("#preset-field"), "自建实例不显示预设列表");
    check(!(await page.isHidden("#base-url-field")), "自建实例显示地址输入框");
    await page.fill("#base-url", "http://10.1.2.3:8080/60s");
    const selfPayload = await submitAndCapture(page, "sites");
    await page.waitForFunction(() => document.getElementById("site-dialog")?.open !== true);
    check(selfPayload.source_kind === "self_built", "自建实例保存为 self_built");
    check(selfPayload.base_url.includes(":8080"), "自建地址原样保存");

    // 切回公开实例：预设列表恢复，地址不被静默改写。
    await page.click("#create-button");
    await page.waitForFunction(
      () => document.getElementById("site-dialog")?.open === true);
    await page.click("#instance-type-self");
    await page.fill("#base-url", "https://custom.example.test/60s");
    await page.click("#instance-type-public");
    check(!(await page.isHidden("#preset-field")), "切回公开后预设列表恢复");
    check(
      await page.inputValue("#base-url") === "https://custom.example.test/60s",
      "切换类型不丢失已填地址",
    );
    await page.selectOption("#instance-preset", "custom");
    await page.fill("#name", "自定义公开站点");
    const customPayload = await submitAndCapture(page, "sites");
    await page.waitForFunction(() => document.getElementById("site-dialog")?.open !== true);
    check(customPayload.source_kind === "public", "自定义公共实例保存为 public");
    check(
      customPayload.base_url === "https://custom.example.test/60s",
      "自定义地址原样保存",
    );

    // 编辑未知类型：两个选项都不预选。
    await openEditFor(page, "未知类型站点");
    check(
      !(await page.isChecked("#instance-type-public"))
        && !(await page.isChecked("#instance-type-self")),
      "未知类型不预选任何实例类型",
    );
    await page.click("#close-dialog-button");

    // 关闭可能残留的未保存确认浮层。
    if (await page.locator("#discard-confirm-button").isVisible()) {
      await page.click("#discard-confirm-button");
      await page.waitForFunction(
        () => document.getElementById("site-dialog")?.open !== true);
    }

    // 编辑带凭据站点并把地址换到另一个服务：不能默认沿用旧凭据。
    await openEditFor(page, "带凭据站点");
    check(
      await page.isChecked("input[name=\'credential_action\'][value=\'keep\']"),
      "未更换地址时默认为保留原凭据",
    );
    await page.fill("#base-url", "https://another.example.test/n");
    await page.locator("#base-url").blur();
    check(
      await page.isChecked("input[name=\'credential_action\'][value=\'clear\']"),
      "更换服务地址后默认改为清除凭据",
    );
    const credentialNotice = await page.textContent("#credential-state");
    check(
      credentialNotice.includes("服务地址已更换"),
      "更换地址后说明凭据处理：" + credentialNotice,
    );
    await page.fill("#name", "带凭据站点改名");
    const tokenPayload = await submitAndCapture(
      page, "sites/site-token/update",
    );
    check(
      tokenPayload.credential_action !== "keep",
      "更换服务后不会静默保留旧凭据：" + tokenPayload.credential_action,
    );

    // 预设 → 自定义切换保留已输入地址。
    await page.click("#create-button");
    await page.waitForFunction(
      () => document.getElementById("site-dialog")?.open === true);
    await page.fill("#base-url", "https://keep.example.test/60s");
    await page.selectOption("#instance-preset", "https://60s.7se.cn");
    check(
      await page.inputValue("#base-url") === "https://60s.7se.cn",
      "选择预设后地址变为预设完整地址",
    );
    await page.selectOption("#instance-preset", "custom");
    check(
      await page.inputValue("#base-url") === "https://keep.example.test/60s",
      "切回自定义后恢复先前填写的地址",
    );
    await page.click("#close-dialog-button");

    // 编辑带凭据站点，直接选不同域名的公共预设：凭据不能静默保留。
    if (await page.locator("#discard-confirm-button").isVisible()) {
      await page.click("#discard-confirm-button");
      await page.waitForFunction(
        () => document.getElementById("site-dialog")?.open !== true);
    }
    await openEditFor(page, "带凭据站点");
    await page.selectOption("#instance-preset", "https://api.cczo.cc/60s");
    check(
      await page.inputValue("#base-url") === "https://api.cczo.cc/60s",
      "选预设后地址为预设完整地址",
    );
    check(
      await page.isChecked("input[name='credential_action'][value='clear']"),
      "预设换到不同域名后默认清除凭据",
    );
    await page.fill("#name", "预设换址站点");
    const presetSwitchPayload = await submitAndCapture(
      page, "sites/site-token/update",
    );
    check(
      presetSwitchPayload.base_url === "https://api.cczo.cc/60s",
      "预设换址保存载荷为预设地址",
    );
    check(
      presetSwitchPayload.credential_action !== "keep",
      "预设换址不会静默保留旧凭据：" + presetSwitchPayload.credential_action,
    );

    // 编辑自定义地址后输入新地址，再切换公开/自建：当前输入必须保留。
    await openEditFor(page, "公开站点");
    await page.fill("#base-url", "https://edited.example.test/60s");
    await page.click("#instance-type-self");
    check(
      await page.inputValue("#base-url") === "https://edited.example.test/60s",
      "切换到自建后仍保留当前输入",
    );
    await page.click("#instance-type-public");
    check(
      await page.inputValue("#base-url") === "https://edited.example.test/60s",
      "再切回公开仍保留当前输入",
    );
    await page.fill("#name", "公开站点改名二");
    const editKeepPayload = await submitAndCapture(
      page, "sites/site-public/update",
    );
    check(
      editKeepPayload.base_url === "https://edited.example.test/60s",
      "类型切换后保存的是当前编辑的地址：" + editKeepPayload.base_url,
    );

    // 保存中所有控件（含预设下拉）都应禁用，结束后恢复。
    await page.click("#create-button");
    await page.waitForFunction(
      () => document.getElementById("site-dialog")?.open === true);
    const disabledDuringSave = await page.evaluate(() => {
      const form = document.getElementById("site-form");
      const preset = document.getElementById("instance-preset");
      form.querySelectorAll("input, select, textarea, button").forEach((el) => {
        el.disabled = true;
      });
      const disabled = preset.disabled;
      form.querySelectorAll("input, select, textarea, button").forEach((el) => {
        el.disabled = false;
      });
      return disabled;
    });
    check(disabledDuringSave, "保存中预设下拉也应禁用");
    await page.click("#close-dialog-button");
    console.log("SITE SOURCE KIND UI PASS", { assertions });
  } finally {
    await browser.close();
    server.close();
  }
})().catch((error) => {
  console.error("FAILED:", error.stack || error.message);
  process.exit(1);
});
