// Chrome + actual API/WebSocket/LiveKit/PostgreSQL. Only network failure/delay is injected.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'D:/Temp/kltn-browser-verification/node_modules/playwright');
const assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path');
const { randomUUID, randomBytes } = require('node:crypto'), { spawnSync } = require('node:child_process');
const frontend = process.env.FRONTEND_TEST_URL || 'http://localhost:3000', backend = process.env.LIVE_API_URL || 'http://localhost:8000';
const marker = 'phase7b-' + randomUUID(), out = path.resolve(__dirname, '../../evidence/phase-7b-uc38-uc40'); fs.mkdirSync(out, { recursive: true });
const accounts = ['owner','member','outsider'].map(role => ({ role, email: `${marker}-${role}@example.com`, password: randomBytes(24).toString('base64url') }));
const report = { marker, checks: [], snapshots: [] }; let browser;
const sleep = ms => new Promise(r => setTimeout(r, ms));
function evidence(action) {
  const dir = path.resolve(__dirname, '../../backend');
  const r = spawnSync(path.join(dir, '.venv-win/Scripts/python.exe'), ['-m','scripts.verify_learning_analytics_live'], { cwd: dir, input: JSON.stringify({ marker, action }), encoding: 'utf8', timeout: 30000 });
  assert.equal(r.status,0,r.stdout+r.stderr); return r.stdout.trim();
}
async function api(actor, method, route, body, status=200) {
  const r=await fetch(backend+'/api/v1'+route,{method,headers:{Connection:'close',...(actor?.token?{Authorization:'Bearer '+actor.token}:{}),...(body?{'Content-Type':'application/json'}:{})},body:body?JSON.stringify(body):undefined});
  assert.equal(r.status,status,route+': '+await r.clone().text());return status===204?null:(await r.json()).data;
}
const analytics='/users/me/learning-analytics';
function micros(value) { const extra=(value.match(/\.(\d+)/)?.[1]||'').padEnd(6,'0').slice(3,6);return BigInt(Date.parse(value))*1000n+BigInt(extra); }
function union(spans) {
  const sorted=spans.filter(([a,b])=>b>a).sort((a,b)=>a[0]<b[0]?-1:1), merged=[];
  for(const [a,b] of sorted) { const last=merged.at(-1);if(last&&a<=last[1]) last[1]=b>last[1]?b:last[1];else merged.push([a,b]); }
  return merged;
}
const duration=spans=>union(spans).reduce((n,[a,b])=>n+b-a,0n);
async function rawJoin(page, name, room, actor) {
  await page.evaluate(({name,url,token})=>new Promise((resolve,reject)=>{
    const ws=new WebSocket(url);window.analyticsSockets??={};window.analyticsSockets[name]={ws};let joined=false,resolved=false;
    ws.onopen=()=>ws.send(JSON.stringify({type:'auth',access_token:token}));
    ws.onmessage=e=>{const f=JSON.parse(e.data);if(f.type!=='room.state')return;if(!joined){joined=true;ws.send(JSON.stringify({type:'join',user_id:'00000000-0000-0000-0000-000000000000',timestamp:'1900-01-01'}));}
      else if(!resolved&&f.data.self_state==='IN_ROOM'){resolved=true;window.analyticsSockets[name].timer=setInterval(()=>{if(ws.readyState===1)ws.send(JSON.stringify({type:'state'}));},2000);resolve();}};
    ws.onerror=reject;
  }),{name,url:backend.replace('http','ws')+'/api/v1/ws/study-rooms/'+room.id,token:actor.token});
}
async function rawLeave(page,name) { await page.evaluate(name=>{const entry=window.analyticsSockets[name];clearInterval(entry.timer);entry.ws.send(JSON.stringify({type:'leave'}));},name);await sleep(300); }
(async()=>{
  for(const a of accounts){a.id=(await api(null,'POST','/auth/register',{full_name:'UC7B '+a.role,email:a.email,password:a.password,confirm_password:a.password},201)).id;a.token=(await api(null,'POST','/auth/login',{email:a.email,password:a.password})).access_token;}
  const [owner,member,outsider]=accounts;
  const workspace=await api(owner,'POST','/workspaces',{name:marker},201);
  const invite=await api(owner,'POST',`/workspaces/${workspace.id}/invitations`,{invitee_user_id:member.id},201);await api(member,'POST',`/workspace-invitations/${invite.token}/accept`);
  const room=await api(owner,'POST',`/workspaces/${workspace.id}/channels`,{name:'analytics-room',type:'STUDY_ROOM'},201);
  const other=await api(owner,'POST',`/workspaces/${workspace.id}/channels`,{name:'overlap-room',type:'STUDY_ROOM'},201);
  await api(null,'GET',analytics,undefined,401);await api(outsider,'GET',analytics+`?workspace_id=${workspace.id}`,undefined,403);
  assert.equal((await api(member,'GET',analytics+`?user_id=${owner.id}`)).total_duration_us,0);
  report.checks.push('real 401/403; membership and principal scope reject spoofed user');
  browser=await chromium.launch({channel:'chrome',headless:true,args:['--use-fake-device-for-media-stream','--auto-accept-camera-and-microphone-capture','--autoplay-policy=no-user-gesture-required']});
  const context=await browser.newContext({permissions:['camera','microphone']});const page=await context.newPage();page.setDefaultTimeout(30000);
  await page.addInitScript(()=>{window.roomSockets=[];const Native=window.WebSocket;window.WebSocket=class extends Native{constructor(...args){super(...args);if(String(args[0]).includes('/ws/study-rooms/'))window.roomSockets.push(this);}};});
  await page.goto(frontend+'/login');await page.getByLabel('Email',{exact:true}).fill(owner.email);await page.getByLabel('Mật khẩu',{exact:true}).fill(owner.password);await page.getByRole('button',{name:'Đăng nhập',exact:true}).click();
  await page.getByRole('heading',{name:'Xin chào, UC7B owner'}).waitFor();await page.getByRole('link',{name:'Thống kê học tập',exact:true}).click();
  await page.getByText('Chưa đủ dữ liệu để thống kê.',{exact:true}).waitFor();assert.equal(await page.getByTestId('study-total').innerText(),'0 giờ 0 phút');
  await page.getByLabel('Khoảng thời gian',{exact:true}).selectOption('30');await page.locator('[aria-label="Biểu đồ thời gian tự học theo ngày"] time').nth(29).waitFor();
  await page.getByLabel('Khoảng thời gian',{exact:true}).selectOption('7');await page.getByText('Chưa đủ dữ liệu để thống kê.',{exact:true}).waitFor();
  await page.route('**/users/me/learning-analytics?*',r=>r.abort());await page.getByRole('button',{name:'Cập nhật',exact:true}).click();await page.getByRole('alert').waitFor();
  await page.unroute('**/users/me/learning-analytics?*');await page.getByRole('button',{name:'Thử lại',exact:true}).click();await page.getByTestId('study-total').waitFor();
  await page.route('**/users/me/learning-analytics?*',async r=>{await sleep(800);await r.continue();});await page.getByRole('button',{name:'Cập nhật',exact:true}).click();await page.getByText('Đang tải thống kê…',{exact:true}).waitFor();await page.getByTestId('study-total').waitFor();await page.unroute('**/users/me/learning-analytics?*');
  let delayed=false;
  await page.route('**/users/me/learning-analytics?*',async r=>{if(!delayed){delayed=true;await sleep(1500);}await r.continue();});
  await page.getByLabel('Khoảng thời gian',{exact:true}).selectOption('30');await sleep(150);await page.getByLabel('Khoảng thời gian',{exact:true}).selectOption('7');
  await page.getByTestId('study-total').waitFor();await sleep(1800);assert.equal(await page.locator('[aria-label="Biểu đồ thời gian tự học theo ngày"] time').count(),7);await page.unroute('**/users/me/learning-analytics?*');
  await page.screenshot({path:path.join(out,'dashboard-empty.png'),fullPage:true});report.checks.push('Chrome empty + 7/30-day ranges + loading + actual network failure/retry + delayed request race');
  await page.goto(frontend+'/workspaces/'+workspace.id);await page.getByRole('button',{name:'◇ analytics-room STUDY_ROOM',exact:true}).click();await page.getByRole('button',{name:'Tham gia phòng',exact:true}).click();await page.getByText('Đã kết nối media',{exact:true}).waitFor();
  const started=Date.now();
  await rawJoin(page,'tab2',room,owner);await rawJoin(page,'other',other,owner);
  const pomodoro=`/channels/${room.id}/study-room/pomodoro`;
  const mutate=async(action,seconds=6)=>{const state=await api(owner,'GET',pomodoro);return api(owner,'POST',pomodoro,{action,revision:state.revision,focus_seconds:seconds,break_seconds:30});};
  await mutate('start');await sleep(1100);await mutate('pause');await sleep(1200);await mutate('resume');await rawJoin(page,'late-member',room,member);
  let state;for(let n=0;n<35;n++){state=await api(owner,'GET',pomodoro);if(state.phase==='break')break;await sleep(250);}assert.equal(state.phase,'break');assert.equal(state.completed_focus,1);
  await mutate('end');await mutate('start',30);await sleep(1300);await mutate('end');
  await rawLeave(page,'tab2');
  await page.evaluate(()=>window.roomSockets.find(s=>s.readyState===1&&!Object.values(window.analyticsSockets).some(e=>e.ws===s)).close());
  await page.getByRole('button',{name:'Kết nối lại',exact:true}).waitFor();await sleep(3100);await page.getByRole('button',{name:'Kết nối lại',exact:true}).click();await page.getByText('Bạn đang ở trong phòng.',{exact:true}).waitFor();
  const dashboard=await context.newPage();await dashboard.goto(frontend+'/learning-analytics');await dashboard.getByTestId('study-total').waitFor();assert.equal(await dashboard.getByTestId('study-total').innerText(),'0 giờ 0 phút');
  // Keep real heartbeat participation for >60s: valid streak is earned, never backdated.
  while(Date.now()-started<65000)await sleep(2000);
  await rawLeave(page,'late-member');await rawLeave(page,'other');
  await page.getByRole('button',{name:'Rời phòng',exact:true}).click();await page.getByText('Bạn đang ở ngoài phòng.',{exact:true}).waitFor();
  await sleep(400);
  const db=JSON.parse(evidence('inspect'));report.snapshots.push({label:'real_completed_sessions_focus_and_segments',...db});
  const ownSessions=db.sessions.filter(s=>s.user_id===owner.id);assert.equal(ownSessions.length,2);assert.ok(ownSessions.every(s=>s.status==='COMPLETED'&&s.duration_us===s.ledger_duration_us));
  const ownIds=new Set(ownSessions.map(s=>s.id));const spans=db.intervals.filter(i=>ownIds.has(i.session_id)).map(i=>[micros(i.started_at),micros(i.ended_at)]);
  const actual=await api(owner,'GET',analytics);assert.equal(BigInt(actual.total_duration_us),duration(spans));assert.ok(actual.total_duration_us<ownSessions.reduce((n,s)=>n+s.duration_us,0));
  assert.equal(actual.streak.current,1);assert.equal(actual.focus.completed_intervals,1);assert.equal(actual.focus.early_ended_intervals,1);assert.ok(actual.focus.duration_us>=7_000_000&&actual.focus.duration_us<8_500_000);
  const memberData=await api(member,'GET',analytics);assert.equal(memberData.focus.completed_intervals,0);assert.equal(memberData.focus.early_ended_intervals,1);
  const main=ownSessions.find(s=>s.channel_id===room.id);assert.equal(db.intervals.filter(i=>i.session_id===main.id).length,2);assert.ok(micros(main.ended_at)-micros(main.started_at)-BigInt(main.duration_us)>3_000_000n);
  assert.equal(db.completions.length,1);assert.equal(db.focus_segments.length,3);
  report.api=actual;report.memberApi=memberData;
  report.checks.push('real >60s attendance, same-room tabs and overlapping rooms; exact DB intervals match UC37 and API union');
  report.checks.push('real 6-second focus natural completion + pause/resume + early room stop; late member not assigned full completion');
  report.checks.push('actual socket drop/reconnect excludes offline gap; only completed sessions appear; streak earned today');
  await dashboard.getByRole('button',{name:'Cập nhật',exact:true}).click();await dashboard.getByTestId('focus-completed').waitFor();assert.equal(await dashboard.getByTestId('focus-completed').innerText(),'1');assert.equal(await dashboard.getByTestId('streak-current').innerText(),'1');
  await dashboard.reload();await dashboard.getByTestId('focus-completed').waitFor();assert.equal(await dashboard.getByTestId('focus-completed').innerText(),'1');
  await dashboard.getByLabel('Khoảng thời gian',{exact:true}).selectOption('30');await dashboard.locator('[aria-label="Biểu đồ thời gian tự học theo ngày"] time').nth(29).waitFor();assert.equal(await dashboard.getByTestId('streak-current').innerText(),'1');
  await dashboard.getByLabel('Khoảng thời gian',{exact:true}).selectOption('custom');await dashboard.getByLabel('Từ ngày',{exact:true}).fill('2020-01-01');await dashboard.getByLabel('Đến ngày',{exact:true}).fill('2020-01-07');await dashboard.getByRole('button',{name:'Áp dụng',exact:true}).click();await dashboard.getByText('Chưa đủ dữ liệu để thống kê.',{exact:true}).waitFor();assert.equal(await dashboard.getByTestId('streak-current').innerText(),'1');
  await dashboard.getByLabel('Khoảng thời gian',{exact:true}).selectOption('7');await dashboard.getByTestId('focus-completed').waitFor();await dashboard.screenshot({path:path.join(out,'dashboard-desktop.png'),fullPage:true});
  await dashboard.setViewportSize({width:390,height:844});assert.equal(await dashboard.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),true);await dashboard.screenshot({path:path.join(out,'dashboard-mobile.png'),fullPage:true});
  report.checks.push('actual dashboard updates without refresh and after refresh; 30/custom historical range leaves all-history streak unchanged; mobile no overflow');
  report.status='PASS';
})().catch(e=>{report.status='NOT READY';report.failure=e.stack;console.error(e);process.exitCode=1;}).finally(async()=>{
  fs.writeFileSync(path.join(out,'browser.json'),JSON.stringify(report,null,2));if(browser)await browser.close();
  try{console.log(evidence('cleanup'));}catch(e){console.error(e);process.exitCode=1;}
  console.log(JSON.stringify({status:report.status,checks:report.checks,failure:report.failure},null,2));
});
