import json
import unittest
from types import SimpleNamespace
from app.core.exceptions import AppError
from app.services.ai_chat_v2 import orchestrate,prepare_history
from app.services.ai_chat_v2_finalization import normalize_reply,technical_citations,source_review
from app.services.ai_chat_v2_evidence import EvidenceLedger,digest


class EvidenceTests(unittest.TestCase):
    def test_pdf_content_stream_dictionary_truthiness_is_not_a_blank_page_test(self):
        from tests.ai_chat_v2_fixture_support import native_pdf
        from app.services.ai_chat_v2_pdf import physical_inventory
        result=physical_inventory(native_pdf(['Real content in an empty-dictionary stream.']))
        self.assertEqual(result['physical_pages'],[1]);self.assertEqual(result['proven_blank_pages'],[])
    def passage(self,text='row | agree | disagree\nvenue | | X'):
        return {'id':'c','chunk_id':'c','document_id':'d','revision':'rev',
            'text':text,'location':{'page':24,'table_block':1,'row':2}}

    def test_inventory_counts_not_semantic_coverage(self):
        p=self.passage();ledger=EvidenceLedger()
        ledger.scope({'c':{'content_sha256':digest(p['text']),'location':p['location'],'revision':'rev'}})
        ledger.observed(p)
        self.assertEqual(ledger.progress()['unread_source_ids'],[])
        self.assertEqual(ledger.progress()['unanalyzed_source_ids'],['c'])
        ledger.register([{'id':'grid-columns','objective':'Interpret row/column including blank cells','source_ids':['c']}],{'c':p})
        self.assertEqual(ledger.progress()['missing_child_ids'],['grid-columns'])
        self.assertEqual(ledger.progress(['grid-columns'])['missing_child_ids'],[])
        self.assertEqual(ledger.progress()['semantic_completeness'],'not_proven_by_inventory_counts')

    def test_changed_table_column_or_revision_rejected(self):
        p=self.passage();ledger=EvidenceLedger()
        ledger.scope({'c':{'content_sha256':digest(p['text']),'location':p['location'],'revision':'rev'}})
        for other in [{**p,'text':'row | disagree | agree\nvenue | X |'}, {**p,'revision':'new'}]:
            with self.assertRaises(AppError):ledger.observed(other)

    def test_forged_child_reference_rejected(self):
        with self.assertRaises(AppError):
            EvidenceLedger().register([{'id':'a','objective':'skill','source_ids':['private-other']}],{})

    def test_general_prose_does_not_require_exhaustive_claim_spans(self):
        result=normalize_reply({'answer':'General explanation. Example.',
            'claims':[{'id':'a','text':'Example.','kind':'supplemental'}]})
        self.assertEqual(result['answer'],'General explanation. Example.')
        self.assertEqual(technical_citations(result,{})[2],[])


class ClaimTests(unittest.IsolatedAsyncioTestCase):
    async def test_verified_history_summary_keeps_recent_full_turns_and_exact_old_questions(self):
        history=[{'question':f'Question {i}: why choose B?','answer':'x'*5000,'sources':[],'status':'answered'} for i in range(20)]
        calls=[]
        async def provider(messages):
            calls.append(messages)
            return {'summary':'Keep original unresolved choices A/B; no answers revealed.'} if len(calls)==1 else {'faithful':True,'referents_preserved':True}
        state={}
        compact=await prepare_history(history,provider,state)
        self.assertEqual(compact[:12],history[:12])
        for old in history[12:]:self.assertIn(old['question'],compact[-1]['question'])
        self.assertTrue(state['history_summary']['verified'])
        await prepare_history(history,provider,state)
        self.assertEqual(len(calls),2) # verified digest cache; no redundant model calls

    async def test_unfaithful_summary_rejected_without_mutating_original_history(self):
        history=[{'question':str(i),'answer':'x'*5000,'sources':[]} for i in range(20)]
        count=0
        async def provider(messages):
            nonlocal count
            count+=1
            return {'summary':'lost choices'} if count==1 else {'faithful':True,'referents_preserved':False}
        state={}
        with self.assertRaises(AppError):await prepare_history(history,provider,state)
        self.assertNotIn('history_summary',state);self.assertEqual(len(history),20)

    async def test_duplicate_or_missing_review_claims_fail_closed(self):
        reply=normalize_reply({'answer':'Source fact.','claims':[{'id':'a','text':'Source fact.','kind':'source'}]})
        for verdict in [{'claims':[{'id':'a','verdict':'supported'},{'id':'a','verdict':'unsupported'}]}, {'claims':[]}]:
            self.assertTrue(source_review(verdict,reply) is None or source_review(verdict,reply)['source_claims'][0]['verdict']=='uncertain')
    async def test_keep_supported_source_and_supplemental_example_drop_wrong_claim(self):
        p=EvidenceTests().passage('Source fact.')
        class Tools:
            context={'c':p}
            async def call(self,*args):return {}
        calls=[]
        candidate={'answer':'Source fact.\n\nMy own example.\n\nWrong fact.',
            'claims':[{'id':'a','text':'Source fact.','kind':'source'},
                {'id':'b','text':'My own example.','kind':'supplemental'},
                {'id':'c','text':'Wrong fact.','kind':'source'}],
            'citations':[{'claim_id':'a','source_id':'c','quote':'Source fact.'},
                {'claim_id':'c','source_id':'c','quote':'Source fact.'}]}
        async def model(messages):
            calls.append(messages)
            if len(calls)==1:return candidate
            if len(calls)==2:return {'claims':[{'id':'a','verdict':'supported'},{'id':'b','verdict':'supported'},
                {'id':'c','verdict':'unsupported'}],'safety':True,'answer_covers_question':False}
            return {'answer':'Invalid repair'}
        result=await orchestrate('Teach',[],Tools(),model)
        self.assertEqual(result['status'],'partial')
        self.assertIn('Source fact.',result['answer']);self.assertIn('My own example.',result['answer'])
        self.assertNotIn('Wrong fact.',result['answer'])
        self.assertEqual(len(calls),2) # source-only review; no mandatory lesson rewrite/repair

    async def test_declared_complete_still_partial_with_unread_inventory(self):
        p=EvidenceTests().passage('Source fact.')
        ledger=EvidenceLedger()
        ledger.scope({'c':{'document_id':'d','content_sha256':digest(p['text']),'location':p['location'],'revision':'rev'},
            'unread':{'document_id':'d','content_sha256':'unknown','location':{'page':2},'revision':'rev'}})
        ledger.observed(p)
        ledger.register([{'id':'child','objective':'Explain fact','source_ids':['c']}],{'c':p})
        class Tools:
            context={'c':p};evidence=ledger
            async def call(self,*args):return {}
            def checkpoint(self):return {'evidence':ledger.snapshot()}
        count=0
        async def model(messages):
            nonlocal count
            count+=1
            if count==1:return {'answer':'Source fact.','claims':[{'id':'a','text':'Source fact.','kind':'source','child_ids':['child']}],
                'citations':[{'claim_id':'a','source_id':'c','quote':'Source fact.'}],'partial':False}
            return {'claims':[{'id':'a','verdict':'supported'}],'children':[{'id':'child','covered':True}],
                'safety':True,'answer_covers_question':True,'inventory_complete':True}
        result=await orchestrate('Teach whole document',[],Tools(),model)
        self.assertEqual(result['status'],'partial')
        self.assertEqual(result['checkpoint']['progress']['unread_source_ids'],['unread'])
