// Actual local app/API/MinIO/original PDF. No mocked document data or screenshots.
// Bootstrap in-memory frontend auth with an EXISTING validated session token;
// all /users/me and document requests are real authenticated backend requests.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'D:/Temp/kltn-browser-verification/node_modules/playwright');
const fs = require('fs');
const path = require('path');
const assert = require('node:assert/strict');
const crypto = require('crypto');
const state=JSON.parse(fs.readFileSync('D:/Temp/kltn-large-document-auth.json','utf8'));
const out=process.env.AI_CHAT_V2_EVIDENCE_DIR;
if (!out) throw new Error('Explicit evidence directory required');
const frontend='http://127.0.0.1:3031';
const results={model_calls:0,document_responses:'REAL HTTP, no mocked document data',
  auth:'Existing active authorized bearer; refresh bootstrap only, no cookie/session changes',errors:[],checks:[],page_hashes:{},full_pdf_requests:0};
(async()=>{
  const browser=await chromium.launch({channel:'chrome',headless:true});
  try {
    const context=await browser.newContext({viewport:{width:1600,height:1100}});
    const page=await context.newPage();page.setDefaultTimeout(90000);
    page.on('pageerror',error=>results.errors.push(error.message));
    await page.route('**/api/v1/**',async route=>{
      const url=new URL(route.request().url());
      const cors={'access-control-allow-origin':frontend,'access-control-allow-credentials':'true'};
      if(url.pathname.endsWith('/auth/refresh')) {
        const check=await context.request.get(state.api+'/api/v1/users/me',{headers:{Authorization:'Bearer '+state.access_token}});
        assert.equal(check.status(),200);
        return route.fulfill({status:200,headers:{...cors,'content-type':'application/json'},body:JSON.stringify({data:{access_token:state.access_token}})});
      }
      if(url.pathname.includes('/questions')) {results.model_calls++;return route.abort();}
      const response=await route.fetch({url:state.api+url.pathname+url.search});
      await route.fulfill({response,headers:{...response.headers(),...cors}});
    });
    await page.route('**/api/learning-documents/**',async route=>{
      const url=new URL(route.request().url());
      if (/\/(preview|download)$/.test(url.pathname)) { results.full_pdf_requests++;return route.abort(); }
      const response=await route.fetch({url:state.api+url.pathname.replace('/api/','/api/v1/')+url.search,timeout:90000});
      assert.equal(response.status(),200);
      results.page_hashes[url.pathname]=crypto.createHash('sha256').update(await response.body()).digest('hex');
      await route.fulfill({response});
    });
    await page.routeWebSocket('**/api/v1/ws/**',ws=>ws.close()); // Realtime chat is outside this read-only test.
    await page.goto(frontend+'/workspaces/'+state.workspace_id);
    const item=page.locator('li[data-document-id="'+state.document_id+'"]');
    await item.getByText(/0\/81/).waitFor();
    await item.getByText(/81/).first().waitFor();
    await item.getByText(/AI đọc ảnh: chưa bật/).waitFor();
    results.checks.push('actual 81-unit partial index and original capability shown');
    await item.screenshot({path:path.join(out,'actual-file-partial-status.png')});
    await item.getByRole('button',{name:'Xem tài liệu',exact:true}).click();
    const viewer=page.getByRole('dialog',{name:'Xem tài liệu',exact:true});
    await viewer.getByText('Trang 1 / 81',{exact:true}).waitFor();
    for(let i=0;i<5;i++)await viewer.getByRole('button',{name:'Trang sau',exact:true}).click();
    await viewer.getByText('Trang 6 / 81',{exact:true}).waitFor();
    await page.waitForFunction(()=>Array.from(document.querySelectorAll('button')).some(b=>b.textContent==='Trang sau'&&!b.disabled));
    await viewer.getByRole('img').evaluate(image=>image.decode());
    await viewer.screenshot({path:path.join(out,'actual-original-unit1-page6.png')});
    results.checks.push('actual original PDF opens through page6 while index partial');
    const expected=JSON.parse(fs.readFileSync(path.join(out,'actual-original-pages.json'),'utf8')).find(p=>p.physical_page===6);
    assert.equal(results.page_hashes['/api/learning-documents/'+state.document_id+'/pages/6/image'],expected.sha256);
    assert.equal(results.full_pdf_requests,0);
    const anonymous=await context.request.get(state.api+'/api/v1/learning-documents/'+state.document_id+'/pages/6/image');
    assert.equal(anonymous.status(),401);
    results.checks.push('anonymous original page denied 401');
    assert.equal(results.model_calls,0);assert.deepEqual(results.errors,[]);
    await context.close();
  } finally { await browser.close(); }
  fs.writeFileSync(path.join(out,'actual-browser-results.json'),JSON.stringify(results,null,2));
  console.log(JSON.stringify(results));
})().catch(error=>{
  results.failure=String(error);fs.writeFileSync(path.join(out,'actual-browser-results.json'),JSON.stringify(results,null,2));
  console.error(error.message);process.exitCode=1;
});
