"""Synthetic fixture generator + deterministic provider for local integration ONLY.

No production setting selects this provider. It never imports an OpenAI client.
"""
import io
import json
import copy
from uuid import uuid4
from app.services.ai_observability import v2_usage_sink

# Deliberately scripted test answers, not a production classifier or GPT quality result.
GENERAL_FIXTURE_ANSWERS={
    'Giải thích câu điều kiện loại 2 và tự tạo 3 ví dụ':
        'Câu điều kiện loại 2 diễn tả giả định khó xảy ra ở hiện tại. Cấu trúc: If + past simple, would + V.\n\n'
        'Ví dụ tự tạo:\n1. If I had more time, I would read more.\n2. If she lived nearby, we would meet often.\n3. If I were you, I would ask for help.',
    'Cho ví dụ khác, vẫn đừng lộ đáp án':
        'Ví dụ tự tạo: với 3y + 2 = 11, hãy nghĩ xem cần loại bỏ số hạng nào trước. Mình chưa tiết lộ kết quả.'
}


def native_pdf(texts):
    from pypdf import PdfWriter
    from pypdf.generic import NameObject,DictionaryObject,DecodedStreamObject
    writer=PdfWriter()
    for text in texts:
        page=writer.add_blank_page(width=612,height=792)
        font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
        page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
        stream=DecodedStreamObject()
        escaped=text.replace('\\','\\\\').replace('(','\\(').replace(')','\\)')
        stream.set_data(('BT /F1 12 Tf 40 740 Td ('+escaped+') Tj ET').encode('ascii'))
        page[NameObject('/Contents')]=writer._add_object(stream)
    out=io.BytesIO();writer.write(out);return out.getvalue()


class DeterministicProvider:
    def __init__(self,model):
        self.model=model
        self.boundaries=[]
        self.hook=None

    async def __call__(self,messages):
        self.boundaries.append(copy.deepcopy(messages))
        if self.hook:await self.hook(messages)
        sink=v2_usage_sink.get()
        if sink is not None:sink.append({'model':self.model,'input_tokens':100,'output_tokens':50,
            'request_id':'fake-local-'+uuid4().hex,'response_id':'fake-local-'+uuid4().hex})
        system=messages[0]['content']
        def decode(content):
            if isinstance(content,list):return json.loads(content[0]['text'])
            return json.loads(content)
        if system.startswith('Independently review'):
            body=decode(messages[-1]['content'])
            claims=[];children=[]
            for passage in body['evidence']:
                text=passage['text']+' (Deterministic integration fixture.)'
                if text not in body['answer']:continue
                refs=[i for i,c in enumerate(body['citations']) if c['source_id']==passage['chunk_id']]
                claims.append({'text':text,'verdict':'supported','citation_indices':refs})
                identity='child-'+passage['chunk_id']
                if identity in body['obligations']:children.append({'id':identity,'covered':True,'taught_in_answer':text})
            return {'source_claims':claims,'all_document_claims_reviewed':True,
                'children':children,'source_task_complete':True,
                'required_scope':'whole' if 'whole' in body['question'].lower() else 'focused',
                'scope_resolved':True,'required_document_ids':list({p['document_id'] for p in body['evidence']}),
                'security_violation':False}
        body=decode(messages[1]['content'])
        checkpoint=body.get('checkpoint',{})
        last=decode(messages[-1]['content']).get('tool_result',{})
        if 'documents' in last:
            if body['question'] in GENERAL_FIXTURE_ANSWERS:return GENERAL_FIXTURE_ANSWERS[body['question']]
            docs=last['documents']
            if not docs:
                text='Không có tài liệu READY. Đây là ví dụ kiến thức chung của provider giả lập.'
                return text
            if 'whole' in body['question'].lower():
                unread=checkpoint.get('progress',{}).get('unread_source_ids',[])
                if checkpoint.get('evidence',{}).get('inventory'):
                    return {'tool':'read_document_segments','arguments':{'chunk_ids':unread[:16]}}
                return {'tool':'begin_whole_document','arguments':{'document_ids':[d['document_id'] for d in docs]}}
            return {'tool':'search_documents','arguments':{'query':body['question']}}
        if 'inventory' in last:
            ids=list(last['inventory'])[:16]
            return {'tool':'read_document_segments','arguments':{'chunk_ids':ids}}
        if last.get('passages'):
            if 'whole' in body['question'].lower():
                return {'tool':'register_child_obligations','arguments':{'children':[{
                    'id':'child-'+p['chunk_id'],'objective':'Understand the distinct fixture passage '+p['chunk_id'],
                    'source_ids':[p['chunk_id']]} for p in last['passages']]}}
            passages=last['passages'][:2]
        elif 'registered_child_ids' in last:
            passages=[]
            for message in messages:
                if message['role']=='user':
                    packet=decode(message['content'])
                    if packet.get('tool_result',{}).get('passages'):
                        passages=packet['tool_result']['passages']
        else:raise AssertionError('Unexpected deterministic fixture protocol')
        paragraphs=[];citations=[]
        for p in passages:
            text=p['text']+' (Deterministic integration fixture.)'
            paragraphs.append(text+f' [{len(citations)+1}](#source-{len(citations)})')
            citations.append({'source_id':p['chunk_id'],'quote':p['text'][:300]})
        example='Ví dụ bổ sung do provider giả lập tạo; không phải trích dẫn tài liệu.'
        if not checkpoint.get('accepted'):paragraphs.append(example)
        return '\n\n'.join(paragraphs)+'\n<source_refs>'+json.dumps({'citations':citations,
            'task':'whole' if 'whole' in body['question'].lower() else 'assisted','partial':False},ensure_ascii=False)+'</source_refs>'
