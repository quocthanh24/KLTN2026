// Real app/API/PostgreSQL; independent browser contexts, exact-run fixtures only.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const { randomUUID, randomBytes } = require('node:crypto');
const { spawnSync } = require('node:child_process');
const path = require('node:path');
const frontend = process.env.FRONTEND_TEST_URL || 'http://localhost:3000';
const backend = process.env.LIVE_API_URL || 'http://localhost:8000';
const marker = `invitation-${randomUUID()}`;
const accounts = ['owner', 'member', 'outsider'].map(role => ({ role, email: `${marker}-${role}@example.com`, password: randomBytes(24).toString('base64url'), name: `Invite ${role}` }));
let checks = 0;
const pass = message => console.log(`PASS ${++checks}: ${message}`);
async function api(account, method, route, body, expected = 200, code) {
  const response = await fetch(`${backend}/api/v1${route}`, { method, headers: { ...(account?.token ? { Authorization: `Bearer ${account.token}` } : {}), ...(body ? { 'Content-Type': 'application/json' } : {}) }, body: body ? JSON.stringify(body) : undefined });
  assert.equal(response.status, expected, `API ${method}: expected ${expected}, got ${response.status}`);
  if (expected === 204) return;
  const result = await response.json();
  if (code) assert.equal(result.error.code, code);
  return result.data;
}
function database(data) {
  const cwd = path.resolve(__dirname, '../../backend');
  const result = spawnSync(process.env.LIVE_PYTHON || path.join(cwd, '.venv-win/Scripts/python.exe'), ['-m', 'scripts.verify_invitation_live'], { cwd, input: JSON.stringify({ marker, ...data }), encoding: 'utf8', timeout: 30000 });
  assert.equal(result.status, 0, `Scoped DB verification failed: ${result.stdout.trim()}`);
  console.log(result.stdout.trim());
}
async function login(page, account, navigate = true) {
  if (navigate) await page.goto(`${frontend}/login`);
  await page.getByRole('heading', { name: 'Đăng nhập', exact: true }).waitFor();
  await page.getByLabel('Email', { exact: true }).fill(account.email);
  await page.getByLabel('Mật khẩu', { exact: true }).fill(account.password);
  const event = page.waitForResponse(r => r.url().endsWith('/api/v1/auth/login') && r.request().method() === 'POST');
  await page.getByRole('button', { name: 'Đăng nhập', exact: true }).click();
  const response = await event; assert.equal(response.status(), 200);
  const data = (await response.json()).data; account.token = data.access_token; account.id = data.user.id;
  assert.equal(data.user.email, account.email);
  await page.getByRole('heading', { name: `Xin chào, ${account.name}`, exact: true }).waitFor();
}
(async () => {
  let browser; const errors = [];
  try {
    for (const account of accounts.filter(a => a.role !== 'member')) await api(null, 'POST', '/auth/register', { full_name: account.name, email: account.email, password: account.password, confirm_password: account.password }, 201);
    browser = await chromium.launch({ channel: process.env.INVITATION_BROWSER || 'chrome', headless: true });
    const pages = []; let workspace;
    for (const account of accounts) {
      const context = await browser.newContext({ permissions: ['clipboard-read', 'clipboard-write'] });
      const page = await context.newPage(); page.setDefaultTimeout(15000);
      page.on('pageerror', e => errors.push(e.message));
      if (account.role === 'member') {
        await login(page, { ...accounts[0] });
        await page.goto(`${frontend}/workspaces/${workspace.id}`);
        await page.getByRole('button', { name: 'Xóa Workspace', exact: true }).waitFor();
        await page.getByRole('link', { name: 'Trang chính', exact: true }).click();
        await page.getByRole('button', { name: 'Đăng xuất', exact: true }).click();
        await page.getByRole('link', { name: 'Đăng ký tài khoản', exact: true }).click();
        await page.getByLabel('Họ tên', { exact: true }).fill(account.name);
        await page.getByLabel('Email', { exact: true }).fill(account.email);
        await page.getByLabel('Mật khẩu', { exact: true }).fill(account.password);
        await page.getByLabel('Xác nhận mật khẩu', { exact: true }).fill(account.password);
        await page.getByRole('button', { name: 'Đăng ký', exact: true }).click();
        await login(page, account, false);
        await page.goto(`${frontend}/workspaces/${workspace.id}`);
        await page.getByText('Bạn không thuộc Workspace', { exact: true }).waitFor();
        assert.equal(await page.getByRole('button', { name: 'Xóa Workspace', exact: true }).count(), 0);
        pass('Second context: A logout → register B → login B denies A workspace before invitation');
      } else await login(page, account);
      if (account.role === 'owner') workspace = await api(account, 'POST', '/workspaces', { name: marker }, 201);
      pages.push(page);
    }
    const [owner, member, outsider] = accounts; const [a, b] = pages;
    await a.goto(`${frontend}/workspaces/${workspace.id}`);
    await a.getByRole('heading', { name: marker, exact: true }).waitFor();
    assert.equal(await a.getByLabel('User ID người nhận (để trống để tạo lời mời chung)').inputValue(), '');
    assert.equal(await a.getByLabel('Hết hạn (không bắt buộc)').inputValue(), '');
    const created = a.waitForResponse(r => r.url().endsWith(`/workspaces/${workspace.id}/invitations`) && r.request().method() === 'POST');
    await a.getByRole('button', { name: 'Tạo lời mời', exact: true }).click();
    const response = await created; assert.equal(response.status(), 201);
    const invitation = (await response.json()).data;
    assert.equal(invitation.invitee_user_id, null); assert.equal(invitation.expires_at, null);
    const link = await a.getByLabel('Liên kết lời mời', { exact: true }).inputValue();
    assert.equal(link, `${frontend}/invitations/${encodeURIComponent(invitation.token)}`);
    await a.getByRole('button', { name: 'Sao chép', exact: true }).click();
    assert.equal(await a.evaluate(() => navigator.clipboard.readText()), link);
    database({ action: 'inspect', id: invitation.id, token: invitation.token });
    pass('A creates/copies general invitation; exact link matches committed token hash and NULL fields');
    const createInvitation = body => api(owner, 'POST', `/workspaces/${workspace.id}/invitations`, body, 201);
    const targeted = await createInvitation({ invitee_user_id: outsider.id });
    const expired = await createInvitation({ expires_at: new Date(Date.now() - 60000).toISOString() });
    const revoked = await createInvitation({}); database({ action: 'revoke', id: revoked.id });
    const rejected = async (token, status, code) => {
      const event = b.waitForResponse(r => r.url().includes('/api/v1/workspace-invitations/'));
      await b.goto(`${frontend}/invitations/${encodeURIComponent(token)}`);
      const response = await event;
      const body = await response.json();
      assert.equal(response.status(), status); assert.equal(body.error.code, code);
      await b.getByText(body.error.message, { exact: true }).waitFor();
      assert.equal(await b.getByRole('button', { name: 'Tham gia', exact: true }).count(), 0);
      await api(member, 'POST', `/workspace-invitations/${encodeURIComponent(token)}/accept`, undefined, status, code);
    };
    for (const [token, status, code] of [[targeted.token, 403, 'INVITATION_NOT_FOR_USER'], [expired.token, 410, 'INVITATION_EXPIRED'], [revoked.token, 409, 'INVITATION_ALREADY_USED'], [`invalid-${randomUUID()}`, 404, 'INVITATION_INVALID']]) {
      await rejected(token, status, code);
    }
    pass('Targeted wrong user, expired, revoked and invalid links reject both browser preview and accept');
    for (const url of [link, `${frontend}/invitations/${encodeURIComponent(link)}`]) {
      await b.goto(url); await b.getByRole('heading', { name: marker, exact: true }).waitFor();
    }
    await b.goto(`${frontend}/workspaces`);
    await b.getByLabel('Mã lời mời', { exact: true }).fill(invitation.token);
    await b.getByRole('button', { name: 'Xem lời mời', exact: true }).click();
    await b.getByRole('heading', { name: marker, exact: true }).waitFor();
    pass('Direct link, legacy URL-encoded link and raw-code form all preview the same pending invitation');
    assert.equal((await api(member, 'GET', '/users/me')).email, member.email);
    const previewEvent = b.waitForResponse(r => r.url().includes('/api/v1/workspace-invitations/'));
    // Manual regression: paste the copied URL into the existing invitation-code form.
    await b.goto(`${frontend}/workspaces`);
    await b.getByLabel('Mã lời mời', { exact: true }).fill(link);
    await b.getByRole('button', { name: 'Xem lời mời', exact: true }).click();
    const preview = await previewEvent;
    console.log('Preview diagnostics:', JSON.stringify({ tokenMatches: decodeURIComponent(preview.url().split('/').at(-1)) === invitation.token, status: preview.status(), code: (await preview.json()).error?.code }));
    assert.equal(preview.status(), 200);
    await b.getByRole('heading', { name: marker, exact: true }).waitFor();
    if (process.env.LIVE_EVIDENCE_DIR) await b.screenshot({ path: path.join(process.env.LIVE_EVIDENCE_DIR, 'invitation-preview.png'), fullPage: true });
    await b.getByRole('button', { name: 'Tham gia', exact: true }).click();
    await b.waitForURL(`${frontend}/workspaces/${workspace.id}`);
    await b.getByText('Vai trò của bạn:', { exact: false }).filter({ hasText: 'MEMBER' }).waitFor();
    await b.locator('.members').getByText(member.name, { exact: true }).waitFor();
    if (process.env.LIVE_EVIDENCE_DIR) await b.screenshot({ path: path.join(process.env.LIVE_EVIDENCE_DIR, 'invitation-member.png'), fullPage: true });
    assert.equal(await b.getByRole('button', { name: 'Xóa Workspace', exact: true }).count(), 0);
    assert.equal((await api(member, 'GET', `/workspaces/${workspace.id}`)).role, 'MEMBER');
    assert.equal((await api(owner, 'GET', `/workspaces/${workspace.id}`)).role, 'OWNER');
    database({ action: 'inspect', id: invitation.id, token: invitation.token, status: 'ACCEPTED' });
    pass('B previews and accepts in real browser as MEMBER; A remains OWNER');
    await b.goto(`${frontend}/workspaces`); await b.getByRole('link', { name: marker, exact: true }).waitFor();
    pass('Joined workspace appears in B list');
    await rejected(invitation.token, 409, 'INVITATION_ALREADY_USED');
    await api(member, 'POST', `/workspaces/${workspace.id}/invitations`, {}, 403, 'WORKSPACE_PERMISSION_DENIED');
    pass('Accepted invitation cannot be reused; B cannot issue OWNER/ADMIN invitations');
    // Same-tab account switch and browser Back must not resurrect cached OWNER UI.
    await b.getByRole('link', { name: 'Trang chính', exact: true }).click();
    await b.getByRole('button', { name: 'Đăng xuất', exact: true }).click();
    await login(b, { ...owner }, false);
    await b.goto(`${frontend}/workspaces/${workspace.id}`);
    await b.getByRole('button', { name: 'Xóa Workspace', exact: true }).waitFor();
    await b.getByRole('link', { name: 'Trang chính', exact: true }).click();
    await b.getByRole('button', { name: 'Đăng xuất', exact: true }).click();
    await login(b, outsider, false);
    await b.goBack();
    await b.waitForURL(`${frontend}/workspaces/${workspace.id}`);
    await b.getByText('Bạn không thuộc Workspace', { exact: true }).waitFor();
    assert.equal(await b.getByRole('button', { name: 'Xóa Workspace', exact: true }).count(), 0);
    assert.equal((await api(owner, 'GET', `/workspaces/${workspace.id}`)).role, 'OWNER');
    pass('Same-tab A logout → B login → Back to owner workspace denies access without cached OWNER UI');
    const c = pages[2];
    await c.goto(`${frontend}/invitations/${targeted.token}`);
    await c.getByRole('heading', { name: marker, exact: true }).waitFor();
    await c.getByRole('button', { name: 'Tham gia', exact: true }).click();
    await c.waitForURL(`${frontend}/workspaces/${workspace.id}`);
    await c.getByText('Vai trò của bạn:', { exact: false }).filter({ hasText: 'MEMBER' }).waitFor();
    assert.equal((await api(owner, 'GET', `/workspaces/${workspace.id}`)).role, 'OWNER');
    pass('Targeted recipient C accepts as MEMBER; owner A unchanged');
    assert.deepEqual(errors, []);
    console.log(`INVITATION LIVE PASS: ${checks} checks`);
  } finally {
    if (browser) await browser.close();
    database({ action: 'cleanup' });
  }
})().catch(error => { console.error(error.message); process.exitCode = 1; });
