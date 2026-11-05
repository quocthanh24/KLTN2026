const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const ts = require('typescript');
require.extensions['.ts'] = (module, filename) => module._compile(ts.transpileModule(fs.readFileSync(filename, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText, filename);
const { documentFileError, documentStatusLabel, documentProcessingError, MAX_DOCUMENT_BYTES } = require('../src/features/learning-documents/contracts.ts');
const { documentPresentation } = require('../src/features/learning-documents/contracts.ts');
test('AI readiness requires complete processing and reports blocked navigation separately', () => {
  assert.equal(documentPresentation({status:'PROCESSING'}).label, 'Đang xử lý');
  assert.equal(documentPresentation({status:'READY',derived_state:{full_pipeline_complete:true}}).label, 'Sẵn sàng cho AI');
  assert.equal(documentPresentation({status:'READY',derived_state:{phase:'INDEXED'}}).label, 'Cần xử lý bổ sung');
  assert.equal(documentPresentation({status:'FAILED',derived_state:{retry_blocker:'DOCUMENT_PAGE_LIMIT'}}).label, 'Cần xử lý bổ sung');
  assert.equal(documentPresentation({status:'FAILED',derived_state:{phase:'ERROR'}}).label, 'Xử lý thất bại');
});
test('PDF and DOCX allow supported types and exact size boundary', () => {
  assert.equal(MAX_DOCUMENT_BYTES, 200 * 1024 * 1024);
  assert.equal(documentFileError({ name: 'lesson.PDF', type: 'application/pdf', size: MAX_DOCUMENT_BYTES }), '');
  assert.equal(documentFileError({ name: 'lesson.docx', type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', size: 200 }), '');
});
test('unsupported MIME, extension, empty and oversized file reject before upload', () => {
  for (const file of [
    { name: 'x.txt', type: 'text/plain', size: 12 }, { name: 'x.pdf', type: 'text/plain', size: 12 },
    { name: 'x.pdf', type: 'application/pdf', size: 0 }, { name: 'x.pdf', type: 'application/pdf', size: MAX_DOCUMENT_BYTES + 1 },
  ]) assert.ok(documentFileError(file));
});
test('status labels distinguish actual processing, ready and failed', () => {
  assert.equal(new Set(Object.values(documentStatusLabel)).size, 3);
});
test('OCR errors retain source and identify failed page without raw details', () => {
  assert.match(documentProcessingError('DOCUMENT_VISION_QUOTA:page=7'), /Trang 7:.*quota/);
  assert.match(documentProcessingError('DOCUMENT_VISION_KEY_MISSING'), /API key/);
  assert.match(documentProcessingError('DOCX_IMAGES_UNSUPPORTED'), /Ảnh nhúng/);
  assert.ok(!documentProcessingError('raw-secret-error').includes('raw-secret-error'));
});

test('private image relay rejects anonymous and malformed locators before upstream', async t => {
  const { GET } = require('../src/app/api/learning-documents/[id]/[action]/[locator]/image/route.ts');
  let calls=0;t.mock.method(global,'fetch',async()=>{calls++;throw new Error('unexpected upstream');});
  const id='12345678-1234-1234-1234-123456789012';
  assert.equal((await GET(new Request('http://local/api'),{params:Promise.resolve({id,action:'regions',locator:'rId9'})})).status,401);
  for(const [action,locator] of [['regions','../secret'],['pages','10001'],['external','rId9']]) {
    assert.equal((await GET(new Request('http://local/api',{headers:{Authorization:'Bearer offline'}}),{params:Promise.resolve({id,action,locator})})).status,404);
  }
  assert.equal(calls,0);
});

test('private image relay forwards authorization only to fixed backend and keeps no-store', async t => {
  const { GET } = require('../src/app/api/learning-documents/[id]/[action]/[locator]/image/route.ts');
  const old=process.env.NEXT_PUBLIC_API_BASE_URL;process.env.NEXT_PUBLIC_API_BASE_URL='http://fixed-backend';
  const id='12345678-1234-1234-1234-123456789012';
  t.mock.method(global,'fetch',async(url,options)=>{
    assert.equal(url,`http://fixed-backend/api/v1/learning-documents/${id}/regions/rId9/image`);
    assert.equal(options.headers.Authorization,'Bearer offline');assert.equal(options.redirect,'error');
    return new Response(new Uint8Array([255,216,255]),{headers:{'content-type':'image/jpeg'}});
  });
  try{
    const response=await GET(new Request('http://local/api',{headers:{Authorization:'Bearer offline'}}),{params:Promise.resolve({id,action:'regions',locator:'rId9'})});
    assert.equal(response.status,200);assert.match(response.headers.get('cache-control'),/private, no-store/);
    assert.equal((await response.arrayBuffer()).byteLength,3);
  }finally{if(old===undefined)delete process.env.NEXT_PUBLIC_API_BASE_URL;else process.env.NEXT_PUBLIC_API_BASE_URL=old;}
});
test('large PDF page 301 uses the same private bounded image relay', async t => {
  const { GET } = require('../src/app/api/learning-documents/[id]/[action]/[locator]/image/route.ts');
  const old=process.env.NEXT_PUBLIC_API_BASE_URL;process.env.NEXT_PUBLIC_API_BASE_URL='http://fixed-backend';
  const id='12345678-1234-1234-1234-123456789012';
  t.mock.method(global,'fetch',async(url,options)=>{
    assert.equal(url,`http://fixed-backend/api/v1/learning-documents/${id}/pages/301/image`);
    assert.equal(options.headers.Authorization,'Bearer offline');assert.equal(options.redirect,'error');
    return new Response(new Uint8Array([255,216,255]),{headers:{'content-type':'image/jpeg'}});
  });
  try {
    const response=await GET(new Request('http://local/api',{headers:{Authorization:'Bearer offline'}}),{params:Promise.resolve({id,action:'pages',locator:'301'})});
    assert.equal(response.status,200);
  } finally { if(old===undefined)delete process.env.NEXT_PUBLIC_API_BASE_URL;else process.env.NEXT_PUBLIC_API_BASE_URL=old; }
});

test('original page relay preserves citation revision and rejects malformed revision', async t => {
  const { GET } = require('../src/app/api/learning-documents/[id]/[action]/[locator]/image/route.ts');
  const old=process.env.NEXT_PUBLIC_API_BASE_URL;process.env.NEXT_PUBLIC_API_BASE_URL='http://fixed-backend';
  const id='12345678-1234-1234-1234-123456789012',revision='a'.repeat(64);let calls=0;
  t.mock.method(global,'fetch',async url=>{
    calls++;assert.equal(url,`http://fixed-backend/api/v1/learning-documents/${id}/pages/6/image?channel_id=${id}&revision=${revision}`);
    return new Response(new Uint8Array([255,216,255]),{headers:{'content-type':'image/jpeg'}});
  });
  try {
    const options={params:Promise.resolve({id,action:'pages',locator:'6'})};
    assert.equal((await GET(new Request(`http://local/api?channel_id=${id}&revision=${revision}`,{headers:{Authorization:'Bearer offline'}}),options)).status,200);
    assert.equal((await GET(new Request('http://local/api?revision=bad',{headers:{Authorization:'Bearer offline'}}),options)).status,422);
    assert.equal(calls,1);
  } finally { if(old===undefined)delete process.env.NEXT_PUBLIC_API_BASE_URL;else process.env.NEXT_PUBLIC_API_BASE_URL=old; }
});
