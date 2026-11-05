// Actual staging app + PG + guarded SFU. No authorization/transport mocks.
const {chromium}=require('D:/Temp/kltn-browser-verification/node_modules/playwright');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {spawnSync}=require('node:child_process');
const {randomUUID,randomBytes,createHmac}=require('node:crypto');
const ROOT='D:/Temp/kltn-media-integration-20261104',repo=path.resolve(__dirname,'../..');
const OUT=path.join(repo,'evidence/phase-7c-media-integration');fs.mkdirSync(OUT,{recursive:true});
const API='http://127.0.0.1:8821/api/v1';
const receipts=[];const contexts=[];let browser;let down=false;
const stage=(...args)=>{const r=spawnSync(path.join(repo,'backend/.venv-win/Scripts/python.exe'),[path.join(repo,'backend/scripts/media_stage.py'),...args],{encoding:'utf8',timeout:60000});assert.equal(r.status,0,r.stderr);return r.stdout;};
function seed(){stage('run','scripts.media_stage_fixture','seed');return JSON.parse(fs.readFileSync(ROOT+'/fixture.json'));}
async function api(actor,method,route,body,status=200){const response=await fetch(API+route,{method,headers:{...(actor?{Authorization:'Bearer '+actor.token}:{}),...(body?{'Content-Type':'application/json'}:{})},body:body?JSON.stringify(body):undefined});assert.equal(response.status,status,route);return status===204?null:(await response.json()).data;}
const pass=(name,details={})=>{receipts.push({name,status:'PASS',...details});console.log('PASS '+name);};
async function tab(credential,expect=true,samplingMs=100){
 const context=await browser.newContext({permissions:['camera','microphone']});contexts.push(context);const page=await context.newPage();
 await page.goto('http://127.0.0.1:8821/docs');await page.evaluate(()=>document.title='Staging capture');
 await page.addScriptTag({path:path.join(repo,'frontend/node_modules/livekit-client/dist/livekit-client.umd.js')});
 const result=await page.evaluate(async ({grant,samplingMs})=>{
  window.r=new LivekitClient.Room({adaptiveStream:false});window.samples=[];window.events=[];window.pcs=[];window.tracks=[];
  r.on(LivekitClient.RoomEvent.TrackSubscribed,track=>{tracks.push({source:track.source,kind:track.kind});const el=track.attach();el.muted=true;document.body.appendChild(el);el.play().catch(()=>{});});
  r.on(LivekitClient.RoomEvent.Disconnected,reason=>events.push({event:'disconnected',at:Date.now(),reason}));
  r.on(LivekitClient.RoomEvent.Reconnected,()=>events.push({event:'reconnected',at:Date.now()}));
  window.sample=async()=>{for(const t of [r.engine?.pcManager?.publisher,r.engine?.pcManager?.subscriber])if(t?._pc&&!pcs.includes(t._pc))pcs.push(t._pc);
   let bytes=0,packets=0;for(const pc of pcs){try{(await pc.getStats()).forEach(v=>{if(v.type==='inbound-rtp'){bytes+=v.bytesReceived||0;packets+=v.packetsReceived||0;}});}catch{}}
   const s={at:Date.now(),bytes,packets,state:r.state,pcs:pcs.map(p=>p.connectionState)};samples.push(s);return s;};
  window.timer=setInterval(()=>sample(),samplingMs);
  try{await r.connect(grant.url,grant.token,{maxRetries:0});return true;}catch{return false;}
 },{grant:credential,samplingMs});assert.equal(result,expect,'direct SDK authorization');return page;
}
async function flow(page){await page.waitForFunction(()=>samples.at(-1)?.bytes>1000,{},{timeout:15000});const a=await page.evaluate(()=>sample());await page.waitForTimeout(500);const b=await page.evaluate(()=>sample());assert.ok(b.bytes>a.bytes);return b.bytes-a.bytes;}
async function publish(page,screen=false){await page.evaluate(async screen=>{await r.localParticipant.setCameraEnabled(true);await r.localParticipant.setMicrophoneEnabled(true);if(screen)await r.localParticipant.setScreenShareEnabled(true,{audio:false});},screen);}
async function refresh(page,initial){await page.waitForFunction(initial=>r.engine.token!==initial,initial);return page.evaluate(()=>r.engine.token);}
async function grant(f,role){return api(f.actors[role],'POST',`/channels/${f.room.id}/study-room/media-token`);}
async function change(f,scope,target,enabled=false){const current=await api(f.actors.admin,'GET',`/admin/${scope}/${target}`);return api(f.actors.admin,'PATCH',`/admin/${scope}/${target}/state`,{version:current.admin_version,enabled,reason:'Isolated media staging verification'},enabled?200:202);}
async function complete(f,operation){for(let i=0;i<70;i++){const current=await api(f.actors.admin,'GET','/admin/media-revocations/'+operation.id);if(current.state==='COMPLETE')return current;await new Promise(r=>setTimeout(r,100));}throw Error('SFU ACK did not complete');}
function dbOperations(){return JSON.parse(stage('run','scripts.media_stage_fixture','inspect'));}
async function rtpStopped(page,operation,label){
 await page.waitForFunction(()=>r.state==='disconnected',{},{timeout:5000});await page.waitForTimeout(700);
 const a=await page.evaluate(()=>sample());await page.waitForTimeout(800);const b=await page.evaluate(()=>sample());assert.equal(a.bytes,b.bytes);assert.equal(a.packets,b.packets);
 const trace=await page.evaluate(()=>({samples,events,tracks}));const row=dbOperations().find(x=>x.id===operation.id);
 assert.equal(row.state,'COMPLETE');assert.equal(row.ack.barrier,'transport-closed-v1');assert.equal(row.ack.pending,false);assert.ok(row.ack.boot_id);
 const commit=Date.parse(row.commit_at);assert.ok(Number.isFinite(commit),'real PG commit timestamp required');
 const last=trace.samples.filter((s,i,arr)=>i>0&&s.bytes>arr[i-1].bytes).at(-1);
 const latency=Math.max(0,(last?.at||commit)-commit);assert.ok(latency<=2500,'commit to last observed RTP growth <=2.5s');
 assert.ok((last?.at||commit)<=row.ack.closed_at_ms+700,'packet in-flight allowance <=700ms');
 fs.writeFileSync(path.join(OUT,label+'.json'),JSON.stringify({operation:row,trace,commit_to_last_observed_rtp_ms:latency,sampling_ms:100,in_flight_allowance_ms:700,stable_window_ms:800},null,2));return latency;
}
async function close(...pages){for(const page of pages)await page.evaluate(()=>r.disconnect()).catch(()=>{});}
async function waitBackend(){for(let i=0;i<60;i++){try{if((await fetch(API+'/health')).ok)return;}catch{}await new Promise(r=>setTimeout(r,100));}throw Error('staging backend unavailable');}
(async()=>{try{
 browser=await chromium.launch({channel:'chrome',headless:true,args:['--use-fake-device-for-media-stream','--auto-accept-camera-and-microphone-capture','--autoplay-policy=no-user-gesture-required','--auto-select-tab-capture-source-by-title=Staging capture','--disable-background-timer-throttling','--disable-renderer-backgrounding']});
 if(process.argv.includes('--resume-binding')){
  const f=seed();const publisher=await tab(f.actors.owner.media),receiver=await tab(f.actors.member.media);await publish(publisher);await flow(receiver);
  const login=await api(null,'POST','/auth/login',{email:f.actors.member.email,password:f.actors.member.password});const secondActor={...f.actors.member,token:login.access_token};
  const secondGrant=await api(secondActor,'POST',`/channels/${f.room.id}/study-room/media-token`);assert.notEqual(secondGrant.grant_id,f.actors.member.media.grant_id);
  // Use the real SDK serializer/decoder: v1 wrapped join_request overrides
  // legacy reconnect query parameters, and WS OPEN is not a resume ACK.
  const attempt=await receiver.evaluate(async token=>{
   if(typeof r.engine.url!=='string'||!r.engine.client.options)throw Error('SDK reconnect options unavailable');
   const signal=new r.engine.client.constructor();signal.options={...r.engine.client.options};
   try{await signal.reconnect(r.engine.url,token,r.localParticipant.sid);return 'accepted';}catch{return 'rejected';}finally{await signal.close();}
  },secondGrant.token);assert.equal(attempt,'rejected');await flow(receiver);
  const replacement=await tab(secondGrant);await flow(replacement);
  fs.writeFileSync(path.join(OUT,'resume-binding.json'),JSON.stringify({status:'PASS',scope:'real SDK/WS',different_auth_session_resume_rejected:true,original_transport_unaffected:true,authorized_full_join_with_new_binding:true},null,2));pass('resume cannot swap immutable grant/AuthSession binding; authorized fresh full join works');await close(publisher,receiver,replacement);return;
 }
 if(process.argv.includes('--soak')){
  const f=seed();const publisher=await tab(f.actors.owner.media),receiver=await tab(f.actors.member.media);await publish(publisher,true);await flow(receiver);
  const first=await refresh(receiver,f.actors.member.media.token);const started=Date.now(),observations=[];
  for(let i=0;i<6;i++){await receiver.waitForTimeout(50000);observations.push({elapsed_ms:Date.now()-started,rtp_delta:await flow(receiver)});console.log('STAGING SOAK '+Math.round((Date.now()-started)/1000)+'s RTP active');}
  await receiver.waitForFunction(first=>r.engine.token!==first,first,{timeout:30000});const periodic=await receiver.evaluate(()=>r.engine.token);
  const claims=token=>JSON.parse(Buffer.from(token.split('.')[1],'base64url'));
  assert.equal(claims(periodic).metadata,claims(first).metadata);assert.ok(claims(periodic).exp>claims(first).exp);
  const revoked=await change(f,'users',f.actors.member.id);await complete(f,revoked.media_revocation);const latency=await rtpStopped(receiver,revoked.media_revocation,'periodic-refresh-revoke');await tab({...f.actors.member.media,token:periodic},false);
  fs.writeFileSync(path.join(OUT,'periodic-refresh.json'),JSON.stringify({status:'PASS',scope:'actual staging AuthSession/PG + SFU periodic refresh, no paid APIs',elapsed_ms:Date.now()-started,observations,periodic_refresh:true,binding_preserved:true,periodic_token_replay_rejected:true,commit_to_rtp_ms:latency},null,2));
  pass('actual 5-minute server refresh remains bound to durable grant and cannot bypass lock');await close(publisher,receiver);return;
 }
 if(process.argv.includes('--lifecycle')){
  const f=seed();assert.equal(spawnSync('docker',['pause','kltn-media-stage-sfu']).status,0);
  const locked=await change(f,'users',f.actors.member.id);await new Promise(r=>setTimeout(r,3600));assert.equal((await api(f.actors.admin,'GET','/admin/media-revocations/'+locked.media_revocation.id)).state,'PENDING');
  await change(f,'users',f.actors.member.id,true);const currentMember=await grant(f,'member');assert.notEqual(currentMember.grant_id,f.actors.member.media.grant_id);
  assert.equal(spawnSync('docker',['unpause','kltn-media-stage-sfu']).status,0);
  const pub=await tab(f.actors.owner.media),recv=await tab(currentMember);await publish(pub);await flow(recv);await complete(f,locked.media_revocation);await flow(recv);await tab(f.actors.member.media,false);
  pass('late retry of pre-unlock operation cannot revoke the new generation');
  const disabled=await change(f,'workspaces',f.workspace.id);await complete(f,disabled.media_revocation);const duration=await rtpStopped(recv,disabled.media_revocation,'lifecycle-disable');
  await change(f,'workspaces',f.workspace.id,true);await tab(currentMember,false);await tab(f.actors.owner.media,false);
  const authorizedOwner=await grant(f,'owner'),authorizedObserver=await grant(f,'observer');
  assert.equal(spawnSync('docker',['restart','kltn-media-stage-sfu'],{encoding:'utf8',timeout:20000}).status,0);await new Promise(r=>setTimeout(r,600));
  await tab(currentMember,false);await tab(f.actors.member.media,false);const after=await tab(authorizedOwner),watch=await tab(authorizedObserver);await publish(after);await flow(watch);
  const unsigned=await fetch('http://127.0.0.1:8940/revoke',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({operation:randomUUID(),grants:[]})});assert.equal(unsigned.status,403);
  pass('Workspace restore + SFU hard restart: revoked credentials remain fenced, authorized generations work');
  fs.writeFileSync(path.join(OUT,'lifecycle.json'),JSON.stringify({status:'PASS',receipts,workspace_commit_to_rtp_ms:duration},null,2));await close(pub,recv,after,watch);return;
 }
 if(process.argv.includes('--adversarial')){
  const f=seed();const publisher=await tab(f.actors.owner.media),receiver=await tab(f.actors.member.media),observer=await tab(f.actors.observer.media);await publish(publisher);await flow(receiver);await flow(observer);
  await receiver.evaluate(()=>{r.engine.client.onClose=()=>events.push({event:'detached-signaling',at:Date.now()});r.engine.client.ws.close();});await flow(receiver);
  const result=await change(f,'users',f.actors.member.id);await complete(f,result.media_revocation);await receiver.waitForTimeout(900);
  const a=await receiver.evaluate(()=>sample());await receiver.waitForTimeout(900);const b=await receiver.evaluate(()=>sample());assert.equal(b.bytes,a.bytes);assert.equal(b.packets,a.packets);await flow(observer);
  const trace=await receiver.evaluate(()=>({samples,events,tracks,state:r.state}));const op=dbOperations().find(x=>x.id===result.media_revocation.id);assert.equal(op.ack.barrier,'transport-closed-v1');assert.equal(op.state,'COMPLETE');
  const commit=Date.parse(op.commit_at);const last=trace.samples.filter((s,i,arr)=>i>0&&s.bytes>arr[i-1].bytes).at(-1);assert.ok((last?.at||commit)-commit<=2500);
  fs.writeFileSync(path.join(OUT,'adversarial.json'),JSON.stringify({status:'PASS',operation:op,trace,stable_window_ms:900,commit_to_last_rtp_ms:Math.max(0,(last?.at||commit)-commit),sdk_state:trace.state},null,2));
  pass('actual SFU transport ACK stops RTP even when SDK signaling teardown callback is suppressed');await close(publisher,receiver,observer);return;
 }
 if(process.argv.includes('--load')){
  const f=seed();const pages=[];const credentials=[];
  for(let i=0;i<20;i++){
   const email=`media-load-${randomUUID()}@example.com`,password=randomBytes(24).toString('base64url');
   const user=await api(null,'POST','/auth/register',{full_name:'isolated load '+i,email,password,confirm_password:password},201);
   const login=await api(null,'POST','/auth/login',{email,password});const actor={id:user.id,token:login.access_token};
   const invite=await api(f.actors.owner,'POST',`/workspaces/${f.workspace.id}/invitations`,{invitee_user_id:user.id},201);await api(actor,'POST',`/workspace-invitations/${invite.token}/accept`);
   const c=await api(actor,'POST',`/channels/${f.room.id}/study-room/media-token`);credentials.push({actor,credential:c});
  }
  const publisher=await tab(f.actors.owner.media,true,2000);for(const c of credentials)pages.push(await tab(c.credential,true,pages.length===0?100:1000));
  const audioOnly=process.argv.includes('--audio-only');
  if(audioOnly)await publisher.evaluate(()=>r.localParticipant.setMicrophoneEnabled(true));else await publish(publisher);
  for(const p of pages)await flow(p);
  const key=fs.readFileSync(ROOT+'/service.key');let next=0;const timings=[];let denied=0;const statusCounts={};
  async function callbackWorker(){while(true){const index=next++;if(index>=2000)return;const {actor,credential}=credentials[index%credentials.length];const raw=JSON.stringify({grant:credential.grant_id,identity:actor.id,room:credential.room});const stamp=String(Date.now()),nonce=randomBytes(16).toString('hex');const mac=createHmac('sha256',key).update(stamp+'\n'+nonce+'\n'+raw).digest('hex');const start=performance.now();
   const response=await fetch(API+'/internal/media/authorize',{method:'POST',headers:{'Content-Type':'application/json','X-Media-Time':stamp,'X-Media-Nonce':nonce,'X-Media-MAC':mac},body:raw});const body=await response.json();timings.push(performance.now()-start);statusCounts[response.status]=(statusCounts[response.status]||0)+1;
   assert.ok(response.status===200&&body.allow===true||response.status===503,'only genuine allow or explicit fail-closed backpressure');if(response.status===503)denied++;
  }}
  await Promise.all(Array.from({length:32},callbackWorker));
  fs.writeFileSync(path.join(OUT,audioOnly?'load-audio-pressure.json':'load-video-pressure.json'),JSON.stringify({profile:audioOnly?'microphone only':'camera + microphone',callback_requests:2000,callback_concurrency:32,denied,latencies_ms:timings},null,2));
  // The join pool is bounded: 503 is an explicit admission failure, not a
  // successful authorization. Active renewals have reserved capacity and MUST
  // retain real RTP during this pressure; verify every peer below.
  for(const p of pages)await flow(p);
  const resources=spawnSync('docker',['stats','--no-stream','--format','{{.Name}} {{.CPUPerc}} {{.MemUsage}}','kltn-media-stage-sfu'],{encoding:'utf8'}).stdout.trim();
  const result=await change(f,'workspaces',f.workspace.id);await complete(f,result.media_revocation);
  const latency=await rtpStopped(pages[0],result.media_revocation,'load-disable');
  for(const p of pages)assert.equal(await p.evaluate(()=>r.state),'disconnected');
  const sorted=timings.sort((a,b)=>a-b);
  fs.writeFileSync(path.join(OUT,audioOnly?'load-audio.json':'load.json'),JSON.stringify({status:'PASS',profile:audioOnly?'microphone only':'camera + microphone',active_peers:21,publishers:1,subscribers:20,callback_requests:2000,callback_concurrency:32,purpose:'join admission pressure; actual SFU lease uses reserved pool',fail_closed_admission_rejections:denied,status_counts:statusCounts,p50_ms:sorted[999],p95_ms:sorted[1899],p99_ms:sorted[1979],max_ms:sorted.at(-1),workspace_commit_to_last_observed_rtp_ms:latency,resources,scope:'all 21 active peers retain RTP despite admission backpressure; not all join requests guaranteed under overload'},null,2));
  pass('21 real peers remain connected under 2000 authority/PG calls; all revoked with ACK');await close(publisher,...pages);return;
 }
 const f=seed();const owner=await tab(f.actors.owner.media);const member=await tab(f.actors.member.media);const observer=await tab(f.actors.observer.media);
 await publish(owner,true);await flow(member);await flow(observer);await member.waitForFunction(()=>tracks.some(x=>x.source==='screen_share')&&tracks.some(x=>x.kind==='audio'));
 const oldRefresh=await refresh(member,f.actors.member.media.token);
 const resumeUrl=await member.evaluate(token=>{const url=new URL(r.engine.client.ws.url);url.pathname='/rtc';url.search='';url.searchParams.set('protocol','16');url.searchParams.set('sdk','js');url.searchParams.set('version','2.17.2');url.searchParams.set('access_token',token);url.searchParams.set('reconnect','1');url.searchParams.set('sid',r.localParticipant.sid);return url.toString();},oldRefresh);
 await observer.evaluate(()=>r.engine.client.ws.close());await observer.waitForFunction(()=>events.some(e=>e.event==='reconnected'),{},{timeout:15000});await flow(observer);
 pass('real AuthSession/channel grants, camera/mic/screen and authorized reconnect');
 const locked=await change(f,'users',f.actors.member.id);assert.equal(locked.media_revocation.state,'PENDING');await complete(f,locked.media_revocation);
 const lockMs=await rtpStopped(member,locked.media_revocation,'user-lock');await flow(observer);
 await tab({...f.actors.member.media,token:oldRefresh},false);await tab(f.actors.member.media,false);
 const rawResume=await observer.evaluate(url=>new Promise(resolve=>{const ws=new WebSocket(url);const timer=setTimeout(()=>{ws.close();resolve('timeout');},3000);ws.onopen=()=>{clearTimeout(timer);ws.close();resolve('opened');};ws.onerror=()=>{clearTimeout(timer);resolve('rejected');};}),resumeUrl);assert.equal(rawResume,'rejected');
 await api(f.actors.member,'POST',`/channels/${f.room.id}/study-room/media-token`,null,403);
 await api(f.actors.owner,'GET','/admin/media-revocations/'+locked.media_revocation.id,null,403);
 pass('UC42 lock: real SFU ACK, old/refreshed replay denied, other user unaffected',{commit_to_rtp_ms:lockMs});
 await change(f,'users',f.actors.member.id,true);await tab({...f.actors.member.media,token:oldRefresh},false);
 const renewed=await grant(f,'member');const first=await tab(renewed);const secondCredential=await grant(f,'member');const second=await tab(secondCredential);await flow(second);
 const lock2=await change(f,'users',f.actors.member.id);await complete(f,lock2.media_revocation);await rtpStopped(second,lock2.media_revocation,'multi-tab-lock');await tab(renewed,false);pass('unlock never revives old version; multi-tab replacement and revocation');
 await api(f.actors.owner,'DELETE',`/workspaces/${f.workspace.id}/members/${f.actors.observer.id}`,null,204);
 await observer.waitForFunction(()=>r.state==='disconnected');await tab(f.actors.observer.media,false);pass('membership removal affects direct SDK without app WS');
 const reInvite=await api(f.actors.owner,'POST',`/workspaces/${f.workspace.id}/invitations`,{invitee_user_id:f.actors.observer.id},201);await api(f.actors.observer,'POST',`/workspace-invitations/${reInvite.token}/accept`);await tab(f.actors.observer.media,false);const reMember=await tab(await grant(f,'observer'));await flow(reMember);await close(reMember);pass('membership remove/re-add cannot resurrect prior grant binding');
 await close(owner,member,observer,first,second);
 const w=seed();const wp=await tab(w.actors.owner.media);const wq=await tab(w.actors.member.media);await publish(wp);await flow(wq);
 const concurrent=await Promise.all([change(w,'workspaces',w.workspace.id),change(w,'users',w.actors.member.id)]);
 for(const result of concurrent)await complete(w,result.media_revocation);
 const workspaceMs=await rtpStopped(wq,concurrent[0].media_revocation,'workspace-disable');await tab(w.actors.owner.media,false);
 pass('UC43 concurrent disable/lock: fenced barrier and RTP stop',{commit_to_rtp_ms:workspaceMs});await close(wp,wq);
 const pending=seed();const pause=spawnSync('docker',['pause','kltn-media-stage-sfu'],{encoding:'utf8'});assert.equal(pause.status,0);
 const request=await change(pending,'users',pending.actors.member.id);await new Promise(r=>setTimeout(r,3700));
 const op=await api(pending.actors.admin,'GET','/admin/media-revocations/'+request.media_revocation.id);assert.equal(op.state,'PENDING');assert.ok(op.error_code);
 const unpause=spawnSync('docker',['unpause','kltn-media-stage-sfu'],{encoding:'utf8'});assert.equal(unpause.status,0);await complete(pending,request.media_revocation);
 pass('no fake COMPLETE while SFU ACK unavailable; durable retry completes after recovery');
 const h=seed();const hp=await tab(h.actors.owner.media);const hq=await tab(h.actors.member.media);await publish(hp);await flow(hq);
 const cached=await refresh(hq,h.actors.member.media.token);const preloaded=await tab(h.actors.observer.media);await preloaded.evaluate(()=>r.disconnect());
 stage('backend-stop');down=true;await hq.waitForFunction(()=>r.state==='disconnected',{},{timeout:5000});
 const denied=await preloaded.evaluate(async c=>{const candidate=new LivekitClient.Room();try{await candidate.connect(c.url,c.token,{maxRetries:0});await candidate.disconnect();return false;}catch{return true;}},{...h.actors.member.media,token:cached});assert.equal(denied,true);
 stage('backend-start');down=false;await waitBackend();const recovered=await tab(await grant(h,'member'));const recoveredPub=await tab(await grant(h,'owner'));await publish(recoveredPub);await flow(recovered);
 pass('full staging backend hard restart: independent lease fail-closed and authorized recovery');
 const sessionMedia=await grant(h,'member');await api(h.actors.member,'POST','/auth/logout',null,204);await recovered.waitForFunction(()=>r.state==='disconnected',{},{timeout:5000});await tab(sessionMedia,false);await tab({...h.actors.member.media,token:cached},false);pass('AuthSession logout revokes original and refreshed media JWT');
 await close(hp,hq,preloaded,recovered,recoveredPub);
 fs.writeFileSync(path.join(OUT,'media-browser.json'),JSON.stringify({status:'PASS',scope:'staging only; target runtime not cut over',receipts},null,2));
 }catch(error){const diagnostics=[];for(const context of contexts)for(const page of context.pages())diagnostics.push(await page.evaluate(()=>({state:window.r?.state,samples:window.samples?.slice(-4),events:window.events,tracks:window.tracks})).catch(()=>({closed:true})));const result={status:'NOT READY',receipts,error:{name:error.name,message:error.message,stack:error.stack},diagnostics};fs.writeFileSync(path.join(OUT,process.argv.includes('--load')?'load.json':'media-browser.json'),JSON.stringify(result,null,2));fs.writeFileSync(path.join(OUT,'failed-attempt-'+Date.now()+'.json'),JSON.stringify(result,null,2));console.error(error);process.exitCode=1;
 }finally{spawnSync('docker',['unpause','kltn-media-stage-sfu'],{stdio:'ignore'});if(down){stage('backend-start');await waitBackend();}for(const c of contexts)await c.close().catch(()=>{});await browser?.close();}})();
