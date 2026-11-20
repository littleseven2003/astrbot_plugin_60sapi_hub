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
  // 同一任务先失败后重试成功的，只保留最终结果：重试成功即视为已修复，不再计入报告。
  const openFailures = new Map();
  const settle = () => problems.filter((item) => !item.remediated);
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
    return settle();
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
        const failure = { step: request, detail: `status=${res.status} body=${brief(res.body)}` };
        openFailures.set(task.id, failure);
        problems.push(failure);
      } else {
        const prior = openFailures.get(task.id);
        if (prior) {
          prior.remediated = true;
          openFailures.delete(task.id);
        }
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
    return settle();
  }
  // 第一步：按追踪到的任务 ID 删除。改名不改变 id，这条路径不依赖名称。
  for (const task of first.tasks.filter((item) => item && tracked.has(item.id))) {
    await deleteTask(task, '按 ID 清理本轮任务');
  }

  // 第二步：重新列一次（revision 会随写操作变化），把仍然存在的本轮任务全部再删一遍：
  // 既覆盖 UI 创建成功但没取回 id 的历史情况，也给第一步的 409/瞬态失败一次机会。
  // revision 取自这次紧邻的 GET /tasks，重试不依赖任何陈旧版本号；
  // 按 ID 追踪到的任务即使已被改名成前缀之外的异常名字，也在这里被重新列出并删除。
  const named = parseTasks(await listTasks());
  if (named.error) {
    note('重试前复核任务列表（GET /tasks）', named.error);
    note('重试清理本轮任务（POST /tasks/:id/delete）',
      '已跳过：无法读取任务列表');
  } else {
    for (const task of named.tasks.filter(isRoundTask)) {
      await deleteTask(task, tracked.has(task.id)
        ? '按 ID 重试清理本轮任务'
        : '按名称兜底清扫本轮任务');
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
  if (!roundSiteId) return settle();
  if (!tasksCleared) {
    note('删除本轮自建站点（POST /sites/:id/delete）',
      '已跳过：任务尚未确认清理，站点被引用时删除会失败');
    return settle();
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
  return settle();
}

const ep = (suffix) => `/api/v1/plugins/extensions/${P}${suffix}`;
const briefJson = (value) => {
  let text;
  try { text = JSON.stringify(value); } catch { text = String(value); }
  if (text === undefined) return 'undefined';
  return text.length > 400 ? text.slice(0, 400) + '…' : text;
};

/* 清理前拍快照：用户既有数据的 name/revision，用作“清理不得改动用户数据”的对照。 */
async function snapshotUserData(token, taskId, siteId) {
  const snapshot = { taskId, siteId, task: null, site: null, problems: [] };
  if (!token) return snapshot;
  for (const [key, kind, id, label] of [
    ['task', 'tasks', taskId, '任务'], ['site', 'sites', siteId, '站点'],
  ]) {
    if (!id) continue;
    try {
      const res = await api(token, 'GET', ep(`/${kind}/${id}`));
      if (res.status !== 200 || !res.body || !res.body.data) {
        snapshot.problems.push({
          step: `清理前读取用户既有${label}（GET /${kind}/:id）`,
          detail: `status=${res.status} body=${briefJson(res.body)}`,
        });
        continue;
      }
      snapshot[key] = { revision: res.body.data.revision, name: res.body.data.name };
    } catch (error) {
      snapshot.problems.push({
        step: `清理前读取用户既有${label}（GET /${kind}/:id）`,
        detail: '请求异常：' + String(error && error.message || error),
      });
    }
  }
  return snapshot;
}

/*
 * 清理后复核两件事：
 *   1. 本轮任务全部消失——创建成功、改名成功、改名失败（名字已落到本轮前缀之外）三种情况都按 ID 复核；
 *   2. 夹具里的用户任务与用户站点仍在，且 name/revision 未变。
 * 只收集证据，不抛错，由 finally 汇总成报告。
 */
async function verifyRoundCleanup(token, roundRoles, userSnapshot) {
  const problems = [];
  const note = (step, detail) => problems.push({ step, detail });
  if (!token) return problems;
  for (const [role, entry] of Object.entries(roundRoles || {})) {
    for (const id of (entry && entry.ids) || []) {
      if (!id) continue;
      try {
        const res = await api(token, 'GET', ep(`/tasks/${id}`));
        if (res.status !== 404) {
          note(`复核本轮任务已删除（${role}，GET /tasks/:id）`,
            `任务 ${id} 仍然存在：status=${res.status} body=${briefJson(res.body)}`);
        }
      } catch (error) {
        note(`复核本轮任务已删除（${role}，GET /tasks/:id）`,
          '请求异常：' + String(error && error.message || error));
      }
    }
  }
  for (const [kind, id, expected, label] of [
    ['tasks', userSnapshot.taskId, userSnapshot.task, '任务'],
    ['sites', userSnapshot.siteId, userSnapshot.site, '站点'],
  ]) {
    if (!id || !expected) continue;
    try {
      const res = await api(token, 'GET', ep(`/${kind}/${id}`));
      if (res.status !== 200) {
        note(`复核用户既有${label}未被删除（GET /${kind}/:id）`,
          `用户数据被清理动了：status=${res.status} body=${briefJson(res.body)}`);
        continue;
      }
      const data = res.body.data;
      if (data.revision !== expected.revision || data.name !== expected.name) {
        note(`复核用户既有${label}未被改动（GET /${kind}/:id）`,
          `期望 name=${expected.name} revision=${expected.revision}，`
          + `实际 name=${briefJson(data.name)} revision=${briefJson(data.revision)}`);
      }
    } catch (error) {
      note(`复核用户既有${label}（GET /${kind}/:id）`,
        '请求异常：' + String(error && error.message || error));
    }
  }
  return problems;
}

/* 显式删除本脚本预置的夹具数据：先证明清理没动过它，再由脚本自己收尾。 */
async function deleteFixture(token, kind, id, label) {
  const problems = [];
  if (!token || !id) return problems;
  try {
    const current = await api(token, 'GET', ep(`/${kind}/${id}`));
    if (current.status === 404) return problems;
    if (current.status !== 200 || !current.body || !current.body.data) {
      problems.push({
        step: `删除夹具${label}（GET /${kind}/:id）`,
        detail: `status=${current.status} body=${briefJson(current.body)}`,
      });
      return problems;
    }
    const res = await api(token, 'POST', ep(`/${kind}/${id}/delete`),
      { expected_revision: current.body.data.revision });
    if (res.status !== 200 || !res.body || res.body.status !== 'ok') {
      problems.push({
        step: `删除夹具${label}（POST /${kind}/:id/delete）`,
        detail: `status=${res.status} body=${briefJson(res.body)}`,
      });
    }
  } catch (error) {
    problems.push({
      step: `删除夹具${label}`,
      detail: '请求异常：' + String(error && error.message || error),
    });
  }
  return problems;
}

const BASE=process.env.ASTRBOT_URL,PW=process.env.ASTRBOT_PASSWORD,P=process.env.ASTRBOT_PLUGIN||'astrbot_plugin_60sapi_hub',CHROME=process.env.CHROME_PATH;if(require.main===module&&(!BASE||!PW||!CHROME)){console.error('ASTRBOT_URL, ASTRBOT_PASSWORD and CHROME_PATH are required');process.exit(2);}
module.exports={cleanupRound,roundNameMatches,snapshotUserData,verifyRoundCleanup,deleteFixture};
async function api(t,m,p,d){const r=await fetch(BASE+p,{method:m,headers:{'Content-Type':'application/json',Authorization:`Bearer ${t}`},body:d?JSON.stringify(d):undefined});return {status:r.status,body:await r.json()};}
if(require.main===module)(async()=>{const roundTaskIds=new Set();async function syncRoundTaskIds(focus){try{const res=await api(token,'GET',`/api/v1/plugins/extensions/${P}/tasks`);const tasks=res&&res.body&&res.body.data&&res.body.data.tasks;if(!Array.isArray(tasks))return null;for(const t of tasks){if(t&&t.id&&(roundTaskIds.has(t.id)||roundNameMatches(t.name,names)))roundTaskIds.add(t.id);}if(focus){const hit=tasks.find(t=>t&&t.name===focus);if(hit&&hit.id)roundTaskIds.add(hit.id);}return tasks;}catch{return null;}}const b=await chromium.launch({headless:true,executablePath:CHROME,args:['--no-sandbox']});const page=await b.newPage({viewport:{width:1400,height:1200}});const names=[];const roundRoles={created:{ids:[]},renamed:{ids:[]},renameFailed:{ids:[]}};const user={siteId:null,taskId:null};let userSnapshot={taskId:null,siteId:null,task:null,site:null,problems:[]};let token;let mainOk=false;try{const lr=await fetch(`${BASE}/api/v1/auth/login`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:'astrbot',password:PW})});token=(await lr.json()).data.token;/*
 * 夹具：预置一个“用户既有站点”和一个“用户既有任务”，两者名字都刻意不匹配本轮前缀，
 * 本轮所有任务都挂在这个既有站点上（= 后台“使用已有站点”路径）。
 * 因此清理调用 cleanupRound(token, names, null, roundTaskIds)：只允许按 ID 清本轮任务，
 * 用户既有站点与任务必须在清理后仍然存在（finally 里 verifyRoundCleanup 复核）。
 */
const stamp=Date.now();
const siteRes=await api(token,'POST',ep('/sites'),{name:'用户既有站点'+stamp,base_url:'http://example.test/proxy',enabled:true,timeout_seconds:5});
if(siteRes.status!==201||!siteRes.body||!siteRes.body.data)throw new Error('无法创建夹具用户站点，宿主返回 '+siteRes.status+' body='+briefJson(siteRes.body));
user.siteId=siteRes.body.data.id;
const userTaskName='用户既有任务'+stamp;
const userTaskRes=await api(token,'POST',ep('/tasks'),{name:userTaskName,items:[{content_type:'daily_news'}],sites:[user.siteId],output_format:'text',umos:[],schedule:{kind:'daily',timezone:'Asia/Shanghai',time:'07:15'}});
if(userTaskRes.status!==201||!userTaskRes.body||!userTaskRes.body.data)throw new Error('无法创建夹具用户任务，宿主返回 '+userTaskRes.status+' body='+briefJson(userTaskRes.body));
user.taskId=userTaskRes.body.data.id;
assert.equal(roundNameMatches(userTaskName,names),false);
const sid=user.siteId;
await page.goto(BASE+'/',{waitUntil:'domcontentloaded'});await page.fill('input[name=username]','astrbot');await page.fill('input[name=password]',PW);await Promise.all([page.waitForURL(u=>!u.toString().includes('/auth/login')),page.getByRole('button',{name:'登录',exact:true}).click()]);await closeHostFirstPrompt(page, PW);
await page.goto(`${BASE}/#/plugin-view/${P}/sites`,{waitUntil:'domcontentloaded'});await page.waitForTimeout(500);const clear=async()=>closeHostPrompts(page);let f=await frameOfPage(page);f=await showView(f,'tasks');await f.waitForFunction(()=>document.getElementById('tasks-list')?.getAttribute('aria-busy')==='false');
const card=n=>f.locator('#tasks-list li').filter({hasText:n});let lastUpdateBody=null;page.on('request',r=>{if(r.method()==='POST'&&/\/tasks\/[^/]+\/update$/.test(new URL(r.url()).pathname)){try{lastUpdateBody=JSON.parse(r.postData());}catch{lastUpdateBody=null;}}});
async function dialogSnapshot(){return f.evaluate(()=>({taskDialogOpen:Boolean(document.getElementById('task-dialog')?.open),discardDialogOpen:Boolean(document.getElementById('discard-dialog')?.open),tasksBusy:document.getElementById('tasks-list')?.getAttribute('aria-busy'),})).catch((error)=>({error:String(error&&error.message||error)}));}async function clickUntil(label,action,expect,budgetMs){const budget=budgetMs||30000;const deadline=Date.now()+budget;let attempts=0;let lastError=null;while(Date.now()<deadline){attempts+=1;try{await action();}catch(error){lastError=error;}try{await expect(Math.max(1000,Math.min(4000,deadline-Date.now())));return attempts;}catch(error){lastError=error;if(Date.now()>=deadline)break;await page.waitForTimeout(200);}}throw new Error(label+' 在 '+budget+'ms 内未达成（尝试 '+attempts+' 次）：'+(lastError?lastError.message:'未知错误')+'；实际状态 '+JSON.stringify(await dialogSnapshot()));}async function waitTaskDialogOpen(timeout){await f.waitForSelector('#task-dialog[open]',{timeout});}async function waitTaskDialogClosed(timeout){await f.waitForFunction(()=>!document.getElementById('task-dialog')?.open,null,{timeout});}async function openCreate(){await clear();f=await showView(f,'tasks');await clickUntil('打开新增任务对话框',()=>f.locator('#create-task-button').click({timeout:5000}),waitTaskDialogOpen,30000);}
async function openEdit(n){await clear();f=await showView(f,'tasks');await clickUntil('打开编辑任务对话框（'+n+'）',()=>card(n).getByRole('button',{name:`编辑推送任务 ${n}`}).click({timeout:5000}),waitTaskDialogOpen,30000);}
const scheduleModeRadio={daily:'#task-schedule-daily',none:'#task-schedule-none'};
// 计划方式走真实点击：点可见 label（radio 的真实命中区），再显式等待 radio 选中与计划时间框可用性，
// 不再在 evaluate 里改 checked 再派发合成 change 事件绕过用户操作路径。
async function mode(m){const sel=scheduleModeRadio[m];await clickUntil('选择计划方式（'+m+'）',()=>f.locator('label').filter({has:f.locator(sel)}).click({timeout:5000}),async budget=>{await f.waitForFunction(([target,blocked])=>{const radio=document.querySelector(target);const time=document.getElementById('task-schedule-hour');return Boolean(radio&&radio.checked&&time)&&time.disabled===blocked;},[sel,m==='none'],{timeout:budget});},30000);}
async function setTime(h,m){await f.selectOption('#task-schedule-hour',h);await f.selectOption('#task-schedule-minute',m);}
async function save(){const resp=page.waitForResponse(r=>r.request().method()==='GET'&&r.url().endsWith('/tasks'),{timeout:30000}).catch(()=>null);await clickUntil('保存任务',()=>f.locator('#task-save-button').click({timeout:5000}),waitTaskDialogClosed,30000);const refreshed=await resp;if(!refreshed)throw new Error('保存任务后未观察到任务列表刷新请求');}
async function close(){await clear();await clickUntil('关闭任务对话框',async()=>{const discardOpen=await f.evaluate(()=>Boolean(document.getElementById('discard-dialog')?.open));if(discardOpen){await f.locator('#discard-confirm-button').click({timeout:5000});}else{await f.locator('#task-cancel-button').click({timeout:5000});}},waitTaskDialogClosed,30000);}
// 1 invalid time -> none -> save succeeds
const n1='校验无计划'+Date.now();names.push(n1);await openCreate();await f.fill('#task-name',n1);await f.selectOption('#task-item-picker','daily_news');await f.locator('#task-item-add-button').click();await f.selectOption('#task-site-picker',sid);await f.locator('#task-site-add-button').click();await mode('daily');await setTime('08','30');await mode('none');assert.equal(await f.locator('#task-schedule-hour').isDisabled(),true);await save();await f.waitForFunction(n=>document.getElementById('tasks-list')?.innerText.includes(n),n1);const n1Id=((await syncRoundTaskIds(n1))||[]).find(t=>t&&t.name===n1)?.id;roundRoles.created.ids.push(n1Id);assert.match(await card(n1).innerText(),/无计划/);
// 2 abnormal fixture: name-only omitted and preserved; 3 clear
const n2='校验异常计划'+Date.now();names.push(n2);const c=await api(token,'POST',`/api/v1/plugins/extensions/${P}/tasks`,{name:n2,items:[{content_type:'daily_news'}],sites:[sid],output_format:'text',umos:[],schedule:{kind:'daily',timezone:'Asia/Shanghai',time:'08:30'}});const id=c.body.data.id;if(id){roundTaskIds.add(id);roundRoles.created.ids.push(id);}
await page.route('**/tasks',async r=>{if(r.request().method()!=='GET')return r.continue();const resp=await r.fetch();const j=await resp.json();const item=j.data.tasks.find(t=>t.id===id);if(item){item.schedule=null;item.schedule_raw={kind:'daily',timezone:'Asia/Shanghai',time:'99:99'};item.schedule_error='schedule_invalid';item.schedule_preview={evaluated_at:'2026-10-31T00:00:00+00:00',next_run_at:null,error:'schedule_invalid'};}await r.fulfill({response:resp,json:j});});
await page.reload({waitUntil:'domcontentloaded'});await page.waitForTimeout(500);await clear();f=await frameOfPage(page);f=await showView(f,'tasks');await f.waitForFunction(n=>document.getElementById('tasks-list')?.innerText.includes(n),n2);await openEdit(n2);assert.equal(await f.locator('#task-schedule-error').isVisible(),true);await f.fill('#task-name',n2+'改');lastUpdateBody=null;await save();await syncRoundTaskIds(n2+'改');roundRoles.renamed.ids.push(id);assert.equal(Object.prototype.hasOwnProperty.call(lastUpdateBody,'schedule'),false);
let db=await api(token,'GET',`/api/v1/plugins/extensions/${P}/tasks/${id}`);assert.equal(db.body.data.schedule.time,'08:30');assert.equal(db.body.data.schedule_error,null);
await openEdit(n2+'改');await mode('none');lastUpdateBody=null;await save();assert.equal(lastUpdateBody.schedule,null);db=await api(token,'GET',`/api/v1/plugins/extensions/${P}/tasks/${id}`);assert.equal(db.body.data.schedule,null);await page.unroute('**/tasks');
// 3b explicitly fix the formerly abnormal task to a valid daily plan
await page.reload({waitUntil:'domcontentloaded'});await page.waitForTimeout(500);await clear();f=await frameOfPage(page);f=await showView(f,'tasks');await f.waitForFunction(n=>document.getElementById('tasks-list')?.innerText.includes(n),n2+'改');await openEdit(n2+'改');await mode('daily');await setTime('08','45');await f.selectOption('#task-schedule-timezone','Asia/Shanghai');lastUpdateBody=null;await save();assert.deepEqual(lastUpdateBody.schedule,{kind:'daily',timezone:'Asia/Shanghai',time:'08:45'});db=await api(token,'GET',`/api/v1/plugins/extensions/${P}/tasks/${id}`);assert.equal(db.body.data.schedule.time,'08:45');
// 4 normal create/edit/cancel/reopen
await page.reload({waitUntil:'domcontentloaded'});await page.waitForTimeout(500);await clear();f=await frameOfPage(page);f=await showView(f,'tasks');await f.waitForFunction(()=>document.getElementById('tasks-list')?.getAttribute('aria-busy')==='false');
const n3='校验正常计划'+Date.now();names.push(n3);await openCreate();await f.fill('#task-name',n3);await f.selectOption('#task-item-picker','daily_news');await f.locator('#task-item-add-button').click();await f.selectOption('#task-site-picker',sid);await f.locator('#task-site-add-button').click();await mode('daily');await setTime('08','00');await f.selectOption('#task-schedule-timezone','Asia/Shanghai');await save();await f.waitForFunction(n=>document.getElementById('tasks-list')?.innerText.includes(n),n3);const n3Id=((await syncRoundTaskIds(n3))||[]).find(t=>t&&t.name===n3)?.id;roundRoles.created.ids.push(n3Id);await openEdit(n3);await setTime('09','00');await close();await openEdit(n3);assert.equal(await f.inputValue('#task-schedule-hour'),'08');assert.equal(await f.inputValue('#task-schedule-minute'),'00');await close();
// 5 改名失败：外部先把任务改名到一个落在本轮所有前缀之外的名字，UI 保存必得 409；
//   该任务此后无法被前缀匹配扫到，清理只能按 ID 删——这正是 roundRoles.renameFailed 要证明的。
await openEdit(n3);
const conflictName='冲突改名'+stamp;
const curTask=await api(token,'GET',ep(`/tasks/${n3Id}`));
if(curTask.status!==200||!curTask.body||!curTask.body.data)throw new Error('改名失败场景读取任务失败，宿主返回 '+curTask.status+' body='+briefJson(curTask.body));
const conflictRes=await api(token,'POST',ep(`/tasks/${n3Id}/update`),{name:conflictName,items:[{content_type:'daily_news'}],sites:[sid],output_format:'text',umos:[],expected_revision:curTask.body.data.revision});
if(conflictRes.status!==200)throw new Error('改名失败场景的外部改名未成功，宿主返回 '+conflictRes.status+' body='+briefJson(conflictRes.body));
roundRoles.renameFailed.ids.push(n3Id);
await setTime('09','30');
await clickUntil('保存任务（外部改名导致版本冲突）',()=>f.locator('#task-save-button').click({timeout:5000}),waitTaskDialogClosed,30000);
await f.waitForFunction(()=>{const s=document.getElementById('status');return Boolean(s)&&!s.hidden&&s.innerText.includes('已被修改或删除');},{timeout:15000});
const conflictState=await f.evaluate(()=>{const s=document.getElementById('status');return {status:s?s.innerText:'',dialogOpen:Boolean(document.getElementById('task-dialog')?.open)};});
assert.equal(conflictState.dialogOpen,false);
assert.match(conflictState.status,/已被修改或删除/);
const afterConflict=await api(token,'GET',ep(`/tasks/${n3Id}`));
assert.equal(afterConflict.status,200);
assert.equal(afterConflict.body.data.name,conflictName);
assert.equal(roundNameMatches(afterConflict.body.data.name,names),false);
assert.equal(roundTaskIds.has(n3Id),true);
assert.equal(await f.locator(`#tasks-list li[data-task-id="${n3Id}"]`).count(),1);
mainOk=true;
}finally{
  let problems=[];
  // 先给用户既有数据拍快照，清理之后才谈得上“没被改动”。
  try{userSnapshot=await snapshotUserData(token,user.taskId,user.siteId);problems=problems.concat(userSnapshot.problems);}catch(error){problems.push({step:'清理前读取用户既有数据',detail:'读取异常：'+String(error&&error.message||error)});}
  try{problems=problems.concat(await cleanupRound(token,names,null,roundTaskIds));}catch(error){problems.push({step:'清理本轮测试数据',detail:'清理过程异常：'+String(error&&error.message||error)});}
  if(token){
    try{problems=problems.concat(await verifyRoundCleanup(token,roundRoles,userSnapshot));}catch(error){problems.push({step:'复核清理结果',detail:'复核异常：'+String(error&&error.message||error)});}
  }
  try{await b.close();}catch(error){problems.push({step:'关闭浏览器',detail:String(error&&error.message||error)});}
  // 夹具由脚本自己收尾：先证明清理没动过它，再显式删掉。
  try{problems=problems.concat(await deleteFixture(token,'tasks',user.taskId,'用户既有任务'));}catch(error){problems.push({step:'删除夹具用户任务',detail:'删除异常：'+String(error&&error.message||error)});}
  try{problems=problems.concat(await deleteFixture(token,'sites',user.siteId,'用户既有站点'));}catch(error){problems.push({step:'删除夹具用户站点',detail:'删除异常：'+String(error&&error.message||error)});}
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
