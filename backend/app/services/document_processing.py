"""Sequential, restart-safe UC29 worker with atomic publication and lease fencing."""
import asyncio
from datetime import UTC, datetime, timedelta
import json
import logging
from pathlib import Path
import sys
import subprocess
from tempfile import TemporaryDirectory
from uuid import uuid4
from sqlalchemy import delete, or_, select
from app.core.config import get_settings
from app.core.learning_documents import MAX_DOCUMENT_BYTES
from app.db.session import session_factory
from app.models.learning_document import DocumentChunk, LearningDocument, DocumentStatus
from app.services.document_pipeline import PipelineFailure
from app.services.storage_service import get_storage_service

FAILURE_CODES = {"RAG_NOT_CONFIGURED", "EXTRACTION_FAILED", "VECTOR_PROCESSING_FAILED", "PDF_NO_TEXT", "DOCUMENT_NO_TEXT"}
FAILURE_CODES.update({'DOCUMENT_' + suffix for suffix in (
    'PAGE_LIMIT', 'RENDER_LIMIT', 'RENDER_FAILED', 'PAGE_UNREADABLE',
    'VISION_DISABLED', 'VISION_KEY_MISSING', 'VISION_KEY_INVALID', 'VISION_MODEL_UNSUPPORTED',
    'VISION_CALL_CAP', 'VISION_QUOTA', 'VISION_RATE_LIMIT', 'VISION_TIMEOUT',
    'VISION_INCOMPLETE', 'VISION_INVALID', 'VISION_FAILED')})
FAILURE_CODES.add('DOCX_IMAGES_UNSUPPORTED')
FAILURE_CODES.add('DOCX_LAYOUT_UNSUPPORTED')
FAILURE_CODES.add('DOCUMENT_PASSWORD_PROTECTED')


def processing_timeout():
    settings = get_settings()
    return max(settings.rag_processing_timeout_seconds,
               settings.document_vision_max_calls * settings.openai_timeout_seconds + 120)


async def render_private_page(data: bytes, number: int|str) -> bytes:
    """Render untrusted PDF in the same bounded child isolation as indexing."""
    with TemporaryDirectory(prefix='kltn-page-') as folder:
        source, output = Path(folder) / 'source.pdf', Path(folder) / 'page.jpg'
        source.write_bytes(data)
        interpreter, cwd = pipeline_runtime()
        process = await asyncio.to_thread(subprocess.Popen,
            [interpreter, '-m', 'app.services.document_vision', str(source), str(output), str(number)],
            cwd=cwd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        job = None
        try:
            job = attach_windows_job(process.pid)
            await asyncio.wait_for(wait_pipeline(process), 30)
            if process.returncode or not output.is_file() or output.stat().st_size > 8 * 1024 * 1024:
                raise PipelineFailure('DOCUMENT_RENDER_FAILED')
            return output.read_bytes()
        finally:
            if process.poll() is None:
                process.kill()
                await asyncio.to_thread(process.wait)
            close_windows_job(job)


def pipeline_runtime():
    """Use the project environment even when Uvicorn was launched globally."""
    backend = Path(__file__).resolve().parents[2]
    environment = backend / ('.venv-win/Scripts/python.exe' if sys.platform == 'win32' else '.venv/bin/python')
    return str(environment) if environment.is_file() else sys.executable, str(backend)


def attach_windows_job(pid):
    """Kill our child on parent exit and bound private commit to 1.5 GiB."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes
    class Basic(ctypes.Structure):
        _fields_ = [("process_time", ctypes.c_longlong), ("job_time", ctypes.c_longlong),
            ("flags", wintypes.DWORD), ("min_ws", ctypes.c_size_t), ("max_ws", ctypes.c_size_t),
            ("active", wintypes.DWORD), ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD), ("scheduling", wintypes.DWORD)]
    class IO(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in ("read_ops", "write_ops", "other_ops", "read_bytes", "write_bytes", "other_bytes")]
    class Extended(ctypes.Structure):
        _fields_ = [("basic", Basic), ("io", IO), ("process_memory", ctypes.c_size_t),
            ("job_memory", ctypes.c_size_t), ("peak_process", ctypes.c_size_t), ("peak_job", ctypes.c_size_t)]
    kernel = ctypes.windll.kernel32
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    job = kernel.CreateJobObjectW(None, None)
    limits = Extended()
    limits.basic.flags = 0x2000 | 0x100  # KILL_ON_JOB_CLOSE | PROCESS_MEMORY
    limits.process_memory = 1536 * 1024 * 1024
    handle = kernel.OpenProcess(0x0100 | 0x0001, False, pid)  # SET_QUOTA | TERMINATE
    try:
        if not job or not handle or not kernel.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)) or not kernel.AssignProcessToJobObject(job, handle):
            if job:
                kernel.CloseHandle(job)
            raise PipelineFailure("VECTOR_PROCESSING_FAILED")
        return job
    finally:
        if handle:
            kernel.CloseHandle(handle)


def close_windows_job(job):
    if job:
        import ctypes
        from ctypes import wintypes
        ctypes.windll.kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        ctypes.windll.kernel32.CloseHandle(job)


def child_commit_bytes(pid):
    """Windows private commit, inspected only for the subprocess we created."""
    if sys.platform != "win32":
        return 0
    import ctypes
    from ctypes import wintypes
    class Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("faults", wintypes.DWORD)] + [
            (name, ctypes.c_size_t) for name in ("peak_ws", "ws", "peak_paged", "paged", "peak_nonpaged", "nonpaged", "commit", "peak_commit", "private")]
    kernel, psapi = ctypes.windll.kernel32, ctypes.windll.psapi
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        return 0
    try:
        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        return counters.private if psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb) else 0
    finally:
        kernel.CloseHandle(handle)


async def wait_pipeline(process):
    while process.poll() is None:
        if child_commit_bytes(process.pid) > 1536 * 1024 * 1024:
            raise PipelineFailure("VECTOR_PROCESSING_FAILED")
        await asyncio.sleep(0.1)
    return process.returncode


def mark_failed(document, code):
    if code.split(':')[0] not in FAILURE_CODES:
        raise ValueError("Unknown processing failure")
    if document.status != DocumentStatus.PROCESSING:
        return
    document.status = DocumentStatus.FAILED
    document.processing_error = code
    document.processed_at = datetime.now(UTC)
    document.processing_token = document.processing_lease_until = None


async def pipeline_process(key, filename, checksum, storage):
    settings = get_settings()
    if not settings.rag_model_dir:
        raise PipelineFailure("RAG_NOT_CONFIGURED")
    try:
        data = await storage.read_document(key, MAX_DOCUMENT_BYTES)
    except Exception:
        raise PipelineFailure("EXTRACTION_FAILED") from None
    with TemporaryDirectory(prefix="kltn-rag-") as folder:
        source, output = Path(folder) / "original", Path(folder) / "result.json"
        source.write_bytes(data)
        interpreter, working_directory = pipeline_runtime()
        # Uvicorn --reload uses a Windows Selector loop, which cannot create
        # asyncio subprocesses. Popen in a thread works with both loop types.
        process = await asyncio.to_thread(subprocess.Popen,
            [interpreter, "-m", "app.services.document_pipeline", str(source), str(output),
             Path(filename).suffix.lower(), settings.rag_model_dir, checksum],
            cwd=working_directory, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        async def drain_errors():
            captured = bytearray()
            while block := await asyncio.to_thread(process.stderr.read, 1024):
                if len(captured) < 4096:
                    captured.extend(block[:4096-len(captured)])
            return bytes(captured)
        diagnostics = asyncio.create_task(drain_errors())
        job = None
        try:
            job = attach_windows_job(process.pid)
            await asyncio.wait_for(wait_pipeline(process), processing_timeout())
        except BaseException:
            if process.returncode is None:
                process.kill()
                await asyncio.to_thread(process.wait)
            raise
        finally:
            close_windows_job(job)
            stderr = await diagnostics
            process.stderr.close()
        if process.returncode != 0 or not output.is_file() or output.stat().st_size > 40 * 1024 * 1024:
            category = next((name for name in ('ModuleNotFoundError','ImportError','MemoryError','RuntimeError','OSError') if name.encode() in stderr), 'unknown')
            logging.getLogger(__name__).error('RAG subprocess failed: exit=%s output_present=%s category=%s project_environment=%s', process.returncode, output.is_file(), category, interpreter != sys.executable)
            raise PipelineFailure("VECTOR_PROCESSING_FAILED")
        result = json.loads(output.read_text(encoding="utf-8"))
        if "error" in result:
            logging.getLogger(__name__).warning('RAG stage failed: code=%s exception_type=%s', result['error'] if result['error'] in FAILURE_CODES else 'unknown', result.get('exception_type', 'unknown'))
            error = PipelineFailure(result['error'])
            error.page_number = result.get('page_number')
            raise error
        return result["chunks"]


async def process_pending(db, storage=None, processor=None, *, document_id=None, retry_failed=False):
    if retry_failed and document_id is None:
        raise ValueError('Retry requires an explicit document identity')
    if get_settings().document_progressive_enabled and processor is None:
        from app.services.document_progressive import process_progressive
        return await process_progressive(db,storage,document_id,retry_failed)
    settings = get_settings()
    token = uuid4()
    now = datetime.now(UTC)
    async with db.begin():
        document = await db.scalar(select(LearningDocument).where(
            LearningDocument.status == (DocumentStatus.FAILED if retry_failed else DocumentStatus.PROCESSING),
            LearningDocument.deleted_at.is_(None),
            LearningDocument.id == document_id if document_id is not None else True,
            or_(LearningDocument.processing_lease_until.is_(None), LearningDocument.processing_lease_until < now))
            .order_by(LearningDocument.created_at, LearningDocument.id).with_for_update(skip_locked=True).limit(1))
        if document is None:
            return 0
        document.status = DocumentStatus.PROCESSING
        document.processing_error = None
        document.processing_token = token
        document.processing_lease_until = now + timedelta(seconds=processing_timeout() + 60)
        identity, key, filename, checksum = document.id, document.object_key, document.original_filename, document.checksum
    failure = None
    try:
        chunks = await (processor or pipeline_process)(key, filename, checksum, storage or get_storage_service())
        async with db.begin():
            current = await db.scalar(select(LearningDocument).where(LearningDocument.id == identity).with_for_update().execution_options(populate_existing=True))
            if current is None or current.deleted_at is not None or current.status != DocumentStatus.PROCESSING or current.processing_token != token:
                return 1
            await db.execute(delete(DocumentChunk).where(DocumentChunk.document_id == identity))
            for ordinal, chunk in enumerate(chunks):
                db.add(DocumentChunk(document_id=identity, ordinal=ordinal, **chunk))
            await db.flush()
            if not chunks:
                raise PipelineFailure("VECTOR_PROCESSING_FAILED")
            current.status = DocumentStatus.READY
            current.processing_error = None
            current.processed_at = datetime.now(UTC)
            current.processing_token = current.processing_lease_until = None
        return 1
    except asyncio.CancelledError:
        raise
    except PipelineFailure as error:
        failure = str(error) if str(error) in FAILURE_CODES else "VECTOR_PROCESSING_FAILED"
        page = getattr(error, 'page_number', None)
        if type(page) is int and 1 <= page <= 300:
            failure += f':page={page}'
    except TimeoutError:
        failure = 'DOCUMENT_VISION_TIMEOUT'
    except Exception as error:
        logging.getLogger(__name__).error('Document pipeline failed: exception_type=%s', type(error).__name__)
        failure = "VECTOR_PROCESSING_FAILED"
    async with db.begin():
        current = await db.scalar(select(LearningDocument).where(LearningDocument.id == identity).with_for_update().execution_options(populate_existing=True))
        if current and current.deleted_at is None and current.status == DocumentStatus.PROCESSING and current.processing_token == token:
            await db.execute(delete(DocumentChunk).where(DocumentChunk.document_id == identity))
            mark_failed(current, failure)
            logging.getLogger(__name__).warning('Document processing failed: document_id=%s code=%s', identity, failure)
    return 1


async def processing_worker():
    while True:
        try:
            async with session_factory() as db:
                processed = await process_pending(db)
                from app.services.document_cleanup import cleanup_deleted
                await cleanup_deleted(db)
            if not processed:
                await asyncio.sleep(2)
        except asyncio.CancelledError:
            raise
        except Exception:
            logging.getLogger(__name__).error("Document worker unavailable; pending rows retained")
            await asyncio.sleep(2)
