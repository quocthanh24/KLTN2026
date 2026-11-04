// Run once with test server DOCUMENT_VISION_ENABLED=false, then with true --retry.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const state=JSON.parse(fs.readFileSync('D:/Temp/kltn-multimodal/resume.json','utf8'));
const out=path.resolve(__dirname,'../../docs/progress/evidence/multimodal-document-rag/dependency.json');
const retry=process.argv.includes('--retry');
(async()=>{let browser;const report=retry?JSON.parse(fs.readFileSync(out,'utf8')):{};try{
 browser=await chromium.launch({channel:'chrome',headless:true});const page=await browser.newPage();
 await page.route('**/api/v1/**',r=>r.continue({url:r.request().url().replace(/https?:\/\/[^/]+/,'http://localhost:8015')}));
 await page.goto('http://localhost:3000/login');await page.getByLabel('Email',{exact:true}).fill(state.email);await page.getByLabel('Mật khẩu',{exact:true}).fill(state.password);
 const login=page.waitForResponse(r=>r.request().method()==='POST'&&r.url().endsWith('/auth/login'));
 await page.getByRole('button',{name:'Đăng nhập',exact:true}).click();const token=(await (await login).json()).data.access_token;
 await page.getByRole('heading',{name:'Xin chào, Multimodal Verification'}).waitFor();await page.goto('http://localhost:3000/workspaces/'+state.workspace);
 const area=page.getByRole('region',{name:'Tài liệu học tập',exact:true});
 if(!retry){
  await area.getByRole('button',{name:'Tải tài liệu lên',exact:true}).click();await area.locator('input[type=file]').setInputFiles('D:/Temp/kltn-multimodal/chart-scan.pdf');
  await area.getByLabel('Tên tài liệu (tùy chọn)',{exact:true}).fill('Dependency failure verification.pdf');
  const response=page.waitForResponse(r=>r.request().method()==='POST'&&r.url().endsWith('/learning-documents'));
  await area.getByRole('button',{name:'Xác nhận tải lên',exact:true}).click();report.id=(await (await response).json()).data.id;
 }else{
  await area.locator(`[data-document-id="${report.id}"]`).getByRole('button',{name:'Thử xử lý lại',exact:true}).click();
 }
 let doc;
 for(let i=0;i<60;i++){
  const r=await fetch('http://localhost:8015/api/v1/learning-documents/'+report.id,{headers:{Authorization:'Bearer '+token}});assert.equal(r.status,200);doc=(await r.json()).data;
  if(doc.status!=='PROCESSING')break;await page.waitForTimeout(1000);
 }
 const row=area.locator(`[data-document-id="${report.id}"]`);
 if(!retry){
  assert.equal(doc.status,'FAILED');assert.equal(doc.processing_error,'DOCUMENT_VISION_DISABLED:page=1');
  await row.getByRole('button',{name:'Thử xử lý lại',exact:true}).waitFor();await row.getByText(/Chức năng OCR\/Vision đang tắt/).waitFor();report.disabled_failure=true;
 }else{
  assert.equal(doc.status,'READY');await row.locator('[data-status=READY]').waitFor();report.retry_ready=true;
 }
 const original=await fetch('http://localhost:8015/api/v1/learning-documents/'+report.id+'/download',{headers:{Authorization:'Bearer '+token}});assert.equal(original.status,200);
 assert.deepEqual(Buffer.from(await original.arrayBuffer()),fs.readFileSync('D:/Temp/kltn-multimodal/chart-scan.pdf'));report.original_preserved=true;
 await page.screenshot({path:out.replace('.json',retry?'-retry.png':'-failed.png'),fullPage:true});
 console.log(retry?'PASS real UI retry after re-enabling Vision':'PASS real disabled dependency FAILED + exact page + retained original');
 }catch(e){report.failure=e.message;process.exitCode=1;console.error(e.message);}finally{fs.writeFileSync(out,JSON.stringify(report,null,2));if(browser)await browser.close();}})();
