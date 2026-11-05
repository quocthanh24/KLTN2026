"""Dedicated PostgreSQL only: realistic indexed data and migration rollback, all rolled back."""
import asyncio
import importlib
import json
import os
from datetime import date
from pathlib import Path
from uuid import uuid4
from dotenv import dotenv_values
from sqlalchemy import text
from sqlalchemy.engine import make_url

BACKEND = Path(__file__).resolve().parents[1]
values = dotenv_values(BACKEND/'.env')
test_url = values['TEST_DATABASE_URL']
assert make_url(test_url).database == 'kltn_test'
assert make_url(test_url).database != make_url(values['DATABASE_URL']).database
os.environ['DATABASE_URL'] = test_url
OUT = BACKEND.parent/'evidence/phase-7b-uc38-uc40'


async def main():
    from app.db.session import engine
    from app.services.learning_analytics_service import ANALYTICS_SQL
    async with engine.connect() as conn:
        tx = await conn.begin()
        try:
            # Actual migration downgrade+upgrade inside a rolled-back PostgreSQL DDL transaction.
            from alembic.migration import MigrationContext
            from alembic.operations import Operations
            import importlib.util
            spec=importlib.util.spec_from_file_location('uc38_migration',BACKEND/'alembic/versions/20261103_02_learning_analytics.py')
            migration=importlib.util.module_from_spec(spec);spec.loader.exec_module(migration)
            legacy_user,legacy_workspace,legacy_channel,legacy_session=[uuid4() for _ in range(4)]
            await conn.execute(text("INSERT INTO users(id,full_name,email,password_hash) VALUES(:id,'UC38 migration fixture',:email,'unused')"),{'id':legacy_user,'email':f'uc38-migration-{uuid4()}@example.com'})
            await conn.execute(text("INSERT INTO workspaces(id,name) VALUES(:id,'UC38 migration isolated transaction')"),{'id':legacy_workspace})
            await conn.execute(text("INSERT INTO channels(id,workspace_id,name,type,is_default) VALUES(:c,:w,'legacy','STUDY_ROOM',false)"),{'c':legacy_channel,'w':legacy_workspace})
            await conn.execute(text("""INSERT INTO study_sessions(id,user_id,channel_id,status,started_at,last_activity_at,ended_at,duration_us,end_reason,analytics_tracked)
                VALUES(:s,:u,:c,'COMPLETED','2026-01-01 00:00+00','2026-01-01 00:01+00','2026-01-01 00:01+00',30000000,'LEFT',true)"""),{'s':legacy_session,'u':legacy_user,'c':legacy_channel})
            old_count=await conn.scalar(text('SELECT count(*) FROM study_sessions'))
            def roundtrip(sync):
                with Operations.context(MigrationContext.configure(sync)):
                    migration.downgrade()
                    assert sync.scalar(text("SELECT to_regclass('study_activity_intervals')")) is None
                    assert sync.scalar(text('SELECT count(*) FROM study_sessions')) == old_count
                    migration.upgrade()
                    assert sync.scalar(text('SELECT count(*) FROM study_sessions WHERE analytics_tracked')) == 0
                    assert sync.scalar(text('SELECT duration_us FROM study_sessions WHERE id=:s'),{'s':legacy_session}) == 30000000
                    assert sync.scalar(text('SELECT count(*) FROM study_activity_intervals')) == 0
            await conn.run_sync(roundtrip)
            await tx.rollback()
        finally:
            if tx.is_active: await tx.rollback()
        tx = await conn.begin()
        try:
            user_id,workspace_id,channel_id=uuid4(),uuid4(),uuid4()
            await conn.execute(text("INSERT INTO users(id,full_name,email,password_hash) VALUES(:u,'UC38 performance',:email,'unused')"),{'u':user_id,'email':f'uc38-perf-{uuid4()}@example.com'})
            await conn.execute(text("INSERT INTO workspaces(id,name) VALUES(:w,'UC38 performance isolated transaction')"),{'w':workspace_id})
            await conn.execute(text("INSERT INTO workspace_members(id,workspace_id,user_id,role) VALUES(:id,:w,:u,'OWNER')"),{'id':uuid4(),'w':workspace_id,'u':user_id})
            await conn.execute(text("INSERT INTO channels(id,workspace_id,name,type,is_default) VALUES(:c,:w,'perf','STUDY_ROOM',false)"),{'c':channel_id,'w':workspace_id})
            # 20,000 sessions, 40,000 exact slices: 2,000 selected user sessions + 18,000 other users.
            await conn.execute(text("""INSERT INTO users(id,full_name,email,password_hash)
                SELECT gen_random_uuid(),'UC38 performance other',gen_random_uuid()::text||'@example.com','unused' FROM generate_series(1,9)"""))
            other_users=list((await conn.scalars(text("SELECT id FROM users WHERE full_name='UC38 performance other'"))).all())
            all_users=[user_id,*other_users]
            for uid in all_users:
                await conn.execute(text("""INSERT INTO study_sessions(id,user_id,channel_id,status,started_at,last_activity_at,ended_at,duration_us,end_reason,analytics_tracked)
                SELECT gen_random_uuid(),:u,:c,'COMPLETED', t,t+interval '21 minutes',t+interval '21 minutes',1200000000,'LEFT',true
                FROM (SELECT '2025-01-01 00:00:00+00'::timestamptz + n*interval '3 hours' AS t FROM generate_series(1,2000) n) seed"""),{'u':uid,'c':channel_id})
            await conn.execute(text("""INSERT INTO study_activity_intervals(id,session_id,started_at,ended_at)
                SELECT gen_random_uuid(),id,started_at,started_at+interval '10 minutes' FROM study_sessions WHERE channel_id=:c
                UNION ALL SELECT gen_random_uuid(),id,started_at+interval '11 minutes',ended_at FROM study_sessions WHERE channel_id=:c"""),{'c':channel_id})
            await conn.execute(text("""INSERT INTO pomodoro_sessions(id,channel_id,started_by,started_at,ended_at)
                SELECT gen_random_uuid(),:c,:u,started_at+interval '2 minutes',started_at+interval '8 minutes'
                FROM study_sessions WHERE user_id=:u AND channel_id=:c ORDER BY started_at LIMIT 1000"""),{'u':user_id,'c':channel_id})
            await conn.execute(text("""INSERT INTO pomodoro_focus_intervals(id,session_id,interval,status,started_at,ended_at)
                SELECT gen_random_uuid(),id,1,'COMPLETED',started_at,ended_at FROM pomodoro_sessions WHERE channel_id=:c"""),{'c':channel_id})
            await conn.execute(text("""INSERT INTO pomodoro_focus_segments(id,focus_id,started_at,ended_at)
                SELECT gen_random_uuid(),f.id,f.started_at,f.started_at+interval '2 minutes' FROM pomodoro_focus_intervals f
                JOIN pomodoro_sessions p ON p.id=f.session_id WHERE p.channel_id=:c
                UNION ALL SELECT gen_random_uuid(),f.id,f.started_at+interval '3 minutes',f.ended_at FROM pomodoro_focus_intervals f
                JOIN pomodoro_sessions p ON p.id=f.session_id WHERE p.channel_id=:c"""),{'c':channel_id})
            await conn.execute(text("""INSERT INTO pomodoro_completions(id,session_id,interval,completed_at,focus_seconds)
                SELECT gen_random_uuid(),id,1,ended_at,300 FROM pomodoro_sessions WHERE channel_id=:c"""),{'c':channel_id})
            await conn.execute(text('ANALYZE study_sessions'));await conn.execute(text('ANALYZE study_activity_intervals'))
            await conn.execute(text('ANALYZE pomodoro_focus_intervals'));await conn.execute(text('ANALYZE pomodoro_focus_segments'))
            params=dict(user_id=user_id,workspace_id=None,channel_id=None,timezone='Asia/Ho_Chi_Minh',start_date=date(2025,3,1),end_date=date(2025,3,30))
            plan=await conn.scalar(text('EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) '+ANALYTICS_SQL),params)
            result=dict((await conn.execute(text(ANALYTICS_SQL),params)).mappings().one())
            report={'database':'kltn_test','sessions':20000,'intervals':40000,'selected_user_sessions':2000,
                    'focus_cycles':1000,'focus_segments':2000,
                    'migration_legacy_fixture':{'wall_span_us':60000000,'duration_us_preserved':30000000,'guessed_intervals':0},
                    'migration_roundtrip':'PASS; PostgreSQL transactional DDL rolled back; UC37 rows preserved',
                    'plan':plan,'result':result,'cleanup':'Entire fixture transaction rolled back'}
            OUT.mkdir(parents=True,exist_ok=True)
            (OUT/'postgres-performance.json').write_text(json.dumps(report,default=str,indent=2),encoding='utf8')
            print(json.dumps({'migration':'PASS','execution_ms':plan[0]['Execution Time'],'fixture_sessions':20000,'fixture_intervals':40000}))
        finally:
            await tx.rollback()
    await engine.dispose()


if __name__=='__main__':asyncio.run(main())
