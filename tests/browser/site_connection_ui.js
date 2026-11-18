/*
 * 站点连接测试的宿主浏览器回归：真实宿主 iframe（iframe.plugin-view-frame）+ 真实后端 API。
 * 环境变量：ASTRBOT_URL、ASTRBOT_PASSWORD、CHROME_PATH；缺一即以退出码 2 结束。
 * 上游由本脚本内的本地 HTTP 服务模拟（127.0.0.1），不会请求公网。
 */
const BASE_URL = process.env.ASTRBOT_URL;
const PASSWORD = process.env.ASTRBOT_PASSWORD;
const CHROME = process.env.CHROME_PATH;
if (!BASE_URL || !PASSWORD || !CHROME) {
  console.error('ASTRBOT_URL, ASTRBOT_PASSWORD and CHROME_PATH are required');
  process.exit(2);
}

const assert = require('assert/strict');
const http = require('http');
const { chromium } = require('playwright-core');

const PLUGIN = process.env.ASTRBOT_PLUGIN || 'astrbot_plugin_60sapi_hub';
const API_PREFIX = '/api/v1/plugins/extensions/' + PLUGIN;
const SLOW_DELAY_MS = 1500;
const SITE_TIMEOUT_SECONDS = 20;
const STALE_TEXT = '站点配置已变化，请刷新后重新测试。';
const RAW_LEAK = /secret-token|mock upstream|<html>|SyntaxError|JSON\.parse|Unexpected token|[{}\[\]]/;

let checks = 0;
const failures = [];
function check(passed, label) {
  checks += 1;
  if (passed) {
    console.log('  ✓ ' + label);
    return true;
  }
  failures.push(label);
  console.error('  ✗ ' + label);
  return false;
}
function checkValue(actual, expected, label) {
  const ok = Object.is(actual, expected);
  return check(ok, ok ? label : label + '（实际 ' + JSON.stringify(actual) + '，期望 ' + JSON.stringify(expected) + '）');
}
function checkIncludes(text, needle, label) {
  const ok = typeof text === 'string' && text.includes(needle);
  return check(ok, ok ? label : label + '（实际文本：' + JSON.stringify(text) + '）');
}

async function startUpstream() {
  const hits = { ok: 0, slow: 0, fail: 0, badjson: 0, empty: 0 };
  const server = http.createServer((request, response) => {
    const pathname = new URL(request.url, 'http://127.0.0.1').pathname;
    const mode = pathname.split('/')[1];
    if (!Object.prototype.hasOwnProperty.call(hits, mode)) {
      response.writeHead(404, { 'Content-Type': 'application/json' });
      response.end(JSON.stringify({ code: 404, message: 'unknown mock mode' }));
      return;
    }
    hits[mode] += 1;
    const send = () => {
      if (mode === 'fail') {
        response.writeHead(500, { 'Content-Type': 'application/json' });
        response.end(JSON.stringify({ code: 500, message: 'mock upstream 500 secret-token-do-not-leak' }));
        return;
      }
      if (mode === 'badjson') {
        response.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
        response.end('<html><body>mock upstream is not json secret-token-do-not-leak</body></html>');
        return;
      }
      const daily = pathname.endsWith('/v2/60s');
      let payload;
      if (mode === 'empty') {
        payload = daily ? { code: 200, data: { news: [], date: '2026-01-01' } } : { code: 200, data: [] };
      } else {
        payload = daily
          ? { code: 200, data: { news: ['模拟上游新闻一', '模拟上游新闻二'], date: '2026-01-01' } }
          : { code: 200, data: [{ title: '模拟上游条目一' }, { title: '模拟上游条目二' }] };
      }
      response.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
      response.end(JSON.stringify(payload));
    };
    if (mode === 'slow') {
      setTimeout(send, SLOW_DELAY_MS);
      return;
    }
    send();
  });
  await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', resolve);
  });
  return { server, hits, base: 'http://127.0.0.1:' + server.address().port };
}

async function closeHostFirstPrompt(page, currentPassword) {
  const dialog = page.locator('.account-dialog');
  if (!(await dialog.isVisible().catch(() => false))) return;
  const inputs = dialog.locator('input');
  if ((await inputs.count()) < 3) return;
  const next = process.env.ASTRBOT_NEW_PASSWORD || currentPassword;
  await inputs.nth(0).fill(currentPassword);
  await inputs.nth(1).fill(next);
  await inputs.nth(2).fill(next);
  const save = dialog.getByRole('button', { name: '保存修改' });
  await save.waitFor({ state: 'visible' });
  await page.waitForFunction(() => {
    const button = document.querySelector('.account-dialog button');
    return button && !button.disabled;
  }, null, { timeout: 10000 });
  await save.click();
  await dialog.waitFor({ state: 'hidden', timeout: 15000 });
}

async function closeHostPrompts(page) {
  const account = page.locator('.account-dialog');
  if (await account.isVisible().catch(() => false)) {
    await closeHostFirstPrompt(page, PASSWORD);
  }
  await page.waitForFunction(() => {
    if (localStorage.getItem('astrbot:first_notice_seen:v1') === '1') return true;
    return [...document.querySelectorAll('button')].some((button) =>
      button.offsetParent && button.innerText.trim() === '关闭');
  }, null, { timeout: 15000 });
  if (await page.evaluate(() => localStorage.getItem('astrbot:first_notice_seen:v1') === '1')) return;
  const close = page.getByRole('button', { name: '关闭', exact: true });
  await close.click();
  await close.waitFor({ state: 'hidden', timeout: 15000 });
  await page.waitForFunction(
    () => localStorage.getItem('astrbot:first_notice_seen:v1') === '1',
    null, { timeout: 15000 },
  );
}

async function frameOfPage(page) {
  const iframe = page.locator('iframe.plugin-view-frame').first();
  await iframe.waitFor({ state: 'attached', timeout: 20000 });
  for (let i = 0; i < 60; i += 1) {
    let frame = null;
    try {
      frame = await (await iframe.elementHandle()).contentFrame();
    } catch { /* iframe 正在重建 */ }
    if (frame) {
      try {
        await frame.waitForFunction(
          () => document.getElementById('sites-list')?.getAttribute('aria-busy') === 'false',
          null, { timeout: 2000 },
        );
        return frame;
      } catch { /* 尚未完成引导 */ }
    }
    await page.waitForTimeout(250);
  }
  throw new Error('插件 iframe 在等待窗口内未就绪');
}

function rowOf(frame, siteId) {
  return frame.locator('li.site-row[data-site-id="' + siteId + '"]');
}

async function readChips(frame, siteId) {
  return frame.evaluate((id) => {
    const row = document.querySelector('#sites-list li[data-site-id="' + id + '"]');
    if (!row) return null;
    return [...row.querySelectorAll('.site-test-chip')].map((chip) => ({
      type: chip.dataset.type || '',
      state: chip.dataset.state || '',
      text: (chip.querySelector('.site-test-text') || {}).textContent || '',
      title: chip.getAttribute('title') || '',
    }));
  }, siteId);
}

async function waitChipState(frame, siteId, wanted, timeout = 20000) {
  await frame.waitForFunction(([id, state]) => {
    const row = document.querySelector('#sites-list li[data-site-id="' + id + '"]');
    if (!row) return false;
    const chips = [...row.querySelectorAll('.site-test-chip')];
    return chips.length === 5 && chips.every((chip) => chip.dataset.state === state);
  }, [siteId, wanted], { timeout });
}

async function summaryText(frame) {
  return (await frame.evaluate(() => document.getElementById('site-test-summary')?.textContent || '')).trim();
}

async function waitSummaryDone(frame) {
  await frame.waitForFunction(
    () => (document.getElementById('site-test-summary')?.textContent || '').startsWith('测试完成：'),
    null, { timeout: 30000 },
  );
}

async function readDetails(frame, siteId) {
  return frame.evaluate((id) => {
    const row = document.querySelector('#sites-list li[data-site-id="' + id + '"]');
    const details = row ? row.querySelector('.site-test-details') : null;
    if (!details) return null;
    return {
      open: details.open,
      summary: (details.querySelector('summary') || {}).textContent || '',
      items: [...details.querySelectorAll('.site-test-detail-list li')].map((item) => item.textContent || ''),
    };
  }, siteId);
}

async function detailsOpen(frame, siteId) {
  return frame.evaluate((id) => {
    const row = document.querySelector('#sites-list li[data-site-id="' + id + '"]');
    return Boolean(row && row.querySelector('.site-test-details')?.open);
  }, siteId);
}

async function refreshSites(page, frame) {
  await frame.locator('#refresh-button').click();
  await frame.waitForFunction(
    () => document.getElementById('sites-list')?.getAttribute('aria-busy') === 'false',
    null, { timeout: 20000 },
  );
}

(async () => {
  const stamp = Date.now();
  const nameA = '站点连接宿主A-' + stamp;
  const nameB = '站点连接宿主B-' + stamp;
  const nameC = '站点连接宿主C-' + stamp;
  const created = [];
  const upstream = await startUpstream();
  let browser = null;
  let page = null;
  let token = null;
  let mainOk = false;

  async function api(method, path, data) {
    const response = await fetch(BASE_URL + path, {
      method,
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: 'Bearer ' + token } : {}),
      },
      body: data === undefined ? undefined : JSON.stringify(data),
    });
    const body = await response.json().catch(() => ({}));
    return { status: response.status, body };
  }

  async function createSite(name, mode) {
    const response = await api('POST', API_PREFIX + '/sites', {
      name,
      base_url: upstream.base + '/' + mode,
      enabled: true,
      timeout_seconds: SITE_TIMEOUT_SECONDS,
    });
    assert.equal(response.status, 201, '创建站点 ' + name + ' 失败：HTTP ' + response.status + ' ' + JSON.stringify(response.body));
    const site = { id: response.body.data.id, name, mode, revision: response.body.data.revision };
    created.push(site);
    return site;
  }

  async function updateSite(site, patch) {
    const live = await api('GET', API_PREFIX + '/sites/' + site.id);
    assert.equal(live.status, 200, '读取站点 ' + site.name + ' 失败：HTTP ' + live.status);
    const { mode, ...fields } = patch;
    const nextMode = mode || site.mode;
    const payload = {
      name: site.name,
      base_url: upstream.base + '/' + nextMode,
      enabled: true,
      timeout_seconds: SITE_TIMEOUT_SECONDS,
      ...fields,
      expected_revision: live.body.data.revision,
    };
    const response = await api('POST', API_PREFIX + '/sites/' + site.id + '/update', payload);
    if (fields.name) site.name = fields.name;
    site.mode = nextMode;
    return { response, before: live.body.data.revision };
  }

  console.log('站点连接宿主回归 -> ' + BASE_URL);
  try {
    const login = await api('POST', '/api/v1/auth/login', { username: 'astrbot', password: PASSWORD });
    assert.equal(login.status, 200, '宿主登录失败：HTTP ' + login.status + ' ' + JSON.stringify(login.body));
    token = login.body.data.token;

    const siteA = await createSite(nameA, 'ok');
    const siteB = await createSite(nameB, 'slow');
    const siteC = await createSite(nameC, 'fail');

    browser = await chromium.launch({
      headless: true,
      executablePath: CHROME,
      args: ['--no-sandbox'],
    });
    page = await browser.newPage({ viewport: { width: 1400, height: 1000 } });
    await page.goto(BASE_URL + '/', { waitUntil: 'domcontentloaded' });
    await page.fill('input[name=username]', 'astrbot');
    await page.fill('input[name=password]', PASSWORD);
    await Promise.all([
      page.waitForURL((url) => !url.toString().includes('/auth/login')),
      page.getByRole('button', { name: '登录', exact: true }).click(),
    ]);
    await closeHostFirstPrompt(page, PASSWORD);
    await page.goto(BASE_URL + '/#/plugin-view/' + PLUGIN + '/sites', { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(800);
    await closeHostPrompts(page);
    const frame = await frameOfPage(page);

    for (const site of created) {
      await rowOf(frame, site.id).waitFor({ state: 'visible', timeout: 20000 });
    }
    checkValue(await frame.locator('#sites-list').getAttribute('aria-busy'), 'false', '站点列表加载完成后 aria-busy=false');

    // 1) 成功：本地模拟上游返回合法内容。
    const testButtonA = rowOf(frame, siteA.id).getByRole('button', { name: /^测试站点 / });
    checkValue(await testButtonA.getAttribute('aria-label'), '测试站点 ' + nameA + ' 的五类接口', '行内测试入口的无障碍名称带站点名');
    check(await testButtonA.isEnabled(), '空闲时行内测试入口可用');
    checkValue(await rowOf(frame, siteA.id).locator('.site-test-chip').count(), 5, '站点行渲染 5 个接口状态芯片');
    checkValue(await rowOf(frame, siteA.id).locator('.site-test-chip').first().getAttribute('data-state'), 'idle', '未测试时芯片状态为 idle');
    await testButtonA.click();
    await waitChipState(frame, siteA.id, 'success');
    await waitSummaryDone(frame);
    const successChips = await readChips(frame, siteA.id);
    checkValue(successChips.length, 5, '成功后仍有 5 个芯片');
    check(successChips.every((chip) => chip.state === 'success'), '成功后 5 个芯片的 data-state 均为 success');
    checkValue(successChips.map((chip) => chip.type).join(','), 'daily_news,bilibili_hot,zhihu_hot,douyin_hot,toutiao_hot', '芯片顺序与五类接口一致');
    checkValue(successChips[0].text, '每日新闻 成功', '成功芯片文案为「每日新闻 成功」');
    checkValue(await summaryText(frame), '测试完成：1 个站点 × 5 类；成功 5 项，合法空内容 0 项，失败 0 项。', '单站点成功摘要计数');
    checkValue(await rowOf(frame, siteA.id).locator('.site-test-details').count(), 0, '成功结果不显示说明折叠块');
    checkValue(upstream.hits.ok, 5, '本地模拟上游 /ok 收到 5 次请求');
    checkValue(upstream.hits.fail + upstream.hits.badjson + upstream.hits.slow, 0, '本次只访问了 /ok 模拟路径');

    // 2) 加载中：批量测试在途时按钮忙碌、芯片 running；列表 aria-busy 只用于列表加载。
    await rowOf(frame, siteA.id).locator('.site-select').check();
    await rowOf(frame, siteB.id).locator('.site-select').check();
    checkValue((await frame.locator('#site-selection-count').innerText()).trim(), '已选 2 个', '勾选两个站点后计数正确');
    const batchButton = frame.locator('#site-test-selected');
    checkValue((await batchButton.innerText()).trim(), '批量测试', '批量测试按钮空闲文案为「批量测试」');
    check(await batchButton.isEnabled(), '选中站点后批量测试按钮可用');
    await batchButton.click();
    await waitChipState(frame, siteB.id, 'running', 6000);
    check(await batchButton.isDisabled(), '批量测试在途时按钮禁用');
    checkValue((await batchButton.innerText()).trim(), '测试中…', '批量测试在途时按钮文案为「测试中…」');
    checkIncludes(await summaryText(frame), '正在测试 2 个站点 × 5 类接口', '在途提示包含站点数与接口数');
    checkValue(await frame.locator('#sites-list').getAttribute('aria-busy'), 'false', '接口测试在途时列表 aria-busy 保持 false（未重新加载列表）');
    const inflightB = await readChips(frame, siteB.id);
    check(inflightB.every((chip) => chip.state === 'running'), '在途时延迟站点的 5 个芯片均为 running');
    checkValue(inflightB[0].text, '每日新闻 测试中', '在途芯片文案为「每日新闻 测试中」');
    const inflightA = await readChips(frame, siteA.id);
    check(inflightA.every((chip) => chip.state === 'running' || chip.state === 'success'), '在途时快速站点的芯片为 running 或 success');
    await waitChipState(frame, siteB.id, 'success', 40000);
    await waitChipState(frame, siteA.id, 'success', 40000);
    await waitSummaryDone(frame);
    checkValue(await summaryText(frame), '测试完成：2 个站点 × 5 类；成功 10 项，合法空内容 0 项，失败 0 项。', '批量测试完成摘要计数');
    check(await batchButton.isEnabled(), '批量测试结束后按钮恢复可用');
    checkValue((await batchButton.innerText()).trim(), '批量测试', '批量测试结束后按钮文案恢复');
    checkValue((await readChips(frame, siteB.id))[0].text, '每日新闻 成功', '延迟型上游最终也给出成功状态');

    // 列表加载态：刷新请求被延迟时 aria-busy=true 且刷新按钮禁用。
    const sitesPath = API_PREFIX + '/sites';
    const sitesRoute = '**/api/v1/plugins/extensions/*/sites*';
    await page.route(sitesRoute, async (route) => {
      const request = route.request();
      if (request.method() === 'GET' && new URL(request.url()).pathname === sitesPath) {
        await new Promise((resolve) => setTimeout(resolve, 600));
      }
      await route.continue().catch(() => {});
    });
    await frame.locator('#refresh-button').click();
    let sawBusy = false;
    for (let i = 0; i < 60 && !sawBusy; i += 1) {
      sawBusy = (await frame.locator('#sites-list').getAttribute('aria-busy')) === 'true';
      if (!sawBusy) await page.waitForTimeout(25);
    }
    check(sawBusy, '刷新列表期间 #sites-list 的 aria-busy 变为 true');
    check(await frame.locator('#refresh-button').isDisabled(), '刷新列表期间刷新按钮禁用');
    await frame.waitForFunction(
      () => document.getElementById('sites-list')?.getAttribute('aria-busy') === 'false',
      null, { timeout: 20000 },
    );
    await page.unroute(sitesRoute);
    check(await frame.locator('#refresh-button').isEnabled(), '刷新结束后刷新按钮恢复可用');
    checkValue((await readChips(frame, siteB.id))[0].state, 'success', '普通刷新不会丢弃同一版本的测试结果');

    // 3) 旧响应失效：站点 B 慢测试在途时改配置，旧响应必须变成「需重新测试」。
    const slowBefore = upstream.hits.slow;
    await rowOf(frame, siteB.id).getByRole('button', { name: /^测试站点 / }).click();
    await frame.waitForFunction((id) => {
      const row = document.querySelector('#sites-list li[data-site-id="' + id + '"]');
      return Boolean(row && row.querySelector('.site-test-chip[data-state="running"]'));
    }, siteB.id, { timeout: 6000 });
    const hitDeadline = Date.now() + 10000;
    while (upstream.hits.slow <= slowBefore && Date.now() < hitDeadline) {
      await page.waitForTimeout(50);
    }
    check(upstream.hits.slow > slowBefore, '慢速模拟上游已收到站点 B 的测试请求（请求在途）');
    const rename = await updateSite(siteB, { name: nameB + '改名', timeout_seconds: SITE_TIMEOUT_SECONDS - 5 });
    checkValue(rename.response.status, 200, '测试在途时通过宿主 API 修改站点配置');
    check(Number(rename.response.body.data.revision) > Number(rename.before), '站点版本号在测试在途时递增');
    await waitChipState(frame, siteB.id, 'stale', 40000);
    await waitSummaryDone(frame);
    const staleChips = await readChips(frame, siteB.id);
    checkValue(staleChips.length, 5, '旧响应回来后站点 B 仍有 5 个芯片');
    check(staleChips.every((chip) => chip.state === 'stale'), '旧响应回来后的 5 个芯片 data-state 均为 stale');
    check(staleChips.every((chip) => chip.text.endsWith(' 需重新测试')), '芯片文案为「需重新测试」');
    check(!staleChips.some((chip) => chip.text.endsWith(' 成功')), '旧的成功结果没有覆盖 stale 状态');
    const staleSummary = await summaryText(frame);
    checkIncludes(staleSummary, '另有 5 项因站点配置已变化未采用', '完成摘要标注 5 项旧结果未采用');
    checkIncludes(staleSummary, '成功 0 项', '完成摘要不把旧结果计入成功');
    const staleDetails = await readDetails(frame, siteB.id);
    assert.ok(staleDetails, 'stale 结果必须有说明折叠块');
    checkValue(staleDetails.summary, '查看说明（5 项）', '说明折叠块标题为「查看说明（5 项）」');
    checkValue(staleDetails.open, false, '说明折叠块默认收起');
    checkValue(staleDetails.items.length, 5, 'stale 说明列出 5 条');
    check(staleDetails.items.every((item) => item.includes(STALE_TEXT)), '5 条说明都提示站点配置已变化');
    const staleSummaryControl = rowOf(frame, siteB.id).locator('.site-test-details summary');
    await staleSummaryControl.click();
    check(await detailsOpen(frame, siteB.id), '点击 summary 展开说明');
    await staleSummaryControl.press('Enter');
    check(!(await detailsOpen(frame, siteB.id)), '键盘 Enter 收起说明');
    await staleSummaryControl.press('Enter');
    check(await detailsOpen(frame, siteB.id), '再次用键盘 Enter 展开说明');
    const staleTitle = staleChips[0].title;
    checkIncludes(staleTitle, STALE_TEXT, '芯片 title 也带 stale 说明');
    await refreshSites(page, frame);
    await frame.waitForFunction((id) => {
      const row = document.querySelector('#sites-list li[data-site-id="' + id + '"] .site-name');
      return Boolean(row && (row.textContent || '').includes('改名'));
    }, siteB.id, { timeout: 20000 });
    const refreshedChips = await readChips(frame, siteB.id);
    check(refreshedChips.every((chip) => chip.state === 'idle'), '刷新后站点 B 的旧测试记录被丢弃（芯片回到未测试）');
    checkValue(refreshedChips[0].text, '每日新闻 未测试', '刷新后芯片文案为「每日新闻 未测试」');
    checkValue(await rowOf(frame, siteB.id).locator('.site-test-details').count(), 0, '刷新后不再显示过期说明');

    // 4) 失败提示：500 与非法 JSON 都要给出可读说明，且不泄露原始响应。
    const testButtonC = rowOf(frame, siteC.id).getByRole('button', { name: /^测试站点 / });
    await testButtonC.click();
    await waitChipState(frame, siteC.id, 'error');
    await waitSummaryDone(frame);
    const errorChips = await readChips(frame, siteC.id);
    check(errorChips.every((chip) => chip.state === 'error'), '上游 500 时 5 个芯片均为 error');
    check(errorChips.every((chip) => chip.text.endsWith(' 失败')), '失败芯片文案为「失败」');
    checkValue(await summaryText(frame), '测试完成：1 个站点 × 5 类；成功 0 项，合法空内容 0 项，失败 5 项。', '失败摘要计数');
    const failDetails = await readDetails(frame, siteC.id);
    assert.ok(failDetails, '失败结果必须有说明折叠块');
    checkValue(failDetails.summary, '查看说明（5 项）', '失败说明折叠块标题为「查看说明（5 项）」');
    checkValue(failDetails.items.length, 5, '失败说明列出 5 条');
    check(failDetails.items.every((item) => item.includes('上游返回了失败状态。')), '每条失败说明都是可读原因');
    check(!RAW_LEAK.test(failDetails.items.join('\n')), '失败说明不包含原始响应或异常文本');

    const toBadJson = await updateSite(siteC, { mode: 'badjson' });
    checkValue(toBadJson.response.status, 200, '把站点 C 的上游切换到非法 JSON 路径');
    await refreshSites(page, frame);
    await rowOf(frame, siteC.id).getByRole('button', { name: /^测试站点 / }).click();
    await waitChipState(frame, siteC.id, 'error');
    await waitSummaryDone(frame);
    const badJsonDetails = await readDetails(frame, siteC.id);
    assert.ok(badJsonDetails, '非法 JSON 结果必须有说明折叠块');
    checkValue(badJsonDetails.items.length, 5, '非法 JSON 说明列出 5 条');
    checkIncludes(badJsonDetails.items.join('\n'), '上游返回的内容不是有效 JSON。', '非法 JSON 的说明对用户可读');
    check(!RAW_LEAK.test(badJsonDetails.items.join('\n')), '非法 JSON 的说明不包含原始 HTML 或解析异常');

    const toEmpty = await updateSite(siteC, { mode: 'empty' });
    checkValue(toEmpty.response.status, 200, '把站点 C 的上游切换到合法空内容路径');
    await refreshSites(page, frame);
    await rowOf(frame, siteC.id).getByRole('button', { name: /^测试站点 / }).click();
    await waitChipState(frame, siteC.id, 'empty');
    await waitSummaryDone(frame);
    const emptyChips = await readChips(frame, siteC.id);
    check(emptyChips.every((chip) => chip.state === 'empty'), '合法空内容时 5 个芯片均为 empty');
    checkValue(emptyChips[0].text, '每日新闻 合法空内容', '空内容芯片文案为「每日新闻 合法空内容」');
    checkValue(await summaryText(frame), '测试完成：1 个站点 × 5 类；成功 0 项，合法空内容 5 项，失败 0 项。', '空内容摘要计数');
    const emptyDetails = await readDetails(frame, siteC.id);
    assert.ok(emptyDetails, '空内容结果必须有说明折叠块');
    checkValue(emptyDetails.items.length, 5, '空内容说明列出 5 条');
    check(emptyDetails.items.every((item) => /^[^：]+：/.test(item)), '空内容说明以「接口名：」开头');
    check(!RAW_LEAK.test(emptyDetails.items.join('\n')), '空内容说明不包含原始 JSON');

    check(upstream.hits.ok + upstream.hits.slow + upstream.hits.fail + upstream.hits.badjson + upstream.hits.empty >= 25, '本轮所有测试请求都由本地模拟上游承接');
    mainOk = failures.length === 0;
  } catch (error) {
    console.error('RUN FAILED: ' + (error && error.stack ? error.stack : String(error)));
    process.exitCode = 1;
  } finally {
    const cleanup = [];
    for (const site of created) {
      try {
        const live = await api('GET', API_PREFIX + '/sites/' + site.id);
        if (live.status === 404) continue;
        if (live.status !== 200) {
          throw new Error('读取站点失败：HTTP ' + live.status + ' ' + JSON.stringify(live.body).slice(0, 200));
        }
        const removed = await api('POST', API_PREFIX + '/sites/' + site.id + '/delete', {
          expected_revision: live.body.data.revision,
        });
        if (removed.status !== 200) {
          throw new Error('删除站点失败：HTTP ' + removed.status + ' ' + JSON.stringify(removed.body).slice(0, 300));
        }
        const gone = await api('GET', API_PREFIX + '/sites/' + site.id);
        if (gone.status !== 404) {
          throw new Error('删除后复查未返回 404：HTTP ' + gone.status);
        }
        console.log('  ✓ 已删除站点 ' + site.name + '（' + site.id + '）并复查 404');
      } catch (error) {
        cleanup.push(site.name + '（' + site.id + '）：' + (error && error.message ? error.message : String(error)));
      }
    }
    try {
      await new Promise((resolve, reject) => {
        upstream.server.close((error) => (error ? reject(error) : resolve()));
        if (typeof upstream.server.closeAllConnections === 'function') {
          upstream.server.closeAllConnections();
        }
      });
    } catch (error) {
      cleanup.push('关闭模拟上游失败：' + (error && error.message ? error.message : String(error)));
    }
    if (browser) {
      await browser.close().catch((error) => cleanup.push('关闭浏览器失败：' + error.message));
    }
    if (cleanup.length > 0) {
      console.error('CLEANUP FAILED');
      for (const line of cleanup) console.error('  - ' + line);
      process.exitCode = 1;
    } else {
      console.log('  清理完成：模拟上游已关闭' + (created.length > 0 ? '，' + created.length + ' 个站点已删除并复查 404' : '（本轮未创建站点）'));
    }
  }

  if (mainOk && process.exitCode !== 1) {
    console.log('SITE CONNECTION UI PASS checks=' + checks + ' sites=' + created.length +
      ' upstream=' + JSON.stringify(upstream.hits));
  } else {
    console.error('SITE CONNECTION UI FAILED checks=' + checks + ' failed=' + failures.length +
      (failures.length ? ' [' + failures.join(' | ') + ']' : ''));
    process.exitCode = 1;
  }
})();
