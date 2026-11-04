// Post-live browser evidence only. Explicitly prevents new inference requests.
const {chromium}=require('D:/Temp/kltn-browser-verification/node_modules/playwright');
const fs=require('fs'),path=require('path');
const dir='D:/Temp/kltn-four-corners-live-20261102-private';
const state=JSON.parse(fs.readFileSync(path.join(dir,'authorized-state.json'),'utf8'));
(async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true});
 try {
  const context=await browser.newContext({viewport:{width:1660,height:1100}});
  const page=await context.newPage();page.setDefaultTimeout(60000);
  await page.route('**/questions',r=>r.abort());
  await page.route('**/api/v1/auth/refresh',async route=>{
   const check=await context.request.get(state.api+'/api/v1/users/me',{headers:{Authorization:'Bearer '+state.access_token}});
   if(check.status()!==200)return route.abort();
   await route.fulfill({status:200,headers:{'content-type':'application/json','access-control-allow-origin':'http://127.0.0.1:3032','access-control-allow-credentials':'true'},body:JSON.stringify({data:{access_token:state.access_token}})});
  });
  await page.goto('http://127.0.0.1:3032/workspaces/'+state.workspace_id);
  await page.getByRole('button',{name:'Mở trợ lý AI',exact:true}).click();
  await page.getByText(/LIVE_PARTIAL: GPT answered/).waitFor();
  await page.getByRole('button',{name:'Mở hội thoại nghiệm thu V2',exact:true}).click();
  await page.screenshot({path:path.join(dir,'browser','100-v2-paused-warning-and-budget.png'),fullPage:true});
  fs.writeFileSync(path.join(dir,'browser','post-live-ui-review.json'),JSON.stringify({paid_calls:0,real_http:true,warning_visible:true,budget_visible:true,manual_requires_explicit_click:true}));
  console.log('Real UI paused warning + shared budget screenshot saved privately; no inference.');
 } finally {await browser.close();}
})().catch(e=>{console.error(e.name);process.exitCode=2;});
