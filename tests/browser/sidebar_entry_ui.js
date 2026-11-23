const assert = require('assert/strict');
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright-core');

const BASE = process.env.ASTRBOT_URL;
const PASSWORD = process.env.ASTRBOT_PASSWORD;
const PLUGIN = process.env.ASTRBOT_PLUGIN || 'astrbot_plugin_60sapi_hub';
const CHROME = process.env.CHROME_PATH;
const ZIP = process.env.ASTRBOT_ZIP || '';
const PAGE_NAME = 'sites';
const VIEW_HASH = `#/plugin-view/${PLUGIN}/${PAGE_NAME}`;
const VIEW_TITLE = '60sAPI Hub';
if (!BASE || !PASSWORD || !CHROME) {
  console.error('ASTRBOT_URL, ASTRBOT_PASSWORD and CHROME_PATH are required');
  process.exit(2);
}
const tag = new URL(BASE).port || 'host';
let token = '';

async function api(method, endpoint, data) {
  const response = await fetch(BASE + endpoint, {
    method,
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: data ? JSON.stringify(data) : undefined,
  });
  return { status: response.status, body: await response.json().catch(() => ({})) };
}

async function contestApi(method, endpoint) {
  const response = await fetch(BASE + endpoint, { method });
  return response.status;
}

async function loginPage(page) {
  await page.goto(BASE + '/', { waitUntil: 'domcontentloaded' });
  await page.fill('input[name=username]', 'astrbot');
  await page.fill('input[name=password]', PASSWORD);
  await Promise.all([
    page.waitForURL((url) => !url.toString().includes('/auth/login')),
    page.locator('button[type=submit]').first().click(),
  ]);
  await page.waitForTimeout(1200);
}

function sidebarLinks(page) {
  return page.evaluate((hash) => [...document.querySelectorAll('.v-navigation-drawer a')]
    .filter((link) => link.getAttribute('href') === hash)
    .map((link) => ({
      text: (link.innerText || '').trim().replace(/\s+/g, ' '),
      active: link.classList.contains('v-list-item--active'),
      icon: [...link.querySelectorAll('i, .v-icon')]
        .map((node) => node.className).join(' '),
    })), VIEW_HASH);
}

async function waitSidebar(page, expected, label, budgetMs = 15000) {
  const deadline = Date.now() + budgetMs;
  let last = [];
  while (Date.now() < deadline) {
    last = await sidebarLinks(page);
    if (last.length === expected) return last;
    await page.waitForTimeout(250);
  }
  throw new Error(`${label}：侧栏入口数量 ${last.length}，期望 ${expected}：${JSON.stringify(last)}`);
}

async function pluginFrame(page) {
  const iframe = page.locator('iframe.plugin-view-frame').first();
  await iframe.waitFor({ state: 'attached', timeout: 20000 });
  for (let i = 0; i < 60; i += 1) {
    let frame = null;
    try {
      frame = await (await iframe.elementHandle()).contentFrame();
    } catch {}
    if (frame) {
      try {
        await frame.waitForFunction(
          () => document.getElementById('create-task-button'),
          null, { timeout: 3000 },
        );
        return frame;
      } catch {}
    }
    await page.waitForTimeout(250);
  }
  throw new Error('插件 iframe 未加载出管理页');
}

const results = {};

(async () => {
  const browser = await chromium.launch({
    headless: true, executablePath: CHROME, args: ['--no-sandbox'],
  });
  try {
    const login = await api('POST', '/api/v1/auth/login', {
      username: 'astrbot', password: PASSWORD,
    });
    token = login.body.data.token;

    // ① 登录后侧栏入口：名称、路径、图标、唯一性。
    const page = await browser.newPage({ viewport: { width: 1400, height: 900 } });
    await loginPage(page);
    let links = await waitSidebar(page, 1, '登录后侧栏入口');
    assert.equal(links[0].text, VIEW_TITLE, JSON.stringify(links));
    assert.match(links[0].icon, /mdi-puzzle/, JSON.stringify(links));
    results.entry = links[0];

    // ② 点击入口：路径、选中态、管理页可交互。
    await page.locator(`.v-navigation-drawer a[href="${VIEW_HASH}"]`).click();
    await page.waitForFunction((hash) => location.hash === hash, VIEW_HASH.replace(/^#/, '#'));
    let frame = await pluginFrame(page);
    links = await sidebarLinks(page);
    assert.equal(links.length, 1);
    assert.equal(links[0].active, true, '点击后入口应处于选中态');
    await frame.locator('.nav-item[data-view="tasks"]').click();
    await frame.waitForFunction(() => {
      const view = document.getElementById('view-tasks');
      return Boolean(view) && !view.hidden;
    });
    results.click = { hash: await page.evaluate(() => location.hash), active: links[0].active };
    await page.screenshot({ path: `/tmp/sidebar-entry-${tag}.png` });

    // ③ 未登录：深层链接应回到登录页，受保护接口拒绝。
    const anon = await browser.newContext();
    const anonPage = await anon.newPage();
    await anonPage.goto(`${BASE}/#/plugin-view/${PLUGIN}/${PAGE_NAME}`, {
      waitUntil: 'domcontentloaded',
    });
    await anonPage.waitForURL((url) => url.toString().includes('/auth/login'), { timeout: 15000 });
    results.anonymous = {
      url: await anonPage.evaluate(() => location.hash),
      apiStatus: await contestApi('GET', `/api/v1/plugins/${PLUGIN}/pages`),
    };
    assert.match(results.anonymous.url, /auth\/login/);
    assert.equal([401, 403].includes(results.anonymous.apiStatus), true,
      `未登录接口状态 ${results.anonymous.apiStatus}`);
    await anon.close();

    // ④ 英文环境回退：无英文翻译时也应显示插件显示名。
    const english = await browser.newContext();
    await english.addInitScript(() => {
      try { localStorage.setItem('astrbot-locale', 'en-US'); } catch {}
    });
    const enPage = await english.newPage({ viewport: { width: 1400, height: 900 } });
    await loginPage(enPage);
    const enLinks = await waitSidebar(enPage, 1, '英文侧栏入口');
    assert.equal(enLinks[0].text, VIEW_TITLE, JSON.stringify(enLinks));
    results.english = enLinks[0].text;
    await english.close();

    // ⑤ 停用：记录刷新前后行为，刷新后入口消失且页面不再可达。
    const off = await api('POST', '/api/plugin/off', { name: PLUGIN });
    assert.equal(off.body.status, 'ok', JSON.stringify(off.body));
    await page.waitForTimeout(2500);
    results.offBeforeReload = (await sidebarLinks(page)).length;
    await page.reload({ waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(2500);
    results.offAfterReload = (await sidebarLinks(page)).length;
    assert.equal(results.offAfterReload, 0, '停用并刷新后入口应消失');

    // ⑥ 启用：只恢复一个入口，点击仍可用。
    const on = await api('POST', '/api/plugin/on', { name: PLUGIN });
    assert.equal(on.body.status, 'ok', JSON.stringify(on.body));
    await page.waitForTimeout(2500);
    results.onBeforeReload = (await sidebarLinks(page)).length;
    await page.reload({ waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(2500);
    links = await waitSidebar(page, 1, '重新启用后侧栏入口');
    assert.equal(links[0].text, VIEW_TITLE);
    await page.locator(`.v-navigation-drawer a[href="${VIEW_HASH}"]`).click();
    frame = await pluginFrame(page);
    results.onAfterReload = (await sidebarLinks(page)).length;

    // ⑦ 卸载：入口清除；若提供了 ZIP 则重装并确认只恢复一个入口。
    if (ZIP) {
      const uninstall = await api('POST', '/api/plugin/uninstall', {
        name: PLUGIN, delete_config: false, delete_data: false,
      });
      assert.equal(uninstall.body.status, 'ok', JSON.stringify(uninstall.body));
      await page.reload({ waitUntil: 'domcontentloaded' });
      await page.waitForTimeout(3000);
      results.uninstalled = (await sidebarLinks(page)).length;
      assert.equal(results.uninstalled, 0, '卸载后入口应清除');

      const buffer = fs.readFileSync(ZIP);
      const form = new FormData();
      form.append('file', new Blob([buffer], { type: 'application/zip' }), path.basename(ZIP));
      const upload = await fetch(BASE + '/api/v1/plugins/install/upload', {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
        body: form,
      });
      const uploaded = await upload.json();
      assert.equal(uploaded.status, 'ok', JSON.stringify(uploaded));
      await page.reload({ waitUntil: 'domcontentloaded' });
      await page.waitForTimeout(3000);
      links = await waitSidebar(page, 1, '重装后侧栏入口');
      assert.equal(links[0].text, VIEW_TITLE);
      results.reinstalled = links.length;
    }

    console.log('SIDEBAR ENTRY UI PASS', { base: BASE, ...results });
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
