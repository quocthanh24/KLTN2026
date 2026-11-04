// Owner-approved paid acceptance, real UI/HTTP/provider only. No automatic retries.
const { chromium } = require('D:/Temp/kltn-browser-verification/node_modules/playwright');
const fs=require('fs'),path=require('path');
const privateDir='D:/Temp/kltn-four-corners-live-20261102-private';
const state=JSON.parse(fs.readFileSync(path.join(privateDir,'authorized-state.json'),'utf8'));
const questions=JSON.parse(fs.readFileSync(path.join(privateDir,'questions.json'),'utf8'));
const output=path.join(privateDir,'browser');fs.mkdirSync(output,{recursive:true});
const url='http://127.0.0.1:3032/workspaces/'+state.workspace_id;
(async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:false});
 const context=await browser.newContext({viewport:{width:1660,height:1100}});
 const page=await context.newPage();page.setDefaultTimeout(90000);
 const results={url,channel_id:state.channel_id,cases:[],errors:[],provider:'REAL; no inference interception'};
 page.on('pageerror',e=>results.errors.push(e.message));
 // Bootstrap ONLY existing authorized session; never intercept questions or source/provider data.
 await page.route('**/api/v1/auth/refresh',async route=>{
   const valid=await context.request.get(state.api+'/api/v1/users/me',{headers:{Authorization:'Bearer '+state.access_token}});
   if(valid.status()!==200)return route.abort();
   await route.fulfill({status:200,headers:{'content-type':'application/json','access-control-allow-origin':'http://127.0.0.1:3032','access-control-allow-credentials':'true'},body:JSON.stringify({data:{access_token:state.access_token}})});
 });
 await page.goto(url);
 await page.getByRole('button',{name:'Mở trợ lý AI',exact:true}).click();
 await page.getByText('AI Chat V2 Original-First · gpt-6-luna',{exact:true}).waitFor();
 await page.getByRole('button',{name:'Mở hội thoại nghiệm thu V2',exact:true}).click();
 await page.screenshot({path:path.join(output,'00-v2-channel-before.png'),fullPage:true});
 console.log(JSON.stringify({stage:'UI_READY',channel:state.channel_id,source:'REAL',url}));
 for(const q of questions){
   const status=await context.request.get(state.api+'/api/v1/channels/'+state.channel_id+'/ai/pilot',{headers:{Authorization:'Bearer '+state.access_token}});
   const data=(await status.json()).data;
   if(data.blocked||data.paused){results.stopped_before=q.id;break;}
   const input=page.getByRole('textbox',{name:'Câu hỏi cho trợ lý AI',exact:true});
   await input.fill(q.question);
   const start=Date.now();
   const responsePromise=page.waitForResponse(r=>r.request().method()==='POST'&&r.url().endsWith('/questions'),{timeout:240000});
   await page.getByRole('button',{name:'Gửi câu hỏi',exact:true}).click();
   const response=await responsePromise;
   const body=await response.json();
   fs.writeFileSync(path.join(output,q.id+'-http.json'),JSON.stringify({question:q.question,status:response.status(),latency_ms:Date.now()-start,body},null,2));
   await page.screenshot({path:path.join(output,q.id+'-answer.png'),fullPage:true});
   results.cases.push({case_id:q.id,http_status:response.status(),latency_ms:Date.now()-start});
   fs.writeFileSync(path.join(output,'results.json'),JSON.stringify(results,null,2));
   console.log(JSON.stringify(results.cases.at(-1)));
   if(!response.ok()){results.stopped_after=q.id;break;}
   await input.waitFor({state:'visible'});
   await page.waitForFunction(()=>{const t=document.querySelector('textarea');return t&&!t.disabled;});
 }
 // Stop generating automatically. Keep UI/browser for the owner's explicit manual action.
 await context.request.post(state.api+'/api/v1/channels/'+state.channel_id+'/ai/pilot/pause',{headers:{Authorization:'Bearer '+state.access_token}});
 await page.screenshot({path:path.join(output,'99-paused-for-owner.png'),fullPage:true});
 fs.writeFileSync(path.join(output,'results.json'),JSON.stringify(results,null,2));
 console.log(JSON.stringify({stage:'PAUSED_FOR_OWNER',cases:results.cases.length,url}));
 setInterval(()=>{},60000);
})().catch(e=>{fs.writeFileSync(path.join(output,'fatal.json'),JSON.stringify({error_type:e.name,message:e.message}));console.error('Browser acceptance stopped; inspect private fatal.json; no retry.');process.exitCode=2;});
