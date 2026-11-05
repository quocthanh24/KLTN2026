// Real model/source success; only delivery of one real HTTP response is interrupted.
const { chromium } = require('D:/Temp/kltn-browser-verification/node_modules/playwright');
const assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path');
const { randomUUID, randomBytes } = require('node:crypto'), { spawnSync } = require('node:child_process');
const backend = path.resolve(__dirname, '../../backend'), out = path.resolve(__dirname, '../../evidence/page-teaching-20261103');
fs.mkdirSync(out, { recursive: true });
const marker = 'phase5b-' + randomUUID(), code = 'LOTUS-' + randomBytes(4).toString('hex').toUpperCase();
const report = { marker, code, checks: [], turns: [] }; let token, browser;
const front = process.env.FRONTEND_TEST_URL || 'http://localhost:3000', base = process.env.LIVE_API_URL || 'http://localhost:8000';
function helper(data, legacy = false) {
  const p = spawnSync(path.join(backend, '.venv-win/Scripts/python.exe'), ['-m', legacy ? 'scripts.verify_document_management_live' : 'scripts.verify_page_teaching_live'], { cwd: backend, input: JSON.stringify({ marker, ...data }), encoding: 'utf8', timeout: 60000, maxBuffer: 250 * 1024 * 1024 });
  assert.equal(p.status, 0, p.stderr || p.stdout); return p.stdout.trim();
}
function save() { fs.writeFileSync(path.join(out, 'resilience.json'), JSON.stringify(report, null, 2)); }
async function api(method, route, body, expected = 200) {
  const r = await fetch(base + '/api/v1' + route, { method, headers: { Connection: 'close', ...(token ? { Authorization: 'Bearer ' + token } : {}), ...(body ? { 'Content-Type': 'application/json' } : {}) }, body: body ? JSON.stringify(body) : undefined });
  assert.equal(r.status, expected, route + ': ' + await r.clone().text()); return (await r.json()).data;
}
async function upload(channel, bytes, name) {
  const form = new FormData(); form.append('file', new Blob([Buffer.from(bytes, 'base64')], { type: 'application/pdf' }), name);
  const r = await fetch(base + '/api/v1/channels/' + channel + '/learning-documents', { method: 'POST', headers: { Connection: 'close', Authorization: 'Bearer ' + token }, body: form });
  assert.equal(r.status, 201); return (await r.json()).data;
}
async function finished(doc) { for (let i = 0; i < 150; i++) { const d = await api('GET', '/learning-documents/' + doc.id); if (d.status !== 'PROCESSING') return d; await new Promise(r => setTimeout(r, 1000)); } throw Error('Timeout'); }
(async () => {
  const email = marker + '-owner@example.com', password = randomBytes(24).toString('base64url');
  await api('POST', '/auth/register', { full_name: 'Page resilience verification', email, password, confirm_password: password }, 201);
  token = (await api('POST', '/auth/login', { email, password })).access_token;
  const w = await api('POST', '/workspaces', { name: marker }, 201), channel = (await api('GET', '/workspaces/' + w.id + '/channels')).find(c => c.is_default);
  const fixtures = JSON.parse(helper({ action: 'fixtures', code }));
  const native = await upload(channel.id, fixtures.native, 'pending-biology.pdf');
  helper({ action: 'hold_processing', document_id: native.id }); // Real lease fencing on this test's own fixture only.
  browser = await chromium.launch({ channel: 'chrome', headless: true });
  const page = await browser.newPage({ viewport: { width: 1450, height: 1100 } }); page.setDefaultTimeout(40000);
  await page.goto(front + '/login'); await page.getByLabel('Email', { exact: true }).fill(email); await page.getByLabel('Mật khẩu', { exact: true }).fill(password); await page.getByRole('button', { name: 'Đăng nhập', exact: true }).click();
  await page.getByRole('heading', { name: 'Xin chào, Page resilience verification' }).waitFor();
  async function open() { await page.goto(front + '/workspaces/' + w.id); await page.getByRole('button', { name: 'Mở trợ lý AI', exact: true }).click(); }
  await open(); await page.getByRole('button', { name: 'Hội thoại mới', exact: true }).click();
  const picker = page.getByLabel('Tài liệu cho câu hỏi', { exact: true }), numbering = page.getByLabel('Số trang trong yêu cầu', { exact: true }), input = page.getByRole('textbox', { name: 'Câu hỏi cho trợ lý AI', exact: true });
  await picker.selectOption(native.id); await numbering.selectOption('physical');
  const question = 'Bạn giảng trang số 6 cho mình bằng tiếng Việt. Nêu mã nhận diện và dẫn nguồn.';
  let actual;
  await page.route('**/questions', async route => {
    report.question_endpoint = route.request().url();
    const real = await route.fetch({ timeout: 320000 }); assert.equal(real.status(), 200);
    actual = (await real.json()).data;
    await route.abort('connectionreset'); // Drop the actual saved answer; never substitute an AI result.
  }, { times: 1 });
  assert.equal((await api('GET', '/learning-documents/' + native.id)).status, 'PROCESSING');
  await input.fill(question); await page.getByRole('button', { name: 'Gửi câu hỏi', exact: true }).click();
  await page.getByText(/Kết nối bị gián đoạn trước khi nhận đủ câu trả lời/).waitFor({ timeout: 320000 });
  assert.equal(actual.status, 'answered'); assert.ok(actual.answer.includes(code)); assert.equal(actual.sources[0]?.location.page, 6);
  assert.equal((await api('GET', '/learning-documents/' + native.id)).status, 'PROCESSING');
  report.turns.push({ case: 'pending-source-client-disconnect', response: actual, persisted: JSON.parse(helper({ action: 'inspect_turn', turn_id: actual.id })) });
  await page.screenshot({ path: path.join(out, 'client-disconnect.png'), fullPage: true });
  await page.getByRole('button', { name: 'Gửi câu hỏi', exact: true }).click();
  await page.waitForFunction(() => { const el = document.querySelector('textarea[placeholder="Hỏi hoặc tóm tắt tài liệu…"]'); return el && !el.disabled && !el.value; }, { timeout: 40000 });
  const saved = JSON.parse(helper({ action: 'inspect_turn', turn_id: actual.id })); assert.equal(saved.same_request_count, 1);
  report.checks.push('real PROCESSING source read independently from original; dropped real HTTP response recovered; same request replay has one DB turn'); save();
  helper({ action: 'release_processing', document_id: native.id }); assert.equal((await finished(native)).status, 'READY');
  const original = JSON.parse(helper({ action: 'original_scan' })), scan = await upload(channel.id, original.base64, 'actual-scan81.pdf'); assert.equal((await finished(scan)).status, 'FAILED');
  await open();
  async function ask(label, mode, text) {
    await picker.selectOption(scan.id); await numbering.selectOption(mode); await input.fill(text);
    const pending = page.waitForResponse(r => r.request().method() === 'POST' && r.url().endsWith('/questions'), { timeout: 320000 });
    await page.getByRole('button', { name: 'Gửi câu hỏi', exact: true }).click(); const r = await pending; assert.equal(r.status(), 200);
    const value = (await r.json()).data; report.turns.push({ case: label, response: value, persisted: JSON.parse(helper({ action: 'inspect_turn', turn_id: value.id })) }); save();
    await page.waitForFunction(() => !document.querySelector('textarea[placeholder="Hỏi hoặc tóm tắt tài liệu…"]')?.disabled);
    await page.screenshot({ path: path.join(out, label + '.png'), fullPage: true }); return value;
  }
  await ask('printed-convention-history', 'printed', 'Lượt này mình dùng số trang in trong sách. Bạn ghi nhận cách đánh số này, chưa cần giảng nội dung.');
  const scan6 = await ask('scan6-current-physical', 'physical', 'Bạn giảng trang số 6 cho mình bằng tiếng Việt. Hình số 2 và hình số 4 là môn học nào? Đọc và dẫn nguồn đúng trang đã chọn.');
  assert.equal(scan6.status, 'answered'); assert.equal(scan6.sources[0]?.document_id, scan.id); assert.equal(scan6.sources[0]?.location.page, 6);
  assert.match(scan6.answer, /world geography|địa l[ýí]/i); assert.match(scan6.answer, /algebra|đại số/i);
  assert.ok(!/chủ đề là[^\n]*quy định/i.test(scan6.answer));
  report.checks.push('real original scan81 physical6 primary lesson/citation correct despite preceding printed-page convention');
  const ambiguous = await ask('ambiguous-page-clarification', 'unspecified', 'Tạm bỏ cách đánh số của các lượt trước. Mình muốn giảng trang số 6 nhưng chưa biết đây là số in hay số vị trí PDF. Hãy hỏi rõ trước khi giảng.');
  assert.equal(ambiguous.sources.length, 0); assert.match(ambiguous.answer, /trang.*PDF|vật lý|số.*in/i);
  report.checks.push('ambiguous numbering asks clarification without teaching a guessed page');
  report.status = 'PASS'; save();
})().catch(error => { report.status = 'NOT READY'; report.failure = error.stack; save(); console.error(error); process.exitCode = 1; }).finally(async () => {
  if (browser) await browser.close(); try { console.log(helper({ action: 'cleanup' }, true)); } catch (e) { console.error(e); }
  console.log(JSON.stringify({ status: report.status, checks: report.checks, failure: report.failure }, null, 2));
});
