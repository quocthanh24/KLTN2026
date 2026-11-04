// Real application + LiveKit/WebRTC. Chrome synthetic devices supply camera/mic;
// screen capture selects a real browser tab. No API, SDK, signaling or track mocks.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const { randomUUID, randomBytes } = require('node:crypto');
const { spawnSync } = require('node:child_process');
const path = require('node:path');
const frontend = process.env.FRONTEND_TEST_URL || 'http://localhost:3000';
const backend = process.env.LIVE_API_URL || 'http://localhost:8000';
const screenTest = process.argv.includes('--screen-share');
const memberStatusTest = process.argv.includes('--member-status');
const marker = `phase4c-${randomUUID()}`;
const accounts = ['owner', 'member'].map(role => ({ role, email: `${marker}-${role}@example.com`, password: randomBytes(24).toString('base64url') }));
function disconnectLiveKit(room, identity) {
  // Admin credentials stay in Python/backend configuration; remove only this run's owner.
  assert.equal(identity, accounts[0].id);
  const backendDir = path.resolve(__dirname, '../../backend');
  const code = `import json, sys, time, jwt
from urllib.request import Request, urlopen
from app.core.config import get_settings
try:
    data = json.load(sys.stdin)
    settings = get_settings()
    token = jwt.encode({"iss": settings.livekit_api_key.get_secret_value(), "exp": int(time.time()) + 60, "video": {"roomAdmin": True, "room": data["room"]}}, settings.livekit_api_secret.get_secret_value(), algorithm="HS256")
    url = settings.livekit_url.replace("ws://", "http://", 1).replace("wss://", "https://", 1).rstrip("/")
    request = Request(url + "/twirp/livekit.RoomService/RemoveParticipant", data=json.dumps(data).encode(), headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
    with urlopen(request, timeout=10) as response:
        assert response.status == 200
except Exception as error:
    print(type(error).__name__)
    sys.exit(1)
`;
  const result = spawnSync(process.env.LIVE_PYTHON || path.join(backendDir, '.venv-win/Scripts/python.exe'), ['-c', code], { cwd: backendDir, input: JSON.stringify({ room, identity }), encoding: 'utf8', timeout: 15000 });
  assert.equal(result.status, 0, `LiveKit fixture disconnect failed: ${result.stdout.trim()}`);
}
async function api(actor, method, route, body, status = 200) {
  const response = await fetch(`${backend}/api/v1${route}`, { method, headers: { ...(actor?.token ? { Authorization: `Bearer ${actor.token}` } : {}), ...(body ? { 'Content-Type': 'application/json' } : {}) }, body: body ? JSON.stringify(body) : undefined });
  assert.equal(response.status, status, `${method} ${route}: ${response.status}`);
  return status === 204 ? null : (await response.json()).data;
}
(async () => {
  let browser; const contexts = []; const errors = [];
  try {
    for (const account of accounts) {
      const user = await api(null, 'POST', '/auth/register', { full_name: `Media ${account.role}`, email: account.email, password: account.password, confirm_password: account.password }, 201);
      account.id = user.id;
    }
    browser = await chromium.launch({ channel: 'chrome', headless: true, args: ['--use-fake-device-for-media-stream', '--auto-accept-camera-and-microphone-capture', '--autoplay-policy=no-user-gesture-required', ...(screenTest ? ['--auto-select-tab-capture-source-by-title=UC26 screen fixture'] : [])] });
    let source;
    const openSource = async () => {
      const context = await browser.newContext(); contexts.push(context);
      source = await context.newPage();
      await source.goto('data:text/html,<title>UC26 screen fixture</title><body style="margin:0;background:rgb(20,180,80)"><h1>UC26 real tab capture</h1></body>');
    };
    if (screenTest) await openSource();
    const pages = [];
    for (const account of accounts) {
      const context = await browser.newContext({ permissions: ['camera', 'microphone'] }); contexts.push(context);
      const page = await context.newPage(); page.setDefaultTimeout(25000); page.on('pageerror', e => errors.push(e.message));
      page.on('console', message => { if (/same key|unique.*key/i.test(message.text())) errors.push(message.text()); });
      // Observe real browser streams so cleanup can be checked after DOM removal.
      await page.addInitScript(() => {
        window.roomSockets = [];
        const NativeWebSocket = window.WebSocket;
        window.WebSocket = class extends NativeWebSocket {
          constructor(...args) { super(...args); if (String(args[0]).includes('/ws/study-rooms/')) window.roomSockets.push(this); }
        };
        window.capturedTracks = [];
        const capture = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
        navigator.mediaDevices.getUserMedia = async constraints => { const stream = await capture(constraints); window.capturedTracks.push(...stream.getTracks()); return stream; };
        window.screenTracks = [];
        const display = navigator.mediaDevices.getDisplayMedia.bind(navigator.mediaDevices);
        navigator.mediaDevices.getDisplayMedia = async constraints => { const stream = await display(constraints); window.screenTracks.push(...stream.getTracks()); return stream; };
      });
      await page.goto(`${frontend}/login`);
      await page.getByLabel('Email', { exact: true }).fill(account.email);
      await page.getByLabel('Mật khẩu', { exact: true }).fill(account.password);
      const login = page.waitForResponse(r => r.url().endsWith('/api/v1/auth/login') && r.request().method() === 'POST');
      await page.getByRole('button', { name: 'Đăng nhập', exact: true }).click();
      account.token = (await (await login).json()).data.access_token;
      await page.getByRole('heading', { name: `Xin chào, Media ${account.role}` }).waitFor(); pages.push(page);
    }
    const [owner, member] = accounts; const [a, b] = pages;
    const workspace = await api(owner, 'POST', '/workspaces', { name: marker }, 201);
    const invitation = await api(owner, 'POST', `/workspaces/${workspace.id}/invitations`, { invitee_user_id: member.id }, 201);
    await api(member, 'POST', `/workspace-invitations/${invitation.token}/accept`);
    const study = await api(owner, 'POST', `/workspaces/${workspace.id}/channels`, { name: 'media-smoke', type: 'STUDY_ROOM' }, 201);
    for (const page of pages) {
      await page.goto(`${frontend}/workspaces/${workspace.id}`);
      await page.getByRole('button', { name: '◇ media-smoke STUDY_ROOM', exact: true }).click();
      await page.getByRole('button', { name: 'Tham gia phòng', exact: true }).click();
      await page.getByText('Đã kết nối media', { exact: true }).waitFor();
      assert.equal(await page.evaluate(() => window.capturedTracks.length), 0);
      assert.equal(await page.evaluate(() => window.screenTracks.length), 0);
      if (memberStatusTest && page === a) {
        assert.equal(await page.getByRole('list', { name: 'Thành viên trong phòng', exact: true }).locator('li').count(), 1);
        console.log('PASS UC28: actor alone is exactly one authoritative participant');
      }
    }
    console.log('PASS: two authenticated members connected; devices OFF without capture');
    const remote = b.getByRole('article', { name: 'Media Media owner', exact: true });
    if (memberStatusTest) {
      const list = page => page.getByRole('list', { name: 'Thành viên trong phòng', exact: true });
      const row = (page, name) => list(page).locator('li').filter({ has: page.getByText(name, { exact: true }) });
      for (const page of pages) {
        await page.waitForFunction(() => document.querySelector('ul[aria-label="Thành viên trong phòng"]')?.children.length === 2);
        assert.equal(await list(page).getByRole('button').count(), 0);
      }
      console.log('PASS UC28: two users converge without refresh; participant rows read-only');
      for (const label of ['Microphone', 'Camera']) {
        await b.getByRole('button', { name: `${label}: OFF`, exact: true }).click();
        await row(a, 'Media member').getByTitle(`${label}: ON`, { exact: true }).waitFor();
        await row(a, 'Media owner').getByTitle(`${label}: OFF`, { exact: true }).waitFor();
        await b.getByRole('button', { name: `${label}: ON`, exact: true }).click();
        await row(a, 'Media member').getByTitle(`${label}: OFF`, { exact: true }).waitFor();
      }
      console.log('PASS UC28: B mic/camera ON/OFF affect only B');
      let timer = await api(owner, 'GET', `/channels/${study.id}/study-room/pomodoro`);
      timer = await api(owner, 'POST', `/channels/${study.id}/study-room/pomodoro`, { action: 'start', revision: timer.revision, focus_seconds: 300, break_seconds: 60 });
      for (const paused of [false, true]) {
        if (paused) timer = await api(owner, 'POST', `/channels/${study.id}/study-room/pomodoro`, { action: 'pause', revision: timer.revision });
        await a.evaluate(() => window.roomSockets.forEach(socket => socket.close()));
        await a.getByText('Đã ngắt kết nối phòng. Vui lòng kết nối lại.', { exact: true }).waitFor();
        assert.equal(await list(a).locator('li').count(), 2);
        await b.getByRole('button', { name: 'Camera: OFF', exact: true }).click();
        await b.getByRole('button', { name: 'Microphone: OFF', exact: true }).click();
        if (paused) {
          await b.getByRole('button', { name: 'Chia sẻ màn hình', exact: true }).click();
          await b.getByRole('button', { name: 'Dừng chia sẻ màn hình', exact: true }).waitFor();
        }
        await row(b, 'Media member').getByTitle('Camera: ON', { exact: true }).waitFor();
        assert.ok(await row(a, 'Media member').getByTitle('Camera: OFF', { exact: true }).isVisible());
        await a.getByRole('button', { name: 'Kết nối lại', exact: true }).click();
        await row(a, 'Media member').getByTitle('Camera: ON', { exact: true }).waitFor();
        await row(a, 'Media member').getByTitle('Microphone: ON', { exact: true }).waitFor();
        if (paused) {
          await row(a, 'Media member').getByTitle('Màn hình: ON', { exact: true }).waitFor();
          await a.getByRole('article', { name: 'Media Media member', exact: true }).locator('[data-media-source="screen_share"] video').waitFor();
          await b.getByRole('button', { name: 'Dừng chia sẻ màn hình', exact: true }).click();
          await row(a, 'Media member').getByTitle('Màn hình: OFF', { exact: true }).waitFor();
        }
        await a.getByText('Đã kết nối media', { exact: true }).waitFor();
        assert.equal(await list(a).locator('li').count(), 2);
        const current = await api(owner, 'GET', `/channels/${study.id}/study-room/pomodoro`);
        assert.equal(current.session_id, timer.session_id);
        assert.equal(current.status, paused ? 'paused' : 'running');
        await b.getByRole('button', { name: 'Camera: ON', exact: true }).click();
        await b.getByRole('button', { name: 'Microphone: ON', exact: true }).click();
        await row(a, 'Media member').getByTitle('Camera: OFF', { exact: true }).waitFor();
        console.log(`PASS UC28: real socket interruption retains stale list and recovers latest media; ${current.status} Pomodoro preserved`);
      }
      await a.evaluate(() => window.roomSockets.forEach(socket => socket.close()));
      await a.getByRole('button', { name: 'Kết nối lại', exact: true }).waitFor();
      await b.getByRole('button', { name: 'Rời phòng', exact: true }).click();
      assert.equal(await list(a).locator('li').count(), 2);
      await a.getByRole('button', { name: 'Kết nối lại', exact: true }).click();
      await a.waitForFunction(() => document.querySelector('ul[aria-label="Thành viên trong phòng"]')?.children.length === 1);
      await b.getByRole('button', { name: 'Tham gia phòng', exact: true }).click();
      await b.getByText('Đã kết nối media', { exact: true }).waitFor();
      await a.waitForFunction(() => document.querySelector('ul[aria-label="Thành viên trong phòng"]')?.children.length === 2);
      timer = await api(owner, 'GET', `/channels/${study.id}/study-room/pomodoro`);
      await api(owner, 'POST', `/channels/${study.id}/study-room/pomodoro`, { action: 'end', revision: timer.revision });
      console.log('PASS UC28: missed departure removed by authoritative resync; rejoin has no duplicates');
      if (process.env.LIVE_EVIDENCE_DIR) await a.screenshot({ path: path.join(process.env.LIVE_EVIDENCE_DIR, 'phase4f-participants.png'), fullPage: true });
    }
    if (screenTest) {
      const screen = remote.locator('[data-media-source="screen_share"] video');
      const remoteB = a.getByRole('article', { name: 'Media Media member', exact: true });
      const start = async page => {
        await page.getByRole('button', { name: 'Chia sẻ màn hình', exact: true }).click();
        await page.getByRole('button', { name: 'Dừng chia sẻ màn hình', exact: true }).waitFor();
      };
      const state = async (id, expected) => {
        const deadline = Date.now() + 10000;
        while (Date.now() < deadline) {
          const details = await api(member, 'GET', `/channels/${study.id}/study-room`);
          if (details.participants.find(p => p.user_id === id)?.screen_sharing === expected) {
            if (memberStatusTest) {
              const observer = id === owner.id ? b : a;
              const name = id === owner.id ? 'Media owner' : 'Media member';
              await observer.getByRole('list', { name: 'Thành viên trong phòng', exact: true }).locator('li')
                .filter({ has: observer.getByText(name, { exact: true }) }).getByTitle(`Màn hình: ${expected ? 'ON' : 'OFF'}`, { exact: true }).waitFor();
            }
            return;
          }
          await new Promise(resolve => setTimeout(resolve, 250));
        }
        throw new Error('Presence screen_sharing did not converge');
      };
      await start(a); await screen.waitFor();
      // Pixel evidence proves the selected browser tab is actually captured,
      // rather than Chrome's synthetic screen test pattern or a fake track.
      const expectColor = async (green, blue) => b.waitForFunction(({ green, blue }) => {
        const v = document.querySelector('[data-media-source="screen_share"] video');
        if (!v || v.readyState < 2 || !v.videoWidth) return false;
        const canvas = document.createElement('canvas'); canvas.width = 1; canvas.height = 1;
        const ctx = canvas.getContext('2d'); ctx.drawImage(v, v.videoWidth / 2, v.videoHeight / 2, 1, 1, 0, 0, 1, 1);
        const [r, g, b] = ctx.getImageData(0, 0, 1, 1).data;
        return Math.abs(r - 20) < 25 && Math.abs(g - green) < 25 && Math.abs(b - blue) < 25;
      }, { green, blue });
      await expectColor(180, 80);
      if (process.env.LIVE_EVIDENCE_DIR) await screen.screenshot({ path: path.join(process.env.LIVE_EVIDENCE_DIR, 'phase4d-remote-green.png') });
      await source.evaluate(() => { document.body.style.background = 'rgb(20,80,180)'; });
      await expectColor(80, 180); await state(owner.id, true);
      if (process.env.LIVE_EVIDENCE_DIR) await screen.screenshot({ path: path.join(process.env.LIVE_EVIDENCE_DIR, 'phase4d-remote-blue.png') });
      console.log('PASS UC26: real selected tab pixels and live color change decoded remotely; share state ON');
      await start(b); await remoteB.locator('[data-media-source="screen_share"] video').waitFor();
      await a.getByRole('button', { name: 'Dừng chia sẻ màn hình', exact: true }).click();
      await screen.waitFor({ state: 'detached' }); await state(owner.id, false); await state(member.id, true);
      assert.ok(await b.getByRole('button', { name: 'Dừng chia sẻ màn hình', exact: true }).isVisible());
      console.log('PASS UC26: simultaneous shares; stopping A preserves B');
      await source.close();
      await b.getByRole('button', { name: 'Chia sẻ màn hình', exact: true }).waitFor();
      await remoteB.locator('[data-media-source="screen_share"] video').waitFor({ state: 'detached' }); await state(member.id, false);
      console.log('PASS UC26: browser-ended source reconciles track, tile and socket state');
      await openSource();
      for (const action of ['leave', 'switch', 'disconnect', 'media-disconnect']) {
        await start(a); await screen.waitFor();
        if (action === 'leave') await a.getByRole('button', { name: 'Rời phòng', exact: true }).click();
        if (action === 'switch') await a.getByRole('button', { name: '# general TEXT', exact: true }).click();
        // Playwright offline mode does not reliably terminate existing WebSockets/WebRTC.
        // Close the real presence transport; the application must unmount/close LiveKit.
        if (action === 'disconnect') await a.evaluate(() => window.roomSockets.forEach(socket => socket.close()));
        if (action === 'media-disconnect') disconnectLiveKit(`study-${study.id}`, owner.id);
        await screen.waitFor({ state: 'detached' });
        await a.waitForFunction(() => window.screenTracks.every(t => t.readyState === 'ended'));
        if (action === 'disconnect') {
          await a.reload();
          await a.getByRole('button', { name: '◇ media-smoke STUDY_ROOM', exact: true }).click();
        }
        if (action === 'switch') await a.getByRole('button', { name: '◇ media-smoke STUDY_ROOM', exact: true }).click();
        await a.getByRole('button', { name: 'Tham gia phòng', exact: true }).click();
        await a.getByText('Đã kết nối media', { exact: true }).waitFor();
        assert.equal(await a.getByRole('button', { name: 'Chia sẻ màn hình', exact: true }).getAttribute('aria-pressed'), 'false');
        await state(owner.id, false);
        console.log(`PASS UC26: ${action} cleans screen capture and remote tile; rejoin OFF`);
      }
    }
    await a.getByRole('button', { name: 'Camera: OFF', exact: true }).click();
    await a.getByRole('button', { name: 'Camera: ON', exact: true }).waitFor();
    await remote.locator('video').waitFor();
    await b.waitForFunction(() => [...document.querySelectorAll('article video')].some(v => v.videoWidth > 0 && v.readyState >= 2));
    await a.getByRole('button', { name: 'Microphone: OFF', exact: true }).click();
    await remote.locator('audio').waitFor({ state: 'attached' });
    await b.getByRole('button', { name: 'Cho phép phát âm thanh', exact: true }).click();
    await b.waitForFunction(() => [...document.querySelectorAll('audio')].some(a => !a.paused && a.currentTime > 0 && a.srcObject?.getAudioTracks().some(t => t.readyState === 'live')));
    await remote.getByText('Microphone: ON · Camera: ON', { exact: true }).waitFor();
    console.log('PASS: remote video decodes and audio track plays over real WebRTC');
    for (const device of ['Camera', 'Microphone']) await a.getByRole('button', { name: `${device}: ON`, exact: true }).click();
    await remote.locator('video').waitFor({ state: 'detached' }); await remote.locator('audio').waitFor({ state: 'detached' });
    await remote.getByText('Microphone: OFF · Camera: OFF', { exact: true }).waitFor();
    await a.waitForFunction(() => window.capturedTracks.every(t => t.readyState === 'ended'));
    console.log('PASS: unpublish removes remote tracks and stops local capture');
    await a.getByRole('button', { name: 'Camera: OFF', exact: true }).click(); await remote.locator('video').waitFor();
    await a.getByRole('button', { name: 'Rời phòng', exact: true }).click();
    await remote.waitFor({ state: 'detached' }); await a.waitForFunction(() => window.capturedTracks.every(t => t.readyState === 'ended'));
    console.log('PASS: leave cleans up media and remote participant');
    await a.getByRole('button', { name: 'Tham gia phòng', exact: true }).click(); await a.getByText('Đã kết nối media', { exact: true }).waitFor();
    await a.getByRole('button', { name: 'Camera: OFF', exact: true }).click(); await remote.locator('video').waitFor();
    await a.getByRole('button', { name: '# general TEXT', exact: true }).click();
    await remote.waitFor({ state: 'detached' }); await a.waitForFunction(() => window.capturedTracks.every(t => t.readyState === 'ended'));
    await a.getByRole('button', { name: 'Gửi tin nhắn', exact: true }).waitFor();
    assert.deepEqual(errors, []); console.log('PASS: switching to TEXT unmounts media; no page errors');
  } finally {
    for (const context of contexts) await context.close();
    if (browser) await browser.close();
    const backendDir = path.resolve(__dirname, '../../backend');
    const result = spawnSync(process.env.LIVE_PYTHON || path.join(backendDir, '.venv-win/Scripts/python.exe'), ['-m', 'scripts.cleanup_media_live'], { cwd: backendDir, input: JSON.stringify({ marker }), encoding: 'utf8', timeout: 30000 });
    assert.equal(result.status, 0, 'Scoped fixture cleanup failed'); console.log(result.stdout.trim());
  }
})().catch(error => { console.error(error.message); process.exitCode = 1; });
