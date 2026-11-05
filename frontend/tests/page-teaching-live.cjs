// Actual HTTP/Chrome/storage/model, no invented AI results. Transport interruption only is injected.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'D:/Temp/kltn-browser-verification/node_modules/playwright');
const assert = require('node:assert/strict');
const { randomUUID, randomBytes } = require('node:crypto');
const { spawnSync } = require('node:child_process');
const fs = require('node:fs'), path = require('node:path');
const backend = path.resolve(__dirname, '../../backend');
const output = path.resolve(__dirname, '../../evidence/page-teaching-20261103'); fs.mkdirSync(output, { recursive: true });
const apiBase = process.env.LIVE_API_URL || 'http://localhost:8000';
const front = process.env.FRONTEND_TEST_URL || 'http://localhost:3000';
const marker = 'phase5b-' + randomUUID(), code = 'ORCHID-' + randomBytes(4).toString('hex').toUpperCase();
const report = { marker, code, checks: [], turns: [] }; let token, browser;
function helper(data, legacy = false) {
  const p = spawnSync(path.join(backend, '.venv-win/Scripts/python.exe'), ['-m', legacy ? 'scripts.verify_document_management_live' : 'scripts.verify_page_teaching_live'], { cwd: backend, input: JSON.stringify({ marker, ...data }), encoding: 'utf8', timeout: 60000, maxBuffer: 250 * 1024 * 1024 });
  assert.equal(p.status, 0, p.stderr || p.stdout); return p.stdout.trim();
}
async function api(method, route, body, expected = 200) {
  const response = await fetch(apiBase + '/api/v1' + route, { method, headers: { Connection: 'close', ...(token ? { Authorization: 'Bearer ' + token } : {}), ...(body ? { 'Content-Type': 'application/json' } : {}) }, body: body ? JSON.stringify(body) : undefined });
  assert.equal(response.status, expected, route + ': ' + await response.clone().text());
  return expected === 204 ? null : (await response.json()).data;
}
async function upload(channel, base64, name, mime = 'application/pdf') {
  const form = new FormData(); form.append('file', new Blob([Buffer.from(base64, 'base64')], { type: mime }), name);
  const response = await fetch(apiBase + '/api/v1/channels/' + channel + '/learning-documents', { method: 'POST', headers: { Connection: 'close', Authorization: 'Bearer ' + token }, body: form });
  assert.equal(response.status, 201); return (await response.json()).data;
}
async function finished(doc) {
  for (let i = 0; i < 150; i++) { const d = await api('GET', '/learning-documents/' + doc.id); if (d.status !== 'PROCESSING') return d; await new Promise(r => setTimeout(r, 1000)); }
  throw Error('Document processing timeout');
}
function persist() { fs.writeFileSync(path.join(output, 'live.json'), JSON.stringify(report, null, 2)); }
(async () => {
  report.reported_failure = JSON.parse(helper({ action: 'reported_failure' }));
  const fixtures = JSON.parse(helper({ action: 'fixtures', code }));
  const email = marker + '-owner@example.com', password = randomBytes(24).toString('base64url');
  await api('POST', '/auth/register', { full_name: 'Page teaching verification', email, password, confirm_password: password }, 201);
  token = (await api('POST', '/auth/login', { email, password })).access_token;
  const workspace = await api('POST', '/workspaces', { name: marker }, 201);
  const channel = (await api('GET', '/workspaces/' + workspace.id + '/channels')).find(c => c.is_default);
  const other = await api('POST', '/workspaces/' + workspace.id + '/channels', { name: 'other', type: 'TEXT' }, 201);
  const native = await upload(channel.id, fixtures.native, 'biology-six-pages.pdf');
  assert.equal((await finished(native)).status, 'READY');
  const cover = await upload(channel.id, fixtures.cover, 'biology-with-cover.pdf'); assert.equal((await finished(cover)).status, 'READY');
  const word = await upload(channel.id, fixtures.word, 'lesson.docx', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'); assert.equal((await finished(word)).status, 'READY');
  const blank = await upload(channel.id, fixtures.blank, 'blank.pdf'); assert.equal((await finished(blank)).status, 'FAILED');
  report.checks.push('native PDF, covered PDF, DOCX real pipeline READY; blank real FAILED'); persist();
  browser = await chromium.launch({ channel: 'chrome', headless: true });
  const page = await browser.newPage({ viewport: { width: 1450, height: 1100 } }); page.setDefaultTimeout(40000);
  await page.goto(front + '/login'); await page.getByLabel('Email', { exact: true }).fill(email); await page.getByLabel('Mật khẩu', { exact: true }).fill(password); await page.getByRole('button', { name: 'Đăng nhập', exact: true }).click();
  await page.getByRole('heading', { name: 'Xin chào, Page teaching verification' }).waitFor();
  await page.goto(front + '/workspaces/' + workspace.id); await page.getByRole('button', { name: 'Mở trợ lý AI', exact: true }).click();
  await page.getByRole('button', { name: 'Hội thoại mới', exact: true }).click();
  await page.getByRole('textbox', { name: 'Câu hỏi cho trợ lý AI', exact: true }).waitFor();
  let conversationId;
  async function askUI(label, doc, numbering, question) {
    await page.getByLabel('Tài liệu cho câu hỏi', { exact: true }).selectOption(doc.id);
    await page.getByLabel('Số trang trong yêu cầu', { exact: true }).selectOption(numbering);
    const input = page.getByRole('textbox', { name: 'Câu hỏi cho trợ lý AI', exact: true }); await input.fill(question);
    const reply = page.waitForResponse(r => r.request().method() === 'POST' && r.url().endsWith('/questions'), { timeout: 320000 });
    await page.getByRole('button', { name: 'Gửi câu hỏi', exact: true }).click();
    const r = await reply; assert.equal(r.status(), 200, await r.text()); const body = (await r.json()).data;
    conversationId = r.url().split('/conversations/')[1].split('/')[0];
    await input.waitFor(); await page.waitForFunction(() => !document.querySelector('textarea[placeholder="Hỏi hoặc tóm tắt tài liệu…"]')?.disabled);
    report.turns.push({ case: label, question, response: body, persisted: JSON.parse(helper({ action: 'inspect_turn', turn_id: body.id })) }); persist();
    await page.screenshot({ path: path.join(output, label + '.png'), fullPage: true }); return body;
  }
  const sixth = await askUI('native-page6', native, 'physical', 'Bạn giảng trang số 6 cho mình. Nêu mã nhận diện trên trang, giải thích nội dung bằng tiếng Việt và dẫn nguồn.');
  assert.equal(sixth.status, 'answered'); assert.ok(sixth.answer.includes(code)); assert.ok(sixth.sources.some(s => s.document_id === native.id && s.location.page === 6));
  const source = await api('GET', '/channels/' + channel.id + '/ai/conversations/' + conversationId + '/turns/' + sixth.id + '/sources/0');
  assert.equal(source.location.page, 6); assert.equal(source.document_id, native.id);
  report.checks.push('real model teaches native page6 with random evidence code, original citation validated'); persist();
  await page.reload(); await page.getByRole('button', { name: 'Mở trợ lý AI', exact: true }).click(); await page.getByLabel('Tài liệu cho câu hỏi', { exact: true }).waitFor();
  assert.equal(await page.getByLabel('Tài liệu cho câu hỏi', { exact: true }).inputValue(), native.id);
  assert.equal(await page.getByLabel('Số trang trong yêu cầu', { exact: true }).inputValue(), 'physical');
  report.checks.push('reload restores conversation and explicit source/page convention');
  const ambiguous = await askUI('printed-page6', cover, 'printed', 'Bạn giảng trang số 6 cho mình. Đây là số trang in trong sách, không phải vị trí trong PDF. Nếu chưa đối chiếu được thì hỏi lại, đừng tự chọn trang PDF thứ 6.');
  assert.ok(!ambiguous.sources.some(s => s.document_id === cover.id && s.location.page === 6));
  const mapped = await askUI('covered-page7', cover, 'physical', 'Mình xác nhận trang in 6 nằm ở vị trí trang PDF vật lý 7. Giảng nội dung trang PDF 7, nêu mã nhận diện và dẫn nguồn.');
  assert.equal(mapped.status, 'answered'); assert.ok(mapped.answer.includes(code)); assert.ok(mapped.sources.some(s => s.document_id === cover.id && s.location.page === 7));
  report.checks.push('cover offset: no silent PDF6 substitution; confirmed physical7 evidence correct'); persist();
  const original = JSON.parse(helper({ action: 'original_scan' }));
  const scan = await upload(channel.id, original.base64, 'Four_Corners_3A-scan-copy.pdf');
  const indexState = await finished(scan); assert.equal(indexState.status, 'FAILED');
  // Refresh the actual document picker after the new upload.
  await page.reload(); await page.getByRole('button', { name: 'Mở trợ lý AI', exact: true }).click();
  await page.getByLabel('Tài liệu cho câu hỏi', { exact: true }).locator('option[value="' + scan.id + '"]').waitFor({ state: 'attached' });
  const scanned = await askUI('scan-page6', scan, 'physical', 'Bạn giảng trang số 6 cho mình bằng tiếng Việt, đọc hình và nội dung thật trên trang. Cho biết hình số 2 và số 4 là môn học nào; dẫn nguồn.');
  assert.equal(scanned.status, 'answered'); assert.match(scanned.answer, /world geography|địa l[ýí]/i); assert.match(scanned.answer, /algebra|đại số/i); assert.equal(scanned.sources[0]?.document_id, scan.id); assert.equal(scanned.sources[0]?.location.page, 6); assert.ok(!/chủ đề là[^\n]*quy định/i.test(scanned.answer));
  report.checks.push('actual 81-page scan copy: FAILED index does not block real original image lesson on physical6'); persist();
  const base = '/channels/' + channel.id + '/ai/conversations/' + conversationId;
  await api('POST', '/channels/' + other.id + '/ai/conversations/' + (await api('POST', '/channels/' + other.id + '/ai/conversations', {}, 201)).id + '/questions', { question: 'Giảng trang 6', request_id: randomUUID(), source_selection: { document_id: native.id, page_numbering: 'physical' } }, 404);
  const unavailable = await api('POST', base + '/questions', { question: 'Giảng trang PDF vật lý 600 của tài liệu đã chọn. Nếu không có trang đó thì nói rõ.', request_id: randomUUID(), source_selection: { document_id: native.id, page_numbering: 'physical' } });
  assert.notEqual(unavailable.status, 'answered'); assert.equal(unavailable.sources.length, 0); report.missing_page = unavailable;
  report.checks.push('selected cross-channel document rejected 404 before model; nonexistent page not taught'); persist();
  const retry = await api('POST', '/learning-documents/' + native.id + '/retry'); assert.equal(retry.status, 'PROCESSING'); assert.equal((await finished(native)).status, 'READY');
  report.checks.push('document retry remains PROCESSING then real READY');
  report.status = 'PASS for recorded live cases; no client token streaming endpoint exists'; persist();
})().catch(error => { report.status = 'NOT READY'; report.failure = error.stack; persist(); console.error(error); process.exitCode = 1; }).finally(async () => {
  if (browser) await browser.close();
  try { console.log(helper({ action: 'cleanup' }, true)); } catch (error) { console.error(error); }
  console.log(JSON.stringify({ status: report.status, checks: report.checks, failure: report.failure }, null, 2));
});
