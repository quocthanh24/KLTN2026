// Real browser + authorized MinIO source + V2 + real SDK serialization. No live GPT.
const {chromium}=require('D:/Temp/kltn-browser-verification/node_modules/playwright');
const fs=require('fs'),path=require('path'),assert=require('node:assert/strict');
const dir='D:/Temp/kltn-original-image-fix-20261102-private';
const s=JSON.parse(fs.readFileSync(path.join(dir,'authorized-state.json'),'utf8'));
const out=path.join(dir,'browser');fs.mkdirSync(out,{recursive:true});
(async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true});
 const result={paid_calls:0,real_document_http:true,provider_transport:'OFFLINE MockTransport; protocol only',checks:[],pages:[]};
 try {
  const context=await browser.newContext({viewport:{width:1640,height:1080}});
  const page=await context.newPage();page.setDefaultTimeout(90000);
  await page.route('**/api/v1/auth/refresh',async route=>{
   assert.equal((await context.request.get(s.api+'/api/v1/users/me',{headers:{Authorization:'Bearer '+s.access_token}})).status(),200);
   await route.fulfill({status:200,headers:{'content-type':'application/json','access-control-allow-origin':'http://127.0.0.1:3033','access-control-allow-credentials':'true'},body:JSON.stringify({data:{access_token:s.access_token}})});
  });
  await page.goto('http://127.0.0.1:3033/workspaces/'+s.workspace_id);
  await page.getByRole('button',{name:'Mở trợ lý AI',exact:true}).click();
  await page.getByText('V2 ORIGINAL IMAGE DELIVERY — OFFLINE DRY-RUN',{exact:true}).waitFor();
  for(const physical of [5,6,7]){
   const wait=page.waitForResponse(r=>r.url().endsWith('/ai/original-delivery-dry-run')&&r.request().method()==='POST',{timeout:90000});
   await page.getByRole('button',{name:'Kiểm tra payload trang '+physical,exact:true}).click();
   const response=await wait;assert.equal(response.status(),200);
   const proof=(await response.json()).data;
   assert.equal(proof.channel_id,s.channel_id);assert.equal(proof.reader_called,true);assert.equal(proof.paid_inference_calls,0);
   const sent=proof.dispatches.filter(d=>d.images.length);
   assert(sent.length>=1);assert(sent.every(d=>d.source_page_sent===physical));
   assert(sent.every(d=>d.content_types.includes('input_image')&&d.images[0].mime_type==='image/jpeg'));
   assert(proof.dispatches[0].tool_schema_names.includes('read_original_units'));
   result.pages.push({physical_page:physical,serialized_image_requests:sent.length,sha256:sent[0].images[0].sha256});
   await page.screenshot({path:path.join(out,'sdk-payload-page-'+physical+'.png'),fullPage:true});
  }
  const headers={Authorization:'Bearer '+s.access_token};
  const blocked=await context.request.post(s.api+'/api/v1/channels/'+s.channel_id+'/ai/conversations/6a501b69-f1da-48dc-81f3-0ef2f8b91d5c/questions',{headers,data:{question:'must not call inference',request_id:'00000000-0000-4000-a000-000000000001'}});
  assert.equal(blocked.status(),403);result.checks.push('normal inference endpoint blocked403');
  const anonymous=await context.request.post(s.api+'/api/v1/channels/'+s.channel_id+'/ai/original-delivery-dry-run',{data:{page:6}});
  assert.equal(anonymous.status(),401);result.checks.push('anonymous dry access denied401');
  result.checks.push('pages5–7 original JPEG hashes present in SDK-serialized multimodal input');
  fs.writeFileSync(path.join(out,'results.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result));
 } finally {await browser.close();}
})().catch(e=>{console.error(e.name+': '+e.message.slice(0,200));process.exitCode=2;});
