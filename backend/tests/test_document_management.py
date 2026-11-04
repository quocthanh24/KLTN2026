"""UC30–32 on PostgreSQL, private MinIO and real RAG; scoped rollback fixtures."""
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4
from sqlalchemy import select, update, delete
from app.core.exceptions import AppError
from app.models import WorkspaceMember
from app.models.enums import WorkspaceRole
from app.models.learning_document import LearningDocument, DocumentStatus
from app.schemas.learning_document import DocumentUpdate
from app.services.document_cleanup import cleanup_deleted
from app.services.document_processing import pipeline_process, process_pending
from app.services.document_retrieval import DocumentRetrieval
from app.services.learning_document_service import LearningDocumentService
from tests import test_chat_realtime as fixtures
from tests.test_channel_chat_hardening import HttpAssertions
from tests import test_document_rag as rag
from tests.test_learning_documents import upload_file

setUpModule = fixtures.setUpModule
tearDownModule = fixtures.tearDownModule
pdf_bytes, word_bytes = rag.pdf_bytes, rag.word_bytes


class ManagementTests(HttpAssertions, unittest.IsolatedAsyncioTestCase):
    asyncSetUp = rag.RagTests.asyncSetUp
    asyncTearDown = rag.RagTests.asyncTearDown
    upload = rag.RagTests.upload
    chunks = rag.RagTests.chunks
    assert_app_error = fixtures.ChatRealtimeTests.assert_app_error

    async def test_uc30_uc31_real_preview_download_metadata_and_isolation(self):
        data = pdf_bytes()
        uploaded = await self.upload(data)
        async with self.client() as client:
            path = f'learning-documents/{uploaded.id}'
            detail = await client.get(path, headers=self.headers(self.member))
            self.assertEqual(detail.status_code, 200)
            self.assertTrue(detail.json()['data']['uploader_name'])
            self.assertFalse(detail.json()['data']['can_manage'])
            for action in ('preview', 'download'):
                response = await client.get(f'{path}/{action}?channel_id={self.channel_id}', headers=self.headers(self.member))
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.content, data)
                self.assertIn('no-store', response.headers['cache-control'])
                self.assertIn('inline' if action == 'preview' else 'attachment', response.headers['content-disposition'])
                self.assertEqual(response.headers['content-type'], 'application/pdf')
                self.assertEqual((await client.get(f'{path}/{action}')).status_code, 401)
                self.assertEqual((await client.get(f'{path}/{action}', headers=self.headers(self.outsider))).status_code, 403)
                self.assertEqual((await client.get(f'{path}/{action}?channel_id={self.study_id}', headers=self.headers(self.member))).status_code, 404)
            self.assertEqual(await self.service.list_documents(self.member, self.study_id), [])

    async def test_uc30_docx_fallback_and_failed_processing_original(self):
        uploaded = await self.upload(word_bytes(), True)
        async with self.client() as client:
            self.error(await client.get(f'learning-documents/{uploaded.id}/preview', headers=self.headers(self.member)), 415, 'DOCUMENT_PREVIEW_UNSUPPORTED')
            response = await client.get(f'learning-documents/{uploaded.id}/download', headers=self.headers(self.member))
            self.assertEqual(response.status_code, 200)
            self.assertGreater(len(response.content), 0)
        blank = await self.upload(pdf_bytes(blank=True))
        await process_pending(self.session, self.storage)  # First DOCX succeeds.
        await process_pending(self.session, self.storage)  # Blank PDF fails.
        self.assertEqual((await self.service.detail(self.member, blank.id)).status, DocumentStatus.FAILED)
        self.assertEqual((await self.service.content(self.member, blank.id, preview=True))[0], pdf_bytes(blank=True))

    async def test_uc30_e3_and_uc31_e2_missing_object_storage_failure(self):
        uploaded = await self.upload(pdf_bytes())
        await self.storage.delete(self.keys[0])
        async with self.client() as client:
            for action in ('download', 'preview'):
                self.error(await client.get(f'learning-documents/{uploaded.id}/{action}', headers=self.headers(self.member)), 404, 'DOCUMENT_FILE_UNAVAILABLE')
        fake = SimpleNamespace(read_document=AsyncMock(side_effect=RuntimeError('storage unavailable')))
        await self.assert_app_error('DOCUMENT_STORAGE_FAILED', LearningDocumentService(self.session, fake).content(self.member, uploaded.id))

    async def test_uc32_metadata_edit_preserves_original_vectors_and_validation(self):
        uploaded = await self.upload(word_bytes(), True)
        await process_pending(self.session, self.storage)
        before = await self.chunks(uploaded.id)
        async with self.client() as client:
            path = f'learning-documents/{uploaded.id}'
            response = await client.patch(path, headers=self.headers(self.sender), json={'name': '  Tên mới <script>  ', 'description': '  Mô tả mới  '})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['data']['name'], 'Tên mới <script>')
            self.assertEqual(response.json()['data']['status'], 'READY')
            self.assertEqual((await client.patch(path, headers=self.headers(self.sender), json={'name': '   '})).status_code, 422)
            self.assertEqual((await client.patch(path, headers=self.headers(self.member), json={'name': 'Denied'})).status_code, 403)
            self.assertEqual((await client.delete(path, headers=self.headers(self.member))).status_code, 403)
        after = await self.chunks(uploaded.id)
        self.assertEqual([r.id for r in before], [r.id for r in after])
        self.assertEqual(await self.storage.read_document(self.keys[0], 25 * 1024 * 1024), (await self.service.content(self.member, uploaded.id))[0])

    async def test_uc32_delete_ready_atomic_revocation_and_retry_cleanup(self):
        uploaded = await self.upload(word_bytes(), True)
        await process_pending(self.session, self.storage)
        vector = (await self.chunks(uploaded.id))[0].embedding
        old_link = await self.service.original_url(self.member, uploaded.id)
        failing_storage = SimpleNamespace(delete=AsyncMock(side_effect=RuntimeError('MinIO delete failure')))
        await LearningDocumentService(self.session, failing_storage).delete_document(self.sender, uploaded.id)
        self.assertEqual(await self.service.list_documents(self.member, self.channel_id), [])
        self.assertEqual(await self.chunks(uploaded.id), [])
        self.assertEqual(await DocumentRetrieval(self.session).search(self.member, self.channel_id, vector), [])
        self.assertGreater(len(await self.storage.read_document(self.keys[0], 25 * 1024 * 1024)), 0)
        async with self.client() as client:
            self.assertEqual((await client.get(old_link.removeprefix('/api/v1/'), headers=self.headers(self.member))).status_code, 404)
        self.assertTrue(await cleanup_deleted(self.session, self.storage, uploaded.id))
        self.assertFalse(await cleanup_deleted(self.session, self.storage, uploaded.id))
        await self.service.delete_document(self.sender, uploaded.id)  # Repeated deletion is safe.
        await self.assert_app_error('DOCUMENT_NOT_FOUND', self.service.detail(self.member, uploaded.id))
        await self.assert_app_error('DOCUMENT_NOT_FOUND', self.service.update_document(self.sender, uploaded.id, DocumentUpdate(name='Stale')))
        await self.assert_app_error('DOCUMENT_NOT_FOUND', self.service.upload(self.sender, self.channel_id, upload_file(word_bytes(), 'lesson.docx', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'), upload_id=uploaded.id))

    async def test_worker_delete_race_cannot_publish_ready_or_vectors(self):
        uploaded = await self.upload(word_bytes(), True)
        async def delete_before_publish(*args):
            chunks = await pipeline_process(*args)
            await self.service.delete_document(self.sender, uploaded.id)
            return chunks
        await process_pending(self.session, self.storage, delete_before_publish)
        self.assertEqual(await self.chunks(uploaded.id), [])
        async with self.session.begin():
            document = await self.session.get(LearningDocument, uploaded.id)
            self.assertIsNotNone(document.deleted_at)
            self.assertIsNone(document.processing_token)
            self.assertNotEqual(document.status, DocumentStatus.READY)
        self.assertEqual(await process_pending(self.session, self.storage), 0)

    async def test_revocation_and_admin_management(self):
        uploaded = await self.upload(pdf_bytes())
        async with self.session.begin():
            await self.session.execute(update(WorkspaceMember).where(WorkspaceMember.user_id == self.sender.user.id).values(role=WorkspaceRole.MEMBER))
        await self.assert_app_error('CHANNEL_PERMISSION_DENIED', self.service.update_document(self.sender, uploaded.id, DocumentUpdate(name='No')))
        await self.assert_app_error('CHANNEL_PERMISSION_DENIED', self.service.delete_document(self.sender, uploaded.id))
        async with self.session.begin():
            await self.session.execute(update(WorkspaceMember).where(WorkspaceMember.user_id == self.member.user.id).values(role=WorkspaceRole.ADMIN))
        await self.service.update_document(self.member, uploaded.id, DocumentUpdate(name='Admin'))
        async with self.session.begin():
            await self.session.execute(delete(WorkspaceMember).where(WorkspaceMember.user_id == self.sender.user.id))
        await self.assert_app_error('CHANNEL_PERMISSION_DENIED', self.service.content(self.sender, uploaded.id))
        await self.service.delete_document(self.member, uploaded.id)

    async def test_deletion_db_failure_does_not_delete_original(self):
        uploaded = await self.upload(pdf_bytes())
        with patch.object(self.session, 'execute', AsyncMock(side_effect=RuntimeError('DB delete fault'))):
            with self.assertRaises(RuntimeError):
                await self.service.delete_document(self.sender, uploaded.id)
        self.assertEqual((await self.service.content(self.member, uploaded.id))[0], pdf_bytes())
        self.assertIsNone((await self.service.detail(self.member, uploaded.id)).processing_error)
