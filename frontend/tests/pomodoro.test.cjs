const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const ts = require('typescript');
require.extensions['.ts'] = (module, filename) => module._compile(ts.transpileModule(fs.readFileSync(filename, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText, filename);
const { remaining, formatTime } = require('../src/features/study-room/pomodoro.ts');
const { connectRoom } = require('../src/features/study-room/realtime.ts');
test('server timestamps determine remaining time independent of browser wall clock', () => {
  const state = { status: 'running', deadline: '2026-10-05T00:01:00Z', server_now: '2026-10-05T00:00:00Z' };
  assert.equal(remaining(state, 2.5), 57.5);
  assert.equal(remaining(state, 99), 0);
  assert.equal(formatTime(57.5), '00:58');
  assert.equal(formatTime(1500), '25:00');
});
test('paused time is constant and client never advances phase at zero', () => {
  assert.equal(remaining({ status: 'paused', remaining_seconds: 4.25 }, 100), 4.25);
  assert.equal(remaining({ status: 'ended' }, 0), 0);
  const state = { status: 'running', phase: 'focus', deadline: '2026-10-05T00:00:00Z', server_now: '2026-10-05T00:00:00Z' };
  assert.equal(remaining(state, 10), 0); assert.equal(state.phase, 'focus');
});
test('realtime applies pushed snapshots, preserves viewer permissions, rejects reordered old state', t => {
  t.mock.method(global, 'setTimeout', () => 0);
  t.mock.method(global, 'clearTimeout', () => {});
  t.mock.method(global, 'setInterval', () => 0);
  t.mock.method(global, 'clearInterval', () => {});
  const previous = global.WebSocket, base = process.env.NEXT_PUBLIC_API_BASE_URL;
  const sockets = [], states = [];
  process.env.NEXT_PUBLIC_API_BASE_URL = 'http://backend.example';
  global.WebSocket = class { static OPEN = 1; constructor() { sockets.push(this); } close() {} };
  try {
    const client = connectRoom('room', {}, value => states.push(value), () => {});
    const socket = sockets[0];
    const send = (type, data) => socket.onmessage({ data: JSON.stringify({ type, data }) });
    send('room.state', { self_state: 'IN_ROOM', pomodoro: { revision: 1, status: 'running', can_control: false } });
    send('pomodoro.state', { revision: 2, status: 'paused' });
    assert.equal(states.at(-1).pomodoro.status, 'paused');
    assert.equal(states.at(-1).pomodoro.can_control, false);
    send('pomodoro.state', { revision: 1, status: 'running' });
    assert.equal(states.length, 2);
    send('room.state', { self_state: 'LEFT', pomodoro: { revision: 1, status: 'running', can_control: true } });
    assert.equal(states.at(-1).self_state, 'LEFT');
    assert.equal(states.at(-1).pomodoro.revision, 2);
    assert.equal(states.at(-1).pomodoro.can_control, true);
    client.close();
  } finally { global.WebSocket = previous; if (base === undefined) delete process.env.NEXT_PUBLIC_API_BASE_URL; else process.env.NEXT_PUBLIC_API_BASE_URL = base; }
});
