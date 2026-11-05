// Real UI/API/PostgreSQL/MinIO/E5. No mocked successes or new model calls.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'D:/Temp/kltn-browser-verification/node_modules/playwright');
const assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path');
const { randomUUID, randomBytes } = require('node:crypto'), { spawnSync } = require('node:child_process');
const front = process.env.FRONTEND_TEST_URL || 'http://localhost:3000', back = process.env.LIVE_API_URL || 'http://localhost:8000';
const out = path.resolve(__dirname, '../../evidence/documents-clean-ui-20261103'); fs.mkdirSync(out, { recursive: true });
const marker = 'phase5b-' + randomUUID(), report = { marker, checks: [], documents: [] };
const backend = path.resolve(__dirname, '../../backend'); let browser;
const accounts = ['owner', 'member'].map(role => ({ role, email: marker + '-' + role + '@example.com', password: randomBytes(24).toString('base64url') }));
function helper(action, doc) {
  const p = spawnSync(path.join(backend, '.venv-win/Scripts/python.exe'), ['-m', 'scripts.verify_document_management_live'], { cwd: backend, input: JSON.stringify({ marker, action, document_id: doc?.id }), encoding: 'utf8', timeout: 30000 });
  assert.equal(p.status, 0, p.stdout + p.stderr); return p.stdout.trim();
}
function save() { fs.writeFileSync(path.join(out, 'live.json'), JSON.stringify(report, null, 2)); }
async function api(actor, method, route, body, expected = 200) {
  const r = await fetch(back + '/api/v1' + route, { method, headers: { Connection: 'close', ...(actor?.token ? { Authorization: 'Bearer ' + actor.token } : {}), ...(body ? { 'Content-Type': 'application/json' } : {}) }, body: body ? JSON.stringify(body) : undefined });
  assert.equal(r.status, expected, route + ': ' + await r.clone().text()); return expected === 204 ? null : (await r.json()).data;
}
async function finish(actor, doc) { for (let i = 0; i < 120; i++) { const d = await api(actor, 'GET', '/learning-documents/' + doc.id); if (d.status !== 'PROCESSING') return d; await new Promise(r => setTimeout(r, 500)); } throw Error('Pipeline timeout'); }
(async () => {
  for (const a of accounts) a.id = (await api(null, 'POST', '/auth/register', { full_name: 'Clean documents ' + a.role, email: a.email, password: a.password, confirm_password: a.password }, 201)).id;
  browser = await chromium.launch({ channel: 'chrome', headless: true }); const pages = [];
  for (const a of accounts) {
    const page = await browser.newPage({ viewport: { width: 1400, height: 1000 } }); page.setDefaultTimeout(30000);
    await page.goto(front + '/login'); await page.getByLabel('Email', { exact: true }).fill(a.email); await page.getByLabel('Mật khẩu', { exact: true }).fill(a.password);
    const reply = page.waitForResponse(r => r.request().method() === 'POST' && r.url().endsWith('/auth/login'));
    await page.getByRole('button', { name: 'Đăng nhập', exact: true }).click(); a.token = (await (await reply).json()).data.access_token;
    await page.getByRole('heading', { name: 'Xin chào, Clean documents ' + a.role }).waitFor(); pages.push(page);
  }
  const [owner, member] = accounts, [a, b] = pages;
  const workspace = await api(owner, 'POST', '/workspaces', { name: marker }, 201);
  const invite = await api(owner, 'POST', '/workspaces/' + workspace.id + '/invitations', { invitee_user_id: member.id }, 201);
  await api(member, 'POST', '/workspace-invitations/' + invite.token + '/accept');
  const channel = (await api(owner, 'GET', '/workspaces/' + workspace.id + '/channels')).find(c => c.is_default);
  for (const p of pages) await p.goto(front + '/workspaces/' + workspace.id);
  const area = p => p.getByRole('region', { name: 'Tài liệu học tập', exact: true });
  const row = (p, doc) => area(p).locator('[data-document-id="' + doc.id + '"]');
  const fixture = await browser.newPage(); await fixture.setContent('<p>Private learning document: PostgreSQL stores data; MinIO stores originals.</p>'); const pdf = await fixture.pdf(); await fixture.close();
  const word = Buffer.from(helper('word_fixture'), 'base64');
  async function upload(bytes, name, mime) {
    await area(a).getByRole('button', { name: 'Tải tài liệu lên', exact: true }).click();
    await area(a).getByLabel('Chọn tài liệu', { exact: true }).setInputFiles({ name, mimeType: mime, buffer: bytes });
    const reply = a.waitForResponse(r => r.request().method() === 'POST' && r.url().endsWith('/learning-documents'));
    await area(a).getByRole('button', { name: 'Xác nhận tải lên', exact: true }).click(); const r = await reply; assert.equal(r.status(), 201);
    const doc = (await r.json()).data; await row(a, doc).waitFor(); await area(a).getByText('Đã tải tài liệu lên.', { exact: true }).waitFor(); return doc;
  }
  const pdfDoc = await upload(pdf, 'lesson.pdf', 'application/pdf'), wordDoc = await upload(word, 'lesson.docx', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document');
  assert.equal((await finish(owner, pdfDoc)).status, 'READY'); assert.equal((await finish(owner, wordDoc)).status, 'READY');
  report.documents.push(JSON.parse(helper('inspect', pdfDoc)), JSON.parse(helper('inspect', wordDoc)));
  report.checks.push('PDF/DOCX actual UI uploads; backend pipeline still generates real vectors and READY'); save();
  function noTechnicalText(text) { assert.ok(!/Cần xử lý bổ sung|Một số nội dung chưa thể đọc tự động|Chi tiết xử lý|chỉ mục|OCR|Vision|embedding|chunk|RAG|Thử xử lý lại|Trạng thái xử lý/.test(text), text); }
  noTechnicalText(await area(a).innerText()); noTechnicalText(await area(b).innerText());
  assert.equal(await area(a).locator('[data-status]').count(), 0);
  assert.equal(await row(b, pdfDoc).getByRole('button', { name: 'Chỉnh sửa', exact: true }).count(), 0);
  assert.equal(await row(b, pdfDoc).getByRole('button', { name: 'Xóa tài liệu', exact: true }).count(), 0);
  await api(member, 'PATCH', '/learning-documents/' + pdfDoc.id, { name: 'Forbidden' }, 403);
  await api(member, 'DELETE', '/learning-documents/' + pdfDoc.id, undefined, 403);
  report.checks.push('clean owner/member lists; no processing badge/details/retry; API still rejects member edit/delete');
  await row(a, pdfDoc).getByRole('button', { name: 'Xem tài liệu', exact: true }).click();
  let dialog = a.getByRole('dialog', { name: 'Xem tài liệu', exact: true }); await dialog.locator('canvas').waitFor(); noTechnicalText(await dialog.innerText());
  await dialog.getByRole('button', { name: 'Đóng tài liệu', exact: true }).click();
  await row(b, wordDoc).getByRole('button', { name: 'Xem tài liệu', exact: true }).click();
  dialog = b.getByRole('dialog', { name: 'Xem tài liệu', exact: true }); await dialog.getByText('PostgreSQL lưu trữ dữ liệu quan hệ. MinIO lưu tài liệu gốc riêng tư.', { exact: true }).waitFor();
  await dialog.getByRole('button', { name: 'Đóng tài liệu', exact: true }).click();
  for (const [doc, bytes] of [[pdfDoc, pdf], [wordDoc, word]]) {
    const pending = b.waitForEvent('download'); await row(b, doc).getByRole('button', { name: 'Tải tệp gốc', exact: true }).click(); const file = await pending;
    assert.equal(file.suggestedFilename(), doc.original_filename); assert.deepEqual(fs.readFileSync(await file.path()), bytes);
  }
  report.checks.push('actual PDF canvas and DOCX original text open; member downloads match original bytes');
  await row(a, pdfDoc).getByRole('button', { name: 'Chỉnh sửa', exact: true }).click();
  const edit = a.getByRole('form', { name: 'Chỉnh sửa tài liệu', exact: true }); await edit.getByLabel('Tên tài liệu', { exact: true }).fill('Updated PDF'); await edit.getByLabel('Mô tả', { exact: true }).fill('Short learning description'); await edit.getByRole('button', { name: 'Lưu thay đổi', exact: true }).click(); await row(a, pdfDoc).getByText('Updated PDF', { exact: true }).waitFor();
  const changed = JSON.parse(helper('inspect', pdfDoc)); assert.deepEqual(changed.chunk_ids, report.documents[0].chunk_ids); assert.equal(changed.original_sha256, report.documents[0].original_sha256);
  await a.screenshot({ path: path.join(out, 'clean-document-list.png'), fullPage: true });
  report.checks.push('owner metadata edit persists without vector/original changes');
  // A genuinely failed blank PDF must remain visible/openable/downloadable without technical badges.
  const p = spawnSync(path.join(backend, '.venv-win/Scripts/python.exe'), ['-c', 'import base64;from tests.test_document_rag import pdf_bytes;print(base64.b64encode(pdf_bytes(blank=True)).decode())'], { cwd: backend, encoding: 'utf8' }); assert.equal(p.status, 0);
  const blank = await upload(Buffer.from(p.stdout.trim(), 'base64'), 'blank.pdf', 'application/pdf'); assert.equal((await finish(owner, blank)).status, 'FAILED'); noTechnicalText(await row(a, blank).innerText());
  await row(a, blank).getByRole('button', { name: 'Xem tài liệu', exact: true }).click(); dialog = a.getByRole('dialog', { name: 'Xem tài liệu', exact: true }); await dialog.locator('canvas').waitFor(); await dialog.getByRole('button', { name: 'Đóng tài liệu', exact: true }).click();
  const download = a.waitForEvent('download'); await row(a, blank).getByRole('button', { name: 'Tải tệp gốc', exact: true }).click(); await download;
  const failedState = JSON.parse(helper('inspect', blank)); assert.equal(failedState.chunk_ids.length, 0); assert.ok(!failedState.eligible_documents.includes(blank.id)); report.documents.push(failedState);
  report.checks.push('FAILED processing does not block preview/download; no chunks or RAG eligibility for failed blank');
  helper('remove_original', blank); await row(a, blank).getByRole('button', { name: 'Tải tệp gốc', exact: true }).click(); await area(a).getByRole('alert').filter({ hasText: 'Tệp tài liệu không còn khả dụng' }).waitFor();
  report.checks.push('real missing MinIO original still shows download error');
  await row(a, wordDoc).getByRole('button', { name: 'Xóa tài liệu', exact: true }).click(); await a.getByRole('dialog', { name: 'Xác nhận xóa tài liệu', exact: true }).getByRole('button', { name: 'Xác nhận xóa', exact: true }).click(); await row(a, wordDoc).waitFor({ state: 'detached' });
  assert.equal((await api(owner, 'GET', '/channels/' + channel.id + '/learning-documents')).some(d => d.id === wordDoc.id), false);
  report.checks.push('owner delete removes only the selected test document');
  await a.reload(); await row(a, pdfDoc).waitFor(); noTechnicalText(await row(a, pdfDoc).innerText());
  report.status = 'PASS'; save();
})().catch(error => { report.status = 'FAIL'; report.failure = error.stack; save(); console.error(error); process.exitCode = 1; }).finally(async () => {
  if (browser) await browser.close(); try { console.log(helper('cleanup')); } catch (e) { console.error(e); }
  console.log(JSON.stringify({ status: report.status, checks: report.checks, failure: report.failure }, null, 2));
});
