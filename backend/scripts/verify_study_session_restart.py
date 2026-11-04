"""Real hard-kill/restart against dedicated kltn_test and a private test server only."""
import asyncio
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
from uuid import uuid4
from dotenv import dotenv_values
from sqlalchemy.engine import make_url

BACKEND = Path(__file__).resolve().parents[1]
values = dotenv_values(BACKEND / '.env')
test_url = values.get('TEST_DATABASE_URL', '')
assert make_url(test_url).database == 'kltn_test'
assert make_url(test_url).database != make_url(values['DATABASE_URL']).database
os.environ['DATABASE_URL'] = test_url
os.environ['FRONTEND_URL'] = 'http://localhost:3000'
OUT = BACKEND.parent / 'evidence/phase-7a-uc37'
OUT.mkdir(parents=True, exist_ok=True)


async def main():
    import httpx
    import websockets
    from sqlalchemy import select
    from app.db.session import session_factory, engine
    from app.models.study_session import StudySession
    from app.services.study_session_service import HEARTBEAT_TIMEOUT_SECONDS, RECONNECT_GRACE_SECONDS
    from scripts.verify_study_sessions_live import main as inspect
    marker = 'phase7a-' + str(uuid4())
    report = {'marker': marker, 'database': 'kltn_test', 'checks': [], 'snapshots': []}
    process = None
    log = (OUT / 'restart-server.log').open('w', encoding='utf8')
    token = None

    async def start():
        nonlocal process
        process = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', '8018'],
            cwd=BACKEND, env=os.environ.copy(), stdout=log, stderr=log,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
        for _ in range(160):
            if process.poll() is not None: raise RuntimeError('Dedicated restart test server exited')
            try:
                if (await client.get('http://127.0.0.1:8018/api/v1/health')).status_code == 200:return
            except httpx.TransportError:pass
            await asyncio.sleep(.1)
        raise TimeoutError('Dedicated server startup')

    async def kill():
        nonlocal process
        process.kill(); await asyncio.to_thread(process.wait); process = None

    async def api(method, path, body=None, expected=200):
        r = await client.request(method, 'http://127.0.0.1:8018/api/v1' + path, json=body,
                                 headers={'Authorization': 'Bearer ' + token} if token else {})
        assert r.status_code == expected, (path, r.status_code)
        return r.json()['data']

    async def connect(room):
        ws = await websockets.connect(f'ws://127.0.0.1:8018/api/v1/ws/study-rooms/{room}', origin='http://localhost:3000')
        await ws.send(json.dumps({'type': 'auth', 'access_token': token})); await ws.recv()
        await ws.send(json.dumps({'type': 'join'})); frame = json.loads(await ws.recv())
        assert frame['data']['self_state'] == 'IN_ROOM'
        return ws

    async def snapshot(room, label):
        async with session_factory() as db:
            row = await db.scalar(select(StudySession).where(StudySession.channel_id == room).order_by(StudySession.started_at.desc()))
            from app.services.study_session_service import StudySessionService
            value = StudySessionService.public(row)
            report['snapshots'].append({'label': label, **value})
            (OUT / 'restart.json').write_text(json.dumps(report, default=str, indent=2), encoding='utf8')
            return value

    try:
        async with httpx.AsyncClient(timeout=5) as client:
            await start()
            email = marker + '-owner@example.com'; password = secrets.token_urlsafe(24)
            await api('POST', '/auth/register', {'full_name': 'UC37 restart fixture', 'email': email,
                'password': password, 'confirm_password': password}, 201)
            token = (await api('POST', '/auth/login', {'email': email, 'password': password}))['access_token']
            workspace = await api('POST', '/workspaces', {'name': marker}, 201)
            channel = await api('POST', f"/workspaces/{workspace['id']}/channels", {'name': 'restart-room', 'type': 'STUDY_ROOM'}, 201)
            from uuid import UUID
            room = UUID(channel['id'])
            ws = await connect(room)
            await asyncio.sleep(.2)
            await ws.send(json.dumps({'type': 'state'}))
            await ws.recv()
            before = await snapshot(room, 'before_hard_kill')
            assert before['duration_us'] > 0
            await kill()
            assert (await snapshot(room, 'after_hard_kill_no_finally'))['status'] == 'ACTIVE'
            await start()
            assert (await snapshot(room, 'startup_reconciles_old_connection'))['status'] == 'DISCONNECTED'
            new_ws = await connect(room)
            resumed = await snapshot(room, 'rejoined_within_grace')
            assert resumed['id'] == before['id'] and resumed['duration_us'] == before['duration_us']
            report['checks'].append('real process hard-kill, durable ACTIVE row, startup pending, reconnect same ID without downtime credit')
            await ws.close()
            await kill()
            await new_ws.close()
            # Real elapsed lease + grace, not a forged timestamp or a mocked clock.
            await asyncio.sleep(HEARTBEAT_TIMEOUT_SECONDS + RECONNECT_GRACE_SECONDS + 2)
            await start()
            final = await snapshot(room, 'restart_after_real_grace_elapsed')
            assert final['status'] == 'COMPLETED' and final['ended_at'] == resumed['last_activity_at']
            assert final['duration_us'] == resumed['duration_us'] and final['end_reason'] == 'DISCONNECT_TIMEOUT'
            report['checks'].append('restart after real lease+grace expiry closes at original last activity, no offline credit')
            report['status'] = 'PASS'
    except Exception as error:
        report['status'] = 'NOT READY'; report['error'] = type(error).__name__ + ': ' + str(error)
        raise
    finally:
        if process is not None: await kill()
        if token is not None: await inspect({'marker': marker, 'action': 'cleanup'})
        await engine.dispose(); log.close()
        (OUT / 'restart.json').write_text(json.dumps(report, default=str, indent=2), encoding='utf8')
        print(json.dumps({'status': report.get('status'), 'checks': report['checks']}))


if __name__ == '__main__': asyncio.run(main())
