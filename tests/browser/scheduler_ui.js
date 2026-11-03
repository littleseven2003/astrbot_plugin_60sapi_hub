/*
 * Dashboard scheduler UI regression. Env:
 * ASTRBOT_URL, ASTRBOT_PASSWORD, CHROME_PATH.
 * Real host API creates and removes temporary site/profile/task; scheduling
 * records are route fixtures. No delivery or content preview is invoked.
 */
const assert=require('assert/strict');
const {chromium}=require('playwright-core');

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

const BASE=process.env.ASTRBOT_URL,P=process.env.ASTRBOT_PLUGIN||'astrbot_plugin_60sapi_hub',PW=process.env.ASTRBOT_PASSWORD,CHROME=process.env.CHROME_PATH;
if(!BASE||!PW||!CHROME){console.error('ASTRBOT_URL, ASTRBOT_PASSWORD and CHROME_PATH are required');process.exit(2);}
let token=null,siteId=null,profileId=null,taskId=null;const name='调度页面回归'+Date.now();
async function api(method,path,data){const r=await fetch(BASE+path,{method,headers:{'Content-Type':'application/json',...(token?{Authorization:`Bearer ${token}`}:{})},body:data?JSON.stringify(data):undefined});const body=await r.json().catch(()=>({}));return {status:r.status,body};}
(async()=>{const browser=await chromium.launch({headless:true,executablePath:CHROME,args:['--no-sandbox']});const page=await browser.newPage({viewport:{width:1400,height:1000}});const enablePosts=[];try{
  const login=await api('POST','/api/v1/auth/login',{username:'astrbot',password:PW});assert.equal(login.status,200);token=login.body.data.token;
  let r=await api('POST',`/api/v1/plugins/extensions/${P}/sites`,{name:name+'站点',base_url:'http://example.test/proxy',enabled:true,timeout_seconds:5});assert.equal(r.status,201);siteId=r.body.data.id;
  r=await api('POST',`/api/v1/plugins/extensions/${P}/profiles`,{name:name+'组合',site_id:siteId,items:[{content_type:'daily_news',params:{},limit:null}]});assert.equal(r.status,201);profileId=r.body.data.id;
  r=await api('POST',`/api/v1/plugins/extensions/${P}/tasks`,{name,profile_id:profileId,output_format:'text',umos:['fake_qq_01:GroupMessage:123'],schedule:{kind:'daily',timezone:'Asia/Shanghai',time:'08:30'}});assert.equal(r.status,201);taskId=r.body.data.id;
  page.on('request',req=>{if(req.method()==='POST'&&/\/tasks\/[^/]+\/enable$/.test(new URL(req.url()).pathname))enablePosts.push(req.postData());});
  await page.goto(BASE+'/',{waitUntil:'domcontentloaded'});await page.fill('input[name=username]','astrbot');await page.fill('input[name=password]',PW);
  await Promise.all([page.waitForURL(url=>!url.toString().includes('/auth/login')),page.getByRole('button',{name:'登录',exact:true}).click()]);
  await closeHostFirstPrompt(page, PW);
await page.goto(`${BASE}/#/plugin-view/${P}/sites`,{waitUntil:'domcontentloaded'});await page.waitForTimeout(800);
  const clear=async()=>closeHostPrompts(page);
  await clear();const iframe=page.locator('iframe').first();let frame=page.frames().find(x=>x!==page.mainFrame()&&x.url().includes('/views/sites/'));if(!frame)frame=await (await iframe.elementHandle()).contentFrame();
  await frame.waitForFunction(n=>document.getElementById('tasks-list')?.innerText.includes(n),name);
  await clear();await frame.locator('#tasks-list li').filter({hasText:name}).getByRole('button',{name:/启用定时调度/}).click();await frame.waitForSelector('#task-power-dialog[open]');
  const confirmText=await frame.locator('#task-power-info').innerText();assert.match(confirmText,new RegExp(name));assert.match(confirmText,/08:30/);assert.match(confirmText,/Asia\/Shanghai/);assert.match(confirmText,/fake_qq_01:GroupMessage:123/);
  await frame.locator('#task-power-cancel-button').click();await frame.waitForFunction(()=>!document.getElementById('task-power-dialog')?.open);assert.equal(enablePosts.length,0);
  await clear();await frame.locator('#tasks-list li').filter({hasText:name}).getByRole('button',{name:/启用定时调度/}).click();await frame.waitForSelector('#task-power-dialog[open]');await frame.locator('#task-power-confirm-button').click();await frame.waitForFunction(()=>!document.getElementById('task-power-dialog')?.open);await frame.waitForFunction(n=>document.getElementById('tasks-list')?.innerText.includes(n),name);await frame.waitForFunction(n=>document.getElementById('tasks-list')?.innerText.includes('已启用'),name);
  let current=await api('GET',`/api/v1/plugins/extensions/${P}/tasks/${taskId}`);assert.equal(current.body.data.enabled,true);assert.ok(current.body.data.next_run_at);const firstRevision=current.body.data.revision;
  await clear();await frame.locator('#tasks-list li').filter({hasText:name}).getByRole('button',{name:/停用定时调度/}).click();await frame.waitForSelector('#task-power-dialog[open]');await frame.locator('#task-power-confirm-button').click();await frame.waitForFunction(()=>!document.getElementById('task-power-dialog')?.open);await frame.waitForFunction(n=>document.getElementById('tasks-list')?.innerText.includes('未启用'),name);
  current=await api('GET',`/api/v1/plugins/extensions/${P}/tasks/${taskId}`);assert.equal(current.body.data.enabled,false);assert.ok(current.body.data.revision>firstRevision);assert.equal(current.body.data.next_run_at,null);
  // Re-enable, then a normal edit must automatically disable the task.
  await clear();await frame.locator('#tasks-list li').filter({hasText:name}).getByRole('button',{name:/启用定时调度/}).click();await frame.waitForSelector('#task-power-dialog[open]');await frame.locator('#task-power-confirm-button').click();await frame.waitForFunction(()=>!document.getElementById('task-power-dialog')?.open);await frame.waitForFunction(id=>document.querySelector(`#tasks-list li[data-task-id="${id}"]`)?.innerText.includes('已启用'),taskId);
  current=await api('GET',`/api/v1/plugins/extensions/${P}/tasks/${taskId}`);assert.equal(current.body.data.enabled,true);const beforeEdit=current.body.data.revision;
  await clear();await frame.locator('#tasks-list li').filter({hasText:name}).getByRole('button',{name:/编辑推送任务/}).click();await frame.waitForSelector('#task-dialog[open]');await frame.fill('#task-name',name+'改');await frame.locator('#task-save-button').click();await frame.waitForFunction(()=>!document.getElementById('task-dialog')?.open);await frame.waitForFunction(id=>document.querySelector(`#tasks-list li[data-task-id="${id}"]`)?.innerText.includes('未启用'),taskId);
  current=await api('GET',`/api/v1/plugins/extensions/${P}/tasks/${taskId}`);assert.equal(current.body.data.enabled,false);assert.ok(current.body.data.revision>beforeEdit);assert.equal(current.body.data.next_run_at,null);
  // Revision conflict: hold an old revision in the confirmation, then update externally.
  await clear();await frame.locator('#tasks-list li').filter({hasText:name+'改'}).getByRole('button',{name:/启用定时调度/}).click();await frame.waitForSelector('#task-power-dialog[open]');
  const stale=await api('GET',`/api/v1/plugins/extensions/${P}/tasks/${taskId}`);await api('POST',`/api/v1/plugins/extensions/${P}/tasks/${taskId}/update`,{name:name+'外部',profile_id:profileId,output_format:'text',umos:['fake_qq_01:GroupMessage:123'],expected_revision:stale.body.data.revision});
  await frame.locator('#task-power-confirm-button').click();await frame.waitForFunction(()=>!document.getElementById('task-power-dialog')?.open);await frame.waitForFunction(n=>document.getElementById('tasks-list')?.innerText.includes(n),name+'外部');
  current=await api('GET',`/api/v1/plugins/extensions/${P}/tasks/${taskId}`);assert.equal(current.body.data.enabled,false);
  // Enable request reaches the server, but its response is discarded.
  await clear();await frame.locator('#tasks-list li').filter({hasText:name+'外部'}).getByRole('button',{name:/启用定时调度/}).click();await frame.waitForSelector('#task-power-dialog[open]');
  await page.route('**/tasks/*/enable',async route=>{await route.fetch();await new Promise(r=>setTimeout(r,300));await route.abort('failed');});
  await frame.locator('#task-power-confirm-button').click();await frame.waitForSelector('#task-power-uncertain:not([hidden])');
  await page.keyboard.press('Escape');assert.notEqual(await frame.locator('#task-power-dialog').getAttribute('open'),null); // Escape is prevented while unconfirmed
  await frame.locator('#task-power-recheck-button').click();await frame.waitForFunction(()=>document.getElementById('task-power-review-close-button')&&!document.getElementById('task-power-review-close-button').hidden);await frame.locator('#task-power-review-close-button').click();
  current=await api('GET',`/api/v1/plugins/extensions/${P}/tasks/${taskId}`);assert.equal(current.body.data.enabled,true);const lostRev=current.body.data.revision;assert.ok(lostRev>=1);
  await page.unroute('**/tasks/*/enable');
  await clear();await frame.locator('#tasks-list li').filter({hasText:name+'外部'}).getByRole('button',{name:/停用定时调度/}).click();await frame.waitForSelector('#task-power-dialog[open]');await frame.locator('#task-power-confirm-button').click();await frame.waitForFunction(()=>!document.getElementById('task-power-dialog')?.open);
  current=await api('GET',`/api/v1/plugins/extensions/${P}/tasks/${taskId}`);assert.equal(current.body.data.enabled,false);assert.equal(current.body.data.revision,lostRev+1);
  const calls=[];await page.route('**/schedule-runs*',async route=>{const off=Number(new URL(route.request().url()).searchParams.get('offset')||0);calls.push(off);const claim=i=>({task_id:taskId,scheduled_for:`2026-01-0${i}T00:30:00+00:00`,execution_id:`exec-${i}`,status:'unknown',error_code:'send_unknown',created_at:'t',updated_at:'t'});const skip=i=>({task_id:taskId,scheduled_for:`2026-01-0${i}T00:30:00+00:00`,reason:'late',created_at:'t'});await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({status:'ok',data:{limit:2,offset:off,claims:[claim(off+1)],skips:[skip(off+1),skip(off+2)]}})});});
  await clear();await frame.locator('#schedule-runs-refresh-button').click();await frame.waitForFunction(()=>document.getElementById('schedule-claims-list')?.innerText.includes('exec-1'));assert.match(await frame.locator('#schedule-claims-list').innerText(),/结果不确定/);assert.match(await frame.locator('#schedule-skips-list').innerText(),/超过迟到窗口/);
  await frame.locator('#schedule-runs-more-button').click();await frame.waitForFunction(()=>document.getElementById('schedule-claims-list')?.innerText.includes('exec-3'));assert.deepEqual(calls.slice(-2),[0,2]);
  await page.unroute('**/schedule-runs*');await page.route('**/schedule-runs*',route=>route.abort('failed'));
  await frame.locator('#schedule-runs-refresh-button').click();await frame.waitForFunction(()=>!document.getElementById('schedule-runs-state')?.hidden);
  await frame.waitForFunction(()=>document.getElementById('schedule-claims-list')?.getAttribute('aria-busy')==='false');assert.match(await frame.locator('#schedule-claims-list').innerText(),/exec-3/);
  await page.unroute('**/schedule-runs*');await page.route('**/schedule-runs*',async route=>{const off=Number(new URL(route.request().url()).searchParams.get('offset')||0);const claim=i=>({task_id:taskId,scheduled_for:`2026-01-0${i}T00:30:00+00:00`,execution_id:`exec-${i}`,status:'unknown',error_code:'send_unknown',created_at:'t',updated_at:'t'});await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({status:'ok',data:{limit:2,offset:off,claims:[claim(off+1)],skips:[]}})});});
  await frame.locator('#schedule-runs-retry-button').click();await frame.waitForFunction(()=>document.getElementById('schedule-runs-state')?.hidden===true);
  await page.setViewportSize({width:390,height:1000});await page.screenshot({path:'/tmp/m5-scheduler-ui-390.png',fullPage:true});
  console.log('SCHEDULER UI PASS',{name,calls,enablePosts:enablePosts.length});
}finally{try{if(taskId&&token){const t=await api('GET',`/api/v1/plugins/extensions/${P}/tasks/${taskId}`);if(t.status===200)await api('POST',`/api/v1/plugins/extensions/${P}/tasks/${taskId}/delete`,{expected_revision:t.body.data.revision});}if(profileId&&token)await api('POST',`/api/v1/plugins/extensions/${P}/profiles/${profileId}/delete`,{expected_revision:1});if(siteId&&token)await api('POST',`/api/v1/plugins/extensions/${P}/sites/${siteId}/delete`,{expected_revision:1});}catch{}await browser.close();}})().catch(e=>{console.error(e);process.exit(1)});
