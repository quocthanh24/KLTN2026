"""Reuse document-worker runtime/isolation for non-model PDF metadata."""
import asyncio
import json
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from app.core.exceptions import AppError
from app.services.document_processing import pipeline_runtime,attach_windows_job,close_windows_job,wait_pipeline


async def private_pdf_inventory(data):
    with TemporaryDirectory(prefix='kltn-v2-inventory-') as folder:
        source,output=Path(folder)/'source.pdf',Path(folder)/'inventory.json'
        source.write_bytes(data)
        interpreter,cwd=pipeline_runtime()
        process=await asyncio.to_thread(subprocess.Popen,[interpreter,'-m','app.services.ai_chat_v2_pdf',str(source),str(output)],
            cwd=cwd,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        job=None
        try:
            job=attach_windows_job(process.pid)
            await asyncio.wait_for(wait_pipeline(process),15)
            if process.returncode or not output.is_file() or output.stat().st_size>20000:
                raise AppError(503,'AI_V2_PDF_INVENTORY','Không thể xác minh phạm vi trang của PDF gốc.')
            return json.loads(output.read_text(encoding='utf8'))
        finally:
            if process.poll() is None:
                process.kill();await asyncio.to_thread(process.wait)
            close_windows_job(job)
