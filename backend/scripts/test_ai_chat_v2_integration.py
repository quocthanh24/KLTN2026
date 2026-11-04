"""Dedicated real local integration suite. Non-loopback network is forbidden."""
import ipaddress
import os
import socket
import sys
import unittest
from pathlib import Path
from contextlib import redirect_stdout,redirect_stderr

# Prior ingestion regression explicitly retains the legacy opt-out.
os.environ.setdefault('DOCUMENT_PROGRESSIVE_ENABLED','false')
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'backend'))
os.environ['OPENAI_API_KEY']=''
os.environ['GEMINI_API_KEY']=''
os.environ['DOCUMENT_VISION_ENABLED']='false'
original_dns=socket.getaddrinfo
def local_dns(host,*args,**kwargs):
    if host not in {'localhost','127.0.0.1','::1',None}:
        raise AssertionError('External network disabled for V2 local tests')
    return original_dns(host,*args,**kwargs)
socket.getaddrinfo=local_dns
original_connect=socket.socket.connect
original_connect_ex=socket.socket.connect_ex
def local_connect(sock,address):
    if isinstance(address,tuple) and not ipaddress.ip_address(address[0]).is_loopback:
        raise AssertionError('External network disabled for V2 local tests')
    return original_connect(sock,address)
def local_connect_ex(sock,address):
    if isinstance(address,tuple) and not ipaddress.ip_address(address[0]).is_loopback:
        raise AssertionError('External network disabled for V2 local tests')
    return original_connect_ex(sock,address)
socket.socket.connect=local_connect
socket.socket.connect_ex=local_connect_ex
modules=['tests.test_ai_chat_v2_integration','tests.test_original_first_v2.OriginalFirstIntegrationTests','tests.test_storage_download_filename','tests.test_learning_documents','tests.test_document_management','tests.test_document_worker_loop']
if '--original-only' in sys.argv:
    modules=['tests.test_original_first_v2.OriginalFirstIntegrationTests']
if '--foundation' in sys.argv:
    # Historical channel_backend includes a destructive migration round trip
    # requiring an empty DB; run it separately, never against retained fixtures.
    modules=['tests.test_channel_chat_hardening',
        'tests.test_study_room','tests.test_pomodoro','tests.test_workspace_invitations']
path=Path(os.environ.get('AI_CHAT_V2_EVIDENCE_DIR',str(ROOT/'evidence/ai-chat-v2-completion-20261030')))/'integration-regression.txt'
if '--original-only' in sys.argv:path=path.with_name('original-integration-regression.txt')
if '--foundation' in sys.argv:path=path.with_name('foundation-integration-regression.txt')
path.parent.mkdir(parents=True,exist_ok=True)
with path.open('w',encoding='utf8') as log,redirect_stdout(log),redirect_stderr(log):
    result=unittest.TextTestRunner(stream=log,verbosity=2).run(unittest.defaultTestLoader.loadTestsFromNames(modules))
import json
from tests.test_original_first_v2 import PROOFS
(path.parent/'original-provider-boundaries.json').write_text(json.dumps(PROOFS,ensure_ascii=False,indent=2),encoding='utf8')
print(f'Ran {result.testsRun}; failures={len(result.failures)}; errors={len(result.errors)}; external network disabled; {path}')
sys.exit(0 if result.wasSuccessful() else 1)
