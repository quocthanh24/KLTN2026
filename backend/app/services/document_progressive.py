"""Restart-safe UC29 extraction with atomic vector publication.

New uploads and whole-document retries use the existing complete pipeline.
Legacy navigation batches retain their progress until explicitly retried.
"""
import copy
import hashlib
from datetime import UTC,datetime,timedelta
import logging
from sqlalchemy import select,or_,delete
from app.core.config import get_settings
from app.models.learning_document import LearningDocument,DocumentChunk,DocumentStatus
from app.models.document_derivation import DocumentDerivation
from app.services.ai_chat_v2_original import private_adapter
from app.services.storage_service import get_storage_service


def full_pipeline_blocker(document,state):
    from pathlib import Path
    settings=get_settings()
    if not settings.rag_model_dir or not (Path(settings.rag_model_dir)/'tokenizer.json').is_file():return 'RAG_NOT_CONFIGURED'
    error=(document.processing_error or '').split(':')[0]
    if error=='DOCUMENT_PAGE_LIMIT' and len(state.get('units',{}))>300:return 'DOCUMENT_PAGE_LIMIT'
    visual=any(u['status']=='NEEDS_VISUAL' for u in state.get('units',{}).values()) or error.startswith('DOCUMENT_VISION') or error=='DOCUMENT_PAGE_LIMIT'
    if visual:
        if not settings.document_vision_enabled:return 'DOCUMENT_VISION_DISABLED'
        if not settings.openai_api_key.get_secret_value().strip():return 'DOCUMENT_VISION_KEY_MISSING'
        if settings.openai_model!='gpt-6-luna':return 'DOCUMENT_VISION_MODEL_UNSUPPORTED'
        if document.content_type=='application/pdf' and len(state.get('units',{}))>settings.document_vision_max_pages:return 'DOCUMENT_PAGE_LIMIT'
        if sum(u['status']=='NEEDS_VISUAL' for u in state.get('units',{}).values())>settings.document_vision_max_calls:return 'DOCUMENT_VISION_CALL_CAP'
    return None


def progress_summary(state):
    units=state.get('units',{})
    counts={key:sum(u['status']==key for u in units.values()) for key in ('PENDING','INDEXED','NEEDS_VISUAL','ERROR')}
    return {'phase':'PROCESSING' if counts['PENDING'] else 'PARTIAL' if counts['NEEDS_VISUAL'] or counts['ERROR'] else 'INDEXED',
        'total_units':len(units),'counts':counts,'original_read_by_model':False,
        'index_authority':'navigation_only','inventory_complete':state.get('inventory_complete',False)}


async def process_progressive(db,storage=None,document_id=None,retry_failed=False):
    from uuid import uuid4
    token=uuid4();now=datetime.now(UTC)
    async with db.begin():
        row=await db.scalar(select(LearningDocument).where(LearningDocument.deleted_at.is_(None),
            LearningDocument.id==document_id if document_id else True,
            LearningDocument.status==DocumentStatus.FAILED if retry_failed else LearningDocument.status==DocumentStatus.PROCESSING,
            or_(LearningDocument.processing_lease_until.is_(None),LearningDocument.processing_lease_until<now))
            .order_by(LearningDocument.created_at).with_for_update(skip_locked=True).limit(1))
        if row is None:return 0
        row.processing_token=token
        identity,revision,key,mime=row.id,row.checksum,row.object_key,row.content_type
        filename=row.original_filename
        from app.services.document_processing import processing_timeout
        row.processing_lease_until=now+timedelta(seconds=processing_timeout()+60)
        saved=await db.get(DocumentDerivation,identity)
        state=copy.deepcopy(saved.state) if saved and saved.revision==revision else None
    selected=[]
    try:
        from app.core.learning_documents import MAX_DOCUMENT_BYTES
        raw=await (storage or get_storage_service()).read_document(key,MAX_DOCUMENT_BYTES)
        if hashlib.sha256(raw).hexdigest()!=revision:raise ValueError('Revision checksum changed')
        if state is None:
            info=await private_adapter(raw,mime)
            state={'inventory_complete':True,'units':{str(u['unit']):{'kind':u['kind'],'status':'PENDING'} for u in info['units']},
                'fidelity':info['fidelity'],'limitations':info['limitations'],'full_pipeline_requested':True}
        # Save inventory before expensive work. A cancelled/restarted first batch
        # retains explicit PENDING units and can resume after the lease expires.
        state['summary']=progress_summary(state)
        async with db.begin():
            current=await db.get(LearningDocument,identity,with_for_update=True,populate_existing=True)
            if (current is None or current.deleted_at is not None or current.checksum!=revision
                    or current.processing_token!=token or current.processing_lease_until<=datetime.now(UTC)):
                return 1
            saved=await db.get(DocumentDerivation,identity)
            if saved is None:db.add(DocumentDerivation(document_id=identity,revision=revision,state=copy.deepcopy(state)))
            else:saved.revision=revision;saved.state=copy.deepcopy(state)
        if state.get('full_pipeline_requested'):
            from app.services.document_processing import pipeline_process
            chunks=await pipeline_process(key,filename,revision,storage or get_storage_service())
            if not chunks:raise ValueError('Empty completed pipeline')
            async with db.begin():
                current=await db.get(LearningDocument,identity,with_for_update=True,populate_existing=True)
                if (current is None or current.deleted_at is not None or current.checksum!=revision
                        or current.processing_token!=token or current.processing_lease_until<=datetime.now(UTC)):return 1
                await db.execute(delete(DocumentChunk).where(DocumentChunk.document_id==identity))
                for ordinal,chunk in enumerate(chunks):db.add(DocumentChunk(document_id=identity,ordinal=ordinal,**chunk))
                await db.flush()
                for unit in state['units'].values():unit.update(status='INDEXED')
                state.pop('full_pipeline_requested',None)
                state['full_pipeline_complete']=True
                state['summary']=progress_summary(state)
                saved=await db.get(DocumentDerivation,identity);saved.state=state
                current.status=DocumentStatus.READY;current.processing_error=None
                current.processed_at=datetime.now(UTC)
                current.processing_token=current.processing_lease_until=None
            return 1
        selected=[int(k) for k in sorted(state['units'],key=int) if state['units'][k]['status']=='PENDING'][:4]
        result=await private_adapter(raw,mime,'index',selected) if selected else {'units':{}}
        async with db.begin():
            current=await db.get(LearningDocument,identity,with_for_update=True,populate_existing=True)
            if (current is None or current.deleted_at is not None or current.checksum!=revision
                    or current.processing_token!=token or current.processing_lease_until<=datetime.now(UTC)):
                return 1
            for unit,packet in result['units'].items():
                # Stable per-unit replacement only; successful unrelated units stay intact.
                await db.execute(delete(DocumentChunk).where(DocumentChunk.document_id==identity,
                    DocumentChunk.source_location['original_unit'].as_integer()==int(unit)))
                for number,chunk in enumerate(packet.get('chunks',[])):
                    db.add(DocumentChunk(document_id=identity,ordinal=int(unit)*1000+number,**chunk))
                state['units'][unit].update({k:v for k,v in packet.items() if k!='chunks'})
            state['summary']=progress_summary(state)
            saved=await db.get(DocumentDerivation,identity)
            if saved is None:db.add(DocumentDerivation(document_id=identity,revision=revision,state=state))
            else:saved.revision=revision;saved.state=state
            phase=state['summary']['phase']
            current.status=DocumentStatus.PROCESSING if phase=='PROCESSING' else DocumentStatus.READY if phase=='INDEXED' else DocumentStatus.FAILED
            current.processing_error=None if phase!='PARTIAL' else 'DERIVED_PARTIAL'
            current.processing_token=current.processing_lease_until=None
            current.processed_at=datetime.now(UTC) if phase!='PROCESSING' else None
        return 1
    except Exception as error:
        logging.getLogger(__name__).warning('Progressive processing failed: document_id=%s exception_type=%s',identity,type(error).__name__)
        # Failed inventory/parser/index never deletes good chunks or the original.
        async with db.begin():
            current=await db.get(LearningDocument,identity,with_for_update=True,populate_existing=True)
            if (current and current.deleted_at is None and current.checksum==revision and current.processing_token==token
                    and current.processing_lease_until and current.processing_lease_until>datetime.now(UTC)):
                if state is not None:
                    if state.get('full_pipeline_requested'):
                        selected=[int(k) for k,v in state['units'].items() if v['status']!='INDEXED']
                    for unit in selected:
                        state['units'][str(unit)].update(status='ERROR',
                            error_code='BATCH_TIMEOUT' if isinstance(error,TimeoutError) else 'BATCH_PROCESSING_FAILED')
                    state['summary']=progress_summary(state)
                    if state.get('full_pipeline_requested'):
                        state['summary']['phase']='ERROR'
                    saved=await db.get(DocumentDerivation,identity)
                    if saved is not None and saved.revision==revision:saved.state=state
                from app.services.document_processing import FAILURE_CODES
                code=str(error)
                fallback='VECTOR_PROCESSING_FAILED' if state and state.get('full_pipeline_requested') else 'DERIVED_PROCESSING_FAILED'
                current.status=DocumentStatus.FAILED;current.processing_error=code if code in FAILURE_CODES else fallback
                page=getattr(error,'page_number',None)
                if type(page) is int and 1<=page<=300:current.processing_error+=f':page={page}'
                current.processed_at=datetime.now(UTC)
                current.processing_token=current.processing_lease_until=None
        return 1


async def metadata(db,document):
    if not get_settings().document_progressive_enabled:return None
    saved=await db.get(DocumentDerivation,document.id)
    if saved is None:return {'phase':'LEGACY_INDEX' if document.status==DocumentStatus.READY else
        'ERROR' if document.status==DocumentStatus.FAILED else 'PENDING',
        'original_read_by_model':False,'index_authority':'navigation_only','processing_error':document.processing_error}
    if saved.revision!=document.checksum:return {'phase':'STALE','original_read_by_model':False}
    result=dict(saved.state.get('summary',progress_summary(saved.state)))
    result['retry_blocker']=full_pipeline_blocker(document,saved.state)
    result['full_pipeline_complete']=saved.state.get('full_pipeline_complete',False)
    return result


async def retry_units(db,document,units):
    from app.core.exceptions import AppError
    saved=await db.get(DocumentDerivation,document.id,with_for_update=True)
    if saved is None or saved.revision!=document.checksum:raise AppError(409,'DOCUMENT_PROGRESS_UNAVAILABLE','Chưa có inventory đúng revision để retry theo phần.')
    if not isinstance(units,list) or not 1<=len(units)<=16 or any(type(u) is not int or str(u) not in saved.state['units'] for u in units):
        raise AppError(422,'DOCUMENT_UNITS_INVALID','Chọn tối đa 16 đơn vị gốc hợp lệ.')
    state=copy.deepcopy(saved.state)
    if any(state['units'][str(unit)]['status']=='NEEDS_VISUAL' for unit in units):
        raise AppError(409,'DOCUMENT_VISUAL_FULL_RETRY_REQUIRED','Nội dung hình ảnh cần xử lý lại toàn bộ tài liệu bằng OCR/Vision.')
    for unit in units:
        if state['units'][str(unit)]['status']!='INDEXED':state['units'][str(unit)]['status']='PENDING'
    state['summary']=progress_summary(state);saved.state=state
    document.status=DocumentStatus.PROCESSING;document.processing_error=None
