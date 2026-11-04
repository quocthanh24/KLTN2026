// Staging Chrome UI + real app WS, PostgreSQL, MinIO, guarded SFU.
const {chromium}=require('D:/Temp/kltn-browser-verification/node_modules/playwright');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict'),{spawnSync}=require('node:child_process');
const repo=path.resolve(__dirname,'../..'),root='D:/Temp/kltn-media-integration-20261104',out=path.join(repo,'evidence/phase-7c-media-integration');
const stage=(...args)=>{const r=spawnSync(path.join(repo,'backend/.venv-win/Scripts/python.exe'),[path.join(repo,'backend/scripts/media_stage.py'),...args],{encoding:'utf8',timeout:60000});assert.equal(r.status,0,r.stderr);return r.stdout;};
stage('run','scripts.media_stage_fixture','seed');const fixture=JSON.parse(fs.readFileSync(root+'/fixture.json'));const {actors,workspace,room,chat}=fixture;
const api=async(actor,method,route,body,status=200)=>{const response=await fetch('http://127.0.0.1:8821/api/v1'+route,{method,headers:{Authorization:'Bearer '+actor.token,...(body&&!(body instanceof FormData)?{'Content-Type':'application/json'}:{})},body:body instanceof FormData?body:body?JSON.stringify(body):undefined});assert.equal(response.status,status,route);return status===204?null:(await response.json()).data;};
const receipts=[];const pass=name=>{receipts.push({name,status:'PASS'});console.log('PASS '+name);};let browser;let backendDown=false;
(async()=>{try{
 browser=await chromium.launch({channel:'chrome',headless:true,args:['--use-fake-device-for-media-stream','--auto-accept-camera-and-microphone-capture','--autoplay-policy=no-user-gesture-required']});
 async function login(actor){const ctx=await browser.newContext({permissions:['camera','microphone']});const page=await ctx.newPage();page.setDefaultTimeout(30000);page.on('dialog',d=>d.accept(d.type()==='prompt'?'Isolated staging verification':undefined));await page.goto('http://127.0.0.1:3021/login');await page.getByLabel('Email',{exact:true}).fill(actor.email);await page.getByLabel('Mật khẩu',{exact:true}).fill(actor.password);await page.getByRole('button',{name:'Đăng nhập',exact:true}).click();await page.waitForURL('**/app');return page;}
 const owner=await login(actors.owner),member=await login(actors.member),admin=await login(actors.admin);
 for(const p of [owner,member]){await p.goto('http://127.0.0.1:3021/workspaces/'+workspace.id);await p.getByRole('button',{name:'◇ Stage room STUDY_ROOM',exact:true}).click();await p.getByRole('button',{name:'Tham gia phòng',exact:true}).click();await p.getByText('Đã kết nối media',{exact:true}).waitFor();}
 await owner.getByRole('button',{name:'Camera: OFF',exact:true}).click();await owner.getByRole('button',{name:'Microphone: OFF',exact:true}).click();
 await member.locator('video').first().waitFor();
 let timer=await api(actors.owner,'GET',`/channels/${room.id}/study-room/pomodoro`);
 timer=await api(actors.owner,'POST',`/channels/${room.id}/study-room/pomodoro`,{action:'start',revision:timer.revision,focus_seconds:300,break_seconds:60});
 await api(actors.member,'POST',`/channels/${room.id}/study-room/pomodoro`,{action:'pause',revision:timer.revision},403);
 const paused=await api(actors.owner,'POST',`/channels/${room.id}/study-room/pomodoro`,{action:'pause',revision:timer.revision});
 const resumed=await api(actors.owner,'POST',`/channels/${room.id}/study-room/pomodoro`,{action:'resume',revision:paused.revision});
 assert.equal(resumed.session_id,timer.session_id);
 pass('UC22–28 real UI/WS join, camera/mic and shared Pomodoro start/pause/resume');
 await admin.goto('http://127.0.0.1:3021/admin/users');await admin.getByLabel('Tìm kiếm').fill(actors.outsider.email);await admin.getByRole('button',{name:'outsider '+fixture.marker,exact:true}).click();
 assert.equal(spawnSync('docker',['pause','kltn-media-stage-sfu']).status,0);
 const pending=admin.waitForResponse(r=>r.url().endsWith('/state')&&r.request().method()==='PATCH');await admin.getByRole('button',{name:'Thay đổi trạng thái',exact:true}).click();assert.equal((await pending).status(),202);
 await admin.getByText('Trạng thái đã lưu. Đang chờ xác nhận ngắt media…',{exact:true}).waitFor();await admin.screenshot({path:path.join(out,'admin-pending.png')});
 await admin.reload();await admin.getByLabel('Tìm kiếm').fill(actors.outsider.email);await admin.getByRole('button',{name:'outsider '+fixture.marker,exact:true}).click();
 await admin.getByText('Thu hồi media: Đang chờ xác nhận',{exact:true}).waitFor();assert.equal(await admin.getByRole('button',{name:'Thay đổi trạng thái',exact:true}).isDisabled(),true);
 assert.equal(spawnSync('docker',['unpause','kltn-media-stage-sfu']).status,0);
 await admin.getByText('Đã hoàn tất thu hồi media.',{exact:true}).waitFor();
 await admin.screenshot({path:path.join(out,'admin-complete.png')});pass('admin UI HTTP 202/PENDING persists until genuine SFU barrier ACK');
 // An authority outage closes media, but must not be an explicit Study Room leave.
 stage('backend-stop');backendDown=true;await owner.getByRole('button',{name:'Kết nối lại',exact:true}).waitFor();stage('backend-start');backendDown=false;
 for(let i=0;i<60;i++){try{if((await fetch('http://127.0.0.1:8821/api/v1/health')).ok)break;}catch{}await new Promise(r=>setTimeout(r,100));}
 await owner.getByRole('button',{name:'Kết nối lại',exact:true}).waitFor();await owner.getByRole('button',{name:'Kết nối lại',exact:true}).click();
 await owner.waitForTimeout(1000);
 // Rejoin after application WS recovery is explicit; camera and microphone remain OFF.
 const join=owner.getByRole('button',{name:'Tham gia phòng',exact:true});if(await join.isVisible())await join.click();
 const restore=owner.getByRole('button',{name:'Khôi phục media',exact:true});if(await restore.isVisible())await restore.click();
 await owner.getByText('Đã kết nối media',{exact:true}).waitFor();await owner.getByRole('button',{name:'Camera: OFF',exact:true}).waitFor();await owner.getByRole('button',{name:'Microphone: OFF',exact:true}).waitFor();
 const current=await api(actors.owner,'GET',`/channels/${room.id}/study-room/pomodoro`);assert.equal(current.session_id,timer.session_id);
 await api(actors.owner,'POST',`/channels/${room.id}/study-room/pomodoro`,{action:'end',revision:current.revision});
 await owner.getByRole('button',{name:'Rời phòng',exact:true}).click();pass('UC37 recovery after backend restart: safe devices OFF and Pomodoro session preserved');
 const pdfPage=await browser.newPage();await pdfPage.setContent('<h1>Staging moderation original evidence</h1>');const pdf=await pdfPage.pdf();await pdfPage.close();
 const form=new FormData();form.set('name','staging.pdf');form.set('file',new Blob([pdf],{type:'application/pdf'}),'staging.pdf');
 const doc=await api(actors.owner,'POST',`/channels/${chat.id}/learning-documents`,form,201);
 const download=await fetch(`http://127.0.0.1:8821/api/v1/learning-documents/${doc.id}/download`,{headers:{Authorization:'Bearer '+actors.owner.token}});assert.equal(download.status,200);assert.deepEqual(Buffer.from(await download.arrayBuffer()),pdf);
 const report=await api(actors.member,'POST','/violation-reports',{target_type:'DOCUMENT',target_id:doc.id,reason:'SPAM',description:'isolated staging fixture'},201);
 await admin.goto('http://127.0.0.1:3021/admin/reports');await admin.locator(`[data-report-id="${report.id}"] button`).click();await admin.getByRole('button',{name:'Mở tài liệu báo cáo',exact:true}).click();await admin.locator('[aria-label="Trình xem PDF"] canvas').waitFor();
 await admin.getByRole('button',{name:'Có vi phạm',exact:true}).click();await admin.getByText('Đã xử lý báo cáo.',{exact:true}).waitFor();
 await api(actors.owner,'GET',`/learning-documents/${doc.id}/download`,null,404);
 await api(actors.member,'GET',`/admin/reports/${report.id}/evidence`,null,403);
 await admin.goto('http://127.0.0.1:3021/admin');await admin.getByRole('heading',{name:'Giám sát hệ thống',exact:true}).waitFor();
 await admin.screenshot({path:path.join(out,'admin-dashboard.png')});
 for(const route of ['/admin/users','/admin/workspaces','/admin/reports','/admin/dashboard','/admin/dashboard/details?group=study'])await api(actors.owner,'GET',route,null,403);
 pass('UC41/44/45 report→PDF original MinIO evidence→HIDE, dashboard UI and RBAC');
 await owner.goto('http://127.0.0.1:3021/learning-analytics');await owner.getByRole('heading',{name:'Thống kê học tập',exact:true}).waitFor();
 const analytics=await api(actors.owner,'GET','/users/me/learning-analytics');assert.ok(analytics);pass('UC38–40 analytics actual API and frontend remain accessible');
 fs.writeFileSync(path.join(out,'ui-browser.json'),JSON.stringify({status:'PASS',scope:'staging only',receipts},null,2));
 }catch(error){fs.writeFileSync(path.join(out,'ui-browser.json'),JSON.stringify({status:'NOT READY',receipts,error:{message:error.message,stack:error.stack}},null,2));console.error(error);process.exitCode=1;
 }finally{spawnSync('docker',['unpause','kltn-media-stage-sfu'],{stdio:'ignore'});if(backendDown)stage('backend-start');await browser?.close();}})();
