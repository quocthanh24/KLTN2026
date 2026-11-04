"""Live staging fixtures only; never load application credentials."""
import asyncio,json,secrets,sys
from pathlib import Path
from uuid import UUID,uuid4
import httpx
from sqlalchemy import text
from app.core.config import get_settings
from app.db.session import session_factory,engine
ROOT=Path('D:/Temp/kltn-media-integration-20261104')
assert '127.0.0.1:5553/kltn_media_stage' in str(get_settings().database_url)

async def main():
    action=sys.argv[1]
    if action=='seed':
        marker='media-stage-'+uuid4().hex
        actors={}
        async with httpx.AsyncClient(base_url='http://127.0.0.1:8821/api/v1',timeout=20) as client:
            async def api(actor,method,route,body=None,status=200):
                r=await client.request(method,route,json=body,headers={'Authorization':'Bearer '+actor['token']} if actor else {})
                assert r.status_code==status,(route,r.status_code,r.text[:100]);return r.json()['data'] if status!=204 else None
            for role in ['owner','member','observer','admin','outsider']:
                password=secrets.token_urlsafe(32);email=marker+'-'+role+'@example.com'
                row=await api(None,'POST','/auth/register',{'full_name':role+' '+marker,'email':email,'password':password,'confirm_password':password},201)
                grant=await api(None,'POST','/auth/login',{'email':email,'password':password})
                actors[role]={'id':row['id'],'email':email,'password':password,'token':grant['access_token']}
            from scripts.manage_system_admin import change
            await change(UUID(actors['admin']['id']),True,'Audited isolated staging administrator')
            owner=actors['owner'];workspace=await api(owner,'POST','/workspaces',{'name':marker},201)
            for role in ['member','observer']:
                invite=await api(owner,'POST',f"/workspaces/{workspace['id']}/invitations",{'invitee_user_id':actors[role]['id']},201)
                await api(actors[role],'POST',f"/workspace-invitations/{invite['token']}/accept")
            room=await api(owner,'POST',f"/workspaces/{workspace['id']}/channels",{'name':'Stage room','type':'STUDY_ROOM'},201)
            chat=await api(owner,'POST',f"/workspaces/{workspace['id']}/channels",{'name':'Stage chat','type':'TEXT'},201)
            private={'marker':marker,'actors':actors,'workspace':workspace,'room':room,'chat':chat}
            for role in ['owner','member','observer']:actors[role]['media']=await api(actors[role],'POST',f"/channels/{room['id']}/study-room/media-token")
            (ROOT/'fixture.json').write_text(json.dumps(private))
        print('Created private staging fixture via real HTTP and audited CLI')
    elif action=='inspect':
        async with session_factory() as db:
            rows=await db.execute(text("SELECT id,state,ack,origin_xid,pg_xact_commit_timestamp(((origin_xid % 4294967296)::text)::xid) AS commit_at,completed_at FROM media_revocations ORDER BY created_at"))
            result=[dict(r._mapping) for r in rows]
            print(json.dumps(result,default=str))
    await engine.dispose()
asyncio.run(main())
