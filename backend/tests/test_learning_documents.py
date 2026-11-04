"""UC29 contracts on real PostgreSQL; explicit fake-storage faults plus real MinIO."""
import io
import unittest
import zipfile
from uuid import uuid4
from unittest.mock import AsyncMock, patch
from sqlalchemy import select, func
from starlette.datastructures import UploadFile, Headers
from app.models.learning_document import LearningDocument, DocumentStatus
from app.services.learning_document_service import LearningDocumentService
from app.services.document_processing import mark_failed, process_pending
from app.services.storage_service import get_storage_service
from tests import test_chat_realtime as fixtures
from tests.test_channel_chat_hardening import HttpAssertions

setUpModule = fixtures.setUpModule
tearDownModule = fixtures.tearDownModule
PDF = b"%PDF-1.4\n1 0 obj << /Type /Catalog >> endobj\n%%EOF\n"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def docx():
    body = io.BytesIO()
    with zipfile.ZipFile(body, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        archive.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Learning example</w:t></w:r></w:p></w:body></w:document>')
    return body.getvalue()


def upload_file(body=PDF, name="learning.pdf", mime="application/pdf"):
    return UploadFile(io.BytesIO(body), filename=name, headers=Headers({"content-type": mime}))


class DocumentTests(HttpAssertions, unittest.IsolatedAsyncioTestCase):
    assert_app_error = fixtures.ChatRealtimeTests.assert_app_error
    asyncTearDown = fixtures.ChatRealtimeTests.asyncTearDown

    async def asyncSetUp(self):
        await fixtures.ChatRealtimeTests.asyncSetUp(self)
        self.storage = AsyncMock()
        self.service = LearningDocumentService(self.session, self.storage)

    async def count(self):
        async with self.session.begin():
            return await self.session.scalar(select(func.count()).select_from(LearningDocument).where(LearningDocument.channel_id==self.channel_id))

    async def test_main_upload_metadata_private_key_and_processing(self):
        result = await self.service.upload(self.sender, self.channel_id, upload_file(), "Custom", "Details")
        self.assertEqual((result.channel_id, result.uploader_id, result.name, result.description), (self.channel_id, self.sender.user.id, "Custom", "Details"))
        self.assertEqual(result.status, DocumentStatus.PROCESSING)
        self.assertNotIn("object_key", result.model_dump())
        self.storage.upload.assert_awaited_once()
        self.assertRegex(self.storage.upload.call_args.args[0], r"^learning-documents/[0-9a-f-]+/[0-9a-f]{32}\.pdf$")
        self.assertEqual(len(await self.service.list_documents(self.member, self.channel_id)), 1)

    async def test_a2_fallback_and_word(self):
        result = await self.service.upload(self.sender, self.channel_id, upload_file(docx(), "lesson.docx", DOCX_MIME), " ", " ")
        self.assertEqual(result.name, "lesson.docx")
        self.assertIsNone(result.description)

    async def test_authorization_missing_channel_and_isolation(self):
        for actor in (self.member, self.outsider):
            await self.assert_app_error("CHANNEL_PERMISSION_DENIED", self.service.upload(actor, self.channel_id, upload_file()))
        await self.assert_app_error("CHANNEL_NOT_FOUND", self.service.upload(self.sender, uuid4(), upload_file()))
        await self.service.upload(self.sender, self.channel_id, upload_file())
        self.assertEqual(await self.service.list_documents(self.sender, self.study_id), [])
        await self.assert_app_error("CHANNEL_PERMISSION_DENIED", self.service.list_documents(self.outsider, self.channel_id))

    async def test_admin_can_upload(self):
        from sqlalchemy import update
        from app.models import WorkspaceMember
        from app.models.enums import WorkspaceRole
        async with self.session.begin():
            await self.session.execute(update(WorkspaceMember).where(WorkspaceMember.user_id == self.member.user.id).values(role=WorkspaceRole.ADMIN))
        result = await self.service.upload(self.member, self.channel_id, upload_file())
        self.assertEqual(result.uploader_id, self.member.user.id)

    async def test_e1_format_mime_and_signature(self):
        for body, name, mime, code in [(b"x", "note.txt", "text/plain", "DOCUMENT_FORMAT_UNSUPPORTED"),
            (PDF, "x.pdf", "text/plain", "DOCUMENT_FORMAT_UNSUPPORTED"),
            (b"not pdf", "x.pdf", "application/pdf", "DOCUMENT_INVALID"),
            (b"PK-not-docx", "x.docx", DOCX_MIME, "DOCUMENT_INVALID")]:
            await self.assert_app_error(code, self.service.upload(self.sender, self.channel_id, upload_file(body, name, mime)))
        self.assertEqual(await self.count(), 0)
        self.storage.upload.assert_not_awaited()

    async def test_e2_oversize_empty_and_path_safety(self):
        with patch("app.core.learning_documents.MAX_DOCUMENT_BYTES", 10):
            await self.assert_app_error("DOCUMENT_TOO_LARGE", self.service.upload(self.sender, self.channel_id, upload_file()))
        await self.assert_app_error("DOCUMENT_INVALID", self.service.upload(self.sender, self.channel_id, upload_file(b"")))
        for name in ("../x.pdf", "C:\\x.pdf", "x\x00.pdf"):
            await self.assert_app_error("DOCUMENT_FILENAME_INVALID", self.service.upload(self.sender, self.channel_id, upload_file(name=name)))
        self.assertEqual(await self.count(), 0)

    async def test_docx_safety_limits(self):
        body = io.BytesIO()
        with zipfile.ZipFile(body, "w") as archive:
            archive.writestr("[Content_Types].xml", '<!DOCTYPE x [<!ENTITY x "boom">]><Types/>')
            archive.writestr("word/document.xml", "<document/>")
        await self.assert_app_error("DOCUMENT_INVALID", self.service.upload(self.sender, self.channel_id, upload_file(body.getvalue(), "x.docx", DOCX_MIME)))

    async def test_e3_storage_failure_no_record_compensation(self):
        self.storage.upload.side_effect = RuntimeError("storage fault")
        await self.assert_app_error("DOCUMENT_STORAGE_FAILED", self.service.upload(self.sender, self.channel_id, upload_file()))
        self.assertEqual(await self.count(), 0)
        self.storage.delete.assert_awaited_once()

    async def test_db_failure_deletes_uploaded_object(self):
        with patch.object(self.session, "flush", AsyncMock(side_effect=RuntimeError("DB fault"))):
            with self.assertRaises(RuntimeError):
                await self.service.upload(self.sender, self.channel_id, upload_file())
        self.storage.delete.assert_awaited_once()
        self.assertEqual(await self.count(), 0)

    async def test_retry_idempotency_and_conflicting_payload(self):
        identity = uuid4()
        first = await self.service.upload(self.sender, self.channel_id, upload_file(), upload_id=identity)
        second = await self.service.upload(self.sender, self.channel_id, upload_file(), upload_id=identity)
        self.assertEqual(first.id, second.id)
        self.storage.upload.assert_awaited_once()
        await self.assert_app_error("DOCUMENT_UPLOAD_CONFLICT", self.service.upload(self.sender, self.channel_id, upload_file(), name="different", upload_id=identity))
        self.assertEqual(await self.count(), 1)

    async def test_a3_durable_boundary_failed_dependency_and_idempotency(self):
        result = await self.service.upload(self.sender, self.channel_id, upload_file())
        self.assertEqual(result.status, DocumentStatus.PROCESSING)
        from app.services.document_pipeline import PipelineFailure
        async def unavailable(*args):
            raise PipelineFailure("RAG_NOT_CONFIGURED")
        self.assertEqual(await process_pending(self.session, processor=unavailable), 1)
        self.assertEqual(await process_pending(self.session), 0)
        current = (await self.service.list_documents(self.member, self.channel_id))[0]
        self.assertEqual((current.status, current.processing_error), (DocumentStatus.FAILED, "RAG_NOT_CONFIGURED"))
        self.storage.delete.assert_not_awaited()

    async def test_e4_e5_failure_contract_retains_original_no_ready(self):
        # Contract fault injection only: no claim of real extraction/vector processing.
        for code in ("EXTRACTION_FAILED", "VECTOR_PROCESSING_FAILED"):
            result = await self.service.upload(self.sender, self.channel_id, upload_file())
            async with self.session.begin():
                document = await self.session.get(LearningDocument, result.id)
                mark_failed(document, code)
                mark_failed(document, "RAG_NOT_CONFIGURED")
            current = next(d for d in await self.service.list_documents(self.member, self.channel_id) if d.id == result.id)
            self.assertEqual(current.status, DocumentStatus.FAILED)
            self.assertEqual(current.processing_error, code)
        self.storage.delete.assert_not_awaited()

    async def test_original_authorization_and_http_anonymous(self):
        result = await self.service.upload(self.sender, self.channel_id, upload_file())
        self.storage.read_document.return_value = PDF
        await self.service.original_url(self.member, result.id)
        self.storage.read_document.assert_awaited_once()
        await self.assert_app_error("CHANNEL_PERMISSION_DENIED", self.service.original_url(self.outsider, result.id))
        async with self.client() as client:
            self.assertEqual((await client.get(f"channels/{self.channel_id}/learning-documents")).status_code, 401)
            self.assertEqual((await client.post(f"channels/{self.channel_id}/learning-documents", files={"file": ("x.pdf", PDF, "application/pdf")})).status_code, 401)

    async def test_real_minio_original_preserved_on_failed_processing(self):
        storage = get_storage_service()
        result = await LearningDocumentService(self.session, storage).upload(self.sender, self.channel_id, upload_file())
        async with self.session.begin():
            document = await self.session.get(LearningDocument, result.id)
            key = document.object_key
        try:
            await process_pending(self.session)
            response = storage.client.get_object(Bucket=storage.bucket, Key=key)
            try:
                self.assertEqual(response["Body"].read(), PDF)
            finally:
                response["Body"].close()
        finally:
            await storage.delete(key)

    async def test_e3_real_minio_denies_invalid_credentials_no_record(self):
        import boto3
        from botocore.config import Config
        from app.core.config import get_settings
        from app.services.storage_service import StorageService
        real = get_storage_service()
        client = boto3.client("s3", endpoint_url=get_settings().object_storage_endpoint,
            aws_access_key_id="phase5a-deliberately-invalid", aws_secret_access_key="phase5a-invalid-secret",
            config=Config(signature_version="s3v4", retries={"max_attempts": 0}, connect_timeout=3, read_timeout=3), region_name="us-east-1")
        with self.assertLogs("app.services.learning_document_service", level="ERROR"):
            await self.assert_app_error("DOCUMENT_STORAGE_FAILED", LearningDocumentService(self.session, StorageService(client, real.bucket)).upload(self.sender, self.channel_id, upload_file()))
        self.assertEqual(await self.count(), 0)
