// UC37 real Chrome, WebSockets, PostgreSQL and LiveKit. No mocked room/session results.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'D:/Temp/kltn-browser-verification/node_modules/playwright');
const assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path');
const { randomUUID, randomBytes } = require('node:crypto'), { spawnSync } = require('node:child_process');
const frontend = process.env.FRONTEND_TEST_URL || 'http://localhost:3000', backend = process.env.LIVE_API_URL || 'http://localhost:8000';
const marker = 'phase7a-' + randomUUID(), out = path.resolve(__dirname, '../../evidence/phase-7a-uc37'); fs.mkdirSync(out, { recursive: true });
const accounts = ['owner', 'member', 'outsider'].map(role => ({ role, email: `${marker}-${role}@example.com`, password: randomBytes(24).toString('base64url') }));
const report = { marker, checks: [], snapshots: [] }; let browser;
const sleep = ms => new Promise(r => setTimeout(r, ms));
function evidence(action) {
  const dir = path.resolve(__dirname, '../../backend');
  const r = spawnSync(path.join(dir, '.venv-win/Scripts/python.exe'), ['-m', 'scripts.verify_study_sessions_live'], { cwd: dir, input: JSON.stringify({ marker, action }), encoding: 'utf8', timeout: 30000 });
  assert.equal(r.status, 0, r.stdout + r.stderr); return r.stdout.trim();
}
function record(label) { report.snapshots.push({ label, ...JSON.parse(evidence('inspect')) }); fs.writeFileSync(path.join(out, 'browser.json'), JSON.stringify(report, null, 2)); }
async function api(actor, method, route, body, expected = 200) {
  const r = await fetch(backend + '/api/v1' + route, { method, headers: { Connection: 'close', ...(actor?.token ? { Authorization: 'Bearer ' + actor.token } : {}), ...(body ? { 'Content-Type': 'application/json' } : {}) }, body: body ? JSON.stringify(body) : undefined });
  assert.equal(r.status, expected, route + ': ' + await r.clone().text()); return expected === 204 ? null : (await r.json()).data;
}
(async () => {
  for (const a of accounts) { a.id = (await api(null, 'POST', '/auth/register', { full_name: 'UC37 ' + a.role, email: a.email, password: a.password, confirm_password: a.password }, 201)).id; a.token = (await api(null, 'POST', '/auth/login', { email: a.email, password: a.password })).access_token; }
  const [owner, member, outsider] = accounts;
  const workspace = await api(owner, 'POST', '/workspaces', { name: marker }, 201);
  const invite = await api(owner, 'POST', `/workspaces/${workspace.id}/invitations`, { invitee_user_id: member.id }, 201); await api(member, 'POST', `/workspace-invitations/${invite.token}/accept`);
  const room = await api(owner, 'POST', `/workspaces/${workspace.id}/channels`, { name: 'uc37-room', type: 'STUDY_ROOM' }, 201);
  const route = `/channels/${room.id}/study-room/study-sessions`;
  const own = async () => (await api(owner, 'GET', route)).items;
  async function waitFor(predicate, seconds = 15) { for (let i = 0; i < seconds * 4; i++) { const rows = await own(); if (predicate(rows)) return rows; await sleep(250); } throw Error('Attendance state timeout'); }
  browser = await chromium.launch({ channel: 'chrome', headless: true, args: ['--use-fake-device-for-media-stream', '--auto-accept-camera-and-microphone-capture', '--autoplay-policy=no-user-gesture-required'] });
  const context = await browser.newContext({ permissions: ['camera', 'microphone'] });
  const page = await context.newPage(); page.setDefaultTimeout(30000);
  await page.addInitScript(() => {
    window.roomSockets = []; const Native = window.WebSocket;
    window.WebSocket = class extends Native { constructor(...args) { super(...args); if (String(args[0]).includes('/ws/study-rooms/')) window.roomSockets.push(this); } };
  });
  await page.goto(frontend + '/login'); await page.getByLabel('Email', { exact: true }).fill(owner.email); await page.getByLabel('Mật khẩu', { exact: true }).fill(owner.password); await page.getByRole('button', { name: 'Đăng nhập', exact: true }).click();
  await page.getByRole('heading', { name: 'Xin chào, UC37 owner' }).waitFor(); await page.goto(frontend + '/workspaces/' + workspace.id);
  await page.getByRole('button', { name: '◇ uc37-room STUDY_ROOM', exact: true }).click(); await page.getByRole('button', { name: 'Tham gia phòng', exact: true }).waitFor();
  assert.deepEqual(await own(), []); report.checks.push('actual room preparation/auth observer creates no attendance');
  await api(null, 'GET', route, undefined, 401); await api(outsider, 'GET', route, undefined, 403);
  await page.getByRole('button', { name: 'Tham gia phòng', exact: true }).click(); await page.getByText('Bạn đang ở trong phòng.', { exact: true }).waitFor();
  let rows = await waitFor(r => r.length === 1 && r[0].status === 'ACTIVE'); const first = rows[0].id;
  await sleep(2300); record('UI_join_ACTIVE');
  assert.deepEqual((await api(member, 'GET', route)).items, []);
  // A second ACTUAL socket for the same account proves multi-tab semantics without a second media identity.
  await page.evaluate(({ url, token, fakeUser }) => new Promise((resolve, reject) => {
    const ws = new WebSocket(url); window.extraRoomSocket = ws;
    ws.onopen = () => ws.send(JSON.stringify({ type: 'auth', access_token: token }));
    let joined = false, resolved = false;
    ws.onmessage = e => { const f = JSON.parse(e.data); if (f.type !== 'room.state') return;
      if (!joined) { joined = true; ws.send(JSON.stringify({ type: 'join', user_id: fakeUser, started_at: '1900-01-01' })); }
      else if (!resolved && f.data.self_state === 'IN_ROOM') { resolved = true; window.extraHeartbeat = setInterval(() => ws.send(JSON.stringify({ type: 'state' })), 2000); resolve(true); }
    }; ws.onerror = reject;
  }), { url: backend.replace('http', 'ws') + '/api/v1/ws/study-rooms/' + room.id, token: owner.token, fakeUser: member.id });
  assert.equal((await own()).length, 1);
  await page.getByRole('button', { name: 'Rời phòng', exact: true }).click(); await page.getByText('Bạn đang ở ngoài phòng.', { exact: true }).waitFor();
  assert.equal((await own())[0].status, 'ACTIVE');
  await page.evaluate(() => { clearInterval(window.extraHeartbeat); window.extraRoomSocket.send(JSON.stringify({ type: 'leave' })); });
  rows = await waitFor(r => r[0].status === 'COMPLETED'); assert.equal(rows[0].id, first); assert.equal(rows[0].end_reason, 'LEFT'); assert.ok(rows[0].duration_us > 0);
  await page.evaluate(() => window.extraRoomSocket.close()); record('last_tab_explicit_leave_COMPLETED');
  report.checks.push('two real sockets share one session; spoofed user/time ignored; last-tab leave closes exactly once');
  await page.getByRole('button', { name: 'Tham gia phòng', exact: true }).click(); await page.getByText('Bạn đang ở trong phòng.', { exact: true }).waitFor();
  rows = await waitFor(r => r.length === 2 && r[0].status === 'ACTIVE'); const second = rows[0].id;
  await page.evaluate(() => window.roomSockets.find(s => s.readyState === WebSocket.OPEN && s !== window.extraRoomSocket).close());
  rows = await waitFor(r => r[0].status === 'DISCONNECTED'); record('socket_drop_DISCONNECTED');
  await sleep(3100); await page.getByRole('button', { name: 'Kết nối lại', exact: true }).click(); await page.getByText('Bạn đang ở trong phòng.', { exact: true }).waitFor();
  rows = await waitFor(r => r[0].status === 'ACTIVE'); assert.equal(rows[0].id, second); assert.equal(rows.length, 2);
  assert.ok((Date.parse(rows[0].last_activity_at) - Date.parse(rows[0].started_at)) * 1000 - rows[0].duration_us >= 2_500_000);
  record('short_reconnect_same_session_excludes_gap'); report.checks.push('UI reconnect inside real grace keeps session ID and excludes offline gap');
  await page.evaluate(() => window.roomSockets.filter(s => s.readyState === WebSocket.OPEN).forEach(s => s.close()));
  rows = await waitFor(r => r[0].status === 'DISCONNECTED'); const frozen = rows[0];
  rows = await waitFor(r => r[0].status === 'COMPLETED', 40); assert.equal(rows[0].id, second); assert.equal(rows[0].ended_at, frozen.last_activity_at); assert.equal(rows[0].duration_us, frozen.duration_us); assert.equal(rows[0].end_reason, 'DISCONNECT_TIMEOUT');
  record('real_30s_grace_timeout_no_offline_credit'); report.checks.push('real grace expiry worker closes at last DB activity, duration unchanged');
  await page.getByRole('button', { name: 'Kết nối lại', exact: true }).click(); await page.getByText('Bạn đang ở trong phòng.', { exact: true }).waitFor();
  rows = await waitFor(r => r.length === 3 && r[0].status === 'ACTIVE'); assert.notEqual(rows[0].id, second);
  await page.getByRole('button', { name: 'Rời phòng', exact: true }).click(); await waitFor(r => r[0].status === 'COMPLETED');
  record('post_timeout_rejoin_new_session_and_leave');
  const db = JSON.parse(evidence('inspect')); assert.equal(db.pomodoro_completions, 0); assert.equal(db.connections, 0); assert.ok(db.sessions.every(s => s.status === 'COMPLETED' && s.duration_us <= s.wall_span_us));
  await page.screenshot({ path: path.join(out, 'study-room-after-tracking.png'), fullPage: true });
  report.checks.push('post-timeout rejoin creates new ID; completed invariants; no invented Pomodoro completions'); report.status = 'PASS';
})().catch(e => { report.status = 'NOT READY'; report.failure = e.stack; console.error(e); process.exitCode = 1; }).finally(async () => {
  fs.writeFileSync(path.join(out, 'browser.json'), JSON.stringify(report, null, 2));
  if (browser) await browser.close(); try { console.log(evidence('cleanup')); } catch (e) { console.error(e); }
  console.log(JSON.stringify({ status: report.status, checks: report.checks, failure: report.failure }, null, 2));
});
