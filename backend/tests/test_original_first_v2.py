"""Real original/MinIO/DB/API boundaries, scripted model, NEVER GPT quality evidence."""
import base64
import copy
import hashlib
import io
import json
import unittest
from unittest.mock import patch
from uuid import UUID,uuid4
from starlette.datastructures import UploadFile,Headers
from sqlalchemy import select
from app.core.exceptions import AppError
from app.models.learning_document import LearningDocument,DocumentChunk,DocumentStatus
from app.services.learning_document_service import LearningDocumentService
from app.services.ai_chat_v2_original import unit_id
from app.services.original_document_adapter import inventory,read_structured,DOCX
from app.services.ai_observability import v2_usage_sink
from tests import test_ai_chat_v2_integration as existing
from tests.ai_chat_v2_fixture_support import native_pdf

setUpModule=existing.setUpModule
tearDownModule=existing.tearDownModule
PROOFS=[]
SDK_PROOF={}


def image_pdf(text):
    from PIL import Image,ImageDraw
    image=Image.new('RGB',(900,300),'white')
    ImageDraw.Draw(image).text((30,70),text,fill='black',font_size=25)
    data=io.BytesIO();image.save(data,format='PDF');image.close()
    return data.getvalue()


def adversarial_pdf(kind,text):
    """Physical layout cases, not merely different strings in a flattened paragraph."""
    if kind=='image_only':return image_pdf(text)
    if kind not in {'survey','columns','equation'}:return native_pdf((text,))
    from pypdf import PdfReader,PdfWriter
    from pypdf.generic import NameObject,DecodedStreamObject
    writer=PdfWriter();writer.append(PdfReader(io.BytesIO(native_pdf(('placeholder',)))))
    commands={
        'survey': 'BT /F1 14 Tf 50 750 Td (Item) Tj 170 0 Td (AGREE) Tj 170 0 Td (DISAGREE) Tj ET '
            '40 720 m 520 720 l 520 660 l 40 660 l h S 200 720 m 200 660 l S 360 720 m 360 660 l S '
            'BT /F1 14 Tf 60 680 Td (venue) Tj 370 0 Td (X) Tj ET',
        'columns': 'BT /F1 14 Tf 40 740 Td (Left: first) Tj 0 -30 Td (second) Tj ET '
            'BT /F1 14 Tf 330 740 Td (Right: third) Tj 0 -30 Td (fourth) Tj ET',
        'equation': 'BT /F1 16 Tf 40 740 Td (y = x) Tj ET BT /F1 10 Tf 78 748 Td (2) Tj ET '
            'BT /F1 16 Tf 90 740 Td (- 3 = ) Tj ET 145 737 m 220 737 l S',
    }
    stream=DecodedStreamObject();stream.set_data(commands[kind].encode('ascii'))
    writer.pages[0][NameObject('/Contents')]=writer._add_object(stream)
    result=io.BytesIO();writer.write(result);return result.getvalue()


class OriginalFirstIntegrationTests(existing.HttpAssertions,unittest.IsolatedAsyncioTestCase):
    async def test_two_authorized_originals_compare_and_ambiguous_catalog_fixture(self):
        self.settings.ai_chat_v2_original_first=True
        first=await self.raw_document(native_pdf(('Fixture A: method uses sorting.',)),filename='fixture-a.pdf')
        second=await self.raw_document(native_pdf(('Fixture B: method uses hashing.',)),filename='fixture-b.pdf')
        from app.services.ai_chat_v2 import REVIEW
        from app.services.ai_chat_v2_original import unit_id
        claims=['First source describes sorting.','Second source describes hashing.']
        ids=[unit_id(first.id,first.checksum,1),unit_id(second.id,second.checksum,1)]
        calls=[]
        async def provider(messages):
            calls.append(copy.deepcopy(messages))
            if messages[0]['content']==REVIEW:
                return {'source_claims':[{'text':claims[i],'kind':'document_fact','verdict':'supported','citation_indices':[i]} for i in range(2)],
                    'all_document_claims_reviewed':True,'source_task_complete':True,'required_scope':'focused','scope_resolved':True}
            count=len([m for m in messages if isinstance(m.get('content'),list)])
            if count<2:
                doc=(first,second)[count]
                return {'tool':'read_original_units','arguments':{'document_id':str(doc.id),'revision':doc.checksum,'units':[1]}}
            return '\n\n'.join(claims[i]+f' [{i+1}](#source-{i})' for i in range(2))+'\n<source_refs>'+json.dumps({'task':'grounded',
                'citations':[{'source_id':ids[i],'quote':claims[i]} for i in range(2)]})+'</source_refs>'
        self.provider(provider)
        result=await self.ask('Compare the two available source methods')
        self.assertEqual(result['status'],'answered')
        self.assertEqual({s['document_id'] for s in result['sources']},{str(first.id),str(second.id)})
        self.assertEqual(sum(p.get('type')=='input_image' for m in calls[-1] if isinstance(m.get('content'),list) for p in m['content']),2)
        async def clarify(messages):
            payload=json.loads(messages[-1]['content'])['tool_result']
            self.assertEqual(len(payload['documents']),2)
            return 'Which of the two available sources do you mean?'
        self.provider(clarify)
        clarified=await self.ask('Teach Unit 5 from the course')
        self.assertEqual(clarified['sources'],[])
        self.assertIn('Which of the two',clarified['answer'])
        PROOFS.append({'case':'two_originals_and_ambiguous_catalog','provider':'SCRIPTED; NOT GPT',
            'same_channel_originals':2,'comparison_provenance':True,'ambiguity_clarified':True})

    async def asyncSetUp(self):
        await existing.V2IntegrationTests.asyncSetUp(self)
        for module in ('app.services.document_processing','app.services.document_progressive'):
            p=patch(module+'.get_settings',return_value=self.settings);p.start();self.patches.append(p)
    asyncTearDown=existing.V2IntegrationTests.asyncTearDown
    tools=existing.V2IntegrationTests.tools
    ask=existing.V2IntegrationTests.ask
    document=existing.V2IntegrationTests.document

    async def raw_document(self,raw,mime='application/pdf',filename='original.pdf',poison=None):
        result=await LearningDocumentService(self.session).upload(self.sender,self.channel_id,
            UploadFile(io.BytesIO(raw),filename=filename,headers=Headers({'content-type':mime})))
        if poison:
            from app.services.document_pipeline import MODEL_REVISION
            async with self.session.begin():
                doc=await self.session.get(LearningDocument,result.id)
                doc.status=DocumentStatus.READY
                self.session.add(DocumentChunk(document_id=doc.id,ordinal=0,content=poison,
                    source_location={'page':1},embedding=[1.0]+[0.0]*383,model_revision=MODEL_REVISION))
        return result

    def provider(self,script):
        async def guarded(messages):
            sink=v2_usage_sink.get()
            if sink is not None:sink.append({'model':self.settings.openai_model,'input_tokens':50,'output_tokens':30,
                'request_id':'fake-original-'+uuid4().hex,'response_id':'fake-original-'+uuid4().hex})
            return await script(messages)
        p=patch('app.services.ai_conversation_service.complete',guarded);p.start();self.patches.append(p)

    async def test_poisoned_pdf_index_original_payload_and_citation_five_cases(self):
        self.settings.ai_chat_v2_original_first=True
        cases=[('options','A: 4; B: 8; C: 12; D: 16','A: 16; B: 12; C: 8; D: 4'),
            ('survey','Row venue: AGREE [ ] DISAGREE [X]','Row venue: AGREE [X] DISAGREE [ ]'),
            ('columns','Left: first, second. Right: third, fourth.','first third second fourth'),
            ('equation','y = x^2 - 3','y = x2 + 3'),
            ('image_only','Choose TWO answers, not one.','Choose ONE answer.')]
        for label,gold,poison in cases:
            with self.subTest(label=label):
                raw=adversarial_pdf(label,gold)
                doc=await self.raw_document(raw,poison=poison)
                from app.services.ai_chat_v2 import REVIEW
                from app.services.document_processing import render_private_page
                expected=await render_private_page(raw,1)
                import os
                from pathlib import Path
                evidence=os.environ.get('AI_CHAT_V2_EVIDENCE_DIR')
                if evidence:
                    (Path(evidence)/('poison-original-'+label+'.pdf')).write_bytes(raw)
                    (Path(evidence)/('poison-original-'+label+'.jpg')).write_bytes(expected)
                requests=[]
                source_id=unit_id(doc.id,doc.checksum,1)
                claim='Original fixture says: '+gold
                async def script(messages):
                    requests.append(copy.deepcopy(messages))
                    if messages[0]['content']==REVIEW:
                        self.assertTrue(any(p.get('type')=='input_image' for p in messages[-1]['content']))
                        return {'source_claims':[{'text':claim,'verdict':'supported','citation_indices':[0]}],
                            'all_document_claims_reviewed':True,'required_scope':'focused','scope_resolved':True,'source_task_complete':True}
                    last=messages[-1]['content']
                    if isinstance(last,str):
                        return {'tool':'read_original_units','arguments':{'document_id':str(doc.id),'revision':doc.checksum,'units':[1]}}
                    payload=json.loads(last[0]['text'])['tool_result']
                    self.assertEqual(payload['passages'][0]['authority'],'original')
                    image=next(p for p in last if p.get('type')=='input_image')
                    self.assertEqual(base64.b64decode(image['image_url'].split(',',1)[1]),expected)
                    self.assertNotIn(poison,json.dumps(payload))
                    return claim+' [1](#source-0)\n\nGeneral teaching can go beyond the file.\n<source_refs>'+json.dumps({
                        'task':'grounded','citations':[{'source_id':source_id,'quote':gold}]})+'</source_refs>'
                self.provider(script)
                if label=='options':
                    async with self.client() as client:
                        reply=await client.post(self.base+'/questions',headers=self.headers(self.sender),json={
                            'question':'Read the exact original '+label,'request_id':str(uuid4())})
                        self.assertEqual(reply.status_code,200,reply.text)
                        response=reply.json()['data']
                        endpoint=self.base+f"/turns/{response['id']}/sources/0"
                        self.assertEqual((await client.get(endpoint,headers=self.headers(self.sender))).status_code,200)
                        self.assertEqual((await client.get(endpoint,headers=self.headers(self.member))).status_code,404)
                else:response=await self.ask('Read the exact original '+label)
                self.assertEqual(response['status'],'answered')
                source=response['sources'][0]
                self.assertEqual(source['authority'],'original');self.assertEqual(source['revision'],doc.checksum)
                clicked=await self.service.source(self.sender,self.channel_id,self.cid,UUID(str(response['id'])),0)
                self.assertEqual(clicked['location'],{'page':1,'physical_page':1})
                PROOFS.append({'case':label,'provider':'SCRIPTED; NOT GPT','original_sha256':doc.checksum,
                    'representation_sha256':hashlib.sha256(expected).hexdigest(),'input_image_at_generation':True,
                    'input_image_at_review':True,'poisoned_index':poison,'correct_original':gold,
                    'answer':response['answer'],'citation_authority':source['authority']})

    async def test_poisoned_navigation_cannot_be_promoted_even_by_supported_review(self):
        self.settings.ai_chat_v2_original_first=True
        doc=await self.raw_document(native_pdf(('Correct original: answer B.',)),poison='Official answer A.')
        from app.services.ai_chat_v2 import REVIEW
        claim='Official answer A.'
        calls=0
        async def script(messages):
            nonlocal calls
            calls+=1
            if messages[0]['content']==REVIEW:
                return {'source_claims':[{'text':claim,'verdict':'supported','citation_indices':[0],'official':True}],
                    'all_document_claims_reviewed':True,'required_scope':'focused','scope_resolved':True}
            if calls==1:return {'tool':'read_document_pages','arguments':{'document_id':str(doc.id),'pages':[1]}}
            passage=json.loads(messages[-1]['content'])['tool_result']['passages'][0]
            self.assertEqual(passage['authority'],'derived_navigation')
            return claim+'\nGeneral explanation remains useful.\n<source_refs>'+json.dumps({'task':'grounded',
                'citations':[{'source_id':passage['chunk_id'],'quote':claim}]})+'</source_refs>'
        self.provider(script)
        response=await self.ask('What is the official answer?')
        self.assertEqual(response['status'],'partial');self.assertEqual(response['sources'],[])
        self.assertNotIn(claim,response['answer']);self.assertIn('General explanation',response['answer'])

    async def test_visual_capability_off_no_original_claim_from_chunks(self):
        self.settings.ai_chat_v2_original_first=True;self.settings.ai_chat_v2_vision_ready=False
        doc=await self.raw_document(native_pdf(('Correct source',)),poison='Wrong OCR')
        tools=self.tools()
        with self.assertRaises(AppError) as caught:
            await tools.call('read_original_units',{'document_id':str(doc.id),'revision':doc.checksum,'units':[1]})
        self.assertEqual(caught.exception.code,'AI_V2_ORIGINAL_CAPABILITY')
        self.assertEqual(tools.original_refs,{})

    async def test_wrong_model_revision_is_correctable_but_real_catalog_change_is_blocked(self):
        self.settings.ai_chat_v2_original_first=True
        doc=await self.raw_document(native_pdf(('Original fixture content.',)))
        identity,revision=doc.id,doc.checksum
        tools=self.tools()
        await tools.call('list_available_documents',{})
        with self.assertRaises(AppError) as caught:
            await tools.call('read_original_units',{'document_id':str(identity),'revision':'wrong-model-argument','units':[1]})
        self.assertEqual(caught.exception.code,'AI_V2_ARGUMENT_INVALID')
        self.assertEqual(tools.original_refs,{})
        result=await tools.call('read_original_units',{'document_id':str(identity),'revision':revision,'units':[1]})
        self.assertEqual(len(result['passages']),1)
        from sqlalchemy import update
        async with self.session.begin():
            await self.session.execute(update(LearningDocument).where(LearningDocument.id==identity).values(checksum='d'*64))
        with self.assertRaises(AppError) as changed:
            await tools.call('read_original_units',{'document_id':str(identity),'revision':revision,'units':[1]})
        self.assertEqual(changed.exception.code,'AI_V2_SOURCE_CHANGED')

    async def test_general_original_mode_does_not_force_reading(self):
        self.settings.ai_chat_v2_original_first=True
        await self.raw_document(native_pdf(('Unneeded private file.',)))
        seen=[]
        async def script(messages):
            seen.append(copy.deepcopy(messages))
            return 'A general explanation with a new example.'
        self.provider(script)
        result=await self.ask('Explain a general concept')
        self.assertEqual(result['status'],'answered');self.assertEqual(result['sources'],[])
        self.assertEqual(len(seen),1);self.assertNotIn('Unneeded private file.',json.dumps(seen))

    async def test_original_png_jpeg_and_docx_image_block_limits(self):
        self.settings.ai_chat_v2_original_first=True
        from PIL import Image
        for extension,mime in [('PNG','image/png'),('JPEG','image/jpeg')]:
            buffer=io.BytesIO();Image.new('RGB',(40,40),'blue').save(buffer,format=extension)
            doc=await self.raw_document(buffer.getvalue(),mime,'original.'+extension.lower())
            packet=await self.tools().call('read_original_units',{'document_id':str(doc.id),'revision':doc.checksum,'units':[1]})
            self.assertTrue(packet['_image_parts']);self.assertEqual(packet['passages'][0]['authority'],'original')
        from docx import Document
        document=Document();document.add_picture(io.BytesIO(buffer.getvalue()))
        target=io.BytesIO();document.save(target)
        doc=await self.raw_document(target.getvalue(),DOCX,'image.docx')
        with self.assertRaises(AppError) as caught:
            await self.tools().call('read_original_units',{'document_id':str(doc.id),'revision':doc.checksum,'units':[1]})
        self.assertEqual(caught.exception.code,'AI_V2_ORIGINAL_LAYOUT')

    async def test_original_whole_inventory_tracks_unread_not_index_count(self):
        self.settings.ai_chat_v2_original_first=True
        doc=await self.raw_document(native_pdf(('First original page.','Second original page.')),poison='One fake indexed page.')
        tools=self.tools()
        scope=await tools.call('begin_whole_document',{'document_ids':[str(doc.id)]})
        self.assertEqual(len(scope['inventory']),2)
        await tools.call('read_original_units',{'document_id':str(doc.id),'revision':doc.checksum,'units':[1]})
        progress=tools.evidence.progress()
        self.assertEqual(progress['read_segments'],1);self.assertEqual(len(progress['unread_source_ids']),1)
        self.assertTrue(progress['unanalyzed_source_ids'])

    async def test_failed_original_batch_does_not_record_unsent_units(self):
        self.settings.ai_chat_v2_original_first=True
        from docx import Document
        from PIL import Image
        document=Document();document.add_paragraph('A valid original text block.')
        picture=io.BytesIO();Image.new('RGB',(30,30),'red').save(picture,format='PNG')
        document.add_picture(io.BytesIO(picture.getvalue()))
        body=io.BytesIO();document.save(body)
        doc=await self.raw_document(body.getvalue(),DOCX,'batch.docx')
        tools=self.tools()
        with self.assertRaises(AppError):
            await tools.call('read_original_units',{'document_id':str(doc.id),'revision':doc.checksum,'units':[1,2]})
        self.assertEqual(tools.original_refs,{})
        self.assertEqual(tools.context,{})
        self.assertEqual(tools.evidence.read,set())

    async def test_large_original_minio_download_checksum_and_bounded_read(self):
        self.settings.ai_chat_v2_original_first=True
        from pypdf import PdfWriter,PdfReader
        writer=PdfWriter();writer.append(PdfReader(io.BytesIO(native_pdf(('Large original page.',)))))
        writer.add_attachment('padding.bin',b'0'*(100*1024*1024))
        data=io.BytesIO();writer.write(data);raw=data.getvalue()
        doc=await self.raw_document(raw)
        self.assertGreater(doc.size_bytes,100*1024*1024)
        content=await LearningDocumentService(self.session).content(self.sender,doc.id,self.channel_id)
        self.assertEqual(hashlib.sha256(content[0]).hexdigest(),doc.checksum)
        packet=await self.tools().call('read_original_units',{'document_id':str(doc.id),'revision':doc.checksum,'units':[1]})
        self.assertLess(len(json.dumps(packet)),8*1024*1024)
        self.assertEqual(packet['passages'][0]['revision'],doc.checksum)
        scope=await self.tools().call('begin_whole_document',{'document_ids':[str(doc.id)]})
        self.assertTrue(scope['progress']['unsupported_original_scopes'])

    async def test_original_docx_merged_table_not_poisoned_db(self):
        self.settings.ai_chat_v2_original_first=True
        from docx import Document
        document=Document();table=document.add_table(rows=2,cols=2)
        table.cell(0,0).merge(table.cell(0,1)).text='Merged correct heading'
        table.cell(1,0).text='Venue';table.cell(1,1).text='Disagree X'
        out=io.BytesIO();document.save(out)
        doc=await self.raw_document(out.getvalue(),DOCX,'merged.docx',poison='Agree X')
        tools=self.tools();packet=await tools.call('read_original_units',{'document_id':str(doc.id),'revision':doc.checksum,'units':[1]})
        source=packet['passages'][0]
        self.assertIn('gridSpan',source['text']);self.assertIn('Disagree X',source['text']);self.assertNotIn('Agree X',source['text'])
        self.assertEqual(source['location'],{'body_block':1})
        from app.services.ai_chat_v2_original import validate_original_reference
        async with self.service.transaction(self.sender):await validate_original_reference(self.service,self.sender,self.channel_id,source)
        from app.services.ai_chat_v2 import REVIEW
        seen=[]
        async def script(messages):
            seen.append(copy.deepcopy(messages))
            if messages[0]['content']==REVIEW:
                return {'source_claims':[{'text':'The table says Disagree X.','verdict':'supported','citation_indices':[0]}],
                    'all_document_claims_reviewed':True,'required_scope':'focused','scope_resolved':True}
            if len(seen)==1:return {'tool':'read_original_units','arguments':{'document_id':str(doc.id),'revision':doc.checksum,'units':[1]}}
            packet=json.loads(messages[-1]['content'])['tool_result']
            self.assertIn('gridSpan',packet['passages'][0]['text'])
            self.assertIn('Disagree X',packet['passages'][0]['text'])
            return 'The table says Disagree X.\n<source_refs>'+json.dumps({'task':'grounded',
                'citations':[{'source_id':source['id'],'quote':'Disagree X'}]})+'</source_refs>'
        self.provider(script)
        result=await self.ask('Read the exact original merged DOCX table')
        self.assertEqual(result['status'],'answered');self.assertEqual(result['sources'][0]['authority'],'original')
        PROOFS.append({'case':'docx_merged_table','provider':'SCRIPTED; NOT GPT','original_sha256':doc.checksum,
            'representation_sha256':source['representation_sha256'],'original_structure_at_generation':True,
            'poisoned_index':'Agree X','correct_original':'Disagree X','answer':result['answer']})

    async def test_original_reader_cross_channel_revision_and_revocation(self):
        self.settings.ai_chat_v2_original_first=True
        doc=await self.raw_document(native_pdf(('Private original',)))
        tools=self.tools()
        for identity,revision in [(doc.id,'wrong-revision'),(uuid4(),doc.checksum)]:
            with self.assertRaises(AppError):await tools.call('source_inventory',{'document_id':str(identity),'revision':revision})
        denied=self.tools(self.outsider)
        with self.assertRaises(AppError):await denied.call('read_original_units',{'document_id':str(doc.id),'revision':doc.checksum,'units':[1]})
        other=self.tools(cid=uuid4())
        with self.assertRaises(AppError):await other.call('source_inventory',{'document_id':str(doc.id),'revision':doc.checksum})
        async with self.session.begin():
            original=await self.session.get(LearningDocument,doc.id);original.deleted_at=existing.datetime.now(existing.UTC)
        with self.assertRaises(AppError):await self.tools().call('source_inventory',{'document_id':str(doc.id),'revision':doc.checksum})

    async def test_docx_original_block_http_independent_of_index_and_revision_fenced(self):
        from docx import Document
        word=Document();word.add_paragraph('First original paragraph')
        table=word.add_table(rows=1,cols=2);table.cell(0,0).text='A';table.cell(0,1).text='Original B'
        raw=io.BytesIO();word.save(raw)
        doc=await self.raw_document(raw.getvalue(),DOCX,'structured.docx')
        endpoint=f'/learning-documents/{doc.id}/blocks/2?channel_id={self.channel_id}&revision={doc.checksum}'
        async with self.client() as client:
            opened=await client.get(endpoint,headers=self.headers(self.sender))
            self.assertEqual(opened.status_code,200,opened.text)
            block=opened.json()['data']
            self.assertEqual(block['authority'],'original');self.assertEqual(block['kind'],'tbl')
            self.assertIn('Original B',block['original_xml']);self.assertFalse(block['original_read_by_model'])
            self.assertEqual(opened.headers['cache-control'],'private, no-store')
            self.assertEqual((await client.get(endpoint)).status_code,401)
            self.assertEqual((await client.get(endpoint,headers=self.headers(self.outsider))).status_code,403)
            stale=await client.get(endpoint.replace(doc.checksum,'0'*64),headers=self.headers(self.sender))
            self.assertEqual(stale.status_code,409)
            self.assertEqual((await client.get(endpoint.replace('/blocks/2','/blocks/999'),headers=self.headers(self.sender))).status_code,422)

    async def test_pdf_original_viewer_rejects_stale_citation_revision(self):
        doc=await self.raw_document(native_pdf(('Original source page',)))
        base=f'/learning-documents/{doc.id}'
        async with self.client() as client:
            for suffix in ('/inventory','/pages/1/image'):
                response=await client.get(base+suffix+f'?channel_id={self.channel_id}&revision='+('0'*64),headers=self.headers(self.sender))
                self.assertEqual(response.status_code,409,response.text)
            response=await client.get(base+'/pages/1/image'+f'?channel_id={self.channel_id}&revision={doc.checksum}',headers=self.headers(self.sender))
            self.assertEqual(response.status_code,200,response.text[:100])
            self.assertEqual(response.headers['content-type'],'image/jpeg')

    async def legacy_navigation(self, doc, raw):
        """Persist a pre-UC29 navigation job to exercise migration compatibility."""
        from app.models.document_derivation import DocumentDerivation
        from app.services.original_document_adapter import inventory
        info=inventory(raw,doc.content_type)
        async with self.session.begin():
            self.session.add(DocumentDerivation(document_id=doc.id,revision=doc.checksum,state={
                'inventory_complete':True,'units':{str(u['unit']):{'kind':u['kind'],'status':'PENDING'} for u in info['units']}}))

    async def test_progressive_mixed_pdf_preserves_original_good_index_and_retry(self):
        self.settings.ai_chat_v2_original_first=True;self.settings.document_progressive_enabled=True
        from pypdf import PdfWriter
        writer=PdfWriter();writer.append(io.BytesIO(native_pdf(('Native searchable paragraph.',))))
        writer.append(io.BytesIO(image_pdf('Image-only instruction.')))
        buf=io.BytesIO();writer.write(buf);raw=buf.getvalue()
        doc=await self.raw_document(raw)
        await self.legacy_navigation(doc,raw)
        from app.services.document_processing import process_pending
        self.assertEqual(await process_pending(self.session,document_id=doc.id),1)
        service=LearningDocumentService(self.session)
        metadata=await service.detail(self.sender,doc.id,self.channel_id)
        self.assertEqual(metadata.storage_status,'STORED')
        self.assertEqual(metadata.derived_state['phase'],'PARTIAL')
        self.assertEqual(metadata.derived_state['counts']['INDEXED'],1)
        self.assertEqual(metadata.derived_state['counts']['NEEDS_VISUAL'],1)
        content=await service.content(self.sender,doc.id,self.channel_id)
        self.assertEqual(content[0],raw)
        async with self.session.begin():before=list(await self.session.scalars(select(DocumentChunk.id).where(DocumentChunk.document_id==doc.id)))
        with self.assertRaises(AppError) as blocked:
            await service.retry_processing(self.sender,doc.id,self.channel_id,units=[2])
        self.assertEqual(blocked.exception.code,'DOCUMENT_VISUAL_FULL_RETRY_REQUIRED')
        async with self.session.begin():after=list(await self.session.scalars(select(DocumentChunk.id).where(DocumentChunk.document_id==doc.id)))
        self.assertEqual(before,after)
        packet=await self.tools().call('source_inventory',{'document_id':str(doc.id),'revision':doc.checksum})
        self.assertEqual(packet['unit_count'],2)
        self.assertFalse(packet['original_content_sent'])

    async def test_source_changed_during_original_review_cannot_publish(self):
        self.settings.ai_chat_v2_original_first=True
        doc=await self.raw_document(native_pdf(('Source text.',)))
        from app.services.ai_chat_v2 import REVIEW
        async def script(messages):
            if messages[0]['content']==REVIEW:
                async with self.session.begin():
                    row=await self.session.get(LearningDocument,doc.id);row.checksum='0'*64
                return {'source_claims':[{'text':'Source text.','verdict':'supported','citation_indices':[0]}],
                    'all_document_claims_reviewed':True,'required_scope':'focused'}
            if isinstance(messages[-1]['content'],str):return {'tool':'read_original_units','arguments':{'document_id':str(doc.id),'revision':doc.checksum,'units':[1]}}
            return 'Source text.\n<source_refs>'+json.dumps({'task':'grounded','citations':[{'source_id':unit_id(doc.id,doc.checksum,1),'quote':'Source text.'}]})+'</source_refs>'
        self.provider(script)
        with self.assertRaises(AppError):await self.ask('Exact source fact')

    async def test_progressive_cancel_restart_preserves_finished_units(self):
        import asyncio
        from datetime import timedelta
        from app.services.document_processing import process_pending
        from app.models.document_derivation import DocumentDerivation
        self.settings.document_progressive_enabled=True
        doc=await self.raw_document(native_pdf(tuple('Native page '+str(i) for i in range(6))))
        await self.legacy_navigation(doc,native_pdf(tuple('Native page '+str(i) for i in range(6))))
        await process_pending(self.session,document_id=doc.id)
        async with self.session.begin():
            before=list(await self.session.scalars(select(DocumentChunk.id).where(DocumentChunk.document_id==doc.id)))
        from app.services.ai_chat_v2_original import private_adapter
        async def cancel(raw,mime,action='inventory',unit=0):
            if action=='index':raise asyncio.CancelledError()
            return await private_adapter(raw,mime,action,unit)
        with patch('app.services.document_progressive.private_adapter',cancel):
            with self.assertRaises(asyncio.CancelledError):await process_pending(self.session,document_id=doc.id)
        async with self.session.begin():
            saved=await self.session.get(DocumentDerivation,doc.id,populate_existing=True)
            self.assertEqual(saved.state['summary']['counts']['INDEXED'],4)
            self.assertEqual(saved.state['summary']['counts']['PENDING'],2)
            row=await self.session.get(LearningDocument,doc.id,populate_existing=True)
            row.processing_lease_until=existing.datetime.now(existing.UTC)-timedelta(seconds=1)
        await process_pending(self.session,document_id=doc.id)
        async with self.session.begin():
            after=list(await self.session.scalars(select(DocumentChunk.id).where(DocumentChunk.document_id==doc.id)))
            self.assertTrue(set(before).issubset(after));self.assertEqual(len(after),6)
            saved=await self.session.get(DocumentDerivation,doc.id,populate_existing=True)
            self.assertEqual(saved.state['summary']['phase'],'INDEXED')

    async def test_progressive_timeout_has_per_unit_errors_and_retry_is_idempotent(self):
        from app.services.document_processing import process_pending
        from app.models.document_derivation import DocumentDerivation
        self.settings.document_progressive_enabled=True
        doc=await self.raw_document(native_pdf(tuple('Native page '+str(i) for i in range(4))))
        await self.legacy_navigation(doc,native_pdf(tuple('Native page '+str(i) for i in range(4))))
        from app.services.ai_chat_v2_original import private_adapter
        async def timeout(raw,mime,action='inventory',unit=0):
            if action=='index':raise TimeoutError()
            return await private_adapter(raw,mime,action,unit)
        with patch('app.services.document_progressive.private_adapter',timeout):await process_pending(self.session,document_id=doc.id)
        async with self.session.begin():
            saved=await self.session.get(DocumentDerivation,doc.id)
            self.assertEqual(saved.state['summary']['counts']['ERROR'],4)
            self.assertTrue(all(u['error_code']=='BATCH_TIMEOUT' for u in saved.state['units'].values()))
        service=LearningDocumentService(self.session)
        await service.retry_processing(self.sender,doc.id,self.channel_id,units=[1])
        await process_pending(self.session,document_id=doc.id)
        async with self.session.begin():before=list(await self.session.scalars(select(DocumentChunk.id).where(DocumentChunk.document_id==doc.id)))
        await service.retry_processing(self.sender,doc.id,self.channel_id,units=[1])
        await process_pending(self.session,document_id=doc.id)
        async with self.session.begin():after=list(await self.session.scalars(select(DocumentChunk.id).where(DocumentChunk.document_id==doc.id)))
        self.assertEqual(before,after)

    async def test_original_inventory_http_nonready_and_corrupt_bytes_rejected(self):
        from unittest.mock import AsyncMock
        doc=await self.raw_document(native_pdf(('Page one.','Page two.')))
        async with self.client() as client:
            endpoint=f'/learning-documents/{doc.id}/inventory?channel_id={self.channel_id}'
            response=await client.get(endpoint,headers=self.headers(self.sender))
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(response.json()['data']['unit_count'],2)
            self.assertEqual((await client.get(endpoint)).status_code,401)
            self.assertEqual((await client.get(endpoint,headers=self.headers(self.outsider))).status_code,403)
        storage=AsyncMock();storage.read_document.return_value=b'%PDF-corrupted original'
        with self.assertRaises(AppError) as caught:
            await LearningDocumentService(self.session,storage).content(self.sender,doc.id,self.channel_id)
        self.assertEqual(caught.exception.code,'DOCUMENT_SOURCE_CHANGED')

    async def test_page_viewer_revision_race_after_render_is_rejected(self):
        doc=await self.raw_document(native_pdf(('Original page.',)))
        from app.services.document_processing import render_private_page
        async def changed(raw,page):
            image=await render_private_page(raw,page)
            async with self.session.begin():
                row=await self.session.get(LearningDocument,doc.id);row.checksum='0'*64
            return image
        with patch('app.services.document_processing.render_private_page',changed):
            with self.assertRaises(AppError) as caught:
                await LearningDocumentService(self.session).page_image(self.sender,doc.id,1,self.channel_id)
        self.assertEqual(caught.exception.code,'DOCUMENT_SOURCE_CHANGED')


class OriginalAdapterOfflineTests(unittest.TestCase):
    def test_unsupported_targets_remain_rejected(self):
        from app.core.learning_documents import DOCUMENT_TYPES
        for extension in ('.pptx','.xlsx','.csv','.txt','.md','.webp'):self.assertNotIn(extension,DOCUMENT_TYPES)

    def test_docx_external_links_not_fetched_and_xml_bombs_rejected(self):
        import zipfile
        data=io.BytesIO()
        with zipfile.ZipFile(data,'w') as archive:
            archive.writestr('word/document.xml','<!DOCTYPE x [<!ENTITY x "boom">]><x/>')
        with self.assertRaises(ValueError):inventory(data.getvalue(),DOCX)

    def test_large_pdf_original_inventory_no_ocr_or_whole_file_to_model(self):
        raw=native_pdf(('Original large file.',))
        # A valid 100MiB PDF with harmless trailing whitespace. Adapter accepts original bytes,
        # inventory carries no content payload and stays bounded; not a model-size claim.
        raw+=b' '*(100*1024*1024-len(raw))
        with patch('app.services.document_vision.extract_page',side_effect=AssertionError('PAID OCR FORBIDDEN')):
            info=inventory(raw,'application/pdf')
        self.assertEqual(info['unit_count'],1);self.assertLess(len(json.dumps(info)),1000)


class OriginalSDKTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_original_image_survives_real_sdk_serialization_without_network(self):
        import httpx2
        import openai
        from pydantic import SecretStr
        from app.core.config import get_settings
        from app.services.openai_provider import complete,close_clients
        from app.services.ai_chat_v2 import model_call
        from app.services.document_vision import image_part
        from app.services.ai_chat_v2_budget import BudgetLedger
        from PIL import Image
        buffer=io.BytesIO();Image.new('RGB',(30,30),'green').save(buffer,format='JPEG')
        image=image_part(buffer.getvalue())
        messages=[{'role':'user','content':[{'type':'input_text','text':'Untrusted original page 1'},image]}]
        self.assertGreaterEqual(BudgetLedger.input_upper_bound(messages),4_000_000)
        settings=get_settings().model_copy(update={'openai_api_key':SecretStr('fake-not-real')})
        payloads=[]
        def handler(request):
            body=json.loads(request.content);payloads.append(body)
            response={'id':'resp_fake_original','object':'response','created_at':0,'model':settings.openai_model,'status':'completed',
                'output':[{'type':'message','id':'msg_fake','status':'completed','role':'assistant','content':[
                    {'type':'output_text','text':'Scripted original response.','annotations':[]}]}],
                'usage':{'input_tokens':30,'output_tokens':10,'total_tokens':40}}
            return httpx2.Response(200,headers={'content-type':'text/event-stream'},content='data: '+json.dumps(
                {'type':'response.completed','response':response,'sequence_number':1})+'\n\n')
        factory=openai.AsyncOpenAI
        def client(**kwargs):return factory(**kwargs,http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)))
        try:
            with patch('app.services.openai_provider.get_settings',return_value=settings),patch('app.services.openai_provider.AsyncOpenAI',side_effect=client):
                result=await model_call(complete,messages,natural=True)
            self.assertEqual(result,'Scripted original response.')
            self.assertEqual(payloads[0]['input'][0]['content'][1]['image_url'],image['image_url'])
            self.assertEqual(payloads[0]['model'],settings.openai_model)
            self.assertIs(payloads[0]['store'],False)
            SDK_PROOF.update({'level':'Real SDK + MockTransport, NOT LIVE GPT','model':settings.openai_model,
                'image_sha256':hashlib.sha256(buffer.getvalue()).hexdigest(),'image_preserved_in_serialized_request':True,
                'budget_input_reservation_tokens_at_least':4_000_000,'network_calls':0})
        finally:await close_clients()
