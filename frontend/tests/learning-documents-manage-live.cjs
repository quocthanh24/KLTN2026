// Real Chrome/PostgreSQL/MinIO/pgvector/RAG/PDF.js; no intercepted API responses.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const { randomUUID, randomBytes } = require('node:crypto');
const { spawnSync } = require('node:child_process');
const fs = require('node:fs'), path = require('node:path');
const frontend = process.env.FRONTEND_TEST_URL || 'http://localhost:3010';
const backend = process.env.LIVE_API_URL || 'http://localhost:8010';
const marker = `phase5b-${randomUUID()}`;
const accounts = ['owner', 'member', 'outsider'].map(role => ({ role, email: `${marker}-${role}@example.com`, password: randomBytes(24).toString('base64url') }));
let checks = 0;
function pass(message) { console.log(`PASS ${++checks}: ${message}`); }
function evidence(action, document) {
  const dir = path.resolve(__dirname, '../../backend');
  const result = spawnSync(path.join(dir, '.venv-win/Scripts/python.exe'), ['-m', 'scripts.verify_document_management_live'], {
    cwd: dir, input: JSON.stringify({ marker, action, document_id: document?.id }), encoding: 'utf8', timeout: 30000,
  });
  assert.equal(result.status, 0, result.stdout.trim()); return result.stdout.trim();
}
const inspect = document => JSON.parse(evidence('inspect', document));
async function api(actor, method, route, body, expected = 200) {
  const response = await fetch(`${backend}/api/v1${route}`, { method, headers: {
    ...(actor ? { Authorization: `Bearer ${actor.token}` } : {}), ...(body ? { 'Content-Type': 'application/json' } : {}),
  }, body: body ? JSON.stringify(body) : undefined });
  assert.equal(response.status, expected, `${method} ${route}: HTTP ${response.status}`);
  return expected === 204 ? null : (await response.json()).data;
}
async function directUpload(actor, channel, bytes, name, mime) {
  const body = new FormData(); body.append('file', new Blob([bytes], { type: mime }), name);
  const response = await fetch(`${backend}/api/v1/channels/${channel}/learning-documents`, { method: 'POST', headers: { Authorization: `Bearer ${actor.token}` }, body });
  assert.equal(response.status, 201); return (await response.json()).data;
}
async function waitReady(actor, channel, document) {
  for (let i = 0; i < 120; i++) {
    const current = await api(actor, 'GET', `/learning-documents/${document.id}?channel_id=${channel}`);
    if (current.status === 'READY') return;
    assert.notEqual(current.status, 'FAILED'); await new Promise(r => setTimeout(r, 500));
  }
  throw new Error('Real RAG READY timeout');
}
(async () => {
  let browser; const contexts = [], errors = [];
  try {
    for (const actor of accounts) actor.id = (await api(null, 'POST', '/auth/register', { full_name: `Documents ${actor.role}`, email: actor.email, password: actor.password, confirm_password: actor.password }, 201)).id;
    browser = await chromium.launch({ channel: 'chrome', headless: true });
    const pages = [];
    for (const actor of accounts.slice(0, 2)) {
      const context = await browser.newContext(); contexts.push(context); const page = await context.newPage(); page.setDefaultTimeout(20000);
      page.on('pageerror', e => errors.push(e.message));
      page.on('console', m => { if (/same key|unique.*key/i.test(m.text())) errors.push(m.text()); });
      await page.goto(`${frontend}/login`);
      await page.getByLabel('Email', { exact: true }).fill(actor.email); await page.getByLabel('Mật khẩu', { exact: true }).fill(actor.password);
      const login = page.waitForResponse(r => r.url().endsWith('/auth/login') && r.request().method() === 'POST');
      await page.getByRole('button', { name: 'Đăng nhập', exact: true }).click(); actor.token = (await (await login).json()).data.access_token;
      await page.getByRole('heading', { name: `Xin chào, Documents ${actor.role}` }).waitFor(); pages.push(page);
    }
    const [owner, member, outsider] = accounts, [a, b] = pages;
    outsider.token = (await api(null, 'POST', '/auth/login', { email: outsider.email, password: outsider.password })).access_token;
    const workspace = await api(owner, 'POST', '/workspaces', { name: marker }, 201);
    const invite = await api(owner, 'POST', `/workspaces/${workspace.id}/invitations`, { invitee_user_id: member.id }, 201);
    await api(member, 'POST', `/workspace-invitations/${invite.token}/accept`);
    const channel = (await api(owner, 'GET', `/workspaces/${workspace.id}/channels`)).find(c => c.is_default);
    const other = await api(owner, 'POST', `/workspaces/${workspace.id}/channels`, { name: 'documents-other', type: 'TEXT' }, 201);
    for (const page of pages) await page.goto(`${frontend}/workspaces/${workspace.id}`);
    const area = page => page.getByRole('region', { name: 'Tài liệu học tập', exact: true });
    const row = (page, document) => area(page).locator(`[data-document-id="${document.id}"]`);
    await area(a).getByText('Chưa có tài liệu học tập trong kênh.', { exact: true }).waitFor();
    assert.equal(await area(b).getByRole('button', { name: 'Chỉnh sửa', exact: true }).count(), 0); pass('UC30 A1 empty state and read-only member controls');
    const fixture = await browser.newPage(); await fixture.setContent('<div style="page-break-after:always"><h1>First real learning page</h1></div><h1>Second real learning page</h1>');
    const pdf = await fixture.pdf(); await fixture.close(); const word = Buffer.from(evidence('word_fixture'), 'base64');
    const pdfDoc = await directUpload(owner, channel.id, pdf, 'Bài học.pdf', 'application/pdf'); await waitReady(owner, channel.id, pdfDoc);
    const wordDoc = await directUpload(owner, channel.id, word, 'lesson.docx', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'); await waitReady(owner, channel.id, wordDoc);
    await row(a, pdfDoc).getByRole('button', { name: 'Xem tài liệu', exact: true }).click();
    const dialog = a.getByRole('dialog', { name: 'Xem tài liệu', exact: true });
    await dialog.getByText('Trang 1 / 2', { exact: true }).waitFor();
    await dialog.getByRole('button', { name: 'Trang sau', exact: true }).waitFor({ state: 'visible' });
    await a.waitForFunction(() => !document.querySelector('[aria-label="Trình xem PDF"] button:last-of-type').disabled);
    const image1 = await dialog.locator('canvas').evaluate(canvas => canvas.toDataURL());
    assert.ok(await dialog.locator('canvas').evaluate(c => c.getContext('2d').getImageData(0, 0, c.width, c.height).data.some((v, i) => i % 4 !== 3 && v < 100)));
    await dialog.getByRole('button', { name: 'Trang sau', exact: true }).click(); await dialog.getByText('Trang 2 / 2', { exact: true }).waitFor();
    await a.waitForFunction(() => !document.querySelector('[aria-label="Trình xem PDF"] button:first-of-type').disabled);
    const image2 = await dialog.locator('canvas').evaluate(c => c.toDataURL()); assert.notEqual(image1, image2);
    await dialog.getByRole('button', { name: 'Trang trước', exact: true }).click(); await dialog.getByText('Trang 1 / 2', { exact: true }).waitFor();
    if (process.env.LIVE_EVIDENCE_DIR) { fs.mkdirSync(process.env.LIVE_EVIDENCE_DIR, { recursive: true }); await a.screenshot({ path: path.join(process.env.LIVE_EVIDENCE_DIR, 'phase5b-pdf-viewer.png'), fullPage: true }); }
    await dialog.getByRole('button', { name: 'Đóng tài liệu', exact: true }).click(); assert.equal(await a.getByRole('dialog', { name: 'Xem tài liệu' }).count(), 0);
    pass('UC30 actual PDF.js canvas renders two distinct pages, previous/next and close');
    async function download(page, button, expected, filename) {
      const event = page.waitForEvent('download'); await button.click(); const file = await event;
      assert.equal(file.suggestedFilename(), filename); assert.deepEqual(fs.readFileSync(await file.path()), expected);
    }
    await download(a, row(a, pdfDoc).getByRole('button', { name: 'Tải tệp gốc', exact: true }), pdf, 'Bài học.pdf');
    await row(b, wordDoc).getByRole('button', { name: 'Xem tài liệu', exact: true }).click();
    const fallback = b.getByRole('dialog', { name: 'Xem tài liệu', exact: true }); await fallback.getByText('DOCX chưa hỗ trợ xem trực tiếp. Hãy tải xuống để xem nội dung gốc.', { exact: true }).waitFor();
    await download(b, fallback.getByRole('button', { name: 'Tải xuống', exact: true }), word, 'lesson.docx'); await fallback.getByRole('button', { name: 'Đóng tài liệu', exact: true }).click();
    pass('UC30 A2 truthful DOCX fallback; UC31 real browser PDF/DOCX downloads match original bytes and UTF-8 filename');
    const before = inspect(pdfDoc);
    await row(a, pdfDoc).getByRole('button', { name: 'Chỉnh sửa', exact: true }).click();
    const edit = a.getByRole('form', { name: 'Chỉnh sửa tài liệu', exact: true });
    await edit.getByLabel('Tên tài liệu', { exact: true }).fill('Cancelled metadata'); await edit.getByRole('button', { name: 'Hủy chỉnh sửa', exact: true }).click();
    assert.equal((await api(owner, 'GET', `/learning-documents/${pdfDoc.id}`)).name, 'Bài học.pdf'); pass('UC32 A2 cancel edit leaves metadata unchanged');
    await row(a, pdfDoc).getByRole('button', { name: 'Chỉnh sửa', exact: true }).click();
    await edit.getByLabel('Tên tài liệu', { exact: true }).fill('Updated learning PDF'); await edit.getByLabel('Mô tả', { exact: true }).fill('Real metadata update');
    await edit.getByRole('button', { name: 'Lưu thay đổi', exact: true }).click(); await row(a, pdfDoc).getByText('Updated learning PDF', { exact: true }).waitFor();
    const after = inspect(pdfDoc); assert.equal(after.description, 'Real metadata update'); assert.deepEqual(after.chunk_ids, before.chunk_ids); assert.equal(after.original_sha256, before.original_sha256);
    pass('UC32 save persists metadata without rebuilding vectors or changing original');
    await row(a, pdfDoc).getByRole('button', { name: 'Xóa tài liệu', exact: true }).click();
    const confirm = a.getByRole('dialog', { name: 'Xác nhận xóa tài liệu', exact: true }); await confirm.getByRole('button', { name: 'Hủy xóa', exact: true }).click();
    assert.equal(inspect(pdfDoc).deleted, false); pass('UC32 cancel delete preserves original and vectors');
    await row(a, pdfDoc).getByRole('button', { name: 'Xóa tài liệu', exact: true }).click(); await confirm.getByRole('button', { name: 'Xác nhận xóa', exact: true }).click(); await row(a, pdfDoc).waitFor({ state: 'detached' });
    const deleted = inspect(pdfDoc); assert.equal(deleted.deleted, true); assert.equal(deleted.object_cleaned, true); assert.equal(deleted.original_sha256, null); assert.deepEqual(deleted.chunk_ids, []); assert.ok(!deleted.eligible_documents.includes(pdfDoc.id));
    for (const action of ['preview', 'download']) assert.equal((await fetch(`${backend}/api/v1/learning-documents/${pdfDoc.id}/${action}`, { headers: { Authorization: `Bearer ${member.token}` } })).status, 404);
    await api(owner, 'DELETE', `/learning-documents/${pdfDoc.id}`, undefined, 204); assert.ok(inspect(wordDoc).chunk_ids.length > 0);
    pass('UC32 confirmed/repeated delete revokes file access, removes vectors and RAG eligibility; other document remains intact');
    const peer = await directUpload(owner, other.id, word, 'peer.docx', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'); await waitReady(owner, other.id, peer);
    await a.getByRole('button', { name: '# documents-other TEXT', exact: true }).click(); await row(a, peer).waitFor(); assert.equal(await row(a, wordDoc).count(), 0);
    await api(owner, 'GET', `/learning-documents/${wordDoc.id}?channel_id=${other.id}`, undefined, 404);
    await a.getByRole('button', { name: '# general TEXT', exact: true }).click(); await row(a, wordDoc).waitFor(); pass('Channel switching and crafted mismatched-channel requests are isolated');
    const processing = await directUpload(owner, channel.id, word, 'processing.docx', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'); assert.equal(processing.status, 'PROCESSING');
    await api(owner, 'DELETE', `/learning-documents/${processing.id}`, undefined, 204); await a.waitForTimeout(4000);
    const revoked = inspect(processing); assert.equal(revoked.deleted, true); assert.notEqual(revoked.status, 'READY'); assert.deepEqual(revoked.chunk_ids, []); pass('Delete PROCESSING document cannot reappear or publish READY/vectors');
    const stale = await directUpload(owner, channel.id, word, 'stale.docx', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'); await waitReady(owner, channel.id, stale); await row(a, stale).waitFor();
    await row(a, stale).getByRole('button', { name: 'Chỉnh sửa', exact: true }).click(); await edit.getByLabel('Tên tài liệu', { exact: true }).fill('Stale update');
    await api(owner, 'DELETE', `/learning-documents/${stale.id}`, undefined, 204); await edit.getByRole('button', { name: 'Lưu thay đổi', exact: true }).click();
    await area(a).getByText('Tài liệu đã bị xóa hoặc không còn khả dụng', { exact: true }).waitFor(); pass('UC32 E1 stale save returns error and refreshes list');
    await api(owner, 'PATCH', `/workspaces/${workspace.id}/members/${member.id}/role`, { role: 'ADMIN' }); await b.reload(); await row(b, wordDoc).getByRole('button', { name: 'Chỉnh sửa', exact: true }).click();
    const bEdit = b.getByRole('form', { name: 'Chỉnh sửa tài liệu', exact: true }); await bEdit.getByLabel('Tên tài liệu', { exact: true }).fill('Permission lost');
    await api(owner, 'PATCH', `/workspaces/${workspace.id}/members/${member.id}/role`, { role: 'MEMBER' }); await bEdit.getByRole('button', { name: 'Lưu thay đổi', exact: true }).click();
    await area(b).getByText('Bạn không có quyền truy cập Channel', { exact: true }).waitFor(); assert.equal(await row(b, wordDoc).getByRole('button', { name: 'Chỉnh sửa', exact: true }).count(), 0);
    pass('UC32 E2 actual demotion rejects save and refreshes read-only document controls');
    await api(owner, 'DELETE', `/workspaces/${workspace.id}/members/${member.id}`, undefined, 204);
    await row(b, wordDoc).getByRole('button', { name: 'Tải tệp gốc', exact: true }).click(); await area(b).getByText(/Bạn không còn quyền truy cập khu vực tài liệu/).waitFor();
    for (const action of ['preview', 'download']) assert.equal((await fetch(`${backend}/api/v1/learning-documents/${wordDoc.id}/${action}`, { headers: { Authorization: `Bearer ${member.token}` } })).status, 403);
    pass('UC30 E1 / UC31 E1 actual membership removal blocks UI and direct file requests');
    evidence('remove_original', wordDoc); await row(a, wordDoc).getByRole('button', { name: 'Tải tệp gốc', exact: true }).click(); await area(a).getByText('Tệp tài liệu không còn khả dụng', { exact: true }).waitFor();
    assert.equal((await fetch(`${backend}/api/v1/learning-documents/${wordDoc.id}/download`, { headers: { Authorization: `Bearer ${owner.token}` } })).status, 404);
    pass('UC31 E2 actual missing MinIO object reports unavailable without issuing broken URL');
    const staleOpen = await directUpload(owner, channel.id, pdf, 'stale-open.pdf', 'application/pdf'); await waitReady(owner, channel.id, staleOpen); await row(a, staleOpen).waitFor();
    let releaseList; let freezeList = true;
    const gate = new Promise(resolve => { releaseList = resolve; });
    await a.route(`**/channels/${channel.id}/learning-documents`, async route => {
      if (freezeList && route.request().method() === 'GET') await gate;
      await route.continue();
    });
    await api(owner, 'DELETE', `/learning-documents/${staleOpen.id}`, undefined, 204);
    await row(a, staleOpen).getByRole('button', { name: 'Xem tài liệu', exact: true }).click();
    await area(a).getByText('Tài liệu đã bị xóa hoặc không còn khả dụng', { exact: true }).waitFor();
    freezeList = false; releaseList(); await row(a, staleOpen).waitFor({ state: 'detached' });
    pass('UC30 E2 deleted-between-list-and-open rejects stale open and refreshes list; only polling delivery delayed');
    const missingPreview = await directUpload(owner, channel.id, pdf, 'missing-preview.pdf', 'application/pdf'); await waitReady(owner, channel.id, missingPreview); await row(a, missingPreview).waitFor();
    evidence('remove_original', missingPreview); await row(a, missingPreview).getByRole('button', { name: 'Xem tài liệu', exact: true }).click();
    await area(a).getByText('Tệp tài liệu không còn khả dụng', { exact: true }).waitFor(); assert.equal(await a.getByRole('dialog', { name: 'Xem tài liệu' }).count(), 0);
    pass('UC30 E3 actual original fetch failure leaves actor in document list with error');
    await a.reload(); await row(a, wordDoc).waitFor(); assert.equal(await row(a, pdfDoc).count(), 0); assert.deepEqual(errors, []);
    if (process.env.LIVE_EVIDENCE_DIR) await a.screenshot({ path: path.join(process.env.LIVE_EVIDENCE_DIR, 'phase5b-documents.png'), fullPage: true });
    pass('Reload persistence, readable desktop UI, no page errors or duplicate-key warnings');
    console.log(`Phase 5B real integration: ${checks}/${checks} PASS`);
  } finally {
    for (const context of contexts) await context.close(); if (browser) await browser.close(); console.log(evidence('cleanup'));
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
