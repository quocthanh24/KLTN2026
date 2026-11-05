"""No SDK/network/database imported. Doubles test generation boundary, not model quality."""
import json
import unittest
from types import SimpleNamespace
from app.services.ai_chat_v2 import orchestrate, enabled, run
from app.core.exceptions import AppError


class Tools:
    def __init__(self, text='Original row | agree | | X; 公式 x²; 中文原文'):
        self.context = {}
        self.text = text
        self.calls = []
    async def call(self, name, args):
        self.calls.append((name,args))
        if name == 'list_available_documents': return {'documents':[{'document_id':'d'}]}
        self.context['c'] = {'id':'c','text':self.text,'location':{'page':24,'table_block':2},'revision':'sha256'}
        return {'passages':list(self.context.values())}


class V2Tests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_review_is_repaired_once_without_publishing_unchecked_text(self):
        from app.services.ai_chat_v2 import REVIEW
        tools=Tools('Verified original fixture')
        tools.settings=SimpleNamespace(ai_chat_billing_mode='metered',ai_chat_v2_source_repair_attempts=1,ai_turn_max_calls=12)
        drafts=0;reviews=0
        async def model(messages):
            nonlocal drafts,reviews
            if messages[0]['content']==REVIEW:
                reviews+=1
                return {'all_document_claims_reviewed':True,'source_claims':[{'block_id':'unknown' if reviews==1 else 'b0',
                    'kind':'document_fact','verdict':'supported','citation_indices':[0]}],
                    'source_task_complete':True,'scope_resolved':True,'required_scope':'focused'}
            drafts+=1
            if drafts==1:return {'tool':'read_document_pages','arguments':{'document_id':'d','pages':[1]}}
            return {'answer':'Verified description [1](#source-0)','task':'grounded',
                'citations':[{'source_id':'c','quote':'Verified original fixture'}]}
        result=await orchestrate('Describe the source',[],tools,model)
        self.assertEqual(result['status'],'answered');self.assertEqual(reviews,2)
        self.assertIn('Verified description',result['answer'])
        self.assertEqual(result['checkpoint']['review_errors'][0]['unknown_block_ids'],['unknown'])

    async def test_incomplete_range_continues_reading_and_consolidates_without_duplicates(self):
        from app.services.ai_chat_v2 import REVIEW
        tools=Tools('Source A and source B')
        tools.settings=SimpleNamespace(ai_chat_billing_mode='metered',ai_chat_v2_source_repair_attempts=1,ai_turn_max_calls=12)
        drafts=0;reviews=0;reads=[]
        original=tools.call
        async def call(name,args):
            reads.append(name)
            return await original(name,args)
        tools.call=call
        async def model(messages):
            nonlocal drafts,reviews
            if messages[0]['content']==REVIEW:
                reviews+=1
                body='Part A and Part B' if reviews==2 else 'Part A'
                return {'source_claims':[{'text':body,'kind':'document_fact','verdict':'supported','citation_indices':[0]}],
                    'all_document_claims_reviewed':True,'source_task_complete':reviews==2,'scope_resolved':True,'required_scope':'focused'}
            drafts+=1
            if drafts in {1,3}:return {'tool':'read_document_pages','arguments':{'document_id':'d','pages':[drafts]}}
            return {'answer':('Part A and Part B' if drafts==4 else 'Part A')+' [1](#source-0)',
                'citations':[{'source_id':'c','quote':'Source A'}],'task':'grounded'}
        result=await orchestrate('Summarize both sections',[],tools,model)
        self.assertEqual(result['status'],'answered')
        self.assertEqual(result['answer'].count('Part A'),1)
        self.assertIn('Part B',result['answer'])
        self.assertEqual(reads.count('read_document_pages'),2)

    async def exercise(self, text=None, quote=None, review=None, history=None):
        tools=Tools(text) if text is not None else Tools()
        seen=[]
        async def model(messages):
            seen.append(json.loads(json.dumps(messages)))
            if len(seen)==1: return {'tool':'read_document_pages','arguments':{'document_id':'d','pages':[24]}}
            if len(seen)==2: return {'answer':'Giải thích tự nhiên [1](#source-0)',
                'claims':[{'id':'a','text':'Giải thích tự nhiên [1](#source-0)','kind':'source'}],
                'citations':[{'claim_id':'a','source_id':'c','quote':quote or tools.text}],'partial':False}
            return review if review is not None else {'claims':[{'id':'a','verdict':'supported'}],
                'children':[],'inventory_complete':True,'answer_covers_question':True,'safety':True,'required_scope':'focused','scope_resolved':True}
        result=await orchestrate('Giải thích và thứ tự học',history or [],tools,model)
        return result,seen,tools

    async def test_f01_raw_and_locator_reach_generation_and_review(self):
        result,seen,tools=await self.exercise()
        packet=json.loads(seen[1][-1]['content'])['tool_result']['passages'][0]
        self.assertEqual(packet['text'],tools.text)
        self.assertEqual(packet['location'],{'page':24,'table_block':2})
        self.assertEqual(packet['revision'],'sha256')
        self.assertIn(tools.text,json.loads(seen[2][-1]['content'])['evidence'][0]['text'])
        self.assertEqual(result['status'],'answered')
        self.assertEqual(result['answer'].count('[1](#source-0)'),1)

    async def test_multisubject_boundary_preservation_only(self):
        for text in ['Algorithm T(n)=2T(n/2)+n','Debit | Credit | 120','DNA → RNA → protein','中文：先学习声调，再练习']:
            with self.subTest(text=text):
                _,seen,_=await self.exercise(text)
                self.assertEqual(json.loads(seen[1][-1]['content'])['tool_result']['passages'][0]['text'],text)

    async def test_forged_quote_rejected(self):
        result,_,_=await self.exercise(quote='invented')
        self.assertEqual(result['status'],'partial')
        self.assertEqual(result['sources'],[])
        self.assertNotIn('Giải thích tự nhiên',result['answer'])

    async def test_incomplete_review_preserves_answer_partial(self):
        result,_,_=await self.exercise(review={'claims':[{'id':'a','verdict':'supported'}],
            'answer_covers_question':False,'safety':True})
        self.assertEqual(result['status'],'partial');self.assertTrue(result['answer'])

    async def test_bad_entailment_not_published_as_answered(self):
        result,_,_=await self.exercise(review={'claims':[{'id':'a','verdict':'unsupported'}],
            'answer_covers_question':True,'safety':True})
        self.assertEqual(result['status'],'partial')
        self.assertNotIn('Giải thích tự nhiên',result['answer'])

    async def test_history_not_mechanically_truncated(self):
        history=[{'question':'Q'+str(i),'answer':'A'*2100,'sources':[]} for i in range(15)]
        _,seen,_=await self.exercise(history=history)
        self.assertEqual(json.loads(seen[0][1]['content'])['history'],list(reversed(history)))

    async def test_live_denied_before_model(self):
        async def forbidden(*args): self.fail('API called')
        with self.assertRaises(AppError) as cm:
            await run(None,None,None,None,'q',[],forbidden,SimpleNamespace(ai_chat_v2_live_approved=False))
        self.assertEqual(cm.exception.code,'AI_V2_LIVE_BLOCKED')

    def test_only_engine_for_every_channel(self):
        self.assertTrue(enabled(SimpleNamespace(ai_chat_v2_enabled=False,ai_chat_v2_channel_ids='c'),'c'))
        self.assertTrue(enabled(SimpleNamespace(ai_chat_v2_enabled=True,ai_chat_v2_channel_ids='c'),'other'))
        self.assertTrue(enabled(SimpleNamespace(ai_chat_v2_enabled=True,ai_chat_v2_channel_ids='c'),'c'))

    async def test_tool_loop_bounded(self):
        class Loop(Tools):
            async def call(self,*args): return {}
        count=0
        async def model(messages):
            nonlocal count
            count+=1
            return {'tool':'get_document_outline','arguments':{}}
        result=await orchestrate('q',[],Loop(),model)
        self.assertEqual(count,7);self.assertEqual(result['status'],'partial')

    async def test_oversize_history_rejected_before_model(self):
        async def model(messages): self.fail('called')
        with self.assertRaises(AppError):
            await orchestrate('q',[{'answer':'a'*90001}],Tools(),model)
