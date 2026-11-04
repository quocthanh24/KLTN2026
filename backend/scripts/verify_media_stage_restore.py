"""Backup, restore and destructive migration checks on a STAGING COPY only."""
import asyncio,json,subprocess,hashlib
import asyncpg
from scripts.media_stage import ROOT,REPO,BIN,PY,env,config
OUT=REPO/'evidence/phase-7c-media-integration'

def run(args,environment=None):
    with (OUT/'migration-restore.log').open('ab') as log:
        subprocess.run([str(a) for a in args],env=environment,cwd=REPO/'backend',check=True,stdout=log,stderr=log,creationflags=subprocess.CREATE_NO_WINDOW)

async def main():
    full=config();cfg=full['env'];url=full.get('operator_database_url',cfg['DATABASE_URL']);assert ':5553/kltn_media_stage' in url
    password=url.split(':',2)[2].split('@')[0]
    e=env();e['PGPASSWORD']=password
    backup=ROOT/'pre-migration-check.dump'
    run([BIN/'pg_dump.exe','-h','127.0.0.1','-p','5553','-U','media_stage','-d','kltn_media_stage','-Fc','-f',backup],e)
    c=await asyncpg.connect(host='127.0.0.1',port=5553,user='media_stage',password=password,database='postgres')
    database='kltn_media_restore'
    if await c.fetchval('SELECT EXISTS(SELECT 1 FROM pg_database WHERE datname=$1)',database):raise RuntimeError('Refusing overwrite restored database')
    await c.execute('CREATE DATABASE kltn_media_restore');await c.close()
    run([BIN/'pg_restore.exe','-h','127.0.0.1','-p','5553','-U','media_stage','-d',database,'--single-transaction','--exit-on-error',backup],e)
    counts=[]
    for name in ['kltn_media_stage',database]:
        c=await asyncpg.connect(host='127.0.0.1',port=5553,user='media_stage',password=password,database=name)
        counts.append({table:await c.fetchval('SELECT count(*) FROM '+table) for table in ['users','workspaces','channels','learning_documents','violation_reports','media_grants','media_revocations']})
        assert await c.fetchval('SELECT count(*) FROM media_grants g LEFT JOIN auth_sessions s ON s.id=g.auth_session_id WHERE s.id IS NULL')==0
        await c.close()
    assert counts[0]==counts[1]
    # Only after restore verification: exercise destructive downgrade on COPY.
    e['DATABASE_URL']=url.replace('/kltn_media_stage','/'+database)
    run([PY,ROOT/'entry.py','alembic','downgrade','20261104_01'],e)
    run([PY,ROOT/'entry.py','alembic','upgrade','head'],e)
    run([PY,ROOT/'entry.py','alembic','check'],e)
    c=await asyncpg.connect(host='127.0.0.1',port=5553,user='media_stage',password=password,database=database)
    for table in ['users','workspaces','channels','learning_documents','violation_reports']:
        assert await c.fetchval('SELECT count(*) FROM '+table)==counts[0][table]
    assert await c.fetchval('SELECT count(*) FROM media_grants')==0
    await c.close()
    (OUT/'restore-migration.json').write_text(json.dumps({'status':'PASS','scope':'STAGING backup/restore; destructive tests on restored copy ONLY','counts':counts,'backup_bytes':backup.stat().st_size,'backup_sha256':hashlib.sha256(backup.read_bytes()).hexdigest(),'head':'20261104_02','downgrade_upgrade_check':'PASS','non_media_business_rows_preserved':True},indent=2))
    print('Verified staging restore BEFORE copy-only downgrade/upgrade; business rows preserved')
asyncio.run(main())
