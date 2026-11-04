// Phase 7C: real Chrome, auth, PostgreSQL, MinIO/E5, WebSocket and LiveKit. No success mocks.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'D:/Temp/kltn-browser-verification/node_modules/playwright');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {randomUUID,randomBytes}=require('node:crypto'),{spawnSync}=require('node:child_process');
const front=process.env.FRONTEND_TEST_URL||'http://localhost:3000',back=process.env.LIVE_API_URL||'http://localhost:8000';
const out=path.resolve(__dirname,'../../evidence/phase-7c-uc41-uc45');fs.mkdirSync(out,{recursive:true});
const marker='phase7c-'+randomUUID(),report={marker,checks:[],blockers:[],snapshots:[]};let browser,workspace;
const roles=['admin','admin2','owner','wadmin','member','outsider'];const accounts=roles.map(role=>({role,name:`UC7C ${role} ${marker.slice(-8)}`,email:`${marker}-${role}@example.com`,password:randomBytes(24).toString('base64url')}));
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
function helper(action,extra={}){const dir=path.resolve(__dirname,'../../backend');const p=spawnSync(path.join(dir,'.venv-win/Scripts/python.exe'),['-m','scripts.verify_administration_live'],{cwd:dir,input:JSON.stringify({marker,action,...extra}),encoding:'utf8',timeout:60000});assert.equal(p.status,0,p.stdout+p.stderr);return p.stdout.trim();}
async function request(actor,method,route,body){return fetch(back+'/api/v1'+route,{method,headers:{Connection:'close',...(actor?.token?{Authorization:'Bearer '+actor.token}:{}),...(body&&!(body instanceof FormData)?{'Content-Type':'application/json'}:{})},body:body instanceof FormData?body:body?JSON.stringify(body):undefined});}
async function api(actor,method,route,body,status=200){const r=await request(actor,method,route,body);assert.equal(r.status,status,route+': '+await r.clone().text());return status===204?null:(await r.json()).data;}
function check(label){report.checks.push(label);console.log('PASS:',label);fs.writeFileSync(path.join(out,'browser.json'),JSON.stringify(report,null,2));}
async function login(actor){const context=await browser.newContext({permissions:['camera','microphone']});const page=await context.newPage();page.setDefaultTimeout(30000);page.on('dialog',d=>d.accept(d.type()==='prompt'?'Phase 7C isolated fixture moderation':undefined));await page.goto(front+'/login');await page.getByLabel('Email',{exact:true}).fill(actor.email);await page.getByLabel('Mật khẩu',{exact:true}).fill(actor.password);await page.getByRole('button',{name:'Đăng nhập',exact:true}).click();await page.getByRole('heading',{name:'Xin chào, '+actor.name,exact:true}).waitFor();actor.context=context;actor.page=page;return page;}
async function reportFromUI(page,container,reason){await container.getByRole('button',{name:'Báo cáo vi phạm',exact:true}).click();const dialog=page.getByRole('dialog',{name:'Báo cáo vi phạm',exact:true});await dialog.getByLabel('Lý do báo cáo').selectOption(reason);await dialog.getByLabel('Mô tả báo cáo').fill(marker);const response=page.waitForResponse(r=>r.url().endsWith('/violation-reports')&&r.request().method()==='POST');await dialog.getByRole('button',{name:'Gửi báo cáo',exact:true}).click();const r=await response;assert.equal(r.status(),201);return (await r.json()).data;}
async function enter(page,room){await page.goto(front+'/workspaces/'+workspace.id);await page.getByRole('button',{name:`◇ ${room.name} STUDY_ROOM`,exact:true}).click();await page.getByRole('button',{name:'Tham gia phòng',exact:true}).click();await page.getByText('Đã kết nối media',{exact:true}).waitFor();}
async function replay(page,credential,label){await page.addScriptTag({path:path.resolve(__dirname,'../node_modules/livekit-client/dist/livekit-client.umd.js')});const receipt=await page.evaluate(async({url,token})=>{const room=new window.LivekitClient.Room();try{await room.connect(url,token);await new Promise(r=>setTimeout(r,1000));return {connected:true,peers:room.remoteParticipants.size,subscribedTracks:[...room.remoteParticipants.values()].reduce((n,p)=>n+[...p.trackPublications.values()].filter(t=>t.isSubscribed).length,0)};}catch(e){return {connected:false,error:String(e.message).slice(0,150)};}finally{await room.disconnect();}},{url:credential.url,token:credential.token});report.snapshots.push({label,...receipt});if(receipt.connected)report.blockers.push({code:'SELF_HOSTED_LIVEKIT_CACHED_TOKEN_REPLAY',scenario:label,...receipt});return receipt;}
(async()=>{
 for(const a of accounts){a.id=(await api(null,'POST','/auth/register',{full_name:a.name,email:a.email,password:a.password,confirm_password:a.password},201)).id;const grant=await api(null,'POST','/auth/login',{email:a.email,password:a.password});assert.equal(grant.user.system_role,'USER');a.token=grant.access_token;}
 const [admin,admin2,owner,wadmin,member,outsider]=accounts;
 helper('grant',{role:'admin'});helper('grant',{role:'admin2'});
 workspace=await api(owner,'POST','/workspaces',{name:marker},201);
 for(const a of [wadmin,member]){const invite=await api(owner,'POST',`/workspaces/${workspace.id}/invitations`,{invitee_user_id:a.id},201);await api(a,'POST',`/workspace-invitations/${invite.token}/accept`);}
 await api(owner,'PATCH',`/workspaces/${workspace.id}/members/${wadmin.id}/role`,{role:'ADMIN'});
 const chat=await api(owner,'POST',`/workspaces/${workspace.id}/channels`,{name:'moderation-chat',type:'TEXT'},201),room=await api(owner,'POST',`/workspaces/${workspace.id}/channels`,{name:'moderation-room',type:'STUDY_ROOM'},201);
 for(const a of [owner,wadmin,member,outsider])for(const route of ['/admin/users','/admin/workspaces','/admin/reports','/admin/dashboard','/admin/dashboard/details?group=documents'])await api(a,'GET',route,undefined,403);
 await api(null,'GET','/admin/dashboard',undefined,401);check('Actual regular / OWNER / Workspace ADMIN / MEMBER / outsider denied every admin group; public signup never grants system admin');
 browser=await chromium.launch({channel:'chrome',headless:true,args:['--use-fake-device-for-media-stream','--auto-accept-camera-and-microphone-capture','--autoplay-policy=no-user-gesture-required']});
 const a=await login(admin),u=await login(owner),m=await login(member);
 const pdfPage=await browser.newPage();await pdfPage.setContent(`<h1>Phase 7C ${marker}</h1><p>PostgreSQL integrity. MinIO original evidence. This is isolated test content.</p>`);const pdf=await pdfPage.pdf();await pdfPage.close();
 const upload=new FormData();upload.set('name','moderation-proof.pdf');upload.set('file',new Blob([pdf],{type:'application/pdf'}),'moderation-proof.pdf');const doc=await api(owner,'POST',`/channels/${chat.id}/learning-documents`,upload,201);
 for(let i=0;i<160;i++){const d=await api(owner,'GET',`/learning-documents/${doc.id}`);if(d.status==='READY')break;assert.notEqual(d.status,'FAILED');await sleep(500);}
 const beforeRag=JSON.parse(helper('rag',{token:owner.token,document_id:doc.id,channel_id:chat.id}));assert.ok(beforeRag.hits.includes(doc.id));
 const message=await api(owner,'POST',`/channels/${chat.id}/messages`,{content:'UC41 harmless fixture '+marker},201);
 await u.goto(front+'/workspaces/'+workspace.id);await u.getByRole('button',{name:'# moderation-chat TEXT',exact:true}).click();const row=u.locator(`[data-message-id="${message.id}"]`);await row.waitFor();
 let submissions=0;u.on('request',r=>{if(r.url().endsWith('/violation-reports')&&r.method()==='POST')submissions++;});await row.getByRole('button',{name:'Báo cáo vi phạm',exact:true}).click();await u.getByRole('button',{name:'Hủy báo cáo',exact:true}).click();await sleep(100);assert.equal(submissions,0);
 const mr=await reportFromUI(u,row,'SPAM');const dr=await reportFromUI(u,u.locator(`[data-document-id="${doc.id}"]`),'COPYRIGHT');
 await api(outsider,'POST','/violation-reports',{target_type:'DOCUMENT',target_id:doc.id,reason:'SPAM'},403);
 await api(owner,'POST','/violation-reports',{target_type:'MESSAGE',target_id:message.id,reason:'SPAM',status:'RESOLVED'},422);
 const dup=await api(owner,'POST','/violation-reports',{target_type:'DOCUMENT',target_id:doc.id,reason:'SPAM'},201);assert.equal(dup.id,dr.id);
 check('UC41 Chrome chat/document reports and cancel; actual PDF upload/MinIO/E5 READY; scope, injection and duplicate rejection');
 await a.goto(front+'/admin/reports');await a.locator(`[data-report-id="${mr.id}"] button`).click();await a.getByRole('button',{name:'Không vi phạm',exact:true}).click();await a.getByText('Đã xử lý báo cáo.',{exact:true}).waitFor();
 assert.ok((await api(owner,'GET',`/channels/${chat.id}/messages`)).some(x=>x.id===message.id));
 await a.goto(front+'/admin/reports');await a.locator(`[data-report-id="${dr.id}"] button`).click();await a.getByRole('button',{name:'Mở tài liệu báo cáo',exact:true}).click();await a.locator('[aria-label="Trình xem PDF"] canvas').waitFor();
 await a.waitForFunction(()=>{const c=document.querySelector('[aria-label="Trình xem PDF"] canvas');if(!c||c.width<=300)return false;const pixels=c.getContext('2d').getImageData(0,0,c.width,c.height).data;let ink=0;for(let i=0;i<pixels.length;i+=4)if(pixels[i]<100&&pixels[i+1]<100&&pixels[i+2]<100&&pixels[i+3]>0)ink++;return ink>100;});
 await a.screenshot({path:path.join(out,'admin-report-evidence.png'),fullPage:true});
 const original=await request(admin,'GET',`/admin/reports/${dr.id}/evidence`);assert.equal(original.status,200);assert.deepEqual(Buffer.from(await original.arrayBuffer()),pdf);
 await a.getByRole('button',{name:'Có vi phạm',exact:true}).click();await a.getByText('Đã xử lý báo cáo.',{exact:true}).waitFor();await api(owner,'GET',`/learning-documents/${doc.id}/download`,undefined,404);
 assert.ok(!(await api(owner,'GET',`/channels/${chat.id}/learning-documents`)).some(d=>d.id===doc.id));
 const afterRag=JSON.parse(helper('rag',{token:owner.token,document_id:doc.id,channel_id:chat.id}));assert.ok(!afterRag.hits.includes(doc.id));
 const blocked=await request(owner,'GET',`/admin/reports/${dr.id}/evidence`);assert.equal(blocked.status,403);
 const illegal=await request(admin2,'POST',`/admin/reports/${dr.id}/resolve`,{version:1,outcome:'VIOLATION',action:'HIDE',reason:'retry test'});assert.equal(illegal.status,409);
 const message2=await api(owner,'POST',`/channels/${chat.id}/messages`,{content:'UC44 hidden fixture '+marker},201),r2=await api(owner,'POST','/violation-reports',{target_type:'MESSAGE',target_id:message2.id,reason:'ABUSE'},201);
 await u.getByRole('button',{name:'Tải lại tin nhắn',exact:true}).click();await u.locator(`[data-message-id="${message2.id}"]`).waitFor();
 const decision={version:1,outcome:'VIOLATION',action:'HIDE',reason:'Phase 7C concurrent administration'};
 const race=await Promise.all([request(admin,'POST',`/admin/reports/${r2.id}/resolve`,decision),request(admin2,'POST',`/admin/reports/${r2.id}/resolve`,decision)]);assert.deepEqual(race.map(r=>r.status).sort(),[200,409]);await u.locator(`[data-message-id="${message2.id}"]`).waitFor({state:'detached'});
 check('UC44 actual review/evidence bytes, no-violation preserves content; hidden document unavailable to download and real vector retrieval; message realtime removal; two admins resolve once');
  const attachmentForm=new FormData();attachmentForm.set('file',new Blob([pdf],{type:'application/pdf'}),'moderation-attachment.pdf');
  const attached=await api(owner,'POST',`/channels/${chat.id}/messages/attachments`,attachmentForm,201),attachment=attached.attachments[0];
  const privateDownload=await fetch(back+`/api/v1/attachments/${attachment.id}/download`,{headers:{Authorization:'Bearer '+owner.token,Connection:'close'},redirect:'manual'});assert.equal(privateDownload.status,200);assert.equal(privateDownload.headers.get('location'),null);assert.deepEqual(Buffer.from(await privateDownload.arrayBuffer()),pdf);
  const attachedRow=u.locator(`[data-message-id="${attached.id}"]`);await attachedRow.waitFor();
  const downloadEvent=u.waitForEvent('download');await attachedRow.getByRole('button',{name:/Tải moderation-attachment.pdf/}).click();const browserDownload=await downloadEvent;
  const chunks=[];for await(const chunk of await browserDownload.createReadStream())chunks.push(chunk);assert.deepEqual(Buffer.concat(chunks),pdf);
  const attachmentReport=await api(owner,'POST','/violation-reports',{target_type:'MESSAGE',target_id:attached.id,reason:'ABUSE'},201);
  await api(admin,'POST',`/admin/reports/${attachmentReport.id}/resolve`,decision);
  await api(owner,'GET',`/attachments/${attachment.id}/download`,undefined,404);
  report.snapshots.push({label:'attachment_after_hide',authenticated_download_before_hide:200,download_after_hide:404,storage_grants_issued:0});
  check('UC44 attachment fix: Chrome UI and API byte-identical downloads, no Location/storage grant, same endpoint rejects after hide');
 const ownerStudy=await owner.context.newPage();await enter(ownerStudy,room);await ownerStudy.getByRole('button',{name:'Camera: OFF',exact:true}).click();await ownerStudy.getByRole('button',{name:'Camera: ON',exact:true}).waitFor();await enter(m,room);
 const cached=await api(member,'POST',`/channels/${room.id}/study-room/media-token`);
 const pomo=`/channels/${room.id}/study-room/pomodoro`;let timer=await api(owner,'GET',pomo);timer=await api(owner,'POST',pomo,{action:'start',revision:timer.revision,focus_seconds:3,break_seconds:30});await sleep(3600);timer=await api(owner,'GET',pomo);assert.equal(timer.completed_focus,1);await api(owner,'POST',pomo,{action:'end',revision:timer.revision});
 await a.goto(front+'/admin/users');await a.getByLabel('Tìm kiếm',{exact:true}).fill(member.email);await a.getByRole('button',{name:member.name,exact:true}).click();await a.getByRole('button',{name:'Thay đổi trạng thái',exact:true}).click();await a.getByText('Đã cập nhật trạng thái.',{exact:true}).waitFor();
 await api(member,'GET','/users/me',undefined,403);await api(member,'POST',`/channels/${room.id}/study-room/media-token`,undefined,403);
 await m.getByRole('button',{name:'Camera: OFF',exact:true}).waitFor();
 const replayResult=await replay(m,cached,'locked_user_reuses_preissued_media_token');
 const locked=await api(admin,'GET',`/admin/users/${member.id}`);await api(admin,'PATCH',`/admin/users/${member.id}/state`,{version:locked.admin_version,enabled:true,reason:'Restore isolated member fixture'});
 assert.equal((await api(member,'GET',`/workspaces/${workspace.id}`)).role,'MEMBER');
 check('UC42 Chrome lock/unlock with existing HTTP token and active Study Room; API denies locked account, UI media closes; membership preserved; cached-token probe recorded separately');
 const cachedOwner=await api(owner,'POST',`/channels/${room.id}/study-room/media-token`);
 await a.goto(front+'/admin/workspaces');await a.getByLabel('Tìm kiếm',{exact:true}).fill(marker);await a.getByRole('button',{name:marker,exact:true}).click();await a.getByRole('button',{name:'Thay đổi trạng thái',exact:true}).click();await a.getByText('Đã cập nhật trạng thái.',{exact:true}).waitFor();
 for(const route of [`/workspaces/${workspace.id}`,`/channels/${chat.id}/messages`,`/channels/${chat.id}/learning-documents`,`/channels/${chat.id}/ai/conversations`,`/channels/${room.id}/study-room`,`/channels/${room.id}/study-room/study-sessions`]){const r=await request(owner,'GET',route);assert.ok([403,404].includes(r.status),route+':'+r.status);}
 await replay(ownerStudy,cachedOwner,'disabled_workspace_reuses_preissued_media_token');
 let w=await api(admin,'GET',`/admin/workspaces/${workspace.id}`);await api(admin,'PATCH',`/admin/workspaces/${workspace.id}/state`,{version:w.admin_version,enabled:true,reason:'Restore isolated workspace fixture'});
 assert.equal((await api(wadmin,'GET',`/workspaces/${workspace.id}`)).role,'ADMIN');assert.equal((await api(owner,'GET',`/workspaces/${workspace.id}`)).role,'OWNER');
 check('UC43 disable/restore from Chrome; actual Workspace/chat/docs/AI/Study Room/history API rejection; restored roles unchanged; cached media token probe recorded separately');
 // Wait actual grace for disconnected fixture sessions; no forged timestamps.
 await sleep(32000);
 const snapshot=JSON.parse(helper('inspect'));report.snapshots.push({label:'final_database_minio_audit',...snapshot});
 const today=new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Ho_Chi_Minh',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());
 const dashboard=await api(admin,'GET',`/admin/dashboard?start_date=${today}&end_date=${today}`);const sql=snapshot.independent_sql;
 assert.equal(dashboard.users.total,sql.users);assert.equal(dashboard.workspaces.total,sql.workspaces);assert.equal(dashboard.documents.total,sql.documents);assert.equal(dashboard.reports.pending,sql.reports_pending);assert.equal(dashboard.reports.resolved,sql.reports_resolved);assert.equal(dashboard.study.duration_us,sql.study_duration_us_today);assert.equal(dashboard.study.excluded_sessions,sql.study_excluded_today);assert.ok(snapshot.documents.every(d=>d.original_retained&&d.stored_sha256===d.metadata_sha256));
 report.dashboard=dashboard;
 await a.goto(front+'/admin');await a.getByTestId('admin-users-total').waitFor();assert.ok((await a.getByTestId('admin-users-total').innerText()).includes(String(sql.users)));await a.getByRole('button',{name:'30 ngày',exact:true}).click();await a.getByTestId('admin-users-total').waitFor();
 await a.getByLabel('Xem chi tiết nhóm').selectOption('documents');await a.getByRole('heading',{name:'Chi tiết Tài liệu',exact:true}).waitFor();
 await a.getByLabel('Từ ngày',{exact:true}).fill('2020-01-01');await a.getByLabel('Đến ngày',{exact:true}).fill('2020-01-07');await a.getByRole('button',{name:'Áp dụng khoảng ngày',exact:true}).click();await a.getByText('Chưa có thời gian học đủ dữ liệu trong kỳ.',{exact:true}).waitFor();await a.getByText('Không có dữ liệu trong kỳ.',{exact:true}).waitFor();
 await a.getByRole('button',{name:'7 ngày',exact:true}).click();await a.getByTestId('admin-users-total').waitFor();await a.screenshot({path:path.join(out,'admin-dashboard-desktop.png'),fullPage:true});await a.setViewportSize({width:390,height:844});assert.equal(await a.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);await a.screenshot({path:path.join(out,'admin-dashboard-mobile.png'),fullPage:true});
 check('UC45 actual five-group dashboard reconciles independent PostgreSQL/interval arithmetic; Chrome 7/30/custom empty range, drill-down, responsive; hidden original retained in MinIO and audit exists');
 report.status=report.blockers.length?'NOT READY':'PASS';if(report.blockers.length)process.exitCode=2;
})().catch(e=>{report.status='NOT READY';report.failure=e.stack;console.error(e);process.exitCode=1;}).finally(async()=>{
 fs.writeFileSync(path.join(out,'browser.json'),JSON.stringify(report,null,2));if(browser)await browser.close();if(workspace)try{console.log(helper('cleanup'));}catch(e){console.error(e);process.exitCode=1;}
 console.log(JSON.stringify({status:report.status,checks:report.checks,blockers:report.blockers,failure:report.failure},null,2));
});
