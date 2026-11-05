"""Private staging only. Never loads .env or controls application containers."""
import asyncio
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import shutil
import asyncpg

REPO=Path(__file__).resolve().parents[2]
ROOT=Path('D:/Temp/kltn-media-integration-20261104')
BIN=Path('D:/PostgreSQL/18/bin')
OUT=REPO/'evidence/phase-7c-media-integration'
PY=REPO/'backend/.venv-win/Scripts/python.exe'
CFG=ROOT/'private.json'

def call(args,**kwargs):return subprocess.run([str(x) for x in args],check=True,**kwargs)
def config():return json.loads(CFG.read_text())
def env():
    # Applies to descendants too (pipeline_runtime launches python -m ...).
    # No staging process may fall back to the application's .env or ledgers.
    (ROOT/'sitecustomize.py').write_text("from app.core.config import Settings\nSettings.model_config['env_file']=None\n")
    result=os.environ.copy()
    # Required settings are synthetic; .env loading is disabled by entry.py.
    result.update(config()['env']);result['PYTHONPATH']=str(ROOT)+os.pathsep+str(REPO/'backend')
    result['AI_CHAT_V2_BUDGET_LEDGER']=str(ROOT/'ai-budget.sqlite3')
    result['DOCUMENT_VISION_LEDGER']=str(ROOT/'vision-ledger.sqlite3')
    return result
def frontend_env():
    keep={'PATH','SYSTEMROOT','WINDIR','TEMP','TMP','USERPROFILE','APPDATA','LOCALAPPDATA','COMSPEC','PATHEXT','NUMBER_OF_PROCESSORS','PROCESSOR_ARCHITECTURE','PROGRAMFILES'}
    e={k:v for k,v in os.environ.items() if k.upper() in keep}
    e.update(NEXT_PUBLIC_API_BASE_URL='http://127.0.0.1:8821',KLTN_TEST_DIST_DIR='.next-media-stage',NODE_ENV='production')
    return e
def start_owned(name,args,cwd,extra=None,frontend=False):
    pid=ROOT/(name+'.pid')
    if pid.exists():raise RuntimeError('Owned PID file exists: '+name)
    e=frontend_env() if frontend else env();e.update(extra or {})
    with (ROOT/(name+'.log')).open('ab') as log:
        p=subprocess.Popen([str(x) for x in args],cwd=cwd,env=e,stdout=log,stderr=log,stdin=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
    pid.write_text(str(p.pid))
def stop_owned(name):
    path=ROOT/(name+'.pid')
    if not path.exists():return
    pid=int(path.read_text())
    cmd=f"Get-CimInstance Win32_Process -Filter 'ProcessId={pid}' | Select-Object CommandLine | ConvertTo-Json -Compress"
    raw=subprocess.check_output(['powershell','-NoProfile','-Command',cmd],text=True).strip()
    if raw:
        line=json.loads(raw)['CommandLine']
        if 'kltn-media-integration-20261104' not in line:raise RuntimeError('Refusing unknown process')
        call(['taskkill','/PID',pid,'/T','/F'],stdout=subprocess.DEVNULL)
    path.unlink()
def pg(args):
    with (ROOT/'pg-command.log').open('ab') as log:call([BIN/args[0],*args[1:]],stdout=log,stderr=log,creationflags=subprocess.CREATE_NO_WINDOW)

def init():
    if CFG.exists():raise RuntimeError('Refusing overwrite existing stage')
    ROOT.mkdir(parents=True,exist_ok=True);OUT.mkdir(parents=True,exist_ok=True)
    for port in [5553,8821,8910,8911,8940,9020,9021,3021]:
        with socket.socket() as s:s.bind(('127.0.0.1',port))
    key=ROOT/'service.key';key.write_bytes(secrets.token_bytes(48))
    password=secrets.token_urlsafe(36);pw=ROOT/'pg-password';pw.write_text(password)
    stage_env={'APP_NAME':'KLTN media staging','APP_ENV':'staging','DATABASE_URL':f'postgresql+asyncpg://media_stage:{password}@127.0.0.1:5553/kltn_media_stage',
        'TEST_DATABASE_URL':f'postgresql+asyncpg://media_stage:{password}@127.0.0.1:5553/kltn_test',
        'FRONTEND_URL':'http://127.0.0.1:3021','JWT_SECRET_KEY':secrets.token_urlsafe(48),'COOKIE_SECURE':'false',
        'MEDIA_AUTHORITY_ENABLED':'true','MEDIA_SERVICE_KEY_FILE':str(key),'MEDIA_CONTROL_URL':'http://127.0.0.1:8940',
        'LIVEKIT_URL':'ws://127.0.0.1:8910','LIVEKIT_API_KEY':'stage'+secrets.token_hex(8),'LIVEKIT_API_SECRET':secrets.token_urlsafe(48),
        'OBJECT_STORAGE_ENDPOINT':'http://127.0.0.1:9020','OBJECT_STORAGE_ACCESS_KEY':'stage'+secrets.token_hex(8),'OBJECT_STORAGE_SECRET_KEY':secrets.token_urlsafe(48),
        'OBJECT_STORAGE_BUCKET':'media-stage','OBJECT_STORAGE_SECURE':'false','RAG_MODEL_DIR':'','OPENAI_API_KEY':'',
        'AI_CHAT_V2_LIVE_APPROVED':'false','DOCUMENT_VISION_ENABLED':'false','NEXT_PUBLIC_API_BASE_URL':'http://127.0.0.1:8821',
        'KLTN_TEST_DIST_DIR':'.next-media-stage'}
    CFG.write_text(json.dumps({'env':stage_env}))
    try:pg(['initdb.exe','-D',str(ROOT/'pgdata'),'-U','media_stage','--pwfile',str(pw),'--auth','scram-sha-256','--encoding','UTF8','--locale','C'])
    finally:pw.unlink(missing_ok=True)
    pg(['pg_ctl.exe','-D',str(ROOT/'pgdata'),'-l',str(ROOT/'postgres.log'),'-o','-p 5553 -h 127.0.0.1','-w','start'])
    async def databases():
        for database in ['postgres','kltn_media_stage','kltn_test']:
            c=await asyncpg.connect(host='127.0.0.1',port=5553,user='media_stage',password=password,database=database)
            if database=='postgres':
                for name in ['kltn_media_stage','kltn_test']:await c.execute('CREATE DATABASE '+name)
            else:await c.execute('CREATE EXTENSION vector')
            await c.close()
    asyncio.run(databases())
    # Every process imports settings with env_file disabled BEFORE app import.
    (ROOT/'entry.py').write_text("import sys,runpy\nfrom app.core.config import Settings\nSettings.model_config['env_file']=None\nmode=sys.argv.pop(1)\nif mode=='server':\n import uvicorn\n uvicorn.run('app.main:app',host='0.0.0.0',port=8821,access_log=False)\nelse:\n sys.argv[0]=mode\n runpy.run_module(mode,run_name='__main__')\n")
    (ROOT/'minio.env').write_text('MINIO_ROOT_USER='+stage_env['OBJECT_STORAGE_ACCESS_KEY']+'\nMINIO_ROOT_PASSWORD='+stage_env['OBJECT_STORAGE_SECRET_KEY']+'\n')
    (ROOT/'sfu.env').write_text('LIVEKIT_KEYS='+stage_env['LIVEKIT_API_KEY']+': '+stage_env['LIVEKIT_API_SECRET']+'\nKLTN_MEDIA_AUTHORITY=http://host.docker.internal:8821/api/v1/internal/media/authorize\nKLTN_MEDIA_AUTHORITY_KEY_FILE=/run/stage/service.key\nKLTN_MEDIA_CONTROL_ADDR=:8940\n')
    (ROOT/'livekit.yaml').write_text('port: 8910\nbind_addresses: ["0.0.0.0"]\nrtc:\n  tcp_port: 8911\n  udp_port: 8912\n  node_ip: 127.0.0.1\n  use_external_ip: false\nlogging:\n  level: warn\n')
    call(['docker','run','-d','--name','kltn-media-stage-minio','--label','kltn.scope=media-staging','--env-file',ROOT/'minio.env','-p','127.0.0.1:9020:9000','-p','127.0.0.1:9021:9001','-v','kltn-media-stage-minio:/data','minio-local:phase3b3','server','/data','--console-address',':9001'],stdout=subprocess.DEVNULL)
    call([PY,ROOT/'entry.py','alembic','upgrade','head'],cwd=REPO/'backend',env=env())
    restrict_runtime_role()
    import boto3
    from botocore.client import Config
    client=boto3.client('s3',endpoint_url='http://127.0.0.1:9020',aws_access_key_id=stage_env['OBJECT_STORAGE_ACCESS_KEY'],aws_secret_access_key=stage_env['OBJECT_STORAGE_SECRET_KEY'],region_name='us-east-1',config=Config(signature_version='s3v4'))
    client.create_bucket(Bucket='media-stage')
    print('Initialized independent staging; application DB/config not loaded')

def restrict_runtime_role():
    cfg=config()
    if cfg.get('operator_database_url'):raise RuntimeError('Runtime role already provisioned')
    operator=cfg['env']['DATABASE_URL'];password=operator.split(':',2)[2].split('@')[0]
    runtime_password=secrets.token_urlsafe(36)
    async def provision():
        c=await asyncpg.connect(host='127.0.0.1',port=5553,user='media_stage',password=password,database='kltn_media_stage')
        # The only interpolated literal is locally generated URL-safe entropy.
        await c.execute("CREATE ROLE media_runtime LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD '"+runtime_password+"'")
        await c.execute('GRANT CONNECT ON DATABASE kltn_media_stage TO media_runtime')
        await c.execute('GRANT USAGE ON SCHEMA public TO media_runtime')
        await c.execute('GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA public TO media_runtime')
        await c.execute('REVOKE INSERT,UPDATE,DELETE ON alembic_version FROM media_runtime')
        await c.execute('GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA public TO media_runtime')
        await c.close()
    asyncio.run(provision())
    cfg['operator_database_url']=operator
    cfg['env']['DATABASE_URL']=f'postgresql+asyncpg://media_runtime:{runtime_password}@127.0.0.1:5553/kltn_media_stage'
    CFG.write_text(json.dumps(cfg))

def sfu():
    call(['docker','run','-d','--name','kltn-media-stage-sfu','--label','kltn.scope=media-staging','--env-file',ROOT/'sfu.env','-v',f'{ROOT}/service.key:/run/stage/service.key:ro','-v',f'{ROOT}/livekit.yaml:/run/stage/livekit.yaml:ro',
        '-p','127.0.0.1:8910:8910','-p','127.0.0.1:8911:8911','-p','127.0.0.1:8912:8912/udp','-p','127.0.0.1:8940:8940','kltn-media-stage:20261104','--config','/run/stage/livekit.yaml'],stdout=subprocess.DEVNULL)
    print('Started staging SFU only')

if __name__=='__main__':
    action=sys.argv[1]
    if action=='init':init()
    elif action=='restrict-runtime-role':restrict_runtime_role()
    elif action=='backend-start':start_owned('backend',[PY,ROOT/'entry.py','server'],REPO/'backend')
    elif action=='backend-stop':stop_owned('backend')
    elif action=='sfu-start':sfu()
    elif action=='sfu-replace':
        labels=json.loads(subprocess.check_output(['docker','inspect','--format','{{json .Config.Labels}}','kltn-media-stage-sfu'],text=True))
        if labels.get('kltn.scope')!='media-staging':raise RuntimeError('Refusing unowned container')
        call(['docker','rm','-f','kltn-media-stage-sfu'],stdout=subprocess.DEVNULL)
        sfu()
    elif action=='frontend-build':
        target=ROOT/'frontend';target.mkdir(exist_ok=True)
        for name in ['src','public','scripts']:
            shutil.copytree(REPO/'frontend'/name,target/name,dirs_exist_ok=True)
        for name in ['package.json','package-lock.json','tsconfig.json','next-env.d.ts','next.config.ts','eslint.config.mjs']:
            shutil.copyfile(REPO/'frontend'/name,target/name)
        if not (target/'node_modules').exists():
            call(['cmd','/c','mklink','/J',str(target/'node_modules'),str(REPO/'frontend/node_modules')],stdout=subprocess.DEVNULL)
        call(['node',REPO/'frontend/node_modules/next/dist/bin/next','build','--webpack'],cwd=target,env=frontend_env())
    elif action=='frontend-start':
        start_owned('frontend',['node',ROOT/'frontend/node_modules/next/dist/bin/next','start','--hostname','127.0.0.1','--port','3021'],ROOT/'frontend',frontend=True)
    elif action=='frontend-stop':stop_owned('frontend')
    elif action=='stop':
        stop_owned('frontend');stop_owned('backend')
        for name,scope in [('kltn-media-stage-sfu','media-staging'),('kltn-media-stage-minio','media-staging'),('kltn-media-stage-dind','media-staging-ci')]:
            labels=json.loads(subprocess.check_output(['docker','inspect','--format','{{json .Config.Labels}}',name],text=True))
            if labels.get('kltn.scope')!=scope:raise RuntimeError('Refusing unowned container: '+name)
            call(['docker','stop',name],stdout=subprocess.DEVNULL)
        pg(['pg_ctl.exe','-D',str(ROOT/'pgdata'),'-m','fast','-w','stop'])
        print('Stopped owned staging only; preserved source, volumes and verified backup')
    elif action=='test':
        import re
        label=sys.argv[2]
        if not re.fullmatch(r'[a-z0-9-]+',label):raise RuntimeError('Invalid receipt label')
        e=env();e['MEDIA_AUTHORITY_ENABLED']='false'
        e['RAG_MODEL_DIR']='D:/Temp/kltn-rag-models/multilingual-e5-small'
        with (OUT/(label+'.log')).open('wb') as log:
            result=subprocess.run([str(PY),str(ROOT/'entry.py'),'unittest',*sys.argv[3:]],cwd=REPO/'backend',env=e,stdout=log,stderr=log,creationflags=subprocess.CREATE_NO_WINDOW)
        receipt={'exit_code':result.returncode,'modules':sys.argv[3:],'database':'owned kltn_test on 5553','env_file_loading':False,'local_e5_cache_only':True}
        (OUT/(label+'.json')).write_text(json.dumps(receipt,indent=2));print(json.dumps(receipt));raise SystemExit(result.returncode)
    elif action=='run':
        e=env()
        if sys.argv[2]=='unittest':
            e['MEDIA_AUTHORITY_ENABLED']='false'
            cached=Path('D:/Temp/kltn-rag-models/multilingual-e5-small')
            if cached.is_dir():e['RAG_MODEL_DIR']=str(cached)  # existing pinned E5, offline regression only
        call([PY,ROOT/'entry.py',*sys.argv[2:]],cwd=REPO/'backend',env=e)
    else:raise SystemExit('init|backend-start|backend-stop|sfu-start|run MODULE ARGS')
