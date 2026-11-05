"""Real PostgreSQL/MinIO/ONNX/parser tests, including real vector-store failure."""
import asyncio
from datetime import UTC, datetime, timedelta
import io
import unittest
from uuid import uuid4
from unittest.mock import patch
from sqlalchemy import func, select, text
from app.models.learning_document import DocumentChunk, LearningDocument, DocumentStatus
from app.services.document_processing import process_pending, pipeline_process
from app.services.document_retrieval import DocumentRetrieval
from app.services.learning_document_service import LearningDocumentService
from app.services.storage_service import get_storage_service
from tests import test_chat_realtime as fixtures
from tests.test_learning_documents import upload_file, DOCX_MIME

setUpModule = fixtures.setUpModule
tearDownModule = fixtures.tearDownModule


def word_bytes():
    from docx import Document
    d = Document()
    d.add_paragraph("PostgreSQL lưu trữ dữ liệu quan hệ. MinIO lưu tài liệu gốc riêng tư.")
    d.add_table(rows=1, cols=1).cell(0, 0).text = "Tài liệu học tập thuộc đúng kênh."
    output = io.BytesIO()
    d.save(output)
    return output.getvalue()


def pdf_bytes(blank=False):
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
    writer = PdfWriter()
    page = writer.add_blank_page(400, 400)
    if not blank:
        font = DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')})
        page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): writer._add_object(font)})})
        stream = DecodedStreamObject()
        stream.set_data(b'BT /F1 12 Tf 30 300 Td (PostgreSQL stores relational data. MinIO stores private originals.) Tj ET')
        page[NameObject('/Contents')] = writer._add_object(stream)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


class RagTests(unittest.IsolatedAsyncioTestCase):
    async def test_worker_uses_project_runtime_outside_backend_directory(self):
        import os
        from pathlib import Path
        from app.services.document_processing import pipeline_runtime
        uploaded=await self.upload(pdf_bytes())
        previous=os.getcwd()
        try:
            os.chdir(Path(__file__).resolve().parents[2])
            interpreter,cwd=pipeline_runtime()
            self.assertTrue(Path(interpreter).is_file())
            self.assertEqual(Path(cwd),Path(__file__).resolve().parents[1])
            self.assertEqual(await process_pending(self.session,self.storage),1)
        finally:
            os.chdir(previous)
        self.assertEqual((await self.service.detail(self.member,uploaded.id)).status,DocumentStatus.READY)
        self.assertTrue(await self.chunks(uploaded.id))

    async def asyncSetUp(self):
        await fixtures.ChatRealtimeTests.asyncSetUp(self)
        self.storage = get_storage_service()
        self.service = LearningDocumentService(self.session, self.storage)
        self.keys = []

    async def asyncTearDown(self):
        for key in self.keys:
            await self.storage.delete(key)
        await fixtures.ChatRealtimeTests.asyncTearDown(self)

    async def upload(self, content, word=False):
        result = await self.service.upload(self.sender, self.channel_id,
            upload_file(content, 'lesson.docx' if word else 'lesson.pdf', DOCX_MIME if word else 'application/pdf'))
        async with self.session.begin():
            document = await self.session.get(LearningDocument, result.id)
            self.keys.append(document.object_key)
        return result

    async def chunks(self, identity):
        async with self.session.begin():
            return list(await self.session.scalars(select(DocumentChunk).where(DocumentChunk.document_id == identity)))

    async def test_real_pdf_docx_ready_source_vectors_and_channel_auth(self):
        for word, content in ((False, pdf_bytes()), (True, word_bytes())):
            uploaded = await self.upload(content, word)
            self.assertEqual(uploaded.status, DocumentStatus.PROCESSING)
            self.assertEqual(await process_pending(self.session, self.storage), 1)
            rows = await self.chunks(uploaded.id)
            self.assertGreater(len(rows), 0)
            self.assertEqual(len(rows[0].embedding), 384)
            self.assertIn('paragraph' if word else 'page', rows[0].source_location)
            self.assertEqual((await self.service.list_documents(self.member, self.channel_id))[0].status, DocumentStatus.READY)
            retrieval = DocumentRetrieval(self.session)
            self.assertGreater(len(await retrieval.search(self.member, self.channel_id, rows[0].embedding)), 0)
            self.assertEqual(await retrieval.search(self.member, self.study_id, rows[0].embedding), [])
            with self.assertRaises(Exception) as error:
                await retrieval.search(self.outsider, self.channel_id, rows[0].embedding)
            self.assertEqual(error.exception.code, 'CHANNEL_PERMISSION_DENIED')
            self.assertEqual(await process_pending(self.session, self.storage), 0)

    async def test_e4_real_blank_pdf_keeps_original_excluded(self):
        uploaded = await self.upload(pdf_bytes(blank=True))
        await process_pending(self.session, self.storage)
        document = (await self.service.list_documents(self.member, self.channel_id))[0]
        self.assertEqual((document.status, document.processing_error), (DocumentStatus.FAILED, 'PDF_NO_TEXT'))
        self.assertEqual(await self.chunks(uploaded.id), [])
        self.assertEqual(await self.storage.read_document(self.keys[0], 25 * 1024 * 1024), pdf_bytes(blank=True))

    async def test_e5_actual_pgvector_constraint_rollback_no_partial_artifacts(self):
        uploaded = await self.upload(word_bytes(), True)
        # Transaction-scoped test-only DB fault: second real chunk cannot be inserted.
        async with self.session.begin():
            await self.session.execute(text(
                f"ALTER TABLE document_chunks ADD CONSTRAINT ck_rag_test_store CHECK (document_id <> '{uploaded.id}' OR ordinal <> 1)"))
        await process_pending(self.session, self.storage)
        document = (await self.service.list_documents(self.member, self.channel_id))[0]
        self.assertEqual((document.status, document.processing_error), (DocumentStatus.FAILED, 'VECTOR_PROCESSING_FAILED'))
        self.assertEqual(await self.chunks(uploaded.id), [])
        self.assertGreater(len(await self.storage.read_document(self.keys[0], 25 * 1024 * 1024)), 0)
        self.assertEqual(await DocumentRetrieval(self.session).search(self.member, self.channel_id, [0.1] * 384), [])

    async def test_restart_expired_lease_recovered_and_live_lease_skipped(self):
        uploaded = await self.upload(word_bytes(), True)
        async with self.session.begin():
            document = await self.session.get(LearningDocument, uploaded.id)
            document.processing_token = uuid4()
            document.processing_lease_until = datetime.now(UTC) + timedelta(seconds=30)
        self.assertEqual(await process_pending(self.session, self.storage), 0)
        async with self.session.begin():
            document.processing_lease_until = datetime.now(UTC) - timedelta(seconds=1)
        self.assertEqual(await process_pending(self.session, self.storage), 1)
        self.assertEqual((await self.service.list_documents(self.member, self.channel_id))[0].status, DocumentStatus.READY)
        self.assertEqual(await process_pending(self.session, self.storage), 0)

    async def test_e5_real_missing_model_failure_keeps_original(self):
        from app.core.config import get_settings
        settings = get_settings().model_copy(update={"rag_model_dir": "D:/Temp/kltn-model-does-not-exist"})
        uploaded = await self.upload(word_bytes(), True)
        with patch('app.services.document_processing.get_settings', return_value=settings):
            await process_pending(self.session, self.storage)
        current = (await self.service.list_documents(self.member, self.channel_id))[0]
        self.assertEqual((current.status, current.processing_error), (DocumentStatus.FAILED, 'VECTOR_PROCESSING_FAILED'))
        self.assertEqual(await self.chunks(uploaded.id), [])
        self.assertGreater(len(await self.storage.read_document(self.keys[0], 25 * 1024 * 1024)), 0)

    async def test_lease_fencing_prevents_obsolete_worker_publication(self):
        uploaded = await self.upload(word_bytes(), True)
        async def superseded(*args):
            chunks = await pipeline_process(*args)
            async with self.session.begin():
                document = await self.session.get(LearningDocument, uploaded.id)
                document.processing_token = uuid4()
            return chunks
        await process_pending(self.session, self.storage, superseded)
        self.assertEqual(await self.chunks(uploaded.id), [])
        self.assertEqual((await self.service.list_documents(self.member, self.channel_id))[0].status, DocumentStatus.PROCESSING)

    async def test_long_vietnamese_chunk_offsets_overlap_and_ready_only_gate(self):
        from docx import Document
        d = Document()
        source = "Cơ sở dữ liệu PostgreSQL lưu trữ tài liệu học tập theo kênh. " * 100
        d.add_paragraph(source)
        body = io.BytesIO()
        d.save(body)
        uploaded = await self.upload(body.getvalue(), True)
        await process_pending(self.session, self.storage)
        rows = sorted(await self.chunks(uploaded.id), key=lambda row: row.ordinal)
        self.assertGreater(len(rows), 2)
        for row in rows:
            position = row.source_location
            self.assertEqual(row.content, source[position['char_start']:position['char_end']])
        self.assertLess(rows[1].source_location['char_start'], rows[0].source_location['char_end'])
        async with self.session.begin():
            document = await self.session.get(LearningDocument, uploaded.id)
            document.status = DocumentStatus.PROCESSING
        self.assertEqual(await DocumentRetrieval(self.session).search(self.member, self.channel_id, rows[0].embedding), [])
