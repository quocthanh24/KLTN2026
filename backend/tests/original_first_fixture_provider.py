"""Scripted gold for exactly the synthetic browser corpus, NEVER a production model."""
import copy
import json
from uuid import uuid4
from app.services.ai_observability import v2_usage_sink
from tests.ai_chat_v2_fixture_support import GENERAL_FIXTURE_ANSWERS
from app.services.ai_chat_v2 import REVIEW


def gold(unit):
    return f'Synthetic lesson page {unit}: distinct skill {unit}, review the supporting passage.'


def claim(unit):
    return 'Original fixture page '+str(unit)+': '+gold(unit)+' (SCRIPTED, not GPT vision.)'


class OriginalFixtureProvider:
    def __init__(self,model):self.model=model;self.boundaries=[]

    async def __call__(self,messages):
        self.boundaries.append(copy.deepcopy(messages))
        sink=v2_usage_sink.get()
        if sink is not None:sink.append({'model':self.model,'input_tokens':100,'output_tokens':50,
            'request_id':'fake-original-'+uuid4().hex,'response_id':'fake-original-'+uuid4().hex})
        def decode(value):return json.loads(value[0]['text'] if isinstance(value,list) else value)
        if messages[0]['content']==REVIEW:
            body=decode(messages[-1]['content']);claims=[];children=[]
            for source in body['evidence']:
                if source.get('authority')!='original' or claim(source['unit']) not in body['answer']:continue
                refs=[i for i,c in enumerate(body['citations']) if c['source_id']==source['id']]
                claims.append({'text':claim(source['unit']),'verdict':'supported','citation_indices':refs})
                children.append({'id':'child-'+source['id'],'covered':True,'taught_in_answer':claim(source['unit'])})
            return {'source_claims':claims,'all_document_claims_reviewed':True,'children':children,
                'required_scope':'whole' if 'whole' in body['question'] else 'focused','scope_resolved':True,
                'source_task_complete':True,'required_document_ids':list({p['document_id'] for p in body['evidence']})}
        body=decode(messages[1]['content']);question=body['question'];state=body.get('checkpoint',{})
        if question in GENERAL_FIXTURE_ANSWERS:return GENERAL_FIXTURE_ANSWERS[question]
        last=decode(messages[-1]['content']).get('tool_result',{})
        whole='whole' in question
        if 'documents' in last:
            doc=next(d for d in last['documents'] if d['name'].startswith('Synthetic curriculum'))
            if whole:
                scoped=state.get('evidence',{}).get('inventory',{})
                if not scoped:return {'tool':'begin_whole_document','arguments':{'document_ids':[doc['document_id']]}}
                unread=state['progress']['unread_source_ids'][:2]
                units=[scoped[i]['unit'] for i in unread]
            else:units=[1]
            return {'tool':'read_original_units','arguments':{'document_id':doc['document_id'],'revision':doc['revision'],'units':units}}
        if 'inventory' in last:
            selected=list(last['inventory'].values())[:2]
            return {'tool':'read_original_units','arguments':{'document_id':selected[0]['document_id'],
                'revision':selected[0]['revision'],'units':[u['unit'] for u in selected]}}
        if last.get('passages'):
            passages=last['passages']
            if whole:return {'tool':'register_child_obligations','arguments':{'children':[
                {'id':'child-'+p['id'],'objective':'Teach fixture skill '+str(p['unit']),'source_ids':[p['id']]} for p in passages]}}
        elif 'registered_child_ids' in last:
            passages=[]
            for message in messages:
                if message['role']=='user':
                    packet=decode(message['content'])
                    if packet.get('tool_result',{}).get('passages'):passages=packet['tool_result']['passages']
        else:raise AssertionError('Unexpected original fixture protocol')
        citations=[{'source_id':p['id'],'quote':gold(p['unit'])} for p in passages]
        text='\n\n'.join(claim(p['unit'])+f' [{i+1}](#source-{i})' for i,p in enumerate(passages))
        return text+'\n<source_refs>'+json.dumps({'task':'whole' if whole else 'grounded','citations':citations})+'</source_refs>'
