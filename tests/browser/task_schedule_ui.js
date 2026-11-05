const assert=require('assert/strict');const {chromium}=require('playwright-core');/*
 * Real Dashboard schedule UI regression. Requires an isolated AstrBot host.
 * Env: ASTRBOT_URL, ASTRBOT_PASSWORD, ASTRBOT_PLUGIN, CHROME_PATH.
 * All task writes use the real host API; only the abnormal saved-plan display
 * and its schedule_error are a controlled route fixture. No delivery request
 * is made. Screenshots/logs go outside the repository.
 */

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

async function frameOfPage(page) {
  const iframe = page.locator("iframe.plugin-view-frame").first();
  await iframe.waitFor({ state: "attached", timeout: 15000 });
  for (let i = 0; i < 60; i += 1) {
    let frame = null;
    try {
      frame = await (await iframe.elementHandle()).contentFrame();
    } catch { /* iframe 正在重建 */ }
    if (frame) {
      try {
        await frame.waitForFunction(
          () => document.getElementById("sites-list")?.getAttribute("aria-busy") === "false",
          null, { timeout: 2000 },
        );
        return frame;
      } catch { /* 尚未完成引导 */ }
    }
    await page.waitForTimeout(250);
  }
  throw new Error("插件 iframe 在等待窗口内未就绪");
}

async function showView(f, view) {
  await f.waitForFunction(
    () => document.getElementById("sites-list")?.getAttribute("aria-busy") === "false",
    null, { timeout: 15000 },
  );
  const alreadyVisible = await f.evaluate((name) => {
    const target = document.getElementById("view-" + name);
    return Boolean(target) && !target.hidden;
  }, view);
  if (!alreadyVisible) {
    await f.locator('.nav-item[data-view="' + view + '"]').click();
  }
  await f.waitForFunction((name) => {
    const target = document.getElementById("view-" + name);
    return Boolean(target) && !target.hidden;
  }, view, { timeout: 15000 });
}

const BASE=process.env.ASTRBOT_URL,PW=process.env.ASTRBOT_PASSWORD,P=process.env.ASTRBOT_PLUGIN||'astrbot_plugin_60sapi_hub',CHROME=process.env.CHROME_PATH;if(!BASE||!PW||!CHROME){console.error('ASTRBOT_URL, ASTRBOT_PASSWORD and CHROME_PATH are required');process.exit(2);}
async function api(t,m,p,d){const r=await fetch(BASE+p,{method:m,headers:{'Content-Type':'application/json',Authorization:`Bearer ${t}`},body:d?JSON.stringify(d):undefined});return {status:r.status,body:await r.json()};}
(async()=>{const b=await chromium.launch({headless:true,executablePath:CHROME,args:['--no-sandbox']});const page=await b.newPage({viewport:{width:1400,height:1200}});const names=[];let token,seedSiteId=null,seedProfileId=null;try{const lr=await fetch(`${BASE}/api/v1/auth/login`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:'astrbot',password:PW})});token=(await lr.json()).data.token;const all=await api(token,'GET',`/api/v1/plugins/extensions/${P}/tasks`);let pid=all.body.data.tasks[0]?all.body.data.tasks[0].profile_id:null;if(!pid){const profs=(await api(token,'GET',`/api/v1/plugins/extensions/${P}/profiles`)).body.data.profiles||[];if(profs.length){pid=profs[0].id;}else{const st=Date.now();const site=await api(token,'POST',`/api/v1/plugins/extensions/${P}/sites`,{name:'任务前置站点'+st,base_url:'http://example.test/proxy',enabled:true,timeout_seconds:5});if(site.status!==201||!site.body.data)throw new Error('无法创建任务前置站点，宿主返回 '+site.status);seedSiteId=site.body.data.id;const prof=await api(token,'POST',`/api/v1/plugins/extensions/${P}/profiles`,{name:'任务前置组合'+st,site_id:seedSiteId,items:[{content_type:'daily_news',params:{},limit:null}]});if(prof.status!==201||!prof.body.data)throw new Error('无法创建任务前置组合，宿主返回 '+prof.status);seedProfileId=prof.body.data.id;pid=seedProfileId;}}
await page.goto(BASE+'/',{waitUntil:'domcontentloaded'});await page.fill('input[name=username]','astrbot');await page.fill('input[name=password]',PW);await Promise.all([page.waitForURL(u=>!u.toString().includes('/auth/login')),page.getByRole('button',{name:'登录',exact:true}).click()]);await closeHostFirstPrompt(page, PW);
await page.goto(`${BASE}/#/plugin-view/${P}/sites`,{waitUntil:'domcontentloaded'});await page.waitForTimeout(500);const clear=async()=>closeHostPrompts(page);const ifr=page.locator('iframe').first();let f=await frameOfPage(page);await showView(f,'tasks');await f.waitForFunction(()=>document.getElementById('tasks-list')?.getAttribute('aria-busy')==='false');
const card=n=>f.locator('#tasks-list li').filter({hasText:n});let lastUpdateBody=null;page.on('request',r=>{if(r.method()==='POST'&&/\/tasks\/[^/]+\/update$/.test(new URL(r.url()).pathname)){try{lastUpdateBody=JSON.parse(r.postData());}catch{lastUpdateBody=null;}}});
async function openCreate(){await clear();await showView(f,'tasks');await f.locator('#create-task-button').click();await f.waitForSelector('#task-dialog[open]');}
async function openEdit(n){await clear();await showView(f,'tasks');await card(n).getByRole('button',{name:`编辑推送任务 ${n}`}).click();await f.waitForSelector('#task-dialog[open]');}
async function mode(m){await f.locator(m==='daily'?'#task-schedule-daily':'#task-schedule-none').evaluate(el=>{el.checked=true;el.dispatchEvent(new Event('change',{bubbles:true}));});}
async function save(){const resp=page.waitForResponse(r=>r.request().method()==='GET'&&r.url().endsWith('/tasks'));await f.locator('#task-save-button').click();await f.waitForFunction(()=>!document.getElementById('task-dialog')?.open);await resp;}
async function close(){await clear();await f.locator('#task-cancel-button').click();await f.waitForFunction(()=>!document.getElementById('task-dialog')?.open);}
// 1 invalid time -> none -> save succeeds
const n1='校验无计划'+Date.now();names.push(n1);await openCreate();await f.fill('#task-name',n1);await f.selectOption('#task-profile',{index:0});await mode('daily');await f.fill('#task-schedule-time','99:99');await mode('none');assert.equal(await f.locator('#task-schedule-time').isDisabled(),true);await save();await f.waitForFunction(n=>document.getElementById('tasks-list')?.innerText.includes(n),n1);assert.match(await card(n1).innerText(),/无计划/);
// 2 abnormal fixture: name-only omitted and preserved; 3 clear
const n2='校验异常计划'+Date.now();names.push(n2);const c=await api(token,'POST',`/api/v1/plugins/extensions/${P}/tasks`,{name:n2,profile_id:pid,output_format:'text',umos:[],schedule:{kind:'daily',timezone:'Asia/Shanghai',time:'08:30'}});const id=c.body.data.id;
await page.route('**/tasks',async r=>{if(r.request().method()!=='GET')return r.continue();const resp=await r.fetch();const j=await resp.json();const item=j.data.tasks.find(t=>t.id===id);if(item){item.schedule=null;item.schedule_raw={kind:'daily',timezone:'Asia/Shanghai',time:'99:99'};item.schedule_error='schedule_invalid';item.schedule_preview={evaluated_at:'2026-10-31T00:00:00+00:00',next_run_at:null,error:'schedule_invalid'};}await r.fulfill({response:resp,json:j});});
await page.reload({waitUntil:'domcontentloaded'});await page.waitForTimeout(500);await clear();f=page.frames().find(x=>x!==page.mainFrame()&&x.url().includes('/views/sites/'));if(!f)f=await (await ifr.elementHandle()).contentFrame();await showView(f,'tasks');await f.waitForFunction(n=>document.getElementById('tasks-list')?.innerText.includes(n),n2);await openEdit(n2);assert.equal(await f.locator('#task-schedule-error').isVisible(),true);await f.fill('#task-name',n2+'改');names[1]=n2+'改';lastUpdateBody=null;await save();assert.equal(Object.prototype.hasOwnProperty.call(lastUpdateBody,'schedule'),false);
let db=await api(token,'GET',`/api/v1/plugins/extensions/${P}/tasks/${id}`);assert.equal(db.body.data.schedule.time,'08:30');assert.equal(db.body.data.schedule_error,null);
await openEdit(n2+'改');await mode('none');lastUpdateBody=null;await save();assert.equal(lastUpdateBody.schedule,null);db=await api(token,'GET',`/api/v1/plugins/extensions/${P}/tasks/${id}`);assert.equal(db.body.data.schedule,null);await page.unroute('**/tasks');
// 3b explicitly fix the formerly abnormal task to a valid daily plan
await page.reload({waitUntil:'domcontentloaded'});await page.waitForTimeout(500);await clear();f=page.frames().find(x=>x!==page.mainFrame()&&x.url().includes('/views/sites/'));if(!f)f=await (await ifr.elementHandle()).contentFrame();await showView(f,'tasks');await f.waitForFunction(n=>document.getElementById('tasks-list')?.innerText.includes(n),n2+'改');await openEdit(n2+'改');await mode('daily');await f.fill('#task-schedule-time','08:45');await f.fill('#task-schedule-timezone','Asia/Shanghai');lastUpdateBody=null;await save();assert.deepEqual(lastUpdateBody.schedule,{kind:'daily',timezone:'Asia/Shanghai',time:'08:45'});db=await api(token,'GET',`/api/v1/plugins/extensions/${P}/tasks/${id}`);assert.equal(db.body.data.schedule.time,'08:45');
// 4 normal create/edit/cancel/reopen
await page.reload({waitUntil:'domcontentloaded'});await page.waitForTimeout(500);await clear();f=await frameOfPage(page);await showView(f,'tasks');await f.waitForFunction(()=>document.getElementById('tasks-list')?.getAttribute('aria-busy')==='false');
const n3='校验正常计划'+Date.now();names.push(n3);await openCreate();await f.fill('#task-name',n3);await f.selectOption('#task-profile',{index:0});await mode('daily');await f.fill('#task-schedule-time','08:00');await f.fill('#task-schedule-timezone','Asia/Shanghai');await save();await f.waitForFunction(n=>document.getElementById('tasks-list')?.innerText.includes(n),n3);await openEdit(n3);await f.fill('#task-schedule-time','09:00');await close();await openEdit(n3);assert.equal(await f.inputValue('#task-schedule-time'),'08:00');await close();
// 5 revision conflict while modal open: external update bumps revision, then UI save
await openEdit(n3);
const curTask=await api(token,'GET',`/api/v1/plugins/extensions/${P}/tasks/${(await api(token,'GET',`/api/v1/plugins/extensions/${P}/tasks`)).body.data.tasks.find(t=>t.name===n3).id}`);const tid=curTask.body.data.id;await api(token,'POST',`/api/v1/plugins/extensions/${P}/tasks/${tid}/update`,{name:n3+'外部改',profile_id:pid,output_format:'text',umos:[],expected_revision:curTask.body.data.revision});await f.fill('#task-schedule-time','09:30');await f.locator('#task-save-button').click();await f.waitForFunction(()=>!document.getElementById('task-dialog')?.open);await page.waitForTimeout(300);assert.match(await f.locator('#tasks-list').innerText(),new RegExp(n3+'外部改'));console.log('VALIDATION UI PASSED',names);
}finally{try{if(seedProfileId&&token)await api(token,'POST',`/api/v1/plugins/extensions/${P}/profiles/${seedProfileId}/delete`,{expected_revision:1});if(seedSiteId&&token)await api(token,'POST',`/api/v1/plugins/extensions/${P}/sites/${seedSiteId}/delete`,{expected_revision:1});}catch{}await b.close();if(token){const all=await api(token,'GET',`/api/v1/plugins/extensions/${P}/tasks`);for(const t of all.body.data.tasks){if(names.some(n=>t.name===n||t.name===n+'改'||t.name===n+'外部改'))await api(token,'POST',`/api/v1/plugins/extensions/${P}/tasks/${t.id}/delete`,{expected_revision:t.revision});}}}})().catch(e=>{console.error(e);process.exit(1)});
