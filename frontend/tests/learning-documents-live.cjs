// Real authenticated Chrome + PostgreSQL + MinIO. No document API/storage mocks.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const { randomUUID, randomBytes, createHash } = require('node:crypto');
const { spawnSync } = require('node:child_process');
const path = require('node:path'), fs = require('node:fs');
const frontend = process.env.FRONTEND_TEST_URL || 'http://localhost:3001';
const backend = process.env.LIVE_API_URL || 'http://localhost:8000';
const marker = `phase5a-${randomUUID()}`;
const accounts = ['owner', 'member', 'outsider'].map(role => ({ role, email: `${marker}-${role}@example.com`, password: randomBytes(24).toString('base64url') }));
let checks = 0;
function pass(message) { checks++; console.log(`PASS ${checks}: ${message}`); }
function evidence(action, extra = {}) {
  const dir = path.resolve(__dirname, '../../backend');
  const result = spawnSync(path.join(dir, '.venv-win/Scripts/python.exe'), ['-m', 'scripts.verify_documents_live'], { cwd: dir, input: JSON.stringify({ marker, action, ...extra }), encoding: 'utf8', timeout: 30000 });
  assert.equal(result.status, 0, `Scoped evidence helper failed: ${result.stdout.trim()}`);
  return result.stdout.trim();
}
async function api(actor, method, route, body, expected = 200) {
  const response = await fetch(`${backend}/api/v1${route}`, { method, headers: { ...(actor?.token ? { Authorization: `Bearer ${actor.token}` } : {}), ...(body ? { 'Content-Type': 'application/json' } : {}) }, body: body ? JSON.stringify(body) : undefined });
  assert.equal(response.status, expected, `${method} ${route}: HTTP ${response.status}`);
  return expected === 204 ? null : (await response.json()).data;
}
async function directUpload(actor, channel, bytes, filename, mime, expected, uploadId) {
  const form = new FormData(); form.append('file', new Blob([bytes], { type: mime }), filename);
  if (uploadId) form.append('upload_id', uploadId);
  const response = await fetch(`${backend}/api/v1/channels/${channel}/learning-documents`, { method: 'POST', headers: { Authorization: `Bearer ${actor.token}` }, body: form });
  assert.equal(response.status, expected);
  return response.json();
}
(async () => {
  let browser; const contexts = [], errors = [];
  try {
    for (const account of accounts) {
      account.id = (await api(null, 'POST', '/auth/register', { full_name: `Documents ${account.role}`, email: account.email, password: account.password, confirm_password: account.password }, 201)).id;
    }
    browser = await chromium.launch({ channel: 'chrome', headless: true });
    const pages = [];
    for (const account of accounts.slice(0, 2)) {
      const context = await browser.newContext(); contexts.push(context);
      const page = await context.newPage(); page.setDefaultTimeout(20000);
      page.on('pageerror', e => errors.push(e.message));
      page.on('console', message => { if (/same key|unique.*key/i.test(message.text())) errors.push(message.text()); });
      await page.goto(`${frontend}/login`);
      await page.getByLabel('Email', { exact: true }).fill(account.email);
      await page.getByLabel('Mật khẩu', { exact: true }).fill(account.password);
      const login = page.waitForResponse(r => r.url().endsWith('/api/v1/auth/login') && r.request().method() === 'POST');
      await page.getByRole('button', { name: 'Đăng nhập', exact: true }).click();
      account.token = (await (await login).json()).data.access_token;
      await page.getByRole('heading', { name: `Xin chào, Documents ${account.role}` }).waitFor(); pages.push(page);
    }
    const [owner, member, outsider] = accounts, [a, b] = pages;
    outsider.token = (await api(null, 'POST', '/auth/login', { email: outsider.email, password: outsider.password })).access_token;
    const workspace = await api(owner, 'POST', '/workspaces', { name: marker }, 201);
    const invitation = await api(owner, 'POST', `/workspaces/${workspace.id}/invitations`, { invitee_user_id: member.id }, 201);
    await api(member, 'POST', `/workspace-invitations/${invitation.token}/accept`);
    const channels = await api(owner, 'GET', `/workspaces/${workspace.id}/channels`);
    const channel = channels.find(c => c.is_default);
    const second = await api(owner, 'POST', `/workspaces/${workspace.id}/channels`, { name: 'other-documents', type: 'TEXT' }, 201);
    for (const page of pages) await page.goto(`${frontend}/workspaces/${workspace.id}`);
    const area = page => page.getByRole('region', { name: 'Tài liệu học tập', exact: true });
    await area(a).getByText('Chưa có tài liệu học tập trong kênh.', { exact: true }).waitFor();
    assert.equal(await area(b).getByRole('button', { name: 'Tải tài liệu lên', exact: true }).count(), 0);
    pass('Authorized channel area/empty state; member has no upload control');
    // Chrome prints actual text to a valid PDF; DOCX is an actual OOXML package.
    const fixture = await browser.newPage(); await fixture.setContent('<h1>UC29 Learning document</h1><p>Real PDF for private learning document upload verification.</p>');
    const pdf = await fixture.pdf(); await fixture.close();
    const word = Buffer.from(evidence('word_fixture'), 'base64');
    const choose = async (bytes, filename, mime) => {
      await area(a).getByRole('button', { name: 'Tải tài liệu lên', exact: true }).click();
      await area(a).getByLabel('Chọn tài liệu', { exact: true }).setInputFiles({ name: filename, mimeType: mime, buffer: bytes });
    };
    await area(a).getByRole('button', { name: 'Tải tài liệu lên', exact: true }).click();
    await area(a).getByRole('button', { name: 'Hủy', exact: true }).click();
    await choose(pdf, 'lesson.pdf', 'application/pdf'); await area(a).getByRole('button', { name: 'Hủy', exact: true }).click();
    assert.deepEqual(await api(owner, 'GET', `/channels/${channel.id}/learning-documents`), []);
    pass('A1 cancel before selection and after selection creates no document');
    await choose(Buffer.from('unsupported'), 'bad.txt', 'text/plain');
    await area(a).getByText('Chỉ hỗ trợ PDF và Word DOCX.', { exact: true }).waitFor();
    assert.ok(await area(a).getByRole('button', { name: 'Xác nhận tải lên', exact: true }).isDisabled());
    await area(a).getByRole('button', { name: 'Hủy', exact: true }).click();
    const oversized = Buffer.alloc(25 * 1024 * 1024 + 1, 65);
    await choose(oversized, 'large.pdf', 'application/pdf');
    await area(a).getByText('Tệp vượt quá 25 MiB.', { exact: true }).waitFor();
    await area(a).getByRole('button', { name: 'Hủy', exact: true }).click();
    await directUpload(owner, channel.id, Buffer.from('no'), 'bad.txt', 'text/plain', 422);
    await directUpload(owner, channel.id, oversized, 'large.pdf', 'application/pdf', 413);
    await directUpload(member, channel.id, pdf, 'lesson.pdf', 'application/pdf', 403);
    await directUpload(outsider, channel.id, pdf, 'lesson.pdf', 'application/pdf', 403);
    assert.deepEqual(await api(owner, 'GET', `/channels/${channel.id}/learning-documents`), []);
    pass('E1/E2 real UI and crafted API reject; member/outsider uploads reject; zero metadata');
    let postCount = 0;
    a.on('request', r => { if (r.url().endsWith(`/channels/${channel.id}/learning-documents`) && r.method() === 'POST') postCount++; });
    await choose(pdf, 'lesson.pdf', 'application/pdf');
    await area(a).getByLabel('Tên tài liệu (tùy chọn)', { exact: true }).fill('Custom learning PDF');
    await area(a).getByLabel('Mô tả tài liệu (tùy chọn)', { exact: true }).fill('Supervisor verification');
    const uploaded = a.waitForResponse(r => r.url().endsWith('/learning-documents') && r.request().method() === 'POST');
    await area(a).locator('form').evaluate(form => { form.requestSubmit(); form.requestSubmit(); });
    const response = await uploaded; assert.equal(response.status(), 201);
    const first = (await response.json()).data;
    assert.equal(first.status, 'PROCESSING'); assert.equal(postCount, 1);
    await area(a).getByText('Custom learning PDF', { exact: true }).waitFor();
    pass('Main 1–11 real PDF/custom metadata/upload association; one submit; response Processing; list updates without refresh');
    await a.getByRole('button', { name: '# other-documents TEXT', exact: true }).click();
    await area(a).getByText('Chưa có tài liệu học tập trong kênh.', { exact: true }).waitFor();
    assert.equal(await area(a).getByText('Custom learning PDF', { exact: true }).count(), 0);
    assert.deepEqual(await api(owner, 'GET', `/channels/${second.id}/learning-documents`), []);
    await a.getByRole('button', { name: '# general TEXT', exact: true }).click();
    await area(a).getByText('Sẵn sàng', { exact: true }).waitFor({ timeout: 120000 });
    const current = await api(owner, 'GET', `/channels/${channel.id}/learning-documents`);
    assert.equal(current[0].status, 'READY'); assert.equal(current[0].processing_error, null);
    pass('A3 upload returns Processing; navigation works; real RAG automatically reaches READY; channel isolation');
    const original = await api(member, 'GET', `/learning-documents/${first.id}/original`);
    const bytes = await fetch(`${backend}${original.url}`, { headers: { Authorization: `Bearer ${member.token}` } }); assert.equal(bytes.status, 200);
    assert.deepEqual(Buffer.from(await bytes.arrayBuffer()), pdf);
    await api(outsider, 'GET', `/learning-documents/${first.id}/original`, undefined, 403);
    pass('Processed document retains exact original; authenticated member can access; outsider denied');
    await choose(word, 'lesson.docx', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document');
    const wordResponse = a.waitForResponse(r => r.url().endsWith('/learning-documents') && r.request().method() === 'POST');
    await area(a).getByRole('button', { name: 'Xác nhận tải lên', exact: true }).click();
    const secondDocument = (await (await wordResponse).json()).data;
    assert.equal(secondDocument.name, 'lesson.docx'); assert.equal(secondDocument.description, null);
    await area(a).locator(`[data-document-id="${secondDocument.id}"]`).getByText('Sẵn sàng', { exact: true }).waitFor({ timeout: 120000 });
    pass('A2 real Word DOCX without custom metadata uses filename; actual RAG READY');
    const blankPdf = Buffer.from(evidence('blank_pdf'), 'base64');
    await choose(blankPdf, 'blank.pdf', 'application/pdf');
    const blankResponse = a.waitForResponse(r => r.url().endsWith('/learning-documents') && r.request().method() === 'POST');
    await area(a).getByRole('button', { name: 'Xác nhận tải lên', exact: true }).click();
    const failed = (await (await blankResponse).json()).data;
    await area(a).locator(`[data-document-id="${failed.id}"]`).getByText('Xử lý thất bại', { exact: true }).waitFor({ timeout: 120000 });
    const failedCurrent = (await api(owner, 'GET', `/channels/${channel.id}/learning-documents`)).find(d => d.id === failed.id);
    assert.equal(failedCurrent.processing_error, 'PDF_NO_TEXT');
    const failedOriginal = await api(member, 'GET', `/learning-documents/${failed.id}/original`);
    assert.deepEqual(Buffer.from(await (await fetch(`${backend}${failedOriginal.url}`, { headers: { Authorization: `Bearer ${member.token}` } })).arrayBuffer()), blankPdf);
    pass('E4 actual blank PDF extraction fails; UI shows Failed and original remains accessible');
    await a.reload(); await area(a).getByText('Custom learning PDF', { exact: true }).waitFor();
    assert.equal(await area(a).locator('[data-document-id]').count(), 3);
    const verified = evidence('verify', { channel_id: channel.id, uploader_id: owner.id, documents: [
      { id: first.id, name: first.name, description: first.description, checksum: createHash('sha256').update(pdf).digest('hex') },
      { id: secondDocument.id, name: secondDocument.name, description: null, checksum: createHash('sha256').update(word).digest('hex') },
      { id: failed.id, name: failed.name, description: null, checksum: createHash('sha256').update(blankPdf).digest('hex'), status: 'FAILED', processing_error: 'PDF_NO_TEXT' },
    ] });
    console.log(verified); pass('Refresh persists all records; direct PostgreSQL and private real MinIO bytes verified');
    assert.ok(await a.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    if (process.env.LIVE_EVIDENCE_DIR) { fs.mkdirSync(process.env.LIVE_EVIDENCE_DIR, { recursive: true }); await a.screenshot({ path: path.join(process.env.LIVE_EVIDENCE_DIR, 'phase5a-learning-documents.png'), fullPage: true }); }
    assert.deepEqual(errors, []); pass('Desktop layout, unique rows, no page errors/duplicate-key warnings');
    console.log(`UC29 real browser upload/RAG/READY verification: ${checks}/${checks} PASS; E4/E5/restart verified separately`);
  } finally {
    for (const context of contexts) await context.close();
    if (browser) await browser.close();
    console.log(evidence('cleanup'));
  }
})().catch(error => { console.error(error.stack); process.exitCode = 1; });
