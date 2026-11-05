"""Real hard restart: administration state survives; dedicated test DB/server only."""
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

BACKEND=Path(__file__).resolve().parents[1]
values=dotenv_values(BACKEND/'.env')
test_url=os.environ.get('TEST_DATABASE_URL') or values['TEST_DATABASE_URL']
assert make_url(test_url).database=='kltn_test'
assert make_url(test_url).database!=make_url(values['DATABASE_URL']).database
os.environ['DATABASE_URL']=test_url
OUT=BACKEND.parent/'evidence/phase-7c-uc41-uc45'


async def main():
    import httpx
    from scripts.verify_administration_live import main as cleanup
    marker='phase7c-'+str(uuid4());process=None;workspace=None
    report={'marker':marker,'database':'dedicated kltn_test','port':8018,'checks':[]}
    log=(OUT/'administration-restart-server.log').open('w',encoding='utf8')
    flags=subprocess.CREATE_NO_WINDOW if sys.platform=='win32' else 0
    async with httpx.AsyncClient(timeout=20) as client:
        async def start():
            nonlocal process
            process=subprocess.Popen([sys.executable,'-m','uvicorn','app.main:app','--host','127.0.0.1','--port','8018'],cwd=BACKEND,env=os.environ.copy(),stdout=log,stderr=log,creationflags=flags)
            for _ in range(200):
                if process.poll() is not None:raise RuntimeError('Dedicated restart server exited')
                try:
                    if (await client.get('http://127.0.0.1:8018/api/v1/health')).status_code==200:return
                except httpx.TransportError:pass
                await asyncio.sleep(.1)
            raise TimeoutError('Dedicated restart server startup')
        async def kill():
            nonlocal process
            process.kill();await asyncio.to_thread(process.wait);process=None
        async def api(actor,method,path,body=None,expected=200):
            response=await client.request(method,'http://127.0.0.1:8018/api/v1'+path,json=body,headers={'Authorization':'Bearer '+actor['token']} if actor else {})
            assert response.status_code==expected,(path,response.status_code,response.text)
            return response.json().get('data') if response.content else None
        try:
            await start();accounts={}
            for role in ['admin','owner','member']:
                email=f'{marker}-{role}@example.com';password=secrets.token_urlsafe(24)+'Aa1!'
                user=await api(None,'POST','/auth/register',{'full_name':'Private restart '+role,'email':email,'password':password,'confirm_password':password},201)
                accounts[role]={'id':user['id'],'token':(await api(None,'POST','/auth/login',{'email':email,'password':password}))['access_token']}
            admin,owner,member=[accounts[r] for r in ['admin','owner','member']]
            grant=await asyncio.to_thread(subprocess.run,[sys.executable,'-m','scripts.manage_system_admin',admin['id'],'--grant','--reason','Private restart verification fixture'],cwd=BACKEND,env=os.environ.copy(),stdout=subprocess.PIPE,stderr=subprocess.STDOUT,creationflags=flags)
            assert grant.returncode==0,grant.stdout.decode('utf8',errors='replace')
            workspace=await api(owner,'POST','/workspaces',{'name':marker},201)
            invitation=await api(owner,'POST',f"/workspaces/{workspace['id']}/invitations",{'invitee_user_id':member['id']},201)
            await api(member,'POST',f"/workspace-invitations/{invitation['token']}/accept")
            room=await api(owner,'POST',f"/workspaces/{workspace['id']}/channels",{'name':'restart-room','type':'STUDY_ROOM'},201)
            request={'version':1,'enabled':False,'reason':'Private restart state verification'}
            await api(admin,'PATCH',f"/admin/users/{member['id']}/state",request)
            await api(admin,'PATCH',f"/admin/workspaces/{workspace['id']}/state",request)
            await kill();await start()
            await api(member,'GET','/users/me',expected=403)
            await api(owner,'GET',f"/workspaces/{workspace['id']}",expected=404)
            await api(owner,'GET',f"/channels/{room['id']}/study-room",expected=404)
            user=await api(admin,'GET',f"/admin/users/{member['id']}")
            state=await api(admin,'GET',f"/admin/workspaces/{workspace['id']}")
            assert user['status']=='LOCKED' and user['admin_version']==2
            assert state['status']=='DISABLED' and state['admin_version']==2
            report['checks'].append('Real process hard-kill/restart keeps locked account, disabled Workspace and stale versions; old HTTP tokens remain denied')
            await api(admin,'PATCH',f"/admin/users/{member['id']}/state",request|{'version':2,'enabled':True})
            await api(admin,'PATCH',f"/admin/workspaces/{workspace['id']}/state",request|{'version':2,'enabled':True})
            assert (await api(member,'GET',f"/workspaces/{workspace['id']}"))['role']=='MEMBER'
            assert (await api(owner,'GET',f"/workspaces/{workspace['id']}"))['role']=='OWNER'
            report['checks'].append('Unlock/restore after restart retains original membership and role using existing session tokens')
            report['status']='PASS'
        except Exception as error:
            report['status']='NOT READY';report['error']=type(error).__name__+': '+str(error)
            raise
        finally:
            if process is not None:await kill()
            if workspace:await cleanup({'marker':marker,'action':'cleanup'})
            log.close();(OUT/'administration-restart.json').write_text(json.dumps(report,indent=2),encoding='utf8')
            print(json.dumps({'restart_subtest':report['status'],'checks':report['checks']}))


if __name__=='__main__':asyncio.run(main())
