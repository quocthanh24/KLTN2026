// Mocked document contracts only. READY here is a rendering fixture, never RAG evidence.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const { fixture } = require('./chat-browser.cjs');
const assert = require('node:assert/strict');
const cid = '00000000-0000-0000-0000-000000000002';
const date = '2026-10-07T00:00:00Z';
(async () => {
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  let checks = 0;
  try {
    const f = await fixture(browser), page = f.page;
    const area = page.getByRole('region', { name: 'Tài liệu học tập', exact: true });
    let documents = [], posts = 0, fault = true;
    await page.route('**/channels/*/learning-documents', async route => {
      if (route.request().method() === 'POST') {
        posts++;
        await new Promise(resolve => setTimeout(resolve, 250));
        if (fault) return route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ error: { code: 'DOCUMENT_STORAGE_FAILED', message: 'Không thể lưu tài liệu. Vui lòng thử lại', fields: null } }) });
        documents = [{ id: 'doc1', channel_id: cid, uploader_id: 'owner', name: 'Test PDF', original_filename: 'test.pdf', description: 'Details', size_bytes: 64, content_type: 'application/pdf', status: 'PROCESSING', processing_error: null, created_at: date, updated_at: date, processed_at: null }];
        return route.fulfill({ status: 201, contentType: 'application/json', body: JSON.stringify({ data: documents[0] }) });
      }
      return route.fulfill({ contentType: 'application/json', body: JSON.stringify({ data: route.request().url().includes(cid) ? documents : [] }) });
    });
    await area.getByText('Chưa có tài liệu học tập trong kênh.', { exact: true }).waitFor(); checks++;
    await area.getByRole('button', { name: 'Tải tài liệu lên', exact: true }).click();
    await area.getByLabel('Chọn tài liệu', { exact: true }).setInputFiles({ name: 'test.pdf', mimeType: 'application/pdf', buffer: Buffer.from('%PDF-1.4\n%%EOF') });
    await area.getByLabel('Tên tài liệu (tùy chọn)', { exact: true }).fill('Test PDF');
    await area.getByLabel('Mô tả tài liệu (tùy chọn)', { exact: true }).fill('Details');
    await area.locator('form').evaluate(form => { form.requestSubmit(); form.requestSubmit(); });
    await area.getByRole('button', { name: 'Đang tải tài liệu…', exact: true }).waitFor();
    assert.ok(await area.getByRole('button', { name: 'Hủy', exact: true }).isDisabled());
    await area.getByText('Không thể lưu tài liệu. Vui lòng thử lại', { exact: true }).waitFor();
    assert.equal(posts, 1); assert.equal(documents.length, 0); checks++;
    fault = false; await area.getByRole('button', { name: 'Xác nhận tải lên', exact: true }).click();
    await area.getByText('Đang xử lý', { exact: true }).waitFor();
    assert.equal(await area.locator('form').count(), 0); checks++;
    documents[0] = { ...documents[0], status: 'READY' };
    await area.getByText('Sẵn sàng', { exact: true }).waitFor(); checks++;
    documents[0] = { ...documents[0], status: 'FAILED', processing_error: 'EXTRACTION_FAILED' };
    await area.getByText('Xử lý thất bại', { exact: true }).waitFor();
    await area.getByText('Không thể xử lý nội dung. Tệp gốc vẫn được giữ lại.', { exact: true }).waitFor(); checks++;
    await page.getByRole('button', { name: '# other TEXT', exact: true }).click();
    await area.getByText('Chưa có tài liệu học tập trong kênh.', { exact: true }).waitFor();
    assert.equal(await area.getByText('Test PDF', { exact: true }).count(), 0); checks++;
    await page.getByRole('button', { name: '# general TEXT', exact: true }).click();
    await area.getByText('Test PDF', { exact: true }).waitFor();
    assert.deepEqual(f.errors, []); await f.context.close();
    const member = await fixture(browser, 'MEMBER');
    assert.equal(await member.page.getByRole('region', { name: 'Tài liệu học tập', exact: true }).getByRole('button', { name: 'Tải tài liệu lên', exact: true }).count(), 0);
    assert.deepEqual(member.errors, []); await member.context.close(); checks++;
    console.log(`PASS: ${checks} mocked UC29 browser scenarios; READY/E4 fixtures are rendering contracts only`);
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
