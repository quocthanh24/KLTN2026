"""Real RBAC/storage/checkpoint tests; scripted protocol checks are not live AI evidence."""
import unittest
from uuid import uuid4
from unittest.mock import AsyncMock,patch
from pydantic import ValidationError
from app.api.v1.ai_conversations import SourceSelection
from app.core.exceptions import AppError
from app.services.ai_source_status import source_failure_message
from app.services.ai_chat_v2_tool_contract import tool_schemas
from tests import test_ai_chat_v2_integration as existing
from tests import test_original_first_v2 as original
from tests.ai_chat_v2_fixture_support import native_pdf

setUpModule=existing.setUpModule
tearDownModule=existing.tearDownModule


class PageSelectionTests(existing.HttpAssertions,unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await existing.V2IntegrationTests.asyncSetUp(self)
        self.settings.ai_chat_v2_original_first=True

    asyncTearDown=existing.V2IntegrationTests.asyncTearDown
    raw_document=original.OriginalFirstIntegrationTests.raw_document
    tools=existing.V2IntegrationTests.tools
    document=existing.V2IntegrationTests.document
    provider=original.OriginalFirstIntegrationTests.provider

    async def test_explicit_source_scope_uses_original_while_processing(self):
        selected=await self.raw_document(native_pdf(('Source A, physical page 1.',)))
        other=await self.raw_document(native_pdf(('Source B, unrelated page.',)))
        tools=self.tools()
        tools.source_selection={'document_id':str(selected.id),'page_numbering':'physical'}
        catalog=await tools.call('list_available_documents',{})
        self.assertEqual([d['document_id'] for d in catalog['documents']],[str(selected.id)])
        self.assertEqual(catalog['documents'][0]['status'],'PROCESSING')
        packet=await tools.call('read_original_units',{'document_id':str(selected.id),'revision':selected.checksum,'units':[1]})
        self.assertEqual(packet['passages'][0]['location']['physical_page'],1)
        self.assertTrue(packet['_image_parts'])
        with self.assertRaises(AppError) as denied:
            await tools.call('read_original_units',{'document_id':str(other.id),'revision':other.checksum,'units':[1]})
        self.assertEqual(denied.exception.code,'AI_V2_SOURCE_SELECTION_MISMATCH')
        with self.assertRaises(AppError) as missing:
            await tools.call('read_original_units',{'document_id':str(selected.id),'revision':selected.checksum,'units':[6]})
        self.assertEqual(missing.exception.code,'AI_PAGE_NOT_FOUND')

    async def test_cross_channel_selected_document_fails_before_model(self):
        identity,_,_=await self.document(channel=self.study_id)
        with patch('app.services.ai_conversation_service.complete',new_callable=AsyncMock) as model:
            with self.assertRaises(AppError) as denied:
                await self.service.ask(self.sender,self.channel_id,self.cid,'Giảng trang 6',uuid4(),
                    source_selection={'document_id':str(identity),'page_numbering':'physical'})
            self.assertEqual(denied.exception.status_code,404)
            model.assert_not_awaited()

    async def test_source_controls_persist_and_idempotency_rejects_changed_selection(self):
        doc=await self.raw_document(native_pdf(('A source.',)))
        selection={'document_id':str(doc.id),'page_numbering':'unspecified'}
        from app.services.ai_chat_v2 import REVIEW
        async def model(messages):
            if messages[0]['content']==REVIEW:
                self.assertTrue(any('CURRENT REQUEST:' in str(m.get('content')) for m in messages))
                return {'source_claims':[],'all_document_claims_reviewed':True,'required_scope':'conversation','scope_resolved':True,'source_task_complete':True}
            self.assertTrue(any('CURRENT REQUEST:' in str(m.get('content')) for m in messages))
            return 'Bạn muốn dùng số trang in hay vị trí trang PDF?'
        self.provider(model)
        request=uuid4()
        first=await self.service.ask(self.sender,self.channel_id,self.cid,'Giảng trang 6',request,source_selection=selection)
        again=await self.service.ask(self.sender,self.channel_id,self.cid,'Giảng trang 6',request,source_selection=selection)
        self.assertEqual(first['id'],again['id'])
        self.assertEqual(again['v2_task']['source_selection'],selection)
        with self.assertRaises(AppError) as conflict:
            await self.service.ask(self.sender,self.channel_id,self.cid,'Giảng trang 6',request,
                source_selection={**selection,'page_numbering':'physical'})
        self.assertEqual(conflict.exception.code,'AI_REQUEST_CONFLICT')


class SourceStatusTests(unittest.TestCase):
    def test_page_convention_and_document_id_validate(self):
        for value in ({'document_id':'filename.pdf'},{'document_id':str(uuid4()),'page_numbering':'guess'}):
            with self.assertRaises(ValidationError):SourceSelection.model_validate(value)

    def test_real_causes_are_distinct(self):
        messages=[source_failure_message({'tool_errors':[code]}) for code in (
            'AI_PAGE_NOT_FOUND','DOCUMENT_STORAGE_FAILED','AI_V2_ORIGINAL_CAPABILITY','MODEL_TIMEOUT','REVIEW_PROTOCOL_ERROR')]
        self.assertEqual(len(set(messages)),5)
        self.assertIn('gián đoạn',source_failure_message({'terminal_error_code':'AI_PROVIDER_INCOMPLETE'}))
        self.assertNotIn('trang bạn yêu cầu',source_failure_message({'terminal_error_code':'AI_PROVIDER_INCOMPLETE'}))

    def test_current_catalog_constrains_native_tool_handles(self):
        identity=str(uuid4());revision='a'*64
        schema=next(s for s in tool_schemas({'documents':[{'document_id':identity,'revision':revision}]}) if s['name']=='read_original_units')
        self.assertEqual(schema['parameters']['properties']['document_id']['enum'],[identity])
        self.assertEqual(schema['parameters']['properties']['revision']['enum'],[revision])
