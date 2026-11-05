"""Offline suite with a socket kill switch. Never imports live test modules."""
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
if '--testdeps' in sys.argv:
    sys.path.insert(0,sys.argv[sys.argv.index('--testdeps')+1])
def deny_network(*args,**kwargs):
    raise AssertionError('NETWORK DISABLED: offline suite')
original_connect=socket.socket.connect
original_socketpair=socket.socketpair
creating_socketpair=False
def safe_socketpair(*args,**kwargs):
    global creating_socketpair
    creating_socketpair=True
    try: return original_socketpair(*args,**kwargs)
    finally: creating_socketpair=False
def guarded_connect(sock,address):
    if creating_socketpair and isinstance(address,tuple) and address[0] in {'127.0.0.1','::1'}:
        return original_connect(sock,address)
    return deny_network()
socket.socketpair=safe_socketpair
socket.socket.connect=guarded_connect
socket.socket.connect_ex=deny_network
socket.create_connection=deny_network
modules=['tests.test_original_first_v2.OriginalSDKTransportTests','tests.test_original_first_v2.OriginalAdapterOfflineTests','tests.test_ai_chat_v2_offline','tests.test_ai_chat_v2_structure','tests.test_ai_chat_v2_tools',
    'tests.test_ai_chat_v2_budget','tests.test_ai_chat_v2_evidence',
    'tests.test_ai_chat_v2_knowledge_augmented',
    'tests.test_ai_turn_budget',
    'tests.test_openai_provider','tests.test_original_image_delivery','tests.test_four_corners_pilot_preflight']
path=Path(os.environ.get('AI_CHAT_V2_EVIDENCE_DIR',str(ROOT/'evidence/ai-chat-v2-completion-20261030')))/'offline-regression.txt'
path.parent.mkdir(parents=True,exist_ok=True)
with path.open('w',encoding='utf8') as log,redirect_stdout(log),redirect_stderr(log):
    suite=unittest.defaultTestLoader.loadTestsFromNames(modules)
    result=unittest.TextTestRunner(stream=log,verbosity=2).run(suite)
print(f'Ran {result.testsRun}; failures={len(result.failures)}; errors={len(result.errors)}; network disabled; {path}')
import json
from tests.test_ai_chat_v2_knowledge_augmented import RECORDED_CONVERSATIONS
from tests.test_original_first_v2 import SDK_PROOF
(path.parent/'original-sdk-payload-proof.json').write_text(json.dumps(SDK_PROOF,indent=2),encoding='utf8')
(path.parent/'synthetic-conversations.json').write_text(json.dumps(RECORDED_CONVERSATIONS,ensure_ascii=False,indent=2),encoding='utf8')
sys.exit(0 if result.wasSuccessful() else 1)
