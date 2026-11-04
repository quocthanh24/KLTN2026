"""Actual PostgreSQL/MinIO/auth, no paid API calls."""
import unittest
from uuid import uuid4
from sqlalchemy import select
from app.models.learning_document import LearningDocument, DocumentStatus
from app.services.document_processing import process_pending
from tests import test_chat_realtime as fixtures
from tests.test_channel_chat_hardening import HttpAssertions
from tests import test_document_rag as rag

setUpModule=fixtures.setUpModule
tearDownModule=fixtures.tearDownModule


class MultimodalAccessTests(HttpAssertions,unittest.IsolatedAsyncioTestCase):
    asyncSetUp=rag.RagTests.asyncSetUp
    asyncTearDown=rag.RagTests.asyncTearDown
    upload=rag.RagTests.upload
    chunks=rag.RagTests.chunks

    async def test_retry_ownership_channel_and_idempotency(self):
        uploaded=await self.upload(rag.pdf_bytes(blank=True))
        await process_pending(self.session,self.storage)
        async with self.client() as client:
            path=f'learning-documents/{uploaded.id}/retry'
            for principal in (self.member,self.outsider):
                r=await client.post(path,headers=self.headers(principal))
                self.assertEqual(r.status_code,403)
            self.assertEqual((await client.post(path)).status_code,401)
            self.assertEqual((await client.post(path+f'?channel_id={self.study_id}',headers=self.headers(self.sender))).status_code,404)
            first=await client.post(path,headers=self.headers(self.sender))
            self.assertEqual(first.status_code,200)
            self.assertEqual(first.json()['data']['status'],'PROCESSING')
            second=await client.post(path,headers=self.headers(self.sender))
            self.assertEqual(second.status_code,200)
            self.assertEqual(second.json()['data']['id'],str(uploaded.id))
        self.assertEqual(await self.chunks(uploaded.id),[])

    async def test_private_page_image_actual_render_and_access(self):
        uploaded=await self.upload(rag.pdf_bytes())
        async with self.client() as client:
            path=f'learning-documents/{uploaded.id}/pages/1/image'
            r=await client.get(path,headers=self.headers(self.member))
            self.assertEqual(r.status_code,200)
            self.assertTrue(r.content.startswith(b'\xff\xd8'))
            self.assertIn('no-store',r.headers['cache-control'])
            self.assertEqual((await client.get(path)).status_code,401)
            self.assertEqual((await client.get(path,headers=self.headers(self.outsider))).status_code,403)
            self.assertEqual((await client.get(path+f'?channel_id={self.study_id}',headers=self.headers(self.member))).status_code,404)

    async def test_failed_page_no_partial_vectors_and_retry_publish_once(self):
        from app.core.document_errors import PipelineFailure
        uploaded=await self.upload(rag.pdf_bytes())
        async def fail(*args):
            error=PipelineFailure('DOCUMENT_VISION_QUOTA');error.page_number=4;raise error
        await process_pending(self.session,self.storage,processor=fail,document_id=uploaded.id)
        detail=await self.service.detail(self.member,uploaded.id)
        self.assertEqual(detail.processing_error,'DOCUMENT_VISION_QUOTA:page=4')
        self.assertEqual(await self.chunks(uploaded.id),[])
        await self.service.retry_processing(self.sender,uploaded.id)
        await process_pending(self.session,self.storage,document_id=uploaded.id)
        before=await self.chunks(uploaded.id)
        self.assertTrue(before)
        self.assertEqual(await process_pending(self.session,self.storage,document_id=uploaded.id),0)
        self.assertEqual([c.id for c in before],[c.id for c in await self.chunks(uploaded.id)])
