"""Run legacy empty-database guards on a PRIVATE temporary PostgreSQL cluster.

Never reset the existing application DB or kltn_test. Credentials stay in child env only.
"""
import asyncio
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
from uuid import uuid4
import asyncpg

BACKEND=Path(__file__).resolve().parents[1]
OUT=BACKEND.parent/'evidence/phase-7c-uc41-uc45'
BIN=Path('D:/PostgreSQL/18/bin')


def main():
    folder=Path('D:/Temp')/('kltn-phase7c-pg-'+uuid4().hex)
    folder.mkdir(parents=True);assert folder.resolve().parent==Path('D:/Temp').resolve()
    data=folder/'data';password=secrets.token_urlsafe(32);pwfile=folder/'init-password.txt'
    pwfile.write_text(password,encoding='utf8')
    flags=subprocess.CREATE_NO_WINDOW if sys.platform=='win32' else 0
    def run(args):
        # pg_ctl's detached postgres inherits handles on Windows: use a file, not a pipe.
        command_log=folder/'setup-command.log'
        with command_log.open('wb') as log:
            p=subprocess.run(args,stdout=log,stderr=log,creationflags=flags,timeout=120)
        if p.returncode:raise RuntimeError(command_log.read_text(encoding='utf8',errors='replace'))
    try:
        run([str(BIN/'initdb.exe'),'-D',str(data),'-U','phase7c_operator','--pwfile',str(pwfile),'--auth','scram-sha-256','--encoding','UTF8','--locale','C'])
    finally:
        pwfile.unlink(missing_ok=True)
    started=False
    try:
        run([str(BIN/'pg_ctl.exe'),'-D',str(data),'-l',str(folder/'server.log'),'-o','-p 5547 -h 127.0.0.1','-w','start']);started=True
        async def create():
            db=await asyncpg.connect(host='127.0.0.1',port=5547,user='phase7c_operator',password=password,database='postgres')
            try:await db.execute('CREATE DATABASE kltn_test')
            finally:await db.close()
            db=await asyncpg.connect(host='127.0.0.1',port=5547,user='phase7c_operator',password=password,database='kltn_test')
            try:await db.execute('CREATE EXTENSION vector')
            finally:await db.close()
        asyncio.run(create())
        env=os.environ.copy();env['TEST_DATABASE_URL']=f'postgresql+asyncpg://phase7c_operator:{password}@127.0.0.1:5547/kltn_test'
        results=[]
        for module in ['tests.test_account_api','tests.test_workspace_api','tests.test_workspace_foundation']:
            p=subprocess.run([sys.executable,'-m','unittest',module],cwd=BACKEND,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,creationflags=flags)
            (OUT/(module.split('.')[-1]+'.txt')).write_bytes(p.stdout)
            results.append({'module':module,'exit_code':p.returncode})
            print(json.dumps(results[-1]),flush=True)
        identities=[uuid4() for _ in range(3)]
        async def cli_fixtures():
            db=await asyncpg.connect(host='127.0.0.1',port=5547,user='phase7c_operator',password=password,database='kltn_test')
            try:
                for i,identity in enumerate(identities):
                    await db.execute("INSERT INTO users(id,full_name,email,password_hash,status) VALUES($1,'Private CLI fixture',$2,'unused',$3)",identity,f'{identity}@example.com','LOCKED' if i==2 else 'ACTIVE')
            finally:await db.close()
        asyncio.run(cli_fixtures())
        cli_env=env.copy();cli_env['DATABASE_URL']=env['TEST_DATABASE_URL']
        cli_results=[]
        with (OUT/'admin-cli-integration.txt').open('wb') as log:
            for index,mode,expected in [(0,'--grant',0),(0,'--grant',0),(0,'--revoke',1),(1,'--grant',0),(0,'--revoke',0),(1,'--revoke',1),(2,'--grant',1)]:
                p=subprocess.run([sys.executable,'-m','scripts.manage_system_admin',str(identities[index]),mode,'--reason','Private isolated provisioning test'],cwd=BACKEND,env=cli_env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,creationflags=flags)
                log.write(p.stdout);cli_results.append({'fixture_index':index,'action':mode,'expected_exit':expected,'actual_exit':p.returncode})
        (OUT/'admin-cli-integration.json').write_text(json.dumps({'cases':cli_results,'passed':all(r['expected_exit']==r['actual_exit'] for r in cli_results),'database':'private cluster kltn_test port 5547'},indent=2),encoding='utf8')
        (OUT/'legacy-isolation.json').write_text(json.dumps({'cluster':str(folder),'database':'kltn_test','port':5547,'existing_databases_untouched':True,'results':results,'credentials':'random ephemeral, not saved in evidence','shutdown':'pg_ctl stop in finally'},indent=2),encoding='utf8')
        if any(r['exit_code'] for r in results):raise RuntimeError('Legacy regression failed; inspect individual logs')
        if any(r['actual_exit']!=r['expected_exit'] for r in cli_results):raise RuntimeError('Trusted CLI integration checks failed')
        restart=subprocess.run([sys.executable,'-m','scripts.verify_administration_restart'],cwd=BACKEND,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,creationflags=flags)
        (OUT/'administration-restart-run.txt').write_bytes(restart.stdout)
        print(json.dumps({'restart_integration_exit':restart.returncode}),flush=True)
        if restart.returncode:raise RuntimeError('Administration restart integration failed')
    finally:
        if started:run([str(BIN/'pg_ctl.exe'),'-D',str(data),'-m','fast','-w','stop'])


if __name__=='__main__':main()
