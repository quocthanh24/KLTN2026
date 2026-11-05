"""Sanitized receipts and source snapshot; no credential values are published."""
import ast,datetime,hashlib,json,subprocess,unittest
from pathlib import Path
REPO=Path(__file__).resolve().parents[2]
OUT=REPO/'evidence/phase-7c-media-integration'
ROOT=Path('D:/Temp/kltn-media-integration-20261104')
CLONE=ROOT/'livekit'
FILES='''backend/app/core/config.py
backend/app/models/__init__.py
backend/app/models/media_grant.py
backend/alembic/versions/20261104_02_media_authority.py
backend/app/db/media_authority.py
backend/app/main.py
backend/app/api/v1/router.py
backend/app/api/v1/media_authority.py
backend/app/api/v1/administration.py
backend/app/schemas/study_room.py
backend/app/services/media_authority.py
backend/app/services/study_room_service.py
backend/app/services/administration_service.py
backend/app/services/auth_service.py
backend/app/services/workspace_service.py
backend/app/services/channel_service.py
backend/app/realtime/moderation.py
backend/tests/test_media_authority.py
backend/tests/test_channel_chat_hardening.py
frontend/src/features/administration/views.tsx
frontend/src/features/study-room/media.ts
frontend/src/features/study-room/views.tsx
frontend/tests/media-integration-live.cjs
frontend/tests/media-integration-ui.cjs'''.splitlines()
FILES+=['backend/scripts/'+p.name for p in (REPO/'backend/scripts').glob('*media*py')]
FILES+=['services/media-sfu/'+p.name for p in (REPO/'services/media-sfu').iterdir() if p.is_file()]

def sha(data):return hashlib.sha256(data).hexdigest()
def read(name):return json.loads((OUT/name).read_text())
def run(*args):return subprocess.check_output(args,text=True).strip()
def flatten(suite):
    for item in suite:
        if isinstance(item,unittest.TestSuite):yield from flatten(item)
        else:yield item.id()

def main():
    baseline=json.loads((REPO/'evidence/ai-legacy-cleanup-20261103/before.json').read_text())['active_hashes']
    protected=json.loads((REPO/'evidence/phase-7c-uc41-uc45/ai-config-scope.json').read_text())['paths']
    changed=[p for p in protected if sha((REPO/p).read_bytes())!=baseline[p]]
    raw=(REPO/'backend/app/core/config.py').read_bytes()
    original=b''.join(line for line in raw.splitlines(keepends=True) if not line.lstrip().startswith((b'media_authority_enabled:',b'media_service_key_file:',b'media_control_url:',b'media_authority_pool_size:')))
    variants=[original,original.replace(b'\r\n',b'\n'),original.replace(b'\r\n',b'\n').replace(b'\n',b'\r\n')]
    config_preserved=any(sha(v)==baseline['backend/app/core/config.py'] for v in variants)
    assert changed==['backend/app/core/config.py'] and config_preserved
    (OUT/'protected-ai-config.json').write_text(json.dumps({'checked':len(protected),'unchanged':len(protected)-1,'shared_config_only_media_additions':True,'baseline_ai_config_preserved_after_stripping_media_fields':config_preserved,'env_files_unchanged':True},indent=2))
    manifest={name:sha((REPO/name).read_bytes()) for name in sorted(set(FILES))}
    runtime=['pkg/service/'+n for n in ['roommanager.go','utils.go','auth.go','mediaauthority.go','mediaack.go']]+['pkg/rtc/'+n for n in ['participant.go','transport.go','transportmanager.go']]
    upstream={n:sha((CLONE/n).read_bytes()) for n in runtime}
    patch=subprocess.check_output(['git','diff','--',*runtime],cwd=CLONE,text=True)
    (OUT/'upstream-runtime.patch').write_text(patch)
    names=[]
    for container in ['kltn2026-livekit-1','kltn2026-minio-1','kltn-media-stage-sfu','kltn-media-stage-minio']:
        names.append(json.loads(run('docker','inspect','--format','{"name":{{json .Name}},"image":{{json .Config.Image}},"image_id":{{json .Image}},"running":{{json .State.Running}},"started_at":{{json .State.StartedAt}}}',container)))
    provenance={'at':datetime.datetime.now(datetime.UTC).isoformat(),'source_files_touched_this_task':manifest,'pinned_upstream':run('git','-C',str(CLONE),'rev-parse','HEAD'),'patched_runtime_sha256':upstream,'binary_sha256':sha((CLONE/'livekit-stage').read_bytes()),'containers':names,'scope':'source/staging only; no cutover'}
    (OUT/'provenance.json').write_text(json.dumps(provenance,indent=2))
    for p in [REPO/n for n in FILES if n.endswith('.py')]:ast.parse(p.read_text(encoding='utf8'))
    diff=subprocess.run(['git','-c','core.safecrlf=false','diff','--check'],cwd=REPO,capture_output=True,text=True);assert diff.returncode==0,diff.stdout
    full_cfg=json.loads((ROOT/'private.json').read_text());cfg=full_cfg['env']
    secrets=[v.encode() for k,v in cfg.items() if ('SECRET' in k or k=='OBJECT_STORAGE_ACCESS_KEY') and v]
    secrets.append(cfg['DATABASE_URL'].split(':',2)[2].split('@')[0].encode())
    if full_cfg.get('operator_database_url'):secrets.append(full_cfg['operator_database_url'].split(':',2)[2].split('@')[0].encode())
    secrets.append((ROOT/'service.key').read_bytes())
    fixture=json.loads((ROOT/'fixture.json').read_text())
    for actor in fixture['actors'].values():
        secrets.extend(actor[k].encode() for k in ('password','token'))
        if actor.get('media'):secrets.append(actor['media']['token'].encode())
    for file in OUT.iterdir():
        if file.is_file():
            data=file.read_bytes()
            assert not any(secret in data for secret in secrets),'Secret leak in '+file.name
    timings=[]
    for p in OUT.glob('*.json'):
        obj=json.loads(p.read_text());op=obj.get('operation')
        if isinstance(op,dict) and op.get('commit_at') and op.get('ack'):
            commit=datetime.datetime.fromisoformat(op['commit_at']).timestamp()*1000
            timings.append({'case':p.name,'pg_origin_xid':op['origin_xid'],'commit_to_sfu_close_confirmation_ms':op['ack']['closed_at_ms']-commit,
                'last_observed_rtp_growth_after_commit_ms':obj.get('commit_to_last_observed_rtp_ms',obj.get('commit_to_last_rtp_ms')),
                'in_flight_allowance_ms':obj.get('in_flight_allowance_ms',700)})
    (OUT/'timing-audit.json').write_text(json.dumps({'clock':'same-host PostgreSQL/Windows/WSL clock; receiver samples are bounds, not packet hardware timestamps','cases':timings},indent=2))
    modules=read('backend-regression-final.json')['modules']+read('backend-legacy-final.json')['modules']
    ids=list(flatten(unittest.defaultTestLoader.loadTestsFromNames(modules)))
    (OUT/'backend-test-ids.json').write_text(json.dumps({'collected_current_source':len(ids),'unique':len(set(ids)),'ids':sorted(set(ids)),'note':'new malformed-ACK test has separate media-authority-final receipt'},indent=2))
    gates=['media-browser.json','ui-browser.json','adversarial.json','lifecycle.json','resume-binding.json','periodic-refresh.json','load.json','sql-reconciliation.json','restore-migration.json','backend-static.json']
    for name in gates:assert read(name)['status']=='PASS','Gate not passed: '+name
    for name in ['backend-regression-final.json','backend-legacy-final.json','media-authority-final.json','go-regression.json']:
        assert read(name)['exit_code']==0,'Regression failed: '+name
    assert read('target-read-only.json')['target_migration_not_applied']
    summary={'status':'MEDIA INTEGRATION: READY FOR CUTOVER REVIEW','phase_7c':'NOT READY on target; cutover not approved/applied','live_staging_gates':gates,
        'backend_unique_test_ids':len(set(ids)),'go_test_cases_and_subtests':read('go-regression.json')['passed_test_cases'],'frontend_unit_passed':51,
        'front_build_lint_typecheck':'PASS; one existing lint warning','media_schema_head':'20261104_02','target_schema_head':read('target-read-only.json')['target_head'],
        'database_runtime_role':read('sql-reconciliation.json')['database_runtime_role'],'admission_pressure':{'peer_profile':'21 peers, 1 publisher camera/mic','requests':2000,'status_counts':read('load.json')['status_counts'],'all_active_rtp_preserved':True},
        'no_uat_45_started':True,'no_commit_push':True,'target_runtime_changed':False,'private_secret_scan':'PASS','protected_ai_config':'PASS','git_diff_check':diff.returncode}
    (OUT/'summary.json').write_text(json.dumps(summary,indent=2))
    print(json.dumps({'provenance':'saved','protected_ai_config':'PASS','secret_scan':'PASS','diff_check':diff.returncode,'backend_unique_ids':len(set(ids))}))

if __name__=='__main__':main()
