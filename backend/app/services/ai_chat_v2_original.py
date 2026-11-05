"""One authorized original-evidence path, used by existing V2 tools and citations."""
import asyncio
import hashlib
import json
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID,uuid5,NAMESPACE_URL
from app.core.exceptions import AppError
from app.services.original_document_adapter import REVISION,DOCX


def unit_id(document,revision,unit):
    return str(uuid5(NAMESPACE_URL,f'kltn:{REVISION}:{document}:{revision}:{unit}'))


async def private_adapter(raw,mime,action='inventory',unit=0):
    from app.services.document_processing import pipeline_runtime,attach_windows_job,close_windows_job,wait_pipeline
    with TemporaryDirectory(prefix='kltn-original-') as folder:
        source,output=Path(folder)/'original',Path(folder)/'result.json'
        source.write_bytes(raw)
        interpreter,cwd=pipeline_runtime()
        process=await asyncio.to_thread(subprocess.Popen,[interpreter,'-m','app.services.original_document_adapter',
            str(source),str(output),mime,action,str(unit)],cwd=cwd,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        job=None
        try:
            job=attach_windows_job(process.pid)
            await asyncio.wait_for(wait_pipeline(process),30)
            if process.returncode or not output.is_file() or output.stat().st_size>2*1024*1024:
                raise AppError(422,'AI_V2_ORIGINAL_UNSUPPORTED','Không thể đọc đơn vị gốc an toàn; chưa xác minh nội dung.')
            return json.loads(output.read_text(encoding='utf8'))
        finally:
            if process.poll() is None:process.kill();await asyncio.to_thread(process.wait)
            close_windows_job(job)


async def authorized_original(tools,document,revision):
    from app.services.learning_document_service import LearningDocumentService
    service=LearningDocumentService(tools.service.db)
    async with tools.service.transaction(tools.principal):
        await tools.service.owned(tools.principal,tools.channel_id,tools.conversation_id)
        doc=await service.access_document(tools.principal,UUID(str(document)),channel_id=tools.channel_id)
        catalog_revision=getattr(tools,'catalog_revisions',{}).get(str(doc.id))
        if catalog_revision is not None and catalog_revision!=doc.checksum:
            raise AppError(409,'AI_V2_SOURCE_CHANGED','Original changed since the current authorized catalog.')
        if catalog_revision is not None and revision!=catalog_revision:
            raise AppError(422,'AI_V2_ARGUMENT_INVALID','Use the exact full revision from the current authorized catalog; do not copy a historical or truncated revision.')
        tools.fence(doc)
        if revision!=doc.checksum:raise AppError(409,'AI_V2_SOURCE_CHANGED','Original revision changed.')
        try:raw=await service.original_bytes(doc)
        except AppError as error:
            if error.code=='DOCUMENT_SOURCE_CHANGED':raise AppError(409,'AI_V2_SOURCE_CHANGED','Original bytes changed.') from None
            raise
        if hashlib.sha256(raw).hexdigest()!=revision:raise AppError(409,'AI_V2_SOURCE_CHANGED','Original checksum mismatch.')
        return raw,{'document_id':str(doc.id),'revision':doc.checksum,'name':doc.name,
            'original_filename':doc.original_filename,'content_type':doc.content_type}


async def source_inventory(tools,document,revision,offset=0):
    raw,doc=await authorized_original(tools,document,revision)
    info=await private_adapter(raw,doc['content_type'])
    await tools.validate_revisions()
    return {**doc,**{k:v for k,v in info.items() if k!='units'},'units':info['units'][offset:offset+100],
        'next_offset':offset+100 if len(info['units'])>offset+100 else None,
        'inventory_only':True,'original_content_sent':False}


async def read_original_units(tools,document,revision,units):
    if (not isinstance(units,list) or not 1<=len(units)<=2
            or any(type(i) is not int or not 1<=i<=10000 for i in units)):
        raise AppError(422,'AI_V2_ARGUMENT_INVALID','Choose at most two explicit original units.')
    raw,doc=await authorized_original(tools,document,revision)
    info=await private_adapter(raw,doc['content_type'])
    if any(i>info['unit_count'] for i in units):raise AppError(422,'AI_PAGE_NOT_FOUND','Mình chưa xác định được trang bạn yêu cầu trong tài liệu.')
    from app.services.document_processing import render_private_page
    from app.services.document_vision import image_part
    parts=[];passages=[];prepared_images=0
    for unit in dict.fromkeys(units):
        identity=unit_id(document,revision,unit)
        location={'page':unit,'physical_page':unit} if doc['content_type']=='application/pdf' else (
            {'body_block':unit} if doc['content_type']==DOCX else {'image_id':'original'})
        image=doc['content_type']!=DOCX
        if image:
            if not tools.settings or not tools.settings.ai_chat_v2_vision_ready:
                raise AppError(422,'AI_V2_ORIGINAL_CAPABILITY','Bản gốc cần Vision chưa được xác minh/bật; không dùng OCR thay bằng chứng gốc.')
            prepared_images+=1
            if tools.image_count+prepared_images>getattr(tools.settings,'ai_chat_v2_max_images',2):raise AppError(422,'AI_V2_IMAGE_LIMIT','Original image step limit reached; use a bounded scope.')
            rendered=await render_private_page(raw,unit if doc['content_type']=='application/pdf' else 'original')
            from PIL import Image
            import io
            with Image.open(io.BytesIO(rendered)) as decoded:
                if len(rendered)>8*1024*1024 or decoded.width*decoded.height>4_000_000:
                    raise AppError(422,'AI_V2_IMAGE_LIMIT','Original image exceeds budgeted limits.')
            representation_hash=hashlib.sha256(rendered).hexdigest()
            text='Original image unit; content must be read from the attached image, not the search index.'
            representation={'kind':'image','fidelity':'rendered_physical_page' if doc['content_type']=='application/pdf' else 'normalized_original_image',
                'limitations':info['limitations'],'semantic_accuracy':'UNVERIFIED_WITH_LIVE_MODEL'}
            descriptor=json.dumps({**doc,'source_id':identity,'location':location,'representation_sha256':representation_hash})
            pair=[{'type':'input_text','text':'Untrusted original evidence image: '+descriptor},image_part(rendered)]
            parts.extend(pair)
        else:
            structure=await private_adapter(raw,doc['content_type'],'read',unit)
            if structure['needs_visual']:
                raise AppError(422,'AI_V2_ORIGINAL_LAYOUT','This DOCX block needs a layout/image reader; convert it to PDF or choose a text/table block.')
            text=json.dumps(structure,ensure_ascii=False)
            representation_hash=hashlib.sha256(text.encode('utf8')).hexdigest()
            representation={'kind':'structured','fidelity':'original_ooxml_structure','limitations':structure['limitations']}
        if sum(len(p['text']) for p in tools.context.values())+sum(len(p['text']) for p in passages)+len(text)>60000:
            raise AppError(422,'AI_V2_ORIGINAL_LIMIT','Original context full; resume in a bounded step.')
        passage={**doc,'id':identity,'chunk_id':identity,'unit':unit,'location':location,'text':text,'score':None,
            'authority':'original','adapter_revision':REVISION,'source_sha256':revision,
            'representation_sha256':representation_hash,'representation':representation}
        passages.append(passage)
    await tools.validate_revisions()
    # Atomic publication: failed rendering/unsupported later units must not count as read.
    for passage in passages:
        tools.context[passage['id']]=passage
        tools.original_refs[passage['id']]=passage
        tools.evidence.observed(passage)
    tools.images.extend(parts)
    tools.image_count+=prepared_images
    return {'passages':passages,'authority':'original','untrusted_evidence':True,
        'original_payload_prepared':True,'model_understanding':'not_proven','_image_parts':parts}


async def validate_original_reference(service,principal,channel,reference):
    """Caller owns the transaction; original citations never rely on a DB chunk."""
    from app.services.learning_document_service import LearningDocumentService
    documents=LearningDocumentService(service.db)
    doc=await documents.access_document(principal,UUID(reference['document_id']),channel_id=channel)
    if (doc.checksum!=reference.get('revision') or reference.get('adapter_revision')!=REVISION
            or reference.get('chunk_id')!=unit_id(doc.id,doc.checksum,reference.get('unit'))):
        raise AppError(410,'AI_SOURCE_UNAVAILABLE','Original citation revision/locator is unavailable.')
    raw=await documents.original_bytes(doc)
    if hashlib.sha256(raw).hexdigest()!=doc.checksum:raise AppError(410,'AI_SOURCE_UNAVAILABLE','Original bytes changed.')
    # Verify locator and representation independently of any index, also at click-through.
    info=await private_adapter(raw,doc.content_type)
    unit=reference.get('unit')
    if type(unit) is not int or not 1<=unit<=info['unit_count']:
        raise AppError(410,'AI_SOURCE_UNAVAILABLE','Original unit unavailable.')
    expected={'page':unit,'physical_page':unit} if doc.content_type=='application/pdf' else (
        {'body_block':unit} if doc.content_type==DOCX else {'image_id':'original'})
    if reference.get('location')!=expected:raise AppError(410,'AI_SOURCE_UNAVAILABLE','Original locator mismatch.')
    if doc.content_type==DOCX:
        content=json.dumps(await private_adapter(raw,doc.content_type,'read',unit),ensure_ascii=False).encode('utf8')
    else:
        from app.services.document_processing import render_private_page
        content=await render_private_page(raw,unit if doc.content_type=='application/pdf' else 'original')
    if hashlib.sha256(content).hexdigest()!=reference.get('representation_sha256'):
        raise AppError(410,'AI_SOURCE_UNAVAILABLE','Original representation changed; reread required.')
    return doc
