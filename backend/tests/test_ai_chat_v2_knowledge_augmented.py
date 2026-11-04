"""Behavior contracts with scripted answers, NOT GPT quality evaluations."""
import json
import unittest
from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import patch
from app.core.exceptions import AppError
from app.services.ai_chat_v2 import orchestrate,model_call
from app.services.ai_chat_v2_evidence import EvidenceLedger,digest
from app.services.ai_chat_v2_finalization import normalize_reply
from app.services.ai_observability import v2_natural_output

RECORDED_CONVERSATIONS=[]


class Tools:
    def __init__(self,text='Source-specific exam: reading single, double and triple texts.',empty=False):
        self.identity=str(uuid4());self.document=str(uuid4());self.text=text;self.empty=empty
        self.context={};self.calls=[];self.images=[];self.evidence=EvidenceLedger();self.revisions={}
        self.channel_context={'name':'Synthetic learning Channel','document_scope':'current_channel_only'}
    async def call(self,name,args):
        self.calls.append(name)
        if name=='list_available_documents':return {'documents':[] if self.empty else [
            {'document_id':self.document,'name':'Synthetic course fixture','revision':'rev'}]}
        if name=='begin_whole_document':
            self.evidence.scope({self.identity:{'document_id':self.document,'revision':'rev',
                'content_sha256':digest(self.text),'location':{'page':2}}})
            return {'inventory':self.evidence.inventory}
        if name=='register_child_obligations':
            self.evidence.register(args['children'],self.context)
            return {'registered_child_ids':list(self.evidence.obligations)}
        if name=='read_document_pages':
            item={'id':self.identity,'chunk_id':self.identity,'document_id':self.document,
                'text':self.text,'location':{'page':2},'revision':'rev'}
            self.context[self.identity]=item;self.evidence.observed(item)
            self.revisions[self.document]='rev'
            return {'passages':[item],'untrusted_evidence':True}
        raise AppError(403,'DENIED','Forbidden synthetic action')
    def checkpoint(self):return {'evidence':self.evidence.snapshot(),'revisions':self.revisions}


def footer(tools,body,**metadata):
    refs=[{'source_id':tools.identity,'quote':tools.text}]
    return body+'\n<source_refs>'+json.dumps({'task':'assisted','citations':refs,**metadata},ensure_ascii=False)+'</source_refs>'


def verdict(text,*,scope='focused',children=None,unsupported=False,official=False,violations=None):
    return {'source_claims':[{'text':text,'verdict':'unsupported' if unsupported else 'supported',
        'citation_indices':[] if unsupported else [0],'official':official}] if text else [],
        'all_document_claims_reviewed':True,'required_scope':scope,'scope_resolved':True,
        'source_task_complete':not unsupported,'children':children or [],'constraint_violations':violations or []}


class KnowledgeBehaviorTests(unittest.IsolatedAsyncioTestCase):
    async def sample(self,name,question,replies,tools=None,history=None,expected_intent='general'):
        tools=tools or Tools();seen=[];modes=[]
        async def scripted(messages):
            seen.append(messages);modes.append(v2_natural_output.get())
            reply=replies[len(seen)-1]
            return reply() if callable(reply) else reply
        result=await orchestrate(question,history or [],tools,scripted)
        RECORDED_CONVERSATIONS.append({'case':name,'evidence_level':'SCRIPTED SYNTHETIC; NOT LIVE GPT',
            'question':question,'expected_intent':expected_intent,'tool_decisions':tools.calls,
            'model_calls':len(seen),'natural_output_modes':modes,'answer':result['answer'],
            'status':result['status'],'citations':result['sources'],'reason_codes':result['reason_codes']})
        return result,tools,seen,modes

    async def test_general_in_document_channel_one_call_no_retrieval_no_quotes(self):
        answer='Câu điều kiện loại 2 diễn tả giả định khó xảy ra ở hiện tại.\n\nCấu trúc: **If + past simple, would + V**.\n\nVí dụ tự tạo:\n1. If I had more time, I would read more.\n2. If she lived nearby, we would meet often.\n3. If I were you, I would ask for help.'
        result,tools,seen,modes=await self.sample('general_with_unrelated_documents',
            'Giải thích câu điều kiện loại 2 và tự tạo 3 ví dụ',[answer])
        self.assertEqual(result['answer'],answer);self.assertEqual(result['status'],'answered')
        self.assertEqual(result['sources'],[]);self.assertEqual(tools.calls,['list_available_documents'])
        self.assertEqual(modes,[True]);self.assertEqual(len(seen),1)

    async def test_general_in_empty_channel_is_not_missing_source_failure(self):
        answer='Tìm kiếm nhị phân chia đôi phạm vi trên một dãy đã sắp xếp; độ phức tạp thời gian là O(log n).'
        result,_,_,_=await self.sample('empty_channel_general','Giải thích tìm kiếm nhị phân',[answer],Tools(empty=True))
        self.assertEqual(result['answer'],answer);self.assertEqual(result['status'],'answered')

    async def test_plain_markdown_code_is_preserved_without_claims_or_json(self):
        body='Cách làm:\n\n```python\nprint(2 ** 3)\n```\n\nKết quả là **8**.'
        result,_,_,_=await self.sample('free_markdown','Cho ví dụ Python ngắn',[body])
        self.assertEqual(result['answer'],body)

    async def test_grounded_document_fact_uses_raw_passage_and_exact_citation(self):
        tools=Tools();claim='Đề trong tài liệu có bài đọc đơn, đôi và ba.'
        result,_,seen,modes=await self.sample('grounded_reading_scope','Theo tài liệu, phần đọc có những dạng nào?',[
            {'tool':'read_document_pages','arguments':{'document_id':tools.document,'pages':[2]}},
            lambda:footer(tools,claim+' [1](#source-0)',task='grounded'),verdict(claim)],tools,expected_intent='grounded')
        self.assertEqual(result['status'],'answered');self.assertEqual(result['sources'][0]['quote'],tools.text)
        self.assertIn(tools.text,json.dumps(seen[1],ensure_ascii=False))
        self.assertEqual(modes,[True,True,False]);self.assertNotIn('<source_refs>',result['answer'])

    async def test_augmented_teaching_child_can_be_taught_with_background_knowledge(self):
        tools=Tools();fact='Tài liệu yêu cầu đối chiếu nhiều văn bản.'
        teaching='Cách làm: xác định người gửi, thời điểm và mục đích rồi nối thông tin giữa các văn bản. Ví dụ tự tạo: so lịch hẹn trong email với thông báo đổi giờ.'
        review=verdict(fact,scope='whole',children=[{'id':'cross-text','covered':True,'taught_in_answer':teaching}])
        review['required_document_ids']=[tools.document]
        result,_,_,_=await self.sample('augmented_curriculum','Dựa trên đề, dạy mình cách làm, thêm mẹo và ví dụ',[
            {'tool':'begin_whole_document','arguments':{'document_ids':[tools.document]}},
            {'tool':'read_document_pages','arguments':{'document_id':tools.document,'pages':[2]}},
            lambda:{'tool':'register_child_obligations','arguments':{'children':[{'id':'cross-text',
                'objective':'Reason across several texts','source_ids':[tools.identity]}]}},
            lambda:footer(tools,fact+' [1](#source-0)\n\n'+teaching,task='whole'),review],tools,expected_intent='augmented')
        self.assertEqual(result['status'],'answered');self.assertIn(teaching,result['answer'])
        self.assertNotIn(teaching,tools.text)
        self.assertEqual(result['checkpoint']['progress']['missing_child_ids'],[])

    async def test_followup_preserves_reference_and_no_answer_yet_constraint(self):
        history=[{'question':'Bài toán: 2x+1=5. Chỉ cho gợi ý, chưa nêu đáp án.',
            'answer':'Hãy thử trừ cùng một số ở hai vế.','sources':[],'status':'answered'}]
        body='Ví dụ tương tự: với 3y + 2 = 11, trước hết hãy nghĩ xem cần loại bỏ số hạng nào để cô lập y. Mình chưa tiết lộ kết quả.'
        result,_,seen,_=await self.sample('hints_followup','Cho ví dụ khác, vẫn đừng lộ đáp án',[body],history=history)
        self.assertEqual(json.loads(seen[0][1]['content'])['history'][0],history[0])
        self.assertEqual(result['answer'],body)

    async def test_why_b_is_reasoning_from_preserved_question_not_official_answer(self):
        history=[{'question':'Which value makes 2x=4? A:1, B:2. Mình chọn B.',
            'answer':'Ta có thể kiểm bằng phép thế.','sources':[],'status':'answered'}]
        body='Thay x = 2 vào 2x thì được 4, nên B thỏa điều kiện. Đây là suy luận từ bài bạn đưa, không phải xác nhận đáp án chính thức.'
        result,tools,_,_=await self.sample('why_b','Tại sao chọn B?',[body],history=history)
        self.assertEqual(result['answer'],body);self.assertEqual(tools.calls,['list_available_documents'])

    async def test_official_missing_source_claim_dropped_but_useful_teaching_kept(self):
        false_fact='Đáp án chính thức cô đăng là B.';helpful='Mình vẫn có thể hướng dẫn bạn kiểm tra từng lựa chọn bằng lập luận.'
        answer=false_fact+'\n\n'+helpful+'\n<source_refs>{"task":"grounded","missing_source":true}</source_refs>'
        result,_,_,_=await self.sample('missing_official_source','Đáp án chính thức cô đăng trong Channel là gì?',
            [answer,verdict(false_fact,unsupported=True,official=True)],Tools(empty=True),expected_intent='grounded_missing')
        self.assertEqual(result['status'],'partial');self.assertNotIn(false_fact,result['answer'])
        self.assertIn(helpful,result['answer']);self.assertEqual(result['sources'],[])

    async def test_source_disagreement_does_not_erase_correct_standard_explanation(self):
        tools=Tools('The handout states: zero is positive.');fact='Tài liệu ghi rằng số 0 là số dương.'
        standard='Theo định nghĩa toán học thông thường, 0 không dương cũng không âm; cần phân biệt điều tài liệu ghi với kiến thức chuẩn.'
        result,_,_,_=await self.sample('disagreement','Tài liệu này có đúng không?',[
            {'tool':'read_document_pages','arguments':{'document_id':tools.document,'pages':[2]}},
            lambda:footer(tools,fact+' [1](#source-0)\n\n'+standard),verdict(fact)],tools,expected_intent='augmented_disagreement')
        self.assertEqual(result['status'],'answered');self.assertIn(standard,result['answer'])

    async def test_document_context_does_not_make_general_explanation_source_restricted(self):
        tools=Tools('The course mentions recursion without any examples.')
        body='Đệ quy là khi một hàm giải bài toán bằng các trường hợp nhỏ hơn của chính nó. Ví dụ tự tạo: đếm các thư mục con, dừng khi không còn thư mục con.'
        result,_,_,_=await self.sample('context_only_general_explanation','Giải thích khái niệm này và tự tạo ví dụ',[
            {'tool':'read_document_pages','arguments':{'document_id':tools.document,'pages':[2]}},body,
            {'source_claims':[],'all_document_claims_reviewed':True,'required_scope':'general',
                'source_task_complete':False,'scope_resolved':False}],tools,expected_intent='document_context_general_knowledge')
        self.assertEqual(result['status'],'answered');self.assertEqual(result['answer'],body)
        self.assertEqual(result['sources'],[])

    async def test_source_only_user_constraint_is_checked_without_global_source_only_policy(self):
        tools=Tools();fact='Nguồn yêu cầu đọc nhiều văn bản.';extra='Ví dụ tự tạo ngoài phạm vi người dùng cho phép.'
        review=verdict(fact,violations=[{'text':extra,'reason':'User explicitly requested source only.'}])
        result,_,_,_=await self.sample('explicit_source_only','Chỉ dựa vào tài liệu, không thêm ví dụ',[
            {'tool':'read_document_pages','arguments':{'document_id':tools.document,'pages':[2]}},
            lambda:footer(tools,fact+' [1](#source-0)\n\n'+extra,source_only=True),review],tools,expected_intent='source_only')
        self.assertEqual(result['status'],'partial');self.assertIn(fact,result['answer']);self.assertNotIn(extra,result['answer'])

    async def test_invalid_citation_does_not_fail_entire_useful_answer(self):
        tools=Tools();fact='Khẳng định tài liệu chưa có bằng chứng.';general='Tuy vậy, ta có thể phân tích thuật toán bằng bất biến vòng lặp.'
        result,_,_,_=await self.sample('invalid_citation','Giải thích và đối chiếu nguồn',[
            {'tool':'read_document_pages','arguments':{'document_id':tools.document,'pages':[2]}},
            {'answer':fact+' [1](#source-0)\n\n'+general,'citations':[{'source_id':tools.identity,'quote':'FABRICATED QUOTE'}]},
            verdict(fact)],tools,expected_intent='augmented_invalid_reference')
        self.assertEqual(result['status'],'partial');self.assertIn(general,result['answer'])
        self.assertNotIn(fact,result['answer']);self.assertEqual(result['sources'],[])
        self.assertIn('CITATION_INVALID',result['reason_codes'])

    async def test_supplemental_label_cannot_bypass_review_after_document_access(self):
        tools=Tools();bad='Theo tài liệu, kết quả chính thức là 999.'
        result,_,seen,_=await self.sample('label_bypass','Đọc và giúp mình hiểu',[
            {'tool':'read_document_pages','arguments':{'document_id':tools.document,'pages':[2]}},
            {'answer':bad,'task':'general','claims':[{'id':'a','text':bad,'kind':'supplemental'}]},
            verdict(bad,unsupported=True,official=True)],tools,expected_intent='source_assertion_despite_label')
        self.assertEqual(len(seen),3);self.assertNotIn(bad,result['answer']);self.assertNotEqual(result['status'],'answered')

    async def test_auth_error_never_becomes_general_fallback_or_tool_text(self):
        tools=Tools()
        with self.assertRaises(AppError) as caught:
            await self.sample('auth_denied','Read other Workspace',[
                '<tool_call>{"tool":"private_cross_workspace","arguments":{}}</tool_call>'],tools)
        self.assertEqual(caught.exception.status_code,403)

    async def test_non_auth_tool_failure_allows_general_help_without_fake_source(self):
        tools=Tools()
        original=tools.call
        async def broken(name,args):
            if name=='search_documents':raise AppError(503,'E5_NOT_READY','Synthetic local fault')
            return await original(name,args)
        tools.call=broken
        body='Chưa truy xuất được nguồn. Mình có thể giải thích khái niệm chuẩn: đồ thị gồm đỉnh và cạnh.'
        result,_,_,_=await self.sample('tool_failure_general_help','Giải thích đồ thị và tìm tài liệu',[
            '<tool_call>{"tool":"search_documents","arguments":{"query":"graph"}}</tool_call>',body],tools)
        self.assertEqual(result['answer'],body);self.assertEqual(result['status'],'answered');self.assertEqual(result['sources'],[])

    async def test_useful_old_json_envelope_without_claims_is_not_failed(self):
        result,_,_,_=await self.sample('no_claims_compatibility','Nói ngắn gọn nguyên lý quy nạp',[
            {'answer':'Chứng minh bước cơ sở, rồi chứng minh từ n suy ra n+1.'}])
        self.assertEqual(result['status'],'answered');self.assertNotIn('claims',result['answer'])

    async def test_optional_metadata_format_error_does_not_fail_general_knowledge(self):
        body='Một số nguyên tố có đúng hai ước dương: 1 và chính nó.'
        result,_,seen,_=await self.sample('optional_metadata_error','Số nguyên tố là gì?',[
            body+'\n<source_refs>{invalid optional metadata}</source_refs>'])
        self.assertEqual(result['answer'],body);self.assertEqual(result['status'],'answered')
        self.assertEqual(len(seen),1)

    async def test_user_requested_json_and_literal_citation_code_are_not_rewritten(self):
        for body in ['{"answer":"8","example":"2 ** 3"}',
            'Ví dụ cú pháp:\n\n```markdown\n[1](#source-0)\n```\n\nĐây chỉ là ví dụ mã.',
            'Cú pháp inline là `[1](#source-0)`.']:
            with self.subTest(body=body):
                result,_,seen,_=await self.sample('requested_format','Cho đúng định dạng mình yêu cầu',[body])
                self.assertEqual(result['answer'],body);self.assertEqual(result['status'],'answered')
                self.assertEqual(len(seen),1)


class NaturalProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_sdk_text_serialization_and_legacy_json_default_without_network(self):
        import httpx2
        import openai
        from pydantic import SecretStr
        from app.core.config import get_settings
        from app.services.openai_provider import complete,close_clients
        payloads=[]
        def handler(request):
            body=json.loads(request.content);payloads.append(body)
            text='Ví dụ tự tạo: 2 + 3 = 5.' if body['text']['format']['type']=='text' else '{"answer":"legacy JSON"}'
            response={'id':'resp_fake','object':'response','created_at':0,'model':get_settings().openai_model,'status':'completed',
                'output':[{'type':'message','id':'msg_fake','status':'completed','role':'assistant','content':[
                    {'type':'output_text','text':text,'annotations':[]}]}],
                'usage':{'input_tokens':30,'output_tokens':10,'total_tokens':40}}
            return httpx2.Response(200,headers={'content-type':'text/event-stream'},content='data: '+json.dumps(
                {'type':'response.completed','response':response,'sequence_number':1})+'\n\n')
        settings=get_settings().model_copy(update={'openai_api_key':SecretStr('fake-not-a-real-key')})
        actual_factory=openai.AsyncOpenAI
        def factory(**kwargs):return actual_factory(**kwargs,http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)))
        try:
            with patch('app.services.openai_provider.get_settings',return_value=settings),patch('app.services.openai_provider.AsyncOpenAI',side_effect=factory):
                answer=await model_call(complete,[{'role':'user','content':'Explain'}],natural=True)
                self.assertEqual(answer,'Ví dụ tự tạo: 2 + 3 = 5.')
                self.assertEqual(await complete([{'role':'user','content':'Legacy'}]),{'answer':'legacy JSON'})
                import asyncio
                parallel=await asyncio.gather(model_call(complete,[{'role':'user','content':'V2 parallel'}],natural=True),
                    complete([{'role':'user','content':'Legacy parallel'}]))
                self.assertEqual(parallel,['Ví dụ tự tạo: 2 + 3 = 5.',{'answer':'legacy JSON'}])
            self.assertEqual([p['text']['format']['type'] for p in payloads[:2]],['text','json_object'])
            self.assertEqual({p['text']['format']['type'] for p in payloads[2:]},{'text','json_object'})
            self.assertTrue(all(p['store'] is False for p in payloads))
            self.assertTrue(all(p['model']==settings.openai_model for p in payloads))
        finally:await close_clients()
