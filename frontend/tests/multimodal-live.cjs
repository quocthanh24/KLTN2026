// Real Chrome, authenticated HTTP, PostgreSQL/MinIO/E5/OpenAI. No AI mocks.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {randomUUID,randomBytes}=require('node:crypto');
const backend='http://localhost:8015',frontend='http://localhost:3000';
const dir=path.resolve(__dirname,'../../docs/progress/evidence/multimodal-document-rag');
const marker='multimodal-'+randomUUID(),password=randomBytes(24).toString('base64url');
const report={marker,checks:[],documents:[],answers:[],errors:[]};
const pageOnly=process.argv.includes('--page-only');
function save(){fs.mkdirSync(dir,{recursive:true});fs.writeFileSync(path.join(dir,pageOnly?'page-specific.json':'browser.json'),JSON.stringify(report,null,2));}
function pass(name){report.checks.push(name);save();console.log('PASS '+name);}
async function api(token,method,route,body,status=200){
 const r=await fetch(backend+'/api/v1'+route,{method,headers:{...(token?{Authorization:'Bearer '+token}:{}),...(body?{'Content-Type':'application/json'}:{})},body:body?JSON.stringify(body):undefined});
 assert.equal(r.status,status,`${method} ${route}: ${r.status}`);return status===204?null:(await r.json()).data;
}
(async()=>{let browser;try{
 const resume=process.argv.includes('--resume')?JSON.parse(fs.readFileSync('D:/Temp/kltn-multimodal/resume.json','utf8')):null;
 let token,ws,channel,other;
 if(resume){
  if(!fs.existsSync(path.join(dir,'browser-first-attempt.json')))fs.copyFileSync(path.join(dir,'browser.json'),path.join(dir,'browser-first-attempt.json'));
  report.marker=JSON.parse(fs.readFileSync(path.join(dir,'browser.json'),'utf8')).marker;
  token=resume.token;ws={id:resume.workspace};channel={id:resume.channel};other={id:resume.other};
 }else{
 const email=marker+'@example.com';
 await api(null,'POST','/auth/register',{email,password,confirm_password:password,full_name:'Multimodal Verification'},201);
 token=(await api(null,'POST','/auth/login',{email,password})).access_token;
 ws=await api(token,'POST','/workspaces',{name:marker},201);
 channel=(await api(token,'GET',`/workspaces/${ws.id}/channels`)).find(c=>c.is_default);
 other=await api(token,'POST',`/workspaces/${ws.id}/channels`,{name:'isolated',type:'TEXT'},201);
 }
 report.workspace=ws.id;
 report.channel=channel.id;
 browser=await chromium.launch({channel:'chrome',headless:true});const page=await browser.newPage();page.setDefaultTimeout(45000);
 page.on('pageerror',e=>report.errors.push(e.message));
 // Only changes transport destination to the isolated real server.
 await page.route('**/api/v1/**',r=>r.continue({url:r.request().url().replace(/https?:\/\/[^/]+/,backend)}));
 await page.route('**/api/learning-documents/**',r=>r.continue({url:r.request().url().replace(/https?:\/\/[^/]+\/api\/learning-documents\//,backend+'/api/v1/learning-documents/')}));
 {
 await page.goto(frontend+'/login');await page.getByLabel('Email',{exact:true}).fill(resume?resume.email:marker+'@example.com');await page.getByLabel('Mật khẩu',{exact:true}).fill(resume?resume.password:password);
 const login=page.waitForResponse(r=>r.url().endsWith('/auth/login')&&r.request().method()==='POST');
 await page.getByRole('button',{name:'Đăng nhập',exact:true}).click();await page.getByRole('heading',{name:'Xin chào, Multimodal Verification'}).waitFor();
 token=(await (await login).json()).data.access_token;
 }
 await page.goto(frontend+`/workspaces/${ws.id}`);
 const area=page.getByRole('region',{name:'Tài liệu học tập',exact:true});
 async function upload(file,name){
  const existing=(await api(token,'GET',`/channels/${channel.id}/learning-documents`)).find(d=>d.name===name&&d.status==='READY');
  if(existing){report.documents.push({id:existing.id,name,status:'READY'});save();return existing;}
  await area.getByRole('button',{name:'Tải tài liệu lên',exact:true}).click();await area.locator('input[type=file]').setInputFiles('D:/Temp/kltn-multimodal/'+file);
  await area.getByLabel('Tên tài liệu (tùy chọn)',{exact:true}).fill(name);
  const response=page.waitForResponse(r=>r.request().method()==='POST'&&r.url().endsWith('/learning-documents'));
  await area.getByRole('button',{name:'Xác nhận tải lên',exact:true}).click();const result=await response;assert.equal(result.status(),201);let doc=(await result.json()).data;
  assert.equal(doc.status,'PROCESSING');pass(name+' upload PROCESSING');
  for(let i=0;i<360;i++){
   doc=await api(token,'GET',`/learning-documents/${doc.id}`);
   if(doc.status!=='PROCESSING')break;
   if(i%20===0)console.log('Processing '+name+' '+i*2+'s');
   await page.waitForTimeout(2000);
  }
  report.documents.push({id:doc.id,name,status:doc.status,error:doc.processing_error});save();
  assert.equal(doc.status,'READY',name+': '+doc.processing_error);
  await area.locator(`[data-document-id="${doc.id}"] [data-status=READY]`).waitFor();pass(name+' real pipeline READY');return doc;
 }
 let scan;
 if(resume){
  scan=await api(token,'GET',`/learning-documents/${resume.document}`);
  if(scan.status==='FAILED'){
  const row=area.locator(`[data-document-id="${resume.document}"]`);
  await row.getByRole('button',{name:'Thử xử lý lại',exact:true}).waitFor();
  const [response]=await Promise.all([page.waitForResponse(r=>r.request().method()==='POST'&&r.url().includes('/retry')),
   row.getByRole('button',{name:'Thử xử lý lại',exact:true}).click()]);
  assert.equal(response.status(),200);pass('Retry failed original via UI without re-upload');
  }
  for(let i=0;i<360;i++){
   scan=await api(token,'GET',`/learning-documents/${resume.document}`);
   if(scan.status!=='PROCESSING')break;
   if(i%20===0)console.log('Retry processing '+i*2+'s');await page.waitForTimeout(2000);
  }
  report.documents.push({id:scan.id,name:scan.name,status:scan.status,error:scan.processing_error});save();
  assert.equal(scan.status,'READY','Retry: '+scan.processing_error);pass('16-page actual scan READY after retry');
 }else{scan=await upload('actual-scan.pdf','AV4 scan verification.pdf');}
 await upload('native.pdf','Native regression.pdf');
 await upload('native.docx','Word regression.docx');
 const chart=await upload('chart-scan.pdf','Chart verification.pdf');
 await page.reload();await area.locator(`[data-document-id="${scan.id}"] [data-status=READY]`).waitFor();pass('Reload persists READY');
 const ai=page.getByRole('region',{name:'Trợ lý AI',exact:true});await ai.getByRole('button',{name:'Mở trợ lý AI',exact:true}).click();await ai.getByRole('button',{name:'Hội thoại mới',exact:true}).click();
 const input=ai.getByLabel('Câu hỏi cho trợ lý AI');
 async function ask(question){
  await input.fill(question);const response=page.waitForResponse(r=>r.request().method()==='POST'&&r.url().endsWith('/questions'),{timeout:135000});await input.press('Enter');
  const r=await response;assert.equal(r.status(),200);const answer=(await r.json()).data;
  report.answers.push({question,status:answer.status,sources:answer.sources.map(s=>({document_id:s.document_id,page:s.location.page})),timings:answer.timings_ms});save();
  await page.waitForFunction(()=>!document.querySelector('textarea[disabled]'));return answer;
 }
 let answer=await ask('Trong AV4 scan verification.pdf, trang 1, câu 1 nói gì và bốn lựa chọn là gì?');
 assert.equal(answer.status,'answered');assert.match(answer.answer,/look into/);assert.ok(answer.sources.every(s=>s.document_id===scan.id&&s.location.page===1));pass('Actual scan Q1 and A/B/C/D, citation PDF page 1');
 await ai.getByTitle('Mở nguồn: AV4 scan verification.pdf',{exact:true}).first().click();const viewer=ai.getByRole('dialog',{name:'Nguồn tham khảo',exact:true});await viewer.locator('canvas').first().waitFor();await viewer.getByRole('button',{name:'Đóng nguồn',exact:true}).click();pass('Citation opens real PDF.js');
 if(pageOnly){report.page_specific_acceptance=true;save();return;}
 answer=await ask('Giải thích vì sao chọn đáp án đúng ở câu 1 trang 1 trong AV4 scan verification.pdf, dẫn nguồn trang.');
 report.explanationStatus=answer.status;save();
 if(answer.status==='answered'){pass('Exam explanation grounded in source');}
 else{report.errors.push('Mandatory exam explanation returned '+answer.status);save();}
 answer=await ask('Trong Chart verification.pdf trang 1, cột màu nào cao nhất và giá trị bao nhiêu?');
 assert.equal(answer.status,'answered');assert.match(answer.answer,/30/);assert.ok(answer.sources.every(s=>s.document_id===chart.id&&s.location.page===1));pass('Image-only chart visual Q&A');
 await page.screenshot({path:path.join(dir,'chart-qa.png'),fullPage:true});
 await api(token,'GET',`/learning-documents/${chart.id}/pages/1/image?channel_id=${other.id}`,null,404);
 await api(null,'GET',`/learning-documents/${chart.id}/pages/1/image`,null,401);
 await api(token,'POST',`/learning-documents/${chart.id}/retry?channel_id=${other.id}`,null,404);pass('Wrong-channel page/retry and anonymous denied');
 const image=await fetch(backend+`/api/v1/learning-documents/${chart.id}/pages/1/image?channel_id=${channel.id}`,{headers:{Authorization:'Bearer '+token}});assert.equal(image.status,200);assert.match(image.headers.get('cache-control'),/no-store/);pass('Authenticated private page JPEG');
 assert.equal(report.errors.length,0);report.acceptance=true;save();
 }catch(e){report.failure=e.message;save();console.error(e);process.exitCode=1;}finally{if(browser)await browser.close();}})();
