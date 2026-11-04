// Real Chrome UI + authenticated local HTTP/PG/MinIO. Model is DETERMINISTIC FAKE.
// Routing below is a transport proxy to port 8031, never fabricated API responses.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'D:/Temp/kltn-browser-verification/node_modules/playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const state = JSON.parse(fs.readFileSync(process.env.V2_FIXTURE_STATE || 'D:/Temp/kltn-v2-local-fixture.json', 'utf8'));
const frontend = process.env.FRONTEND_TEST_URL || 'http://127.0.0.1:3031';
const out = process.env.AI_CHAT_V2_EVIDENCE_DIR || path.resolve(__dirname, '../../evidence/ai-chat-v2-completion-20261030');
const results = { model: 'DETERMINISTIC FAKE; no GPT quality evaluation', http: [], browser: [], errors: [] };
fs.mkdirSync(out, { recursive: true });

async function request(method, endpoint, body, token) {
  const r = await fetch(state.api + '/api/v1' + endpoint, { method,
    headers: { ...(token ? { Authorization: `Bearer ${token}` } : {}), ...(body ? { 'Content-Type': 'application/json' } : {}) },
    body: body ? JSON.stringify(body) : undefined });
  return { status: r.status, body: await r.json() };
}

(async () => {
  const login = await request('POST', '/auth/login', { email: state.email, password: state.password });
  assert.equal(login.status, 200); const token = login.body.data.access_token;
  const base = `/channels/${state.channel_id}/ai/conversations`;
  const conv = (await request('POST', base, null, token)).body.data.id;
  const rid = crypto.randomUUID(); const question = 'Explain a skill from the document';
  const simultaneous = await Promise.all([1, 2].map(() => request('POST', `${base}/${conv}/questions`, { question, request_id: rid }, token)));
  assert.ok(simultaneous.some(r => r.status === 200));
  assert.ok(simultaneous.every(r => [200, 409].includes(r.status)));
  const first = simultaneous.find(r => r.status === 200).body.data;
  const retry = await request('POST', `${base}/${conv}/questions`, { question, request_id: rid }, token);
  assert.equal(retry.status, 200); assert.equal(retry.body.data.id, first.id);
  const conflict = await request('POST', `${base}/${conv}/questions`, { question: 'Different question', request_id: rid }, token);
  assert.equal(conflict.status, 409);
  results.http.push({ scenario: 'concurrent_same_request', statuses: simultaneous.map(r => r.status), retry: 200, conflict: 409 });

  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  try {
    const context = await browser.newContext({ viewport: { width: 1600, height: 1100 } });
    const page = await context.newPage();
    page.setDefaultTimeout(45000);
    page.on('pageerror', e => results.errors.push(e.message));
    await page.route('**/api/v1/**', async route => {
      const url = new URL(route.request().url());
      const response = await route.fetch({ url: state.api + url.pathname + url.search });
      await route.fulfill({ response });
    });
    await page.route('**/api/learning-documents/**', async route => {
      const url = new URL(route.request().url());
      const response = await route.fetch({ url: state.api + url.pathname.replace('/api/', '/api/v1/') + url.search });
      await route.fulfill({ response });
    });
    await page.routeWebSocket('**/api/v1/ws/**', ws => ws.close()); // Chat realtime is outside this test.
    await page.goto(frontend + '/login');
    await page.getByLabel('Email', { exact: true }).fill(state.email);
    await page.getByLabel('Mật khẩu', { exact: true }).fill(state.password);
    await page.getByRole('button', { name: 'Đăng nhập', exact: true }).click();
    await page.waitForURL('**/app');
    await page.goto(frontend + '/workspaces/' + state.workspace_id);
    if (state.original_first) {
      const mixed=page.locator('li[data-document-id]').filter({hasText:'Synthetic mixed partial'});
      await mixed.getByText(/Tiến độ chỉ mục: 1\/2/).waitFor();
      await mixed.getByText(/Ch\u1ec9 m\u1ee5c m\u1edbi c\u00f3 m\u1ed9t ph\u1ea7n/).waitFor();
      await mixed.getByRole('button',{name:'Xem t\u00e0i li\u1ec7u',exact:true}).click();
      await page.getByLabel('N\u1ed9i dung PDF trang 1',{exact:true}).waitFor();
      await page.screenshot({path:path.join(out,'browser-partial-index-original-preview.png'),fullPage:true});
      await page.getByRole('button',{name:'\u0110\u00f3ng t\u00e0i li\u1ec7u',exact:true}).click();
      results.browser.push({scenario:'partial_index_keeps_original_preview_available_without_ocr',status:'PASS_INTEGRATION_ONLY'});
    }
    await page.getByRole('button', { name: 'Mở trợ lý AI', exact: true }).click();
    let panel = page.getByRole('dialog', { name: /Trợ lý AI · v2-synthetic-study/ });
    await panel.getByRole('button', { name: 'Hội thoại mới', exact: true }).click();
    const input = panel.getByLabel('Câu hỏi cho trợ lý AI', { exact: true });
    await input.fill('Giải thích câu điều kiện loại 2 và tự tạo 3 ví dụ');
    const generalResponse = page.waitForResponse(r => r.url().includes('/questions') && r.request().method() === 'POST');
    await input.press('Enter');
    const general = (await (await generalResponse).json()).data;
    assert.equal(general.status, 'answered'); assert.equal(general.sources.length, 0);
    assert.equal(general.retrieval.chunks, 0);
    assert.equal(general.timings_ms.v2_budget.length, 1);
    await panel.getByText('If I had more time, I would read more.', { exact: false }).waitFor();
    assert.equal(await panel.getByText('Đã hoàn tất tác vụ.', { exact: true }).count(), 0);
    assert.equal(await panel.getByRole('log').getByText('<source_refs>', { exact: false }).count(), 0);
    await page.screenshot({ path: path.join(out, 'browser-general-natural-1600x1100.png'), fullPage: true });
    results.browser.push({ scenario: 'general_knowledge_with_ready_document_one_call_no_citation_no_task_template', status: 'PASS_INTEGRATION_ONLY' });
    await input.fill('Cho ví dụ khác, vẫn đừng lộ đáp án');
    const followupResponse = page.waitForResponse(r => r.url().includes('/questions') && r.request().method() === 'POST');
    await input.press('Enter');
    const followup = (await (await followupResponse).json()).data;
    assert.equal(followup.status, 'answered'); assert.equal(followup.sources.length, 0);
    assert.match(followup.answer, /chưa tiết lộ kết quả/);
    results.browser.push({ scenario: 'followup_original_example_no_answer_revealed', status: 'PASS_INTEGRATION_ONLY' });
    await input.fill('whole document teaching');
    const firstResponse = page.waitForResponse(r => r.url().includes('/questions') && r.request().method() === 'POST');
    await input.press('Enter');
    let turn = (await (await firstResponse).json()).data;
    assert.equal(turn.status, 'partial'); assert.equal(turn.v2_task.progress.unread_source_ids.length, state.original_first ? 1 : 4);
    await panel.getByRole('button', { name: 'Tiếp tục phần còn lại', exact: true }).waitFor();
    await page.screenshot({ path: path.join(out, 'browser-partial-1600x1100.png'), fullPage: true });
    const resumed = page.waitForResponse(r => r.url().endsWith('/resume') && r.request().method() === 'POST');
    await panel.getByRole('button', { name: 'Tiếp tục phần còn lại', exact: true }).click();
    turn = (await (await resumed).json()).data;
    assert.equal(turn.status, 'answered'); assert.equal(turn.v2_task.status, 'complete');
    assert.equal(turn.sources.length, state.original_first ? 3 : 20);
    await panel.getByRole('button', { name: state.original_first ? /▤.*Trang 3$/ : /▤.*Trang 20$/ }).click();
    const viewer = panel.getByRole('dialog', { name: 'Nguồn tham khảo', exact: true });
    await viewer.getByLabel(state.original_first ? 'Nội dung PDF trang 3' : 'Nội dung PDF trang 20', { exact: true }).waitFor();
    if (state.original_first) assert.ok(turn.sources.every(s => s.authority === 'original' && s.representation_sha256));
    await page.screenshot({ path: path.join(out, state.original_first ? 'browser-original-citation-page3.png' : 'browser-citation-page20-1600x1100.png'), fullPage: true });
    await viewer.getByRole('button', { name: 'Đóng nguồn', exact: true }).click();
    results.browser.push({ scenario: state.original_first ? 'original_payload_partial_resume_pdf_page3' : 'partial_resume_actual_pdf_page20', status: 'PASS_INTEGRATION_ONLY', sources: turn.sources.length });
    await page.reload();
    await page.getByRole('button', { name: 'Mở trợ lý AI', exact: true }).click();
    panel = page.getByRole('dialog', { name: /Trợ lý AI · v2-synthetic-study/ });
    await panel.getByText('Đã hoàn tất tác vụ.', { exact: true }).waitFor();
    await page.screenshot({ path: path.join(out, 'browser-reload-1600x1100.png'), fullPage: true });
    results.browser.push({ scenario: 'history_reload_checkpoint', status: 'PASS_INTEGRATION_ONLY' });
    await panel.getByRole('button', { name: 'Hội thoại mới', exact: true }).click();
    await panel.getByLabel('Câu hỏi cho trợ lý AI', { exact: true }).fill('whole document teaching');
    await panel.getByLabel('Câu hỏi cho trợ lý AI', { exact: true }).press('Enter');
    await panel.getByRole('button', { name: 'Dừng tác vụ', exact: true }).click();
    await panel.getByText('Đã dừng tác vụ.', { exact: true }).waitFor();
    results.browser.push({ scenario: 'cancel_checkpoint', status: 'PASS_INTEGRATION_ONLY' });
    await context.close();
  } finally { await browser.close(); }

  await fetch(state.api + '/__v2_fixture__/funding/off', { method: 'POST', headers: { 'X-V2-Fixture-Control': state.control } });
  try {
    const blocked = await request('POST', `${base}/${conv}/questions`, { question: 'General concept', request_id: crypto.randomUUID() }, token);
    assert.equal(blocked.status, 503); assert.equal(blocked.body.error.code, 'AI_V2_LIVE_BLOCKED');
    results.http.push({ scenario: 'unfunded_v2_blocks_without_fallback', status: 503 });
  } finally {
    await fetch(state.api + '/__v2_fixture__/funding/on', { method: 'POST', headers: { 'X-V2-Fixture-Control': state.control } });
  }
  assert.deepEqual(results.errors, []);
  fs.writeFileSync(path.join(out, 'browser-http-results.json'), JSON.stringify(results, null, 2));
  console.log(JSON.stringify({ http: results.http, browser: results.browser, errors: results.errors.length }));
})().catch(error => {
  results.failure = String(error); fs.writeFileSync(path.join(out, 'browser-http-results.json'), JSON.stringify(results, null, 2));
  console.error(error.message); process.exitCode = 1;
});
