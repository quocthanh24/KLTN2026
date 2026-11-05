// Real Chrome contexts, application API, PostgreSQL, WebSocket and LiveKit.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const { randomUUID, randomBytes } = require('node:crypto');
const { spawnSync } = require('node:child_process');
const path = require('node:path');
const fs = require('node:fs');
const frontend = process.env.FRONTEND_TEST_URL || 'http://localhost:3001';
const backend = process.env.LIVE_API_URL || 'http://localhost:8000';
const marker = `phase4e-${randomUUID()}`;
const accounts = ['owner', 'member'].map(role => ({ role, email: `${marker}-${role}@example.com`, password: randomBytes(24).toString('base64url') }));
let checks = 0;
function pass(message) { checks++; console.log(`PASS ${checks}: ${message}`); }
function database(action, session_id) {
  const dir = path.resolve(__dirname, '../../backend');
  const result = spawnSync(path.join(dir, '.venv-win/Scripts/python.exe'), ['-m', 'scripts.verify_pomodoro_live'], { cwd: dir, input: JSON.stringify({ marker, action, session_id }), encoding: 'utf8', timeout: 30000 });
  assert.equal(result.status, 0, `Database evidence/cleanup failed: ${result.stdout.trim()}`);
  return result.stdout.trim();
}
async function api(actor, method, route, body, status = 200) {
  const response = await fetch(`${backend}/api/v1${route}`, { method, headers: { ...(actor?.token ? { Authorization: `Bearer ${actor.token}` } : {}), ...(body ? { 'Content-Type': 'application/json' } : {}) }, body: body ? JSON.stringify(body) : undefined });
  assert.equal(response.status, status, `${method} ${route}: ${response.status}`);
  return status === 204 ? null : (await response.json()).data;
}
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
(async () => {
  let browser; const contexts = [], errors = [];
  try {
    for (const account of accounts) account.id = (await api(null, 'POST', '/auth/register', { full_name: `Pomodoro ${account.role}`, email: account.email, password: account.password, confirm_password: account.password }, 201)).id;
    browser = await chromium.launch({ channel: 'chrome', headless: true, args: ['--use-fake-device-for-media-stream', '--auto-accept-camera-and-microphone-capture', '--autoplay-policy=no-user-gesture-required'] });
    const pages = [];
    for (const account of accounts) {
      const context = await browser.newContext({ permissions: ['camera', 'microphone'] }); contexts.push(context);
      const page = await context.newPage(); page.setDefaultTimeout(20000); page.on('pageerror', e => errors.push(e.message));
      await page.addInitScript(() => {
        window.roomSockets = []; window.timerEvents = [];
        const Native = window.WebSocket;
        window.WebSocket = class extends Native {
          constructor(...args) { super(...args); if (String(args[0]).includes('/ws/study-rooms/')) { window.roomSockets.push(this); this.addEventListener('message', event => { const frame = JSON.parse(event.data); if (frame.type === 'pomodoro.state') window.timerEvents.push(frame.data); }); } }
        };
      });
      await page.goto(`${frontend}/login`);
      await page.getByLabel('Email', { exact: true }).fill(account.email);
      await page.getByLabel('Mật khẩu', { exact: true }).fill(account.password);
      const login = page.waitForResponse(r => r.url().endsWith('/api/v1/auth/login') && r.request().method() === 'POST');
      await page.getByRole('button', { name: 'Đăng nhập', exact: true }).click();
      account.token = (await (await login).json()).data.access_token;
      await page.getByRole('heading', { name: `Xin chào, Pomodoro ${account.role}` }).waitFor(); pages.push(page);
    }
    const [owner, member] = accounts, [a, b] = pages;
    const workspace = await api(owner, 'POST', '/workspaces', { name: marker }, 201);
    const invitation = await api(owner, 'POST', `/workspaces/${workspace.id}/invitations`, { invitee_user_id: member.id }, 201);
    await api(member, 'POST', `/workspace-invitations/${invitation.token}/accept`);
    const study = await api(owner, 'POST', `/workspaces/${workspace.id}/channels`, { name: 'pomodoro-demo', type: 'STUDY_ROOM' }, 201);
    const route = `/channels/${study.id}/study-room/pomodoro`;
    const read = () => api(owner, 'GET', route);
    const card = page => page.getByRole('region', { name: 'Pomodoro chung', exact: true });
    async function enter(page, join = true) {
      await page.goto(`${frontend}/workspaces/${workspace.id}`);
      await page.getByRole('button', { name: '◇ pomodoro-demo STUDY_ROOM', exact: true }).click();
      await card(page).waitFor();
      if (join) { await page.getByRole('button', { name: 'Tham gia phòng', exact: true }).click(); await page.getByText('Đã kết nối media', { exact: true }).waitFor(); }
    }
    async function both(status, phase) {
      for (const page of pages) await page.waitForFunction(({ status, phase }) => {
        const el = document.querySelector('[aria-label="Pomodoro chung"]');
        return el?.dataset.status === status && (!phase || el.dataset.phase === phase);
      }, { status, phase });
    }
    async function synchronized() {
      const texts = await Promise.all(pages.map(page => card(page).getByLabel('Thời gian còn lại').innerText()));
      const seconds = text => text.split(':').reduce((sum, value) => sum * 60 + Number(value), 0);
      assert.ok(Math.abs(seconds(texts[0]) - seconds(texts[1])) <= 2, `Timer mismatch: ${texts}`);
    }
    await enter(a); await enter(b); await both('idle');
    assert.equal(await card(b).getByRole('button').count(), 0);
    await api(member, 'POST', route, { action: 'start', revision: 0 }, 403);
    pass('A/B joined same room; idle 25:00; member read-only and server mutation denied');
    await card(a).getByLabel('Tập trung (phút)').fill('0');
    assert.ok(await card(a).getByRole('button', { name: 'Bắt đầu Pomodoro', exact: true }).isDisabled());
    await api(owner, 'POST', route, { action: 'start', revision: 0, focus_seconds: 0 }, 422);
    await card(a).getByLabel('Tập trung (phút)').fill('0.2');
    await card(a).getByLabel('Nghỉ (phút)').fill('0.1');
    // Double click must result in one authoritative mutation.
    let starts = 0;
    a.on('request', request => { if (request.url().endsWith(route) && request.method() === 'POST' && request.postDataJSON()?.action === 'start') starts++; });
    await card(a).getByRole('button', { name: 'Bắt đầu Pomodoro', exact: true }).dblclick();
    await both('running', 'focus'); await synchronized(); assert.equal(starts, 1);
    assert.ok(await b.evaluate(() => window.timerEvents.some(s => s.status === 'running' && s.phase === 'focus')));
    pass('UI starts custom 12s/6s; one submit; B receives pushed focus snapshot without refresh; countdowns within 2s');
    let state = await read(), session = state.session_id;
    await card(a).getByRole('button', { name: 'Tạm dừng Pomodoro', exact: true }).click();
    await both('paused', 'focus'); await synchronized();
    const frozen = await card(b).getByLabel('Thời gian còn lại').innerText();
    await sleep(2200); assert.equal(await card(b).getByLabel('Thời gian còn lại').innerText(), frozen);
    pass('Pause propagates to B and remaining time stays frozen');
    await b.reload(); await b.getByRole('button', { name: '◇ pomodoro-demo STUDY_ROOM', exact: true }).click();
    await both('paused'); assert.equal(await card(b).getByLabel('Thời gian còn lại').innerText(), frozen);
    await b.getByRole('button', { name: 'Tham gia phòng', exact: true }).click(); await b.getByText('Đã kết nối media', { exact: true }).waitFor();
    pass('Reload while paused restores exact paused countdown');
    await card(a).getByRole('button', { name: 'Tiếp tục Pomodoro', exact: true }).click();
    await both('running', 'focus'); await synchronized();
    await both('running', 'break'); await synchronized();
    assert.equal(database('count', session), '1');
    pass('Resume synchronizes; natural focus becomes break on both; PostgreSQL exactly one completion');
    await both('running', 'focus'); await synchronized();
    pass('Natural break becomes next focus; both clients converge');
    await card(a).getByRole('button', { name: 'Kết thúc Pomodoro', exact: true }).click();
    await card(a).getByRole('button', { name: 'Hủy', exact: true }).click();
    assert.equal((await read()).status, 'running');
    await card(a).getByRole('button', { name: 'Kết thúc Pomodoro', exact: true }).click();
    await card(a).getByRole('button', { name: 'Xác nhận kết thúc', exact: true }).click();
    await both('ended'); assert.equal(database('count', session), '1');
    pass('End requires confirmation; cancel preserves session; final end reaches B without extra completion');
    await card(a).getByLabel('Tập trung (phút)').fill('1');
    await card(a).getByRole('button', { name: 'Bắt đầu Pomodoro', exact: true }).click(); await both('running', 'focus');
    state = await read(); session = state.session_id;
    await b.reload(); await b.getByRole('button', { name: '◇ pomodoro-demo STUDY_ROOM', exact: true }).click();
    await both('running', 'focus'); await synchronized();
    await b.getByRole('button', { name: 'Tham gia phòng', exact: true }).click(); await b.getByText('Đã kết nối media', { exact: true }).waitFor();
    pass('Reload and mid-focus late join restore current server session');
    await b.getByRole('button', { name: 'Rời phòng', exact: true }).click();
    await b.getByRole('button', { name: 'Tham gia phòng', exact: true }).click(); await b.getByText('Đã kết nối media', { exact: true }).waitFor(); await synchronized();
    pass('Leave/rejoin does not reset shared session or countdown');
    await b.evaluate(() => window.roomSockets.forEach(socket => socket.close()));
    await b.getByRole('button', { name: 'Kết nối lại', exact: true }).click();
    await both('running', 'focus'); await synchronized();
    await b.getByText('Đã kết nối media', { exact: true }).waitFor();
    pass('Real presence WebSocket closure and reconnect recover latest authoritative timer');
    await api(owner, 'PATCH', `/workspaces/${workspace.id}/members/${member.id}/role`, { role: 'ADMIN' });
    await card(b).getByRole('button', { name: 'Tạm dừng Pomodoro', exact: true }).waitFor();
    state = await read();
    const concurrent = await Promise.all([owner, member].map(actor => fetch(`${backend}/api/v1${route}`, { method: 'POST', headers: { Authorization: `Bearer ${actor.token}`, 'Content-Type': 'application/json' }, body: JSON.stringify({ action: 'pause', revision: state.revision }) })));
    assert.deepEqual(concurrent.map(r => r.status).sort(), [200, 409]);
    await both('paused');
    await api(owner, 'POST', route, { action: 'resume', revision: (await read()).revision }); await both('running');
    // Freeze B's real socket snapshots only while its stale UI command is issued.
    // The mutation still hits the real backend; error recovery is real GET.
    await b.evaluate(() => window.roomSockets.filter(s => s.readyState === 1).forEach(s => { s.savedMessage = s.onmessage; s.onmessage = null; }));
    state = await read();
    await api(owner, 'POST', route, { action: 'pause', revision: state.revision });
    await card(b).getByRole('button', { name: 'Tạm dừng Pomodoro', exact: true }).click();
    await card(b).getByRole('alert').filter({ hasText: 'Pomodoro đã thay đổi' }).waitFor();
    await both('paused');
    assert.ok(await card(b).getByRole('button', { name: 'Tiếp tục Pomodoro', exact: true }).isEnabled());
    await b.evaluate(() => window.roomSockets.filter(s => s.readyState === 1).forEach(s => { s.onmessage = s.savedMessage; }));
    pass('Two real controller requests: one succeeds, other 409; stale UI refetches latest paused state');
    await b.evaluate(() => window.roomSockets.filter(s => s.readyState === 1).forEach(s => { s.savedMessage = s.onmessage; s.onmessage = null; }));
    await card(b).getByRole('button', { name: 'Kết thúc Pomodoro', exact: true }).click();
    await api(owner, 'PATCH', `/workspaces/${workspace.id}/members/${member.id}/role`, { role: 'MEMBER' });
    await api(member, 'POST', route, { action: 'resume', revision: (await read()).revision }, 403);
    await card(b).getByRole('button', { name: 'Xác nhận kết thúc', exact: true }).click();
    await card(b).getByRole('alert').filter({ hasText: 'Chỉ chủ sở hữu' }).waitFor();
    await b.waitForFunction(() => !document.querySelector('[aria-label="Pomodoro chung"] button'));
    await b.evaluate(() => window.roomSockets.filter(s => s.readyState === 1).forEach(s => { s.onmessage = s.savedMessage; }));
    pass('Permission revoked after page load: server rejects command and UI returns read-only');
    await api(owner, 'POST', route, { action: 'resume', revision: (await read()).revision }); await both('running');
    await card(a).getByRole('button', { name: 'Kết thúc Pomodoro', exact: true }).click(); await card(a).getByRole('button', { name: 'Xác nhận kết thúc', exact: true }).click();
    await both('ended'); assert.equal(database('count', session), '0');
    pass('Second session ended mid-focus: zero completion records');
    // No browser owns transitions: controller leaves, observer also leaves room,
    // sockets remain viewers; then all presence sockets are closed.
    state = await read(); await api(owner, 'POST', route, { action: 'start', revision: state.revision, focus_seconds: 3, break_seconds: 10 });
    session = (await read()).session_id;
    await a.getByRole('button', { name: 'Rời phòng', exact: true }).click();
    await a.evaluate(() => window.roomSockets.forEach(s => s.close()));
    await b.evaluate(() => window.roomSockets.forEach(s => s.close()));
    await sleep(4200); assert.equal(database('count', session), '1');
    state = await read(); assert.equal(state.phase, 'break');
    pass('Both real room sockets closed: background worker completed focus without browser ownership');
    await enter(a); await enter(b); await synchronized();
    state = await read(); await api(owner, 'POST', route, { action: 'end', revision: state.revision });
    await both('ended');
    if (process.env.VERIFY_BACKEND_RESTART === '1') {
      state = await read();
      await api(owner, 'POST', route, { action: 'start', revision: state.revision, focus_seconds: 60, break_seconds: 10 });
      const before = await read();
      console.log('READY: restart the verification backend now (running persisted session)');
      let outage = false, recovered = false;
      const limit = Date.now() + 60000;
      while (Date.now() < limit) {
        try {
          const response = await fetch(`${backend}/api/v1${route}`, { headers: { Authorization: `Bearer ${owner.token}` }, signal: AbortSignal.timeout(1000) });
          if (outage && response.ok) {
            const after = (await response.json()).data;
            assert.equal(after.session_id, before.session_id); assert.equal(after.deadline, before.deadline);
            assert.equal(after.status, 'running'); recovered = true; break;
          }
        } catch { outage = true; }
        await sleep(100);
      }
      assert.ok(outage && recovered, 'Real backend restart was not observed');
      await enter(a); await enter(b); await both('running', 'focus'); await synchronized();
      state = await read(); await api(owner, 'POST', route, { action: 'end', revision: state.revision }); await both('ended');
      pass('Actual backend process restart: same persisted session/deadline; both browsers rejoin and recover');
    }
    await card(a).getByLabel('Tập trung (phút)').fill('25'); await card(a).getByLabel('Nghỉ (phút)').fill('5');
    await card(a).getByRole('button', { name: 'Bắt đầu Pomodoro', exact: true }).click(); await both('running', 'focus'); await synchronized();
    if (process.env.LIVE_EVIDENCE_DIR) { fs.mkdirSync(process.env.LIVE_EVIDENCE_DIR, { recursive: true }); await card(a).screenshot({ path: path.join(process.env.LIVE_EVIDENCE_DIR, 'phase4e-owner.png') }); await card(b).screenshot({ path: path.join(process.env.LIVE_EVIDENCE_DIR, 'phase4e-member.png') }); }
    state = await read(); await api(owner, 'POST', route, { action: 'end', revision: state.revision }); await both('ended');
    assert.deepEqual(errors, []); pass('Rejoin after closed clients restores state; demo card screenshots; zero browser page errors');
    console.log(`UC27 live verification: ${checks}/${checks} PASS`);
  } finally {
    for (const context of contexts) await context.close();
    if (browser) await browser.close();
    console.log(database('cleanup'));
  }
})().catch(error => { console.error(error.stack); process.exitCode = 1; });
