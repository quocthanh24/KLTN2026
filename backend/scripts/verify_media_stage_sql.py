"""Independent staging SQL reconciliation and real multi-chunk SFU outbox."""
import asyncio,json
from pathlib import Path
from uuid import UUID,uuid4
import asyncpg,httpx
from app.core.config import get_settings
from app.db.session import session_factory,engine
from app.models import User
from app.models.media_grant import MediaRevocation
from app.services.media_authority import revoke_in_transaction,dispatch_one

ROOT=Path('D:/Temp/kltn-media-integration-20261104')
OUT=Path(__file__).resolve().parents[2]/'evidence/phase-7c-media-integration'
assert ':5553/kltn_media_stage' in str(get_settings().database_url)

async def main():
    fixture=json.loads((ROOT/'fixture.json').read_text());owner=UUID(fixture['actors']['owner']['id']);admin=UUID(fixture['actors']['admin']['id'])
    url=str(get_settings().database_url).replace('postgresql+asyncpg','postgresql',1)
    connection=await asyncpg.connect(url)
    runtime_role=dict(await connection.fetchrow("SELECT current_user AS name,rolsuper,rolcreatedb,rolcreaterole,has_schema_privilege(current_user,'public','CREATE') AS schema_create FROM pg_roles WHERE rolname=current_user"))
    assert not any(runtime_role[k] for k in ('rolsuper','rolcreatedb','rolcreaterole','schema_create'))
    async with httpx.AsyncClient(base_url='http://127.0.0.1:8821/api/v1',timeout=15) as client:
        login=await client.post('/auth/login',json={k:fixture['actors']['admin'][k] for k in ('email','password')});login.raise_for_status()
        headers={'Authorization':'Bearer '+login.json()['data']['access_token']}
        response=await client.get('/admin/dashboard',headers=headers);response.raise_for_status();dashboard=response.json()['data']
        sql_users=await connection.fetchval('SELECT count(*) FROM users')
        sql_workspaces=await connection.fetchval('SELECT count(*) FROM workspaces WHERE deleted_at IS NULL')
        sql_reports=await connection.fetchval("SELECT count(*) FROM violation_reports WHERE status='RESOLVED'")
        assert dashboard['users']['total']==sql_users
        assert dashboard['workspaces']['total']==sql_workspaces
        assert dashboard['reports']['resolved']==sql_reports
        mismatch=await connection.fetchval('''SELECT count(*) FROM study_sessions s
        WHERE s.status='COMPLETED' AND s.analytics_tracked AND s.duration_us <> COALESCE((
        SELECT sum((EXTRACT(EPOCH FROM i.ended_at-i.started_at)*1000000)::bigint) FROM study_activity_intervals i WHERE i.session_id=s.id),0)''')
        assert mismatch==0
        # Control-plane fixture rows, NOT 1005 live participants.
        await connection.execute('''INSERT INTO media_grants(id,user_id,auth_session_id,workspace_id,channel_id,membership_id,user_version,workspace_version,issued_at,expires_at)
        SELECT gen_random_uuid(),g.user_id,g.auth_session_id,g.workspace_id,g.channel_id,g.membership_id,g.user_version,g.workspace_version,clock_timestamp(),clock_timestamp()+interval '1 hour'
        FROM (SELECT * FROM media_grants WHERE user_id=$1 ORDER BY issued_at DESC LIMIT 1) g CROSS JOIN generate_series(1,1005) n''',owner)
        async with session_factory() as db,db.begin():
            await db.get(User,owner,with_for_update=True)
            operation=await revoke_in_transaction(db,'USER',owner,admin)
        for _ in range(100):
            result=await client.get('/admin/media-revocations/'+str(operation),headers=headers);result.raise_for_status()
            if result.json()['data']['state']=='COMPLETE':break
            await asyncio.sleep(.1)
        else:raise RuntimeError('Chunked real SFU barrier not complete')
        async with session_factory() as db:
            row=await db.get(MediaRevocation,operation);assert len(row.grant_ids)>=1006
            assert len(row.ack['chunks'])>=2
            assert sum(len(c['fenced_grants']) for c in row.ack['chunks'])==len(row.grant_ids)
            attempts=row.attempts;ack=row.ack;grant_count=len(row.grant_ids)
        async with session_factory() as db:await dispatch_one(db,operation)
        async with session_factory() as db:
            row=await db.get(MediaRevocation,operation);assert row.attempts==attempts and row.ack==ack
    rejected=False
    try:
        async with connection.transaction():
            await connection.execute("INSERT INTO media_revocations(id,scope,scope_id,grant_ids,state,completed_at,ack) VALUES($1,'USER',$2,'[]','COMPLETE',clock_timestamp(),'{}')",uuid4(),owner)
    except asyncpg.CheckViolationError:rejected=True
    assert rejected
    schema=[dict(r) for r in await connection.fetch("SELECT conname,pg_get_constraintdef(oid) definition FROM pg_constraint WHERE conrelid IN ('media_grants'::regclass,'media_revocations'::regclass) ORDER BY conname")]
    indexes=[dict(r) for r in await connection.fetch("SELECT indexname,indexdef FROM pg_indexes WHERE tablename IN ('media_grants','media_revocations') ORDER BY indexname")]
    result={'status':'PASS','scope':'staging only','database_runtime_role':runtime_role,'dashboard_sql':{'users':sql_users,'workspaces':sql_workspaces,'resolved_reports':sql_reports},
        'completed_attendance_ledger_mismatches':mismatch,'control_plane_grants_in_one_operation':grant_count,'real_sfu_chunk_acks':len(ack['chunks']),
        'completed_outbox_idempotent':True,'empty_fake_ack_rejected_by_pg':rejected,'schema':schema,'indexes':indexes}
    (OUT/'sql-reconciliation.json').write_text(json.dumps(result,indent=2))
    await connection.close();await engine.dispose()
    print('SQL reconciliation, real multi-chunk SFU ACK, completed-event idempotency and PG fake-ACK constraint PASS')
asyncio.run(main())
