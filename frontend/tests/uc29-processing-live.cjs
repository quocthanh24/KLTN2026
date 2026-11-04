// Real API, Chrome, PostgreSQL, MinIO and configured E5/Vision. No success mocks.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const { randomUUID, randomBytes } = require('node:crypto');
const { spawnSync } = require('node:child_process');
const fs = require('node:fs'), path = require('node:path');
const base = process.env.LIVE_API_URL || 'http://localhost:8000';
const front = process.env.FRONTEND_TEST_URL || 'http://localhost:3000';
const output = path.resolve(__dirname, '../../evidence/uc29-processing-20261103');
fs.mkdirSync(output, { recursive: true });
const marker = 'phase5b-' + randomUUID();
const report = { marker, checks: [], documents: [] };
let token, browser;
const backend = path.resolve(__dirname, '../../backend');
function helper(action, doc) {
  const p = spawnSync(path.join(backend, '.venv-win/Scripts/python.exe'), ['-m', 'scripts.verify_document_management_live'], { cwd: backend, input: JSON.stringify({ marker, action, document_id: doc?.id }), encoding: 'utf8' });
  assert.equal(p.status, 0, p.stdout + p.stderr); return p.stdout.trim();
}
async function api(method, route, body, expected = 200) {
  const r = await fetch(base + '/api/v1' + route, { method, headers: { Connection: 'close', ...(token ? { Authorization: 'Bearer ' + token } : {}), ...(body ? { 'Content-Type': 'application/json' } : {}) }, body: body ? JSON.stringify(body) : undefined });
  assert.equal(r.status, expected, route + ': ' + await r.clone().text());
  return expected === 204 ? null : (await r.json()).data;
}
async function upload(channel, bytes, name, mime) {
  const f = new FormData(); f.append('file', new Blob([bytes], { type: mime }), name);
  const r = await fetch(base + '/api/v1/channels/' + channel + '/learning-documents', { method: 'POST', headers: { Connection: 'close', Authorization: 'Bearer ' + token }, body: f });
  assert.equal(r.status, 201); return (await r.json()).data;
}
async function finished(doc) {
  for (let i = 0; i < 160; i++) {
    const d = await api('GET', '/learning-documents/' + doc.id);
    if (d.status !== 'PROCESSING') return d;
    await new Promise(r => setTimeout(r, 1000));
  }
  throw Error('Processing timeout');
}
(async () => {
  const password = randomBytes(24).toString('base64url'), email = marker + '-owner@example.com';
  await api('POST', '/auth/register', { full_name: 'UC29 verification', email, password, confirm_password: password }, 201);
  token = (await api('POST', '/auth/login', { email, password })).access_token;
  const w = await api('POST', '/workspaces', { name: marker }, 201);
  const channel = (await api('GET', '/workspaces/' + w.id + '/channels')).find(c => c.is_default);
  const other = await api('POST', '/workspaces/' + w.id + '/channels', { name: 'other', type: 'TEXT' }, 201);
  browser = await chromium.launch({ channel: 'chrome', headless: true });
  const page = await browser.newPage(); page.setDefaultTimeout(30000);
  await page.goto(front + '/login');
  await page.getByLabel('Email', { exact: true }).fill(email);
  await page.getByLabel('Mật khẩu', { exact: true }).fill(password);
  await page.getByRole('button', { name: 'Đăng nhập', exact: true }).click();
  await page.getByRole('heading', { name: 'Xin chào, UC29 verification' }).waitFor();
  await page.goto(front + '/workspaces/' + w.id);
  const fixture = await browser.newPage();
  await fixture.setContent('<p>PostgreSQL stores relational data. MinIO stores private originals.</p>');
  const pdf = await fixture.pdf(); await fixture.close();
  const doc = await upload(channel.id, pdf, 'native.pdf', 'application/pdf');
  assert.equal(doc.status, 'PROCESSING');
  await page.reload();
  const row = page.locator('[data-document-id="' + doc.id + '"]'); await row.waitFor();
  const ready = await finished(doc); assert.equal(ready.status, 'READY'); assert.equal(ready.derived_state.full_pipeline_complete, true);
  assert.equal(await row.locator('[data-status], details').count(), 0);
  assert.equal(await row.getByRole('button', { name: 'Thử xử lý lại', exact: true }).count(), 0);
  await page.screenshot({ path: path.join(output, 'ready-document.png'), fullPage: true });
  report.documents.push({ kind: 'native_pdf', ...JSON.parse(helper('inspect', doc)) });
  report.checks.push('native PDF actual vector READY; refresh persists; document UI hides processing details');
  await api('GET', '/learning-documents/' + doc.id + '?channel_id=' + other.id, undefined, 404);
  assert.deepEqual(await api('GET', '/channels/' + other.id + '/learning-documents'), []);
  report.checks.push('cross-channel detail/list isolation');
  const word = await upload(channel.id, Buffer.from(helper('word_fixture'), 'base64'), 'native.docx', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document');
  assert.equal((await finished(word)).status, 'READY');
  report.documents.push({ kind: 'docx', ...JSON.parse(helper('inspect', word)) });
  const beforeRetry = JSON.parse(helper('inspect', word));
  assert.equal((await api('POST', '/learning-documents/' + word.id + '/retry')).status, 'PROCESSING');
  assert.equal((await finished(word)).status, 'READY');
  const afterRetry = JSON.parse(helper('inspect', word));
  assert.equal(afterRetry.chunk_ids.length, beforeRetry.chunk_ids.length);
  assert.equal(new Set(afterRetry.chunk_ids).size, afterRetry.chunk_ids.length);
  assert.equal(afterRetry.checksum, beforeRetry.checksum);
  report.retry_success = { before: beforeRetry, after: afterRetry };
  const blankFixture = spawnSync(path.join(backend, '.venv-win/Scripts/python.exe'), ['-c', 'import base64;from tests.test_document_rag import pdf_bytes;print(base64.b64encode(pdf_bytes(blank=True)).decode())'], { cwd: backend, encoding: 'utf8' });
  assert.equal(blankFixture.status, 0);
  const blank = await upload(channel.id, Buffer.from(blankFixture.stdout.trim(), 'base64'), 'blank.pdf', 'application/pdf');
  assert.equal((await finished(blank)).status, 'FAILED');
  const count = (await api('GET', '/channels/' + channel.id + '/learning-documents')).length;
  assert.equal((await api('POST', '/learning-documents/' + blank.id + '/retry')).status, 'PROCESSING');
  assert.equal((await finished(blank)).status, 'FAILED');
  assert.equal((await api('GET', '/channels/' + channel.id + '/learning-documents')).length, count);
  report.documents.push({ kind: 'blank_retry', ...JSON.parse(helper('inspect', blank)) });
  report.checks.push('DOCX real READY; empty PDF fails honestly; retry queues, fails honestly, no duplicate original');
  const scanFixture = spawnSync(path.join(backend, '.venv-win/Scripts/python.exe'), ['-c', 'import base64;from tests.test_document_multimodal import scan_pdf;print(base64.b64encode(scan_pdf()).decode())'], { cwd: backend, encoding: 'utf8' });
  assert.equal(scanFixture.status, 0);
  const scan = await upload(channel.id, Buffer.from(scanFixture.stdout.trim(), 'base64'), 'scan.pdf', 'application/pdf');
  const scanResult = await finished(scan);
  report.scan = { status: scanResult.status, error: scanResult.processing_error, complete: scanResult.derived_state?.full_pipeline_complete };
  report.documents.push({ kind: 'scan', ...JSON.parse(helper('inspect', scan)) });
  report.scan_verdict = scanResult.status === 'READY' ? 'PASS: configured real Vision and E5 completed' : 'NOT READY: ' + scanResult.processing_error;
  const largeFixture = spawnSync(path.join(backend, '.venv-win/Scripts/python.exe'), ['-c', 'import base64,io;from pypdf import PdfReader,PdfWriter;from tests.test_document_multimodal import scan_pdf;r=PdfReader(io.BytesIO(scan_pdf()));w=PdfWriter();[w.add_page(r.pages[0]) for _ in range(81)];b=io.BytesIO();w.write(b);print(base64.b64encode(b.getvalue()).decode())'], { cwd: backend, encoding: 'utf8' });
  assert.equal(largeFixture.status, 0);
  const large = await upload(channel.id, Buffer.from(largeFixture.stdout.trim(), 'base64'), 'scan81.pdf', 'application/pdf');
  const blocked = await finished(large);
  assert.equal(blocked.status, 'FAILED');
  assert.equal(blocked.derived_state.retry_blocker, 'DOCUMENT_PAGE_LIMIT');
  await api('POST', '/learning-documents/' + large.id + '/retry', undefined, 409);
  report.large_scan = { status: blocked.status, error: blocked.processing_error, retry_blocker: blocked.derived_state.retry_blocker };
  report.checks.push('81-page scan NOT READY, retry API 409, no cap increase');
  await page.reload(); await page.locator('[data-document-id="' + scan.id + '"]').waitFor();
  await page.screenshot({ path: path.join(output, 'final-state.png'), fullPage: true });
  const largeRow = page.locator('[data-document-id="' + large.id + '"]');
  assert.equal(await largeRow.locator('[data-status], details').count(), 0);
  assert.equal(await largeRow.getByRole('button', { name: 'Thử xử lý lại', exact: true }).count(), 0);
  await page.screenshot({ path: path.join(output, 'scan81-document.png'), fullPage: true });
  report.checks.push('real scan attempted without provider mocks');
  console.log(JSON.stringify(report, null, 2));
})().catch(e => { report.failure = e.stack; console.error(e); process.exitCode = 1; }).finally(async () => {
  fs.writeFileSync(path.join(output, 'live.json'), JSON.stringify(report, null, 2));
  if (browser) await browser.close();
  try { console.log(helper('cleanup')); } catch (e) { console.error(e); }
});
