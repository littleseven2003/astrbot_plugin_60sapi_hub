const assert = require('assert/strict');
const { chromium } = require('playwright-core');

async function closeHostFirstPrompt(page, currentPassword) {
  const dialog = page.locator('.account-dialog');
  if (!(await dialog.isVisible().catch(() => false))) return;
  const inputs = dialog.locator('input');
  if (await inputs.count() < 3) return;
  const next = process.env.ASTRBOT_NEW_PASSWORD || currentPassword;
  await inputs.nth(0).fill(currentPassword);
  await inputs.nth(1).fill(next);
  await inputs.nth(2).fill(next);
  const save = dialog.getByRole('button', { name: '保存修改' });
  await save.waitFor({ state: 'visible' });
  await page.waitForFunction(() => {
    const b = document.querySelector('.account-dialog button');
    return b && !b.disabled;
  }, null, { timeout: 10000 });
  await save.click();
  await dialog.waitFor({ state: 'hidden', timeout: 15000 });
}

async function closeHostPrompts(page) {
  const account = page.locator('.account-dialog');
  if (await account.isVisible().catch(() => false)) {
    await closeHostFirstPrompt(page, process.env.ASTRBOT_PASSWORD);
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

const BASE = process.env.ASTRBOT_URL;
const P = process.env.ASTRBOT_PLUGIN || 'astrbot_plugin_60sapi_hub';
const PW = process.env.ASTRBOT_PASSWORD;
const CHROME = process.env.CHROME_PATH;
if (!BASE || !PW || !CHROME) {
  console.error('ASTRBOT_URL, ASTRBOT_PASSWORD and CHROME_PATH are required');
  process.exit(2);
}

let token;
let siteId;
const createdTaskIds = [];
const stamp = Date.now();
const nameA = '时间控件回归' + stamp;
const nameB = '旧时区回填' + stamp;

async function api(method, path, data) {
  const r = await fetch(BASE + path, {
    method,
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: data ? JSON.stringify(data) : undefined,
  });
  return { status: r.status, body: await r.json().catch(() => ({})) };
}

async function frameOfPage(page) {
  const iframe = page.locator('iframe.plugin-view-frame').first();
  await iframe.waitFor({ state: 'attached', timeout: 15000 });
  for (let i = 0; i < 60; i += 1) {
    let frame = null;
    try {
      frame = await (await iframe.elementHandle()).contentFrame();
    } catch {}
    if (frame) {
      try {
        await frame.waitForFunction(
          () => document.getElementById('sites-list')?.getAttribute('aria-busy') === 'false',
          null, { timeout: 2000 },
        );
        return frame;
      } catch {}
    }
    await page.waitForTimeout(250);
  }
  throw new Error('插件 iframe 在等待窗口内未就绪');
}

async function nav(frame, view) {
  let current = frame;
  const deadline = Date.now() + 10000;
  let lastError = null;
  const isVisible = (target) => current.evaluate((v) => {
    const t = document.getElementById('view-' + v);
    return Boolean(t) && !t.hidden;
  }, target);
  if (await isVisible(view).catch(() => false)) return current;
  while (Date.now() < deadline) {
    if (typeof current.isDetached === 'function' && current.isDetached()) {
      current = await frameOfPage(current.page());
    }
    try {
      await current.locator('.nav-item[data-view="' + view + '"]').click({
        timeout: Math.max(1000, Math.min(5000, deadline - Date.now())),
      });
      await current.waitForFunction((v) => {
        const t = document.getElementById('view-' + v);
        return Boolean(t) && !t.hidden;
      }, view, { timeout: Math.max(500, Math.min(5000, deadline - Date.now())) });
      return current;
    } catch (error) {
      lastError = error;
      if (Date.now() >= deadline) break;
      await current.page().waitForTimeout(200);
    }
  }
  throw new Error('切换到视图 ' + view + ' 失败：' + (lastError ? lastError.message : '未知错误'));
}

async function loginPage(page) {
  await page.goto(BASE + '/', { waitUntil: 'domcontentloaded' });
  await page.fill('input[name=username]', 'astrbot');
  await page.fill('input[name=password]', PW);
  await Promise.all([
    page.waitForURL((u) => !u.toString().includes('/auth/login')),
    page.getByRole('button', { name: '登录', exact: true }).click(),
  ]);
  await closeHostFirstPrompt(page, PW);
  await page.goto(`${BASE}/#/plugin-view/${P}/sites`, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(800);
  await closeHostPrompts(page);
  const frame = await frameOfPage(page);
  await frame.waitForFunction(() => document.getElementById('create-task-button'));
  return nav(frame, 'tasks');
}

async function pressDown(page, times) {
  for (let i = 0; i < times; i += 1) await page.keyboard.press('ArrowDown');
}

async function openEdit(page, frame, taskId) {
  await closeHostPrompts(page);
  const f = await nav(frame, 'tasks');
  await f.locator(`#tasks-list li[data-task-id="${taskId}"]`)
    .getByRole('button', { name: /编辑推送任务/ }).click();
  await f.waitForSelector('#task-dialog[open]');
  return f;
}

async function cancelDialog(f) {
  await f.locator('#task-cancel-button').click();
  await f.waitForFunction(() => {
    const d = document.getElementById('discard-dialog');
    return Boolean(d && d.open) || !document.getElementById('task-dialog')?.open;
  }, null, { timeout: 10000 });
  if (await f.evaluate(() => Boolean(document.getElementById('discard-dialog')?.open))) {
    await f.locator('#discard-confirm-button').click();
  }
  await f.waitForFunction(() => !document.getElementById('task-dialog')?.open, null, { timeout: 10000 });
}

(async () => {
  const browser = await chromium.launch({
    headless: true, executablePath: CHROME, args: ['--no-sandbox'],
  });
  const page = await browser.newPage({ viewport: { width: 1400, height: 1000 } });
  const updateBodies = [];
  page.on('request', (req) => {
    const u = new URL(req.url());
    if (req.method() === 'POST' && /\/tasks\/[^/]+\/update$/.test(u.pathname)) {
      updateBodies.push(JSON.parse(req.postData() || '{}'));
    }
  });
  try {
    const login = await api('POST', '/api/v1/auth/login', { username: 'astrbot', password: PW });
    token = login.body.data.token;
    const site = await api('POST', `/api/v1/plugins/extensions/${P}/sites`, {
      name: nameA + '站点',
      base_url: 'http://example.test/proxy',
      enabled: true,
      timeout_seconds: 5,
    });
    siteId = site.body.data.id;

    let f = await loginPage(page);

    // 1. 键盘选择 23:59 与时区（上海→香港），保存后读回。
    await f.locator('#create-task-button').click();
    await f.waitForSelector('#task-dialog[open]');
    await f.fill('#task-name', nameA);
    await f.selectOption('#task-item-picker', 'daily_news');
    await f.locator('#task-item-add-button').click();
    await f.selectOption('#task-site-picker', siteId);
    await f.locator('#task-site-add-button').click();
    await f.locator('#task-schedule-daily').evaluate((el) => {
      el.checked = true;
      el.dispatchEvent(new Event('change', { bubbles: true }));
    });
    // 真实键盘：下拉聚焦后按数字做原生 typeahead 选择，不调用 selectOption。
    await f.focus('#task-schedule-hour');
    await page.keyboard.type('23');
    await f.focus('#task-schedule-minute');
    await page.keyboard.type('59');
    await f.focus('#task-schedule-timezone');
    await page.keyboard.type('Asia/Tokyo');
    const picked = await f.evaluate(() => ({
      hour: document.getElementById('task-schedule-hour').value,
      minute: document.getElementById('task-schedule-minute').value,
      timezone: document.getElementById('task-schedule-timezone').value,
    }));
    assert.equal(picked.hour, '23');
    assert.equal(picked.minute, '59');
    assert.equal(picked.timezone, 'Asia/Tokyo');
    await f.fill('#task-umos', 'fake_qq_01:GroupMessage:123');
    await f.locator('#task-save-button').click();
    await f.waitForFunction(() => !document.getElementById('task-dialog')?.open);
    await f.waitForFunction((n) => document.getElementById('tasks-list')?.innerText.includes(n), nameA);
    let list = await api('GET', `/api/v1/plugins/extensions/${P}/tasks`);
    const taskA = list.body.data.tasks.find((t) => t.name === nameA);
    createdTaskIds.push(taskA.id);
    let detail = await api('GET', `/api/v1/plugins/extensions/${P}/tasks/${taskA.id}`);
    assert.deepEqual(detail.body.data.schedule, {
      kind: 'daily', timezone: 'Asia/Tokyo', time: '23:59',
    });

    // 2. 编辑后键盘回到 00:00（选项循环），保存读回。
    f = await openEdit(page, f, taskA.id);
    assert.equal(await f.inputValue('#task-schedule-hour'), '23');
    assert.equal(await f.inputValue('#task-schedule-minute'), '59');
    // 重复字符在原生 typeahead 里会循环匹配，按一次「0」即可选中 00。
    await f.focus('#task-schedule-hour');
    await page.keyboard.type('0');
    await f.focus('#task-schedule-minute');
    await page.keyboard.type('0');
    assert.equal(await f.inputValue('#task-schedule-hour'), '00');
    assert.equal(await f.inputValue('#task-schedule-minute'), '00');
    await f.locator('#task-save-button').click();
    await f.waitForFunction(() => !document.getElementById('task-dialog')?.open);
    detail = await api('GET', `/api/v1/plugins/extensions/${P}/tasks/${taskA.id}`);
    assert.equal(detail.body.data.schedule.time, '00:00');

    // 3. 改后放弃：只改表单不保存，取消后重新打开仍是 00:00。
    f = await openEdit(page, f, taskA.id);
    await f.selectOption('#task-schedule-hour', '07');
    await cancelDialog(f);
    f = await openEdit(page, f, taskA.id);
    assert.equal(await f.inputValue('#task-schedule-hour'), '00');
    assert.equal(await f.inputValue('#task-schedule-minute'), '00');
    await cancelDialog(f);

    // 4. 旧任务保存预设外时区：临时选项原值回填，未改计划时保存不写 schedule。
    const created = await api('POST', `/api/v1/plugins/extensions/${P}/tasks`, {
      name: nameB,
      items: [{ content_type: 'daily_news' }],
      sites: [siteId],
      output_format: 'text',
      umos: [],
      schedule: { kind: 'daily', timezone: 'Europe/Paris', time: '06:15' },
    });
    const taskB = created.body.data;
    createdTaskIds.push(taskB.id);
    await page.reload({ waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(500);
    f = await frameOfPage(page);
    f = await nav(f, 'tasks');
    await f.waitForFunction((n) => document.getElementById('tasks-list')?.innerText.includes(n), nameB);
    f = await openEdit(page, f, taskB.id);
    const legacy = await f.evaluate(() => ({
      value: document.getElementById('task-schedule-timezone').value,
      options: [...document.getElementById('task-schedule-timezone').options]
        .map((o) => o.textContent),
      hour: document.getElementById('task-schedule-hour').value,
      minute: document.getElementById('task-schedule-minute').value,
    }));
    assert.equal(legacy.value, 'Europe/Paris');
    assert.equal(legacy.hour, '06');
    assert.equal(legacy.minute, '15');
    assert.ok(legacy.options.some((text) => text.includes('已保存：Europe/Paris')), legacy.options.join('|'));
    await f.fill('#task-name', nameB + '改');
    const beforeSave = updateBodies.length;
    await f.locator('#task-save-button').click();
    await f.waitForFunction(() => !document.getElementById('task-dialog')?.open);
    assert.equal(updateBodies.length, beforeSave + 1);
    assert.equal(Object.prototype.hasOwnProperty.call(updateBodies.at(-1), 'schedule'), false);
    detail = await api('GET', `/api/v1/plugins/extensions/${P}/tasks/${taskB.id}`);
    assert.deepEqual(detail.body.data.schedule, {
      kind: 'daily', timezone: 'Europe/Paris', time: '06:15',
    });

    // 5. 切到上海任务再切回：临时「已保存」选项只在原任务回填时存在。
    f = await openEdit(page, f, taskA.id);
    const presetOptions = await f.evaluate(() => [...document.getElementById('task-schedule-timezone')
      .options].map((o) => o.value));
    assert.equal(presetOptions.includes('Europe/Paris'), false);
    assert.equal(await f.inputValue('#task-schedule-timezone'), 'Asia/Tokyo');
    await cancelDialog(f);
    f = await openEdit(page, f, taskB.id);
    assert.equal(await f.inputValue('#task-schedule-timezone'), 'Europe/Paris');

    // 6. 无计划隐藏时间与时区控件并禁用。
    await f.locator('#task-schedule-none').evaluate((el) => {
      el.checked = true;
      el.dispatchEvent(new Event('change', { bubbles: true }));
    });
    assert.equal(await f.locator('#task-time-field').isHidden(), true);
    assert.equal(await f.locator('#task-schedule-hour').isDisabled(), true);
    assert.equal(await f.locator('#task-schedule-minute').isDisabled(), true);
    assert.equal(await f.locator('#task-schedule-timezone').isDisabled(), true);
    await cancelDialog(f);

    // 桌面截图留档：计划时间与时区控件。
    f = await openEdit(page, f, taskB.id);
    await f.locator('#task-time-field').scrollIntoViewIfNeeded();
    await page.waitForTimeout(200);
    await page.screenshot({ path: '/tmp/time-timezone-desktop.png', fullPage: false });
    await cancelDialog(f);

    // 7. 390px：小时/分钟并排、44px 点击高度、无横向溢出、可见标签。
    const mobile = await browser.newPage({ viewport: { width: 390, height: 900 } });
    let mf = await loginPage(mobile);
    await closeHostPrompts(mobile);
    await mf.locator(`#tasks-list li[data-task-id="${taskA.id}"]`)
      .getByRole('button', { name: /编辑推送任务/ }).click();
    await mf.waitForSelector('#task-dialog[open]');
    const layout = await mf.evaluate(() => {
      const hour = document.getElementById('task-schedule-hour');
      const minute = document.getElementById('task-schedule-minute');
      const hb = hour.getBoundingClientRect();
      const mb = minute.getBoundingClientRect();
      const label = hour.closest('label')?.querySelector('span')?.textContent || '';
      const minuteLabel = minute.closest('label')?.querySelector('span')?.textContent || '';
      const dialog = document.getElementById('task-dialog');
      return {
        sameRow: Math.abs(hb.top - mb.top) < 1 && hb.right <= mb.left,
        hourHeight: Math.round(hb.height),
        minuteHeight: Math.round(mb.height),
        labels: [label.trim(), minuteLabel.trim()],
        overflow: document.documentElement.scrollWidth > document.documentElement.clientWidth ||
          dialog.scrollWidth > dialog.clientWidth,
      };
    });
    assert.equal(layout.sameRow, true, JSON.stringify(layout));
    assert.ok(layout.hourHeight >= 44, JSON.stringify(layout));
    assert.ok(layout.minuteHeight >= 44, JSON.stringify(layout));
    assert.deepEqual(layout.labels, ['小时', '分钟']);
    assert.equal(layout.overflow, false, JSON.stringify(layout));
    await mf.locator('#task-time-field').scrollIntoViewIfNeeded();
    await mobile.waitForTimeout(200);
    await mobile.screenshot({ path: '/tmp/time-timezone-390.png', fullPage: false });
    await mobile.close();

    console.log('TIME TIMEZONE UI PASS', {
      keyboardSave: '23:59 Asia/Tokyo',
      midnight: '00:00',
      legacyTimezone: 'Europe/Paris',
      layout,
    });
  } finally {
    for (const id of createdTaskIds) {
      try {
        const t = await api('GET', `/api/v1/plugins/extensions/${P}/tasks/${id}`);
        if (t.status === 200) {
          await api('POST', `/api/v1/plugins/extensions/${P}/tasks/${id}/delete`, {
            expected_revision: t.body.data.revision,
          });
        }
      } catch {}
    }
    if (siteId && token) {
      try {
        await api('POST', `/api/v1/plugins/extensions/${P}/sites/${siteId}/delete`, {
          expected_revision: 1,
        });
      } catch {}
    }
    if (typeof page.unrouteAll === 'function') await page.unrouteAll({ behavior: 'wait' });
    await browser.close();
  }
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
