"""UC45 EXPLAIN on a real PostgreSQL fixture transaction, always rolled back."""
import asyncio
import json
from datetime import date
from pathlib import Path
from uuid import uuid4
from dotenv import dotenv_values
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from app.services.administration_analytics import PLATFORM_SQL


async def main():
    backend=Path(__file__).resolve().parents[1];values=dotenv_values(backend/'.env')
    url=values['TEST_DATABASE_URL']
    assert make_url(url).database=='kltn_test' and make_url(url).database!=make_url(values['DATABASE_URL']).database
    engine=create_async_engine(url)
    async with engine.connect() as conn:
        tx=await conn.begin()
        try:
            workspace,channel,user=uuid4(),uuid4(),uuid4()
            await conn.execute(text("INSERT INTO users(id,full_name,email,password_hash) VALUES(:u,'Phase7C performance fixture',:email,'unused')"),{'u':user,'email':f'phase7c-perf-{uuid4()}@example.com'})
            await conn.execute(text("INSERT INTO workspaces(id,name) VALUES(:w,'Phase7C performance transaction')"),{'w':workspace})
            await conn.execute(text("INSERT INTO channels(id,workspace_id,name,type,is_default) VALUES(:c,:w,'perf','STUDY_ROOM',false)"),{'c':channel,'w':workspace})
            users=[user]
            users+=list((await conn.scalars(text("INSERT INTO users(id,full_name,email,password_hash) SELECT gen_random_uuid(),'Phase7C performance fixture',gen_random_uuid()::text||'@example.com','unused' FROM generate_series(1,99) RETURNING id"))).all())
            for uid in users:
                await conn.execute(text("""INSERT INTO study_sessions(id,user_id,channel_id,status,started_at,last_activity_at,ended_at,duration_us,end_reason,analytics_tracked)
                SELECT gen_random_uuid(),:u,:c,'COMPLETED',t,t+interval '11 minutes',t+interval '11 minutes',600000000,'LEFT',true
                FROM (SELECT '2026-01-01 00:00+00'::timestamptz+n*interval '1 hour' AS t FROM generate_series(1,100) n) seed"""),{'u':uid,'c':channel})
            await conn.execute(text("""INSERT INTO study_activity_intervals(id,session_id,started_at,ended_at)
                SELECT gen_random_uuid(),id,started_at,started_at+interval '5 minutes' FROM study_sessions WHERE channel_id=:c
                UNION ALL SELECT gen_random_uuid(),id,started_at+interval '6 minutes',ended_at FROM study_sessions WHERE channel_id=:c"""),{'c':channel})
            await conn.execute(text("""INSERT INTO learning_documents(id,channel_id,uploader_id,original_filename,name,content_type,size_bytes,object_key,checksum,status)
                SELECT gen_random_uuid(),:c,:u,'perf.pdf','Performance metadata only','application/pdf',1,'phase7c-perf/'||gen_random_uuid()::text,repeat('a',64),'READY' FROM generate_series(1,1000)"""),{'c':channel,'u':user})
            await conn.execute(text("""INSERT INTO violation_reports(id,reporter_id,target_type,target_id,channel_id,workspace_id,reason,status,version)
                SELECT gen_random_uuid(),:u,'DOCUMENT',id,:c,:w,'SPAM','PENDING',1 FROM learning_documents WHERE channel_id=:c"""),{'u':user,'c':channel,'w':workspace})
            for table in ['users','study_sessions','study_activity_intervals','learning_documents','violation_reports']:
                await conn.execute(text('ANALYZE '+table))
            params={'start':date(2026,1,1),'end':date(2026,1,7)}
            plan=await conn.scalar(text('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+PLATFORM_SQL),params)
            result=await conn.scalar(text(PLATFORM_SQL),params)
            report={'database':'kltn_test','fixture_users':100,'fixture_sessions':10000,'fixture_intervals':20000,'fixture_document_metadata':1000,'fixture_reports':1000,
                    'kind':'real PostgreSQL performance fixture; no claim of real MinIO document uploads here','plan':plan,'result':result,'cleanup':'Entire transaction rolled back; no existing rows deleted'}
            (backend.parent/'evidence/phase-7c-uc41-uc45/postgres-performance.json').write_text(json.dumps(report,indent=2,default=str),encoding='utf8')
            print(json.dumps({'execution_ms':plan[0]['Execution Time'],'fixture_sessions':10000,'fixture_intervals':20000,'result':'query executed on PostgreSQL; transaction rolled back'}))
        finally:await tx.rollback()
    await engine.dispose()


if __name__=='__main__':asyncio.run(main())
