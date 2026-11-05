"""Go package regression using the disposable, separate Docker CI engine."""
import json,subprocess
from pathlib import Path
REPO=Path(__file__).resolve().parents[2]
OUT=REPO/'evidence/phase-7c-media-integration'
outer=subprocess.check_output(['docker','info','--format','{{.ID}}'],text=True).strip()
inner=subprocess.check_output(['docker','exec','kltn-media-stage-dind','docker','info','--format','{{.ID}}'],text=True).strip()
assert inner and inner!=outer
args=['docker','run','--rm','--name','kltn-media-stage-go-final','--network','container:kltn-media-stage-dind','-e','DOCKER_HOST=tcp://127.0.0.1:2375','-e','CGO_ENABLED=0',
    '-v','D:/Temp/kltn-media-integration-20261104/livekit:/workspace','-v','kltn-sfu-proto-gomod:/go/pkg/mod','-v','kltn-sfu-proto-gocache:/root/.cache/go-build','-w','/workspace',
    'golang:1.24-alpine','go','test','-json','./pkg/rtc','./pkg/service','-count=1']
with (OUT/'go-packages.jsonl').open('wb') as log:
    p=subprocess.run(args,stdout=log,stderr=log)
tests=[];packages=[];failed=[]
for line in (OUT/'go-packages.jsonl').read_text(errors='replace').splitlines():
    try:item=json.loads(line)
    except ValueError:continue
    if item.get('Action')=='pass':
        (tests if item.get('Test') else packages).append(item.get('Test') or item['Package'])
    if item.get('Action')=='fail':failed.append(item.get('Test') or item['Package'])
receipt={'exit_code':p.returncode,'separate_ci_engine_verified':inner!=outer,'passed_test_cases':len(tests),'packages':packages,'failed':failed,'live_media_evidence':'separate Chrome/RTP traces; Go suite includes unit fixtures'}
(OUT/'go-regression.json').write_text(json.dumps(receipt,indent=2));print(json.dumps(receipt));raise SystemExit(p.returncode)
