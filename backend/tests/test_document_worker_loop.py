"""Regression for Windows Uvicorn reload's Selector loop; real ONNX pipeline."""
import json
from pathlib import Path
import subprocess
import sys
import unittest


@unittest.skipUnless(sys.platform=='win32','Windows Selector regression')
class WorkerLoopTests(unittest.TestCase):
    def test_real_pipeline_under_selector_loop(self):
        code='''
import asyncio,hashlib,json
from tests.test_document_rag import pdf_bytes
from app.services.document_processing import pipeline_process
asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
data=pdf_bytes()
class StoredFixture:
 async def read_document(self,key,limit):return data
async def main():
 chunks=await pipeline_process('fixture','text.pdf',hashlib.sha256(data).hexdigest(),StoredFixture())
 print(json.dumps({'chunks':len(chunks),'dimensions':len(chunks[0]['embedding'])}))
asyncio.run(main())
'''
        result=subprocess.run([sys.executable,'-c',code],cwd=Path(__file__).resolve().parents[1],capture_output=True,text=True,timeout=120)
        self.assertEqual(result.returncode,0,result.stderr)
        output=json.loads(result.stdout);self.assertGreater(output['chunks'],0);self.assertEqual(output['dimensions'],384)
