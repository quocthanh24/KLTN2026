"""Kill/restart only our own test worker; scoped real DB/MinIO/ONNX fixtures."""
import asyncio
from datetime import UTC, datetime, timedelta
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from uuid import uuid4
from dotenv import dotenv_values

BACKEND = Path(__file__).resolve().parents[1]
if "--worker" not in sys.argv:
    os.environ["DATABASE_URL"] = dotenv_values(BACKEND / ".env")["TEST_DATABASE_URL"]

from sqlalchemy import delete, select
from app.core.config import get_settings
from app.db.session import engine, session_factory
from app.models import AuthSession, Channel, User, Workspace, WorkspaceMember
from app.models.enums import ChannelType, WorkspaceRole
from app.models.learning_document import DocumentChunk, LearningDocument, DocumentStatus
from app.services.auth_service import Principal
from app.services.document_processing import pipeline_process, process_pending
from app.services.learning_document_service import LearningDocumentService
from app.services.storage_service import get_storage_service
from tests.test_document_rag import word_bytes
from tests.test_learning_documents import DOCX_MIME, upload_file


async def worker():
    async def hold(*args):
        chunks = await pipeline_process(*args)
        Path(sys.argv[-1]).write_text("vectors computed, not published", encoding="utf-8")
        if "--delete-race" in sys.argv:
            while not Path(sys.argv[-1]).with_suffix('.release').exists():
                await asyncio.sleep(0.1)
        else:
            await asyncio.sleep(300)
        return chunks
    async with session_factory() as db:
        await process_pending(db, processor=hold if "--hold" in sys.argv else None)


async def main():
    assert engine.url.database == "kltn_test"
    storage = get_storage_service()
    marker = uuid4()
    user_id, workspace_id, channel_id, session_id = [uuid4() for _ in range(4)]
    key = None
    children = []
    try:
        async with session_factory() as db:
            async with db.begin():
                db.add_all([User(id=user_id, full_name="RAG restart fixture", email=f"{marker}@example.com", password_hash="unused"), Workspace(id=workspace_id, name=f"RAG restart {marker}")])
                await db.flush()
                db.add_all([Channel(id=channel_id, workspace_id=workspace_id, name="rag-restart", type=ChannelType.TEXT),
                    WorkspaceMember(workspace_id=workspace_id, user_id=user_id, role=WorkspaceRole.OWNER),
                    AuthSession(id=session_id, user_id=user_id, refresh_token_hash=uuid4().hex + uuid4().hex, expires_at=datetime.now(UTC) + timedelta(hours=1))])
            principal = Principal(SimpleNamespace(id=user_id), SimpleNamespace(id=session_id))
            result = await LearningDocumentService(db, storage).upload(principal, channel_id, upload_file(word_bytes(), "restart.docx", DOCX_MIME))
            async with db.begin():
                document = await db.get(LearningDocument, result.id)
                key = document.object_key
        with TemporaryDirectory(prefix="kltn-rag-restart-") as folder:
            signal = Path(folder) / "ready"
            flags = ['--delete-race'] if '--delete-race' in sys.argv else []
            child = await asyncio.create_subprocess_exec(sys.executable, "-m", "scripts.verify_rag_restart", "--worker", "--hold", *flags, str(signal), stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            children.append(child)
            for _ in range(300):
                if signal.exists():
                    break
                if child.returncode is not None:
                    raise RuntimeError("Worker exited before checkpoint")
                await asyncio.sleep(0.1)
            assert signal.exists(), "Checkpoint timeout"
            if '--delete-race' in sys.argv:
                async with session_factory() as db:
                    await LearningDocumentService(db, storage).delete_document(principal, result.id)
                signal.with_suffix('.release').write_text('resume obsolete publication', encoding='utf-8')
                assert await asyncio.wait_for(child.wait(), 30) == 0
                async with session_factory() as db:
                    async with db.begin():
                        document = await db.get(LearningDocument, result.id)
                        chunks = list(await db.scalars(select(DocumentChunk).where(DocumentChunk.document_id == result.id)))
                        assert document.deleted_at and document.object_deleted_at and not chunks
                        assert document.status != DocumentStatus.READY and document.processing_token is None
                    try:
                        await LearningDocumentService(db, storage).content(principal, result.id)
                        raise AssertionError('Deleted original accessible')
                    except Exception as error:
                        assert getattr(error, 'code', None) == 'DOCUMENT_NOT_FOUND'
                print('PASS: independent worker computes real ONNX vectors; concurrent delete commits; obsolete worker cannot publish READY/vectors or restore access')
                return
            child.kill()
            await child.wait()
            async with session_factory() as db:
                async with db.begin():
                    document = await db.get(LearningDocument, result.id)
                    assert document.status == DocumentStatus.PROCESSING and document.processing_token
                    assert not list(await db.scalars(select(DocumentChunk).where(DocumentChunk.document_id == result.id)))
                    # Simulate elapsed wall-clock lease, without a 180s blocking sleep.
                    document.processing_lease_until = datetime.now(UTC) - timedelta(seconds=1)
            for _ in range(2):
                resumed = await asyncio.create_subprocess_exec(sys.executable, "-m", "scripts.verify_rag_restart", "--worker", stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
                children.append(resumed)
                assert await asyncio.wait_for(resumed.wait(), 120) == 0
                async with session_factory() as db:
                    document = await db.get(LearningDocument, result.id)
                    chunks = list(await db.scalars(select(DocumentChunk).where(DocumentChunk.document_id == result.id)))
                    assert document.status == DocumentStatus.READY and len(chunks) == 2
                    assert all(len(chunk.embedding) == 384 for chunk in chunks)
            print("PASS: real worker killed after ONNX, no partial vectors; expired lease recovered by new process; READY and 2 unique chunks survive second restart")
    finally:
        for child in children:
            if child.returncode is None:
                child.kill()
                await child.wait()
        if key:
            await storage.delete(key)
        async with session_factory() as db:
            async with db.begin():
                await db.execute(delete(Channel).where(Channel.id == channel_id, Channel.workspace_id == workspace_id))
                await db.execute(delete(WorkspaceMember).where(WorkspaceMember.workspace_id == workspace_id))
                await db.execute(delete(Workspace).where(Workspace.id == workspace_id))
                await db.execute(delete(AuthSession).where(AuthSession.id == session_id, AuthSession.user_id == user_id))
                await db.execute(delete(User).where(User.id == user_id, User.email == f"{marker}@example.com"))
        await engine.dispose()
        print("PASS: exact UUID restart fixture cleanup; existing data preserved")


if __name__ == "__main__":
    try:
        asyncio.run(worker() if "--worker" in sys.argv else main())
    except Exception as error:
        print(type(error).__name__)
        sys.exit(1)
