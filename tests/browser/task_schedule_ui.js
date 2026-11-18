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
  let frame = f;
  const isVisible = (target) => frame.evaluate((name) => {
    const view = document.getElementById("view-" + name);
    return Boolean(view) && !view.hidden;
  }, target);
  if (await isVisible(view)) {
    return frame;
  }
  const deadline = Date.now() + 15000;
  let lastError = null;
  while (Date.now() < deadline) {
    if (typeof frame.isDetached === "function" && frame.isDetached()) {
      frame = await frameOfPage(frame.page());
    }
    try {
      await frame.locator('.nav-item[data-view="' + view + '"]').click({
        timeout: Math.max(1000, Math.min(5000, deadline - Date.now())),
      });
      await frame.waitForFunction((name) => {
        const view = document.getElementById("view-" + name);
        return Boolean(view) && !view.hidden;
      }, view, {
        timeout: Math.max(500, Math.min(5000, deadline - Date.now())),
      });
      return frame;
    } catch (error) {
      lastError = error;
      if (Date.now() >= deadline) break;
      await frame.page().waitForTimeout(200);
    }
  }
  throw new Error(
    "切换到视图 " + view + " 失败：" + (lastError ? lastError.message : "未知错误"),
  );
}

/*
 * 本轮任务的第一标识是任务 ID：改名不会改变 id，名称规则只用于兜底。
 */
function roundNameMatches(name, roundNames) {
  if (typeof name !== 'string' || !name) return false;
  return (roundNames || []).some((base) => typeof base === 'string' && Boolean(base)
    && (name === base || name === base + '改' || name === base + '外部改'));
}

/*
 * 清理本轮测试数据：先按追踪到的任务 ID 逐条删除（用 GET /tasks 的实时 revision），
 * 再按名称规则兜底清扫一次，确认没有本轮任务残留后再删本轮自建站点，
 * 最后复核任务引用与站点是否真的消失。只收集诊断，不抛错，由 finally 汇总输出。
 */
async function cleanupRound(authToken, roundNames, roundSiteId, roundTaskIds) {
  const problems = [];
  const note = (step, detail) => problems.push({ step, detail });
  const brief = (value) => {
    let text;
    try { text = JSON.stringify(value); }
    catch { text = String(value); }
    return text.length > 500 ? text.slice(0, 500) + '…' : text;
  };
  const endpoint = (suffix) => `/api/v1/plugins/extensions/${P}${suffix}`;
  const tracked = new Set(roundTaskIds ? Array.from(roundTaskIds) : []);
  const referencesSite = (task, siteId) => Array.isArray(task && task.sites)
    && task.sites.some((entry) => siteId === (typeof entry === 'string' ? entry : entry && entry.id));
  const isRoundTask = (task) => Boolean(task)
    && (tracked.has(task.id) || roundNameMatches(task.name, roundNames)
      || (roundSiteId ? referencesSite(task, roundSiteId) : false));
  if (!authToken) {
    note('清理登录状态', '缺少 token，无法清理本轮测试数据');
    return problems;
  }
  const listTasks = async () => {
    try { return await api(authToken, 'GET', endpoint('/tasks')); }
    catch (error) { return { error }; }
  };
  const parseTasks = (result) => {
    if (!result || result.error) {
      return { error: '请求异常：' + String(result && result.error && result.error.message
        || result && result.error || '无响应') };
    }
    if (result.status !== 200) return { error: `status=${result.status} body=${brief(result.body)}` };
    const tasks = result.body && result.body.data && result.body.data.tasks;
    if (!Array.isArray(tasks)) return { error: '响应缺少 data.tasks：' + brief(result.body) };
    return { tasks };
  };

  const deleteTask = async (task, reason) => {
    const request = `${reason}：删除任务 ${task.name || '(无名称)'} id=${task.id}（POST /tasks/${task.id}/delete）`;
    try {
      const res = await api(authToken, 'POST', endpoint(`/tasks/${task.id}/delete`),
        { expected_revision: task.revision });
      if (res.status !== 200 || !res.body || res.body.status !== 'ok') {
        note(request, `status=${res.status} body=${brief(res.body)}`);
      }
    } catch (error) {
      note(request, '请求异常：' + String(error && error.message || error));
    }
  };

  const first = parseTasks(await listTasks());
  if (first.error) {
    note('列出本轮任务（GET /tasks）', first.error);
    note('按 ID 清理本轮任务（POST /tasks/:id/delete）',
      '已跳过：无法确认本轮任务列表');
    note('删除本轮自建站点（POST /sites/:id/delete）',
      '已跳过：无法确认引用该站点的任务已经清理');
    return problems;
  }
  // 第一步：按追踪到的任务 ID 删除。改名不改变 id，这条路径不依赖名称。
  for (const task of first.tasks.filter((item) => item && tracked.has(item.id))) {
    await deleteTask(task, '按 ID 清理本轮任务');
  }

  // 第二步：名称规则兜底清扫，覆盖 UI 创建成功但没取回 id 的历史情况。
  // 已按 ID 尝试过的任务不再重试，避免对 revision 冲突做盲目重删。
  const named = parseTasks(await listTasks());
  if (named.error) {
    note('兜底清扫前复核任务列表（GET /tasks）', named.error);
    note('按名称兜底清扫本轮任务（POST /tasks/:id/delete）',
      '已跳过：无法读取任务列表');
  } else {
    for (const task of named.tasks.filter((item) => item
      && !tracked.has(item.id) && isRoundTask(item))) {
      await deleteTask(task, '按名称兜底清扫本轮任务');
    }
  }

  const recheck = parseTasks(await listTasks());
  let tasksCleared = false;
  if (recheck.error) {
    note('复核任务清理（GET /tasks）', recheck.error);
  } else {
    const left = recheck.tasks.filter(isRoundTask);
    if (left.length) {
      note('复核任务清理（GET /tasks）', '仍残留本轮任务：'
        + brief(left.map((task) => ({ id: task.id, name: task.name, sites: task.sites }))));
    } else {
      tasksCleared = true;
    }
  }
  if (!roundSiteId) return problems;
  if (!tasksCleared) {
    note('删除本轮自建站点（POST /sites/:id/delete）',
      '已跳过：任务尚未确认清理，站点被引用时删除会失败');
    return problems;
  }

  let current = null;
  try { current = await api(authToken, 'GET', endpoint(`/sites/${roundSiteId}`)); }
  catch (error) { note('读取本轮自建站点（GET /sites/:id）', '请求异常：' + String(error && error.message || error)); }
  if (current) {
    if (current.status === 200) {
      const request = `删除本轮自建站点（POST /sites/${roundSiteId}/delete）`;
      try {
        const res = await api(authToken, 'POST', endpoint(`/sites/${roundSiteId}/delete`),
          { expected_revision: current.body && current.body.data && current.body.data.revision });
        if (res.status !== 200 || !res.body || res.body.status !== 'ok') {
          note(request, `status=${res.status} body=${brief(res.body)}`);
        }
      } catch (error) {
        note(request, '请求异常：' + String(error && error.message || error));
      }
    } else if (current.status !== 404) {
      note('读取本轮自建站点（GET /sites/:id）', `status=${current.status} body=${brief(current.body)}`);
      note('删除本轮自建站点（POST /sites/:id/delete）', '已跳过：无法读取站点当前 revision');
    }
  }

  try {
    const site = await api(authToken, 'GET', endpoint(`/sites/${roundSiteId}`));
    if (site.status !== 404) {
      note('复核本轮自建站点（GET /sites/:id）', `站点仍然存在：status=${site.status} body=${brief(site.body)}`);
    }
  } catch (error) {
    note('复核本轮自建站点（GET /sites/:id）', '请求异常：' + String(error && error.message || error));
  }
  const finalTasks = parseTasks(await listTasks());
  if (finalTasks.error) {
    note('复核站点引用（GET /tasks）', finalTasks.error);
  } else {
    const holders = finalTasks.tasks.filter((task) => referencesSite(task, roundSiteId));
    if (holders.length) {
      note('复核站点引用（GET /tasks）', '仍有任务引用本轮自建站点：'
        + brief(holders.map((task) => ({ id: task.id, name: task.name }))));
    }
  }
  return problems;
}

const BASE=process.env.ASTRBOT_URL,PW=process.env.ASTRBOT_PASSWORD,P=process.env.ASTRBOT_PLUGIN||'astrbot_plugin_60sapi_hub',CHROME=process.env.CHROME_PATH;if(require.main===module&&(!BASE||!PW||!CHROME)){console.error('ASTRBOT_URL, ASTRBOT_PASSWORD and CHROME_PATH are required');process.exit(2);}
module.exports={cleanupRound,roundNameMatches};
async function api(t,m,p,d){const r=await fetch(BASE+p,{method:m,headers:{'Content-Type':'application/json',Authorization:`Bearer ${t}`},body:d?JSON.stringify(d):undefined});return {status:r.status,body:await r.json()};}
if(require.main===module)(async()=>{const roundTaskIds=new Set();async function syncRoundTaskIds(focus){try{const res=await api(token,'GET',`/api/v1/plugins/extensions/${P}/tasks`);const tasks=res&&res.body&&res.body.data&&res.body.data.tasks;if(!Array.isArray(tasks))return null;for(const t of tasks){if(t&&t.id&&(roundTaskIds.has(t.id)||roundNameMatches(t.name,names)))roundTaskIds.add(t.id);}if(focus){const hit=tasks.find(t=>t&&t.name===focus);if(hit&&hit.id)roundTaskIds.add(hit.id);}return tasks;}catch{return null;}}const b=await chromium.launch({headless:true,executablePath:CHROME,args:['--no-sandbox']});const page=await b.newPage({viewport:{width:1400,height:1200}});const names=[];let token,seedSiteId=null;let mainOk=false;try{const lr=await fetch(`${BASE}/api/v1/auth/login`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:'astrbot',password:PW})});token=(await lr.json()).data.token;const all=await api(token,'GET',`/api/v1/plugins/extensions/${P}/tasks`);let sid=null;const knownSites=(await api(token,'GET',`/api/v1/plugins/extensions/${P}/sites`)).body.data.sites||[];const knownIds=new Set(knownSites.map(s=>s&&s.id));const firstTask=all.body.data.tasks[0];if(firstTask&&Array.isArray(firstTask.sites)&&firstTask.sites.length){const candidate=firstTask.sites[0];const candidateId=typeof candidate==='string'?candidate:(candidate&&candidate.id);if(knownIds.has(candidateId))sid=candidateId;}if(!sid&&knownSites.length){sid=knownSites[0].id;}if(!sid){const st=Date.now();const site=await api(token,'POST',`/api/v1/plugins/extensions/${P}/sites`,{name:'任务前置站点'+st,base_url:'http://example.test/proxy',enabled:true,timeout_seconds:5});if(site.status!==201||!site.body.data)throw new Error('无法创建任务前置站点，宿主返回 '+site.status);seedSiteId=site.body.data.id;sid=seedSiteId;}
await page.goto(BASE+'/',{waitUntil:'domcontentloaded'});await page.fill('input[name=username]','astrbot');await page.fill('input[name=password]',PW);await Promise.all([page.waitForURL(u=>!u.toString().includes('/auth/login')),page.getByRole('button',{name:'登录',exact:true}).click()]);await closeHostFirstPrompt(page, PW);
await page.goto(`${BASE}/#/plugin-view/${P}/sites`,{waitUntil:'domcontentloaded'});await page.waitForTimeout(500);const clear=async()=>closeHostPrompts(page);let f=await frameOfPage(page);f=await showView(f,'tasks');await f.waitForFunction(()=>document.getElementById('tasks-list')?.getAttribute('aria-busy')==='false');
const card=n=>f.locator('#tasks-list li').filter({hasText:n});let lastUpdateBody=null;page.on('request',r=>{if(r.method()==='POST'&&/\/tasks\/[^/]+\/update$/.test(new URL(r.url()).pathname)){try{lastUpdateBody=JSON.parse(r.postData());}catch{lastUpdateBody=null;}}});
async function dialogSnapshot(){return f.evaluate(()=>({taskDialogOpen:Boolean(document.getElementById('task-dialog')?.open),discardDialogOpen:Boolean(document.getElementById('discard-dialog')?.open),tasksBusy:document.getElementById('tasks-list')?.getAttribute('aria-busy'),})).catch((error)=>({error:String(error&&error.message||error)}));}async function clickUntil(label,action,expect,budgetMs){const budget=budgetMs||30000;const deadline=Date.now()+budget;let attempts=0;let lastError=null;while(Date.now()<deadline){attempts+=1;try{await action();}catch(error){lastError=error;}try{await expect(Math.max(1000,Math.min(4000,deadline-Date.now())));return attempts;}catch(error){lastError=error;if(Date.now()>=deadline)break;await page.waitForTimeout(200);}}throw new Error(label+' 在 '+budget+'ms 内未达成（尝试 '+attempts+' 次）：'+(lastError?lastError.message:'未知错误')+'；实际状态 '+JSON.stringify(await dialogSnapshot()));}async function waitTaskDialogOpen(timeout){await f.waitForSelector('#task-dialog[open]',{timeout});}async function waitTaskDialogClosed(timeout){await f.waitForFunction(()=>!document.getElementById('task-dialog')?.open,null,{timeout});}async function openCreate(){await clear();f=await showView(f,'tasks');await clickUntil('打开新增任务对话框',()=>f.locator('#create-task-button').click({timeout:5000}),waitTaskDialogOpen,30000);}
async function openEdit(n){await clear();f=await showView(f,'tasks');await clickUntil('打开编辑任务对话框（'+n+'）',()=>card(n).getByRole('button',{name:`编辑推送任务 ${n}`}).click({timeout:5000}),waitTaskDialogOpen,30000);}
async function mode(m){await f.locator(m==='daily'?'#task-schedule-daily':'#task-schedule-none').evaluate(el=>{el.checked=true;el.dispatchEvent(new Event('change',{bubbles:true}));});}
async function save(){const resp=page.waitForResponse(r=>r.request().method()==='GET'&&r.url().endsWith('/tasks'),{timeout:30000}).catch(()=>null);await clickUntil('保存任务',()=>f.locator('#task-save-button').click({timeout:5000}),waitTaskDialogClosed,30000);const refreshed=await resp;if(!refreshed)throw new Error('保存任务后未观察到任务列表刷新请求');}
async function close(){await clear();await clickUntil('关闭任务对话框',async()=>{const discardOpen=await f.evaluate(()=>Boolean(document.getElementById('discard-dialog')?.open));if(discardOpen){await f.locator('#discard-confirm-button').click({timeout:5000});}else{await f.locator('#task-cancel-button').click({timeout:5000});}},waitTaskDialogClosed,30000);}
// 1 invalid time -> none -> save succeeds
const n1='校验无计划'+Date.now();names.push(n1);await openCreate();await f.fill('#task-name',n1);await f.selectOption('#task-item-picker','daily_news');await f.locator('#task-item-add-button').click();await f.selectOption('#task-site-picker',sid);await f.locator('#task-site-add-button').click();await mode('daily');await f.fill('#task-schedule-time','99:99');await mode('none');assert.equal(await f.locator('#task-schedule-time').isDisabled(),true);await save();await f.waitForFunction(n=>document.getElementById('tasks-list')?.innerText.includes(n),n1);await syncRoundTaskIds(n1);assert.match(await card(n1).innerText(),/无计划/);
// 2 abnormal fixture: name-only omitted and preserved; 3 clear
const n2='校验异常计划'+Date.now();names.push(n2);const c=await api(token,'POST',`/api/v1/plugins/extensions/${P}/tasks`,{name:n2,items:[{content_type:'daily_news'}],sites:[sid],output_format:'text',umos:[],schedule:{kind:'daily',timezone:'Asia/Shanghai',time:'08:30'}});const id=c.body.data.id;if(id)roundTaskIds.add(id);
await page.route('**/tasks',async r=>{if(r.request().method()!=='GET')return r.continue();const resp=await r.fetch();const j=await resp.json();const item=j.data.tasks.find(t=>t.id===id);if(item){item.schedule=null;item.schedule_raw={kind:'daily',timezone:'Asia/Shanghai',time:'99:99'};item.schedule_error='schedule_invalid';item.schedule_preview={evaluated_at:'2026-10-31T00:00:00+00:00',next_run_at:null,error:'schedule_invalid'};}await r.fulfill({response:resp,json:j});});
await page.reload({waitUntil:'domcontentloaded'});await page.waitForTimeout(500);await clear();f=await frameOfPage(page);f=await showView(f,'tasks');await f.waitForFunction(n=>document.getElementById('tasks-list')?.innerText.includes(n),n2);await openEdit(n2);assert.equal(await f.locator('#task-schedule-error').isVisible(),true);await f.fill('#task-name',n2+'改');lastUpdateBody=null;await save();await syncRoundTaskIds(n2+'改');assert.equal(Object.prototype.hasOwnProperty.call(lastUpdateBody,'schedule'),false);
let db=await api(token,'GET',`/api/v1/plugins/extensions/${P}/tasks/${id}`);assert.equal(db.body.data.schedule.time,'08:30');assert.equal(db.body.data.schedule_error,null);
await openEdit(n2+'改');await mode('none');lastUpdateBody=null;await save();assert.equal(lastUpdateBody.schedule,null);db=await api(token,'GET',`/api/v1/plugins/extensions/${P}/tasks/${id}`);assert.equal(db.body.data.schedule,null);await page.unroute('**/tasks');
// 3b explicitly fix the formerly abnormal task to a valid daily plan
await page.reload({waitUntil:'domcontentloaded'});await page.waitForTimeout(500);await clear();f=await frameOfPage(page);f=await showView(f,'tasks');await f.waitForFunction(n=>document.getElementById('tasks-list')?.innerText.includes(n),n2+'改');await openEdit(n2+'改');await mode('daily');await f.fill('#task-schedule-time','08:45');await f.fill('#task-schedule-timezone','Asia/Shanghai');lastUpdateBody=null;await save();assert.deepEqual(lastUpdateBody.schedule,{kind:'daily',timezone:'Asia/Shanghai',time:'08:45'});db=await api(token,'GET',`/api/v1/plugins/extensions/${P}/tasks/${id}`);assert.equal(db.body.data.schedule.time,'08:45');
// 4 normal create/edit/cancel/reopen
await page.reload({waitUntil:'domcontentloaded'});await page.waitForTimeout(500);await clear();f=await frameOfPage(page);f=await showView(f,'tasks');await f.waitForFunction(()=>document.getElementById('tasks-list')?.getAttribute('aria-busy')==='false');
const n3='校验正常计划'+Date.now();names.push(n3);await openCreate();await f.fill('#task-name',n3);await f.selectOption('#task-item-picker','daily_news');await f.locator('#task-item-add-button').click();await f.selectOption('#task-site-picker',sid);await f.locator('#task-site-add-button').click();await mode('daily');await f.fill('#task-schedule-time','08:00');await f.fill('#task-schedule-timezone','Asia/Shanghai');await save();await f.waitForFunction(n=>document.getElementById('tasks-list')?.innerText.includes(n),n3);await syncRoundTaskIds(n3);await openEdit(n3);await f.fill('#task-schedule-time','09:00');await close();await openEdit(n3);assert.equal(await f.inputValue('#task-schedule-time'),'08:00');await close();
// 5 revision conflict while modal open: external update bumps revision, then UI save
await openEdit(n3);
const curTask=await api(token,'GET',`/api/v1/plugins/extensions/${P}/tasks/${(await api(token,'GET',`/api/v1/plugins/extensions/${P}/tasks`)).body.data.tasks.find(t=>t.name===n3).id}`);const tid=curTask.body.data.id;await api(token,'POST',`/api/v1/plugins/extensions/${P}/tasks/${tid}/update`,{name:n3+'外部改',items:[{content_type:'daily_news'}],sites:[sid],output_format:'text',umos:[],expected_revision:curTask.body.data.revision});await f.fill('#task-schedule-time','09:30');await clickUntil('保存任务（外部改版本冲突）',()=>f.locator('#task-save-button').click({timeout:5000}),waitTaskDialogClosed,30000);await page.waitForTimeout(300);await syncRoundTaskIds();assert.match(await f.locator('#tasks-list').innerText(),new RegExp(n3+'外部改'));mainOk=true;
}finally{
  let problems=[];
  try{problems=await cleanupRound(token,names,seedSiteId,roundTaskIds);}catch(error){problems=[{step:'清理本轮测试数据',detail:'清理过程异常：'+String(error&&error.message||error)}];}
  try{await b.close();}catch(error){problems.push({step:'关闭浏览器',detail:String(error&&error.message||error)});}
  if(problems.length){
    process.exitCode=1;
    const report='CLEANUP FAILED：本轮测试数据未清理干净（'+problems.length+' 项）\n'+problems.map(p=>'  - '+p.step+'：'+p.detail).join('\n')+'\n';
    await new Promise(resolve=>process.stderr.write(report,resolve));
    if(mainOk)process.exit(1);
  }else if(mainOk){
    console.log('TASK UI PASS',names);
  }
}
})().catch(e=>{console.error(e);process.exit(1)});
