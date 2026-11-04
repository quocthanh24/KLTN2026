"""Scoped fixtures and read-only evidence for live UC33/UC34 verification."""
import asyncio
import base64
import hashlib
import io
import json
import sys
from uuid import UUID
from sqlalchemy import select,func
from app.db.session import session_factory
from app.models.learning_document import LearningDocument,DocumentChunk
from app.models.ai_conversation import AITurn
from app.models.ai_chat_v2_job import AIChatV2Job
from app.services.storage_service import get_storage_service


def native_pdf(marker,cover=False):
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject,NameObject,DecodedStreamObject
    writer=PdfWriter()
    texts=(['Cover: biology learning booklet. No printed page number.'] if cover else [])+[
        f'Printed page {i}. Geometry lesson {i}: triangles and angles.' if i!=6 else
        f'Printed page 6. Photosynthesis. Evidence code {marker}. Plants use sunlight, water and carbon dioxide to make glucose and oxygen. Chlorophyll absorbs light. Glucose stores chemical energy.'
        for i in range(1,7)]
    for text in texts:
        page=writer.add_blank_page(800,600)
        font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
        page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
        lines=[text[i:i+85] for i in range(0,len(text),85)]
        content='BT /F1 14 Tf 35 530 Td '+' '.join(('0 -24 Td ' if i else '')+'('+line+') Tj' for i,line in enumerate(lines))+' ET'
        stream=DecodedStreamObject();stream.set_data(content.encode('ascii'))
        page[NameObject('/Contents')]=writer._add_object(stream)
    buf=io.BytesIO();writer.write(buf);return buf.getvalue()


async def main(data):
    if data['action']=='fixtures':
        from tests.test_document_rag import word_bytes,pdf_bytes
        result={name:base64.b64encode(raw).decode() for name,raw in {
            'native':native_pdf(data['code']), 'cover':native_pdf(data['code'],True),
            'word':word_bytes(),'blank':pdf_bytes(blank=True)}.items()}
        print(json.dumps(result));return
    async with session_factory() as db:
        if data['action'] in {'hold_processing','release_processing'}:
            from app.models import Channel,Workspace
            from datetime import datetime,UTC,timedelta
            from uuid import uuid4
            doc=await db.scalar(select(LearningDocument).join(Channel,Channel.id==LearningDocument.channel_id)
                .join(Workspace,Workspace.id==Channel.workspace_id).where(LearningDocument.id==UUID(data['document_id']),Workspace.name==data['marker']).with_for_update(of=LearningDocument))
            assert doc is not None and data['marker'].startswith('phase5b-')
            if data['action']=='hold_processing':
                from app.models.learning_document import DocumentStatus
                doc.status=DocumentStatus.PROCESSING;doc.processing_token=uuid4()
                doc.processing_lease_until=datetime.now(UTC)+timedelta(minutes=15)
            else:doc.processing_token=doc.processing_lease_until=None
            await db.commit();print(json.dumps({'status':doc.status.value}));return
        if data['action']=='original_scan':
            doc=await db.get(LearningDocument,UUID('5926f3da-7c06-4fe9-b0da-c50896d48f8e'))
            raw=await get_storage_service().read_document(doc.object_key,200*1024*1024)
            assert hashlib.sha256(raw).hexdigest()==doc.checksum
            print(json.dumps({'base64':base64.b64encode(raw).decode(),'checksum':doc.checksum}));return
        if data['action']=='inspect_turn':
            turn=await db.get(AITurn,UUID(data['turn_id']))
            job=await db.scalar(select(AIChatV2Job).where(AIChatV2Job.turn_id==turn.id))
            print(json.dumps({'turn_id':str(turn.id),'status':turn.status,'sources':turn.sources,
                'same_request_count':await db.scalar(select(func.count()).select_from(AITurn).where(AITurn.conversation_id==turn.conversation_id,AITurn.request_id==turn.request_id)),
                'job_status':job.status,'error_code':job.error_code,'checkpoint':job.checkpoint},default=str,ensure_ascii=False));return
        if data['action']=='reported_failure':
            turn=await db.get(AITurn,UUID('15bea7cf-bc98-43ce-a0c3-8af1b3f07002'))
            job=await db.scalar(select(AIChatV2Job).where(AIChatV2Job.turn_id==turn.id))
            print(json.dumps({'turn_id':str(turn.id),'status':turn.status,'job_status':job.status,
                'error_code':job.error_code,'stage':job.stage,'tool_errors':job.checkpoint.get('tool_errors'),
                'read_units':len(job.checkpoint.get('original_refs',{})),
                'reason_codes':job.checkpoint.get('reason_codes')},default=str));return
    raise ValueError('Unknown read-only action')


if __name__=='__main__':asyncio.run(main(json.load(sys.stdin)))
