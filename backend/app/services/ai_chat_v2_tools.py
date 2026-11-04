"""Authorized V2 retrieval. Raw indexed text/location/revision survive generation.
No provider/OCR call is performed by these tools. Images are privately rendered.
"""
import hashlib
from time import perf_counter
from uuid import UUID
from sqlalchemy import select
from app.core.exceptions import AppError
from app.models.learning_document import LearningDocument, DocumentChunk, DocumentStatus
from app.services.document_pipeline import MODEL_REVISION
from app.services.ai_chat_v2_evidence import EvidenceLedger, digest


class DocumentTools:
    names = {'list_available_documents','get_document_outline','search_documents',
        'read_document_pages','read_document_segments','read_document_batch',
        'begin_whole_document','register_child_obligations','inspect_page_image','direct_pdf_capability',
        'source_inventory','read_original_units'}

    def __init__(self, service, principal, channel_id, conversation_id, checkpoint=None, settings=None):
        self.service,self.principal = service,principal
        self.channel_id,self.conversation_id = channel_id,conversation_id
        self.context = {}
        self.original_first = bool(settings and getattr(settings,'ai_chat_v2_original_first',False))
        self.original_requested = False
        self.original_refs = dict((checkpoint or {}).get('original_refs',{}))
        self.calls = 0
        self.image_count = 0
        self.images = []
        self.settings = settings
        self.evidence = EvidenceLedger((checkpoint or {}).get('evidence'))
        self.revisions = dict((checkpoint or {}).get('revisions',{}))
        self.catalog_revisions = {}
        self.source_refs = dict((checkpoint or {}).get('source_refs',{}))

    async def call(self, name, args):
        from app.services.ai_observability import trace
        started=perf_counter()
        reason='completed'
        try:
            return await self._call(name,args)
        except BaseException:
            reason='rejected_or_interrupted'
            raise
        finally:
            current=trace.get()
            if current is not None:
                current.setdefault('v2_tools',[]).append({'stage':'authorized_retrieval',
                    'tool':name if isinstance(name,str) and name in self.names else 'invalid',
                    'duration_ms':round((perf_counter()-started)*1000,2),'reason':reason,
                    'sources':[{'document_id':c['document_id'],'chunk_id':c['chunk_id'],'revision':c['revision'],
                        'location':{k:v for k,v in c['location'].items() if k in {'page','table_block'} and type(v) is int}}
                        for c in self.context.values()]})

    @staticmethod
    def offset(args):
        value=args.get('offset',0)
        if type(value) is not int or not 0<=value<=100000:
            raise AppError(422,'AI_V2_ARGUMENT_INVALID','Invalid offset.')
        return value

    def fence(self, doc):
        revision=self.revisions.setdefault(str(doc.id),doc.checksum)
        if revision!=doc.checksum:
            raise AppError(409,'AI_V2_SOURCE_CHANGED','Document revision changed; start a new task.')

    async def validate_revisions(self):
        async with self.service.transaction(self.principal):
            await self.service.owned(self.principal,self.channel_id,self.conversation_id)
            for identity,revision in self.revisions.items():
                doc=await self.service.db.get(LearningDocument,UUID(identity),populate_existing=True)
                if (doc is None or doc.channel_id!=self.channel_id or doc.deleted_at is not None
                        or (not self.original_first and doc.status!=DocumentStatus.READY) or doc.checksum!=revision):
                    raise AppError(409,'AI_V2_SOURCE_CHANGED','An accepted source is unavailable or changed.')
            if self.source_refs and not self.original_first:
                chunks=list(await self.service.db.scalars(select(DocumentChunk).where(
                    DocumentChunk.id.in_([UUID(i) for i in self.source_refs]))))
                if {str(c.id) for c in chunks}!=set(self.source_refs):
                    raise AppError(409,'AI_V2_SOURCE_CHANGED','Previously read source segments are missing.')
                for chunk in chunks:
                    ref=self.source_refs[str(chunk.id)]
                    if (str(chunk.document_id)!=ref['document_id'] or digest(chunk.content)!=ref['content_sha256']
                            or dict(chunk.source_location)!=ref['location'] or chunk.model_revision!=ref['index_revision']):
                        raise AppError(409,'AI_V2_SOURCE_CHANGED','A previously read passage/location changed.')

    async def _call(self, name, args):
        if not isinstance(name,str) or name not in self.names or not isinstance(args,dict):
            raise AppError(422,'AI_V2_TOOL_INVALID','Invalid tool contract.')
        self.calls+=1
        if self.calls>max(8,getattr(self.settings,'ai_turn_max_calls',12)-4):
            raise AppError(422,'AI_V2_TOOL_LIMIT','Tool step limit reached; checkpoint and resume.')
        allowed={'document_id','document_ids','query','pages','chunk_ids','offset','children','page','region_id','original','revision','units'}
        if set(args)-allowed:
            raise AppError(422,'AI_V2_ARGUMENT_INVALID','Unexpected tool arguments.')
        offset=self.offset(args)
        identity=args.get('document_id')
        if identity is not None:
            try: identity=str(UUID(identity))
            except (ValueError,TypeError,AttributeError):
                raise AppError(422,'AI_V2_ARGUMENT_INVALID','Invalid document identity.') from None
        selection=getattr(self,'source_selection',None)
        if selection and ((identity and identity!=selection['document_id']) or
                any(i!=selection['document_id'] for i in args.get('document_ids',[]))):
            raise AppError(422,'AI_V2_SOURCE_SELECTION_MISMATCH','Use only the explicitly selected document from the current catalog.')
        if name in {'source_inventory','read_original_units'}:
            if name=='read_original_units':self.original_requested=True
            if not self.original_first or not identity or not isinstance(args.get('revision'),str):
                raise AppError(422,'AI_V2_ARGUMENT_INVALID','Original reader needs document/revision and enabled original-first policy.')
            from app.services.ai_chat_v2_original import source_inventory,read_original_units
            if name=='source_inventory':return await source_inventory(self,identity,args['revision'],offset)
            return await read_original_units(self,identity,args['revision'],args.get('units'))
        if name=='begin_whole_document' and self.original_first:
            ids=args.get('document_ids')
            if not isinstance(ids,list) or not 1<=len(ids)<=20:
                raise AppError(422,'AI_V2_ARGUMENT_INVALID','Explicit original document scope required.')
            from app.services.ai_chat_v2_original import authorized_original,private_adapter,unit_id
            from app.services.original_document_adapter import DOCX
            inventory={}
            for document in ids:
                try:document=str(UUID(document))
                except (ValueError,TypeError,AttributeError):raise AppError(422,'AI_V2_ARGUMENT_INVALID','Invalid document ID.') from None
                async with self.service.transaction(self.principal):
                    await self.service.owned(self.principal,self.channel_id,self.conversation_id)
                    doc=await self.service.db.get(LearningDocument,UUID(document),populate_existing=True)
                    if doc is None or doc.channel_id!=self.channel_id or doc.deleted_at is not None:
                        raise AppError(404,'AI_V2_SOURCE_UNAVAILABLE','Original unavailable in current Channel.')
                    revision=doc.checksum
                raw,meta=await authorized_original(self,document,revision)
                info=await private_adapter(raw,meta['content_type'])
                if not info.get('whole_document_supported',True):
                    self.evidence.unsupported_scopes[document]=info['limitations']
                for unit in info['units']:
                    number=unit['unit'];key=unit_id(document,revision,number)
                    location={'page':number,'physical_page':number} if meta['content_type']=='application/pdf' else (
                        {'body_block':number} if meta['content_type']==DOCX else {'image_id':'original'})
                    inventory[key]={'document_id':document,'revision':revision,'unit':number,
                        'location':location,'content_sha256':None,'authority':'original','ordinal':number}
                if len(inventory)>2000:raise AppError(422,'AI_V2_INVENTORY_LIMIT','Use a smaller scope; full original inventory exceeds bounded task.')
            self.evidence.scope(inventory)
            return {'inventory':inventory,'progress':self.evidence.progress(),
                'instruction':'Inventory is NOT content read. Read original units, then teach skills; DB chunks do not satisfy coverage.'}
        async with self.service.transaction(self.principal):
            await self.service.owned(self.principal,self.channel_id,self.conversation_id)
            query=select(LearningDocument).where(LearningDocument.channel_id==self.channel_id,
                LearningDocument.deleted_at.is_(None))
            if selection:query=query.where(LearningDocument.id==UUID(selection['document_id']))
            if not (self.original_first and name=='list_available_documents'):query=query.where(LearningDocument.status==DocumentStatus.READY)
            if name=='list_available_documents':
                docs=list(await self.service.db.scalars(query.order_by(LearningDocument.id).offset(offset).limit(101)))
                self.catalog_revisions.update({str(d.id):d.checksum for d in docs[:100]})
                return {'documents':[{'document_id':str(d.id),'name':d.name,'content_type':getattr(d,'content_type','application/octet-stream'),
                    'revision':d.checksum,'status':d.status.value,'index_is_navigation_only':self.original_first,
                    **({'index_status':d.status.value,'status_scope':'derived_index_only',
                        'original_access':'reauthorize_and_check_storage_on_read',
                        'original_tools':['source_inventory','read_original_units'],
                        'original_access_independent_of_index':True} if self.original_first else {})} for d in docs[:100]],
                    'partial':len(docs)>100,'next_offset':offset+100 if len(docs)>100 else None,
                    'scope':'current_channel','workspace_search':'not_enabled',
                    'vision_ready':bool(self.settings and self.settings.ai_chat_v2_vision_ready)}
            if name=='direct_pdf_capability':
                return {'ready':False,'reason':'Model/account file capability and bounded file-token pricing not verified.'}
            if name=='register_child_obligations':
                if self.original_first and any(self.context.get(i,{}).get('authority')!='original' for c in args.get('children',[]) if isinstance(c,dict) for i in c.get('source_ids',[])):
                    raise AppError(422,'AI_V2_ORIGINAL_REQUIRED','Learning scope needs original units, not only navigation text.')
                self.evidence.register(args.get('children'),self.context)
                return {'registered_child_ids':list(self.evidence.obligations),'progress':self.evidence.progress()}
            selected_ids=args.get('document_ids')
            if selected_ids is not None:
                try:
                    if not isinstance(selected_ids,list) or not 1<=len(selected_ids)<=20: raise ValueError()
                    selected_ids=[UUID(i) for i in selected_ids]
                except (ValueError,TypeError,AttributeError):
                    raise AppError(422,'AI_V2_ARGUMENT_INVALID','Invalid document scope.') from None
                query=query.where(LearningDocument.id.in_(selected_ids))
            elif identity:
                query=query.where(LearningDocument.id==UUID(identity))
            elif name in {'begin_whole_document','get_document_outline','read_document_batch','inspect_page_image'}:
                raise AppError(422,'AI_V2_ARGUMENT_INVALID','Explicit discovered document scope required.')
            docs=list(await self.service.db.scalars(query.order_by(LearningDocument.id).limit(101)))
            if not docs or (selected_ids and {d.id for d in docs}!=set(selected_ids)):
                raise AppError(404,'AI_V2_SOURCE_UNAVAILABLE','Sources unavailable in current Channel.')
            if identity and str(docs[0].id)!=identity:
                raise AppError(404,'AI_V2_SOURCE_UNAVAILABLE','Source unavailable.')
            if len(docs)>100:
                raise AppError(422,'AI_V2_SCOPE_LIMIT','Choose relevant documents from paginated catalog.')
            for doc in docs: self.fence(doc)
            base=select(DocumentChunk,LearningDocument).join(LearningDocument).where(
                LearningDocument.id.in_([d.id for d in docs]),DocumentChunk.model_revision==MODEL_REVISION)
            if name=='begin_whole_document':
                rows=(await self.service.db.execute(base.order_by(DocumentChunk.document_id,DocumentChunk.ordinal).limit(2001))).all()
                if len(rows)>2000:
                    raise AppError(422,'AI_V2_INVENTORY_LIMIT','Inventory exceeds 2000 segments; use a smaller document scope.')
                indexed={str(d.id) for c,d in rows}
                if indexed!={str(d.id) for d in docs}:
                    raise AppError(409,'AI_V2_INDEX_INCOMPLETE','READY source has no current index.')
                inventory={str(c.id):{'document_id':str(d.id),'revision':d.checksum,'index_revision':c.model_revision,
                    'location':dict(c.source_location),'content_sha256':digest(c.content),'ordinal':c.ordinal}
                    for c,d in rows}
                self.evidence.scope(inventory)
                from app.services.learning_document_service import LearningDocumentService
                from app.services.ai_chat_v2_private import private_pdf_inventory
                for doc in docs:
                    if doc.content_type=='application/pdf' and str(doc.id) not in self.evidence.physical_inventory:
                        raw=await LearningDocumentService(self.service.db).original_bytes(doc)
                        if hashlib.sha256(raw).hexdigest()!=doc.checksum:
                            raise AppError(409,'AI_V2_SOURCE_CHANGED','Private PDF checksum mismatch.')
                        physical=await private_pdf_inventory(raw)
                        indexed_pages={p['location'].get('page') for p in inventory.values() if p['document_id']==str(doc.id)}
                        physical['missing_indexed_pages']=sorted(set(physical['physical_pages'])-indexed_pages-set(physical['proven_blank_pages']))
                        self.evidence.physical_inventory[str(doc.id)]=physical
                return {'inventory':inventory,'physical_inventory':self.evidence.physical_inventory,'progress':self.evidence.progress(),
                    'instruction':'Read batches and identify distinct child skills from raw evidence. Counts are not semantic completeness.'}
            if name=='inspect_page_image':
                if not self.settings or not self.settings.ai_chat_v2_vision_ready:
                    return {'ready':False,'reason':'V2 image-to-model capability is not operator-enabled; no image was interpreted.'}
                self.image_count+=1
                if self.image_count>2:
                    raise AppError(422,'AI_V2_IMAGE_LIMIT','At most two page images per bounded step.')
                page=args.get('page')
                if not identity or args.get('revision')!=docs[0].checksum:
                    raise AppError(409,'AI_V2_SOURCE_CHANGED','Image revision does not match discovered source.')
                mime=docs[0].content_type
                if mime=='application/pdf':
                    if type(page) is not int or not 1<=page<=300 or args.get('region_id') or args.get('original'):
                        raise AppError(422,'AI_V2_ARGUMENT_INVALID','Explicit physical PDF page required.')
                    render_locator=page;image_location={'physical_page':page}
                elif mime in {'image/png','image/jpeg'}:
                    if args.get('original') is not True or page is not None or args.get('region_id'):
                        raise AppError(422,'AI_V2_ARGUMENT_INVALID','Use original=true for a raster source.')
                    render_locator='original';image_location={'image_id':'original'}
                elif mime=='application/vnd.openxmlformats-officedocument.wordprocessingml.document':
                    import re
                    region=args.get('region_id')
                    if not isinstance(region,str) or not re.fullmatch(r'rId\d{1,6}',region) or page is not None or args.get('original'):
                        raise AppError(422,'AI_V2_ARGUMENT_INVALID','Use an indexed DOCX embedded image region.')
                    exists=await self.service.db.scalar(select(DocumentChunk.id).where(
                        DocumentChunk.document_id==docs[0].id,DocumentChunk.model_revision==MODEL_REVISION,
                        DocumentChunk.source_location['embedded_image_id'].as_string()==region).limit(1))
                    if not exists:raise AppError(404,'AI_V2_SOURCE_UNAVAILABLE','Image region not present in published index.')
                    render_locator=region;image_location={'embedded_image_id':region}
                else:
                    return {'ready':False,'reason':'Private image inspection is not supported for this file type.'}
                image_document=docs[0].id
                # Rendering and object I/O happen after this transaction, followed by fresh authorization.
            elif name=='read_document_pages':
                pages=args.get('pages')
                if not identity or not isinstance(pages,list) or not 1<=len(pages)<=8 or any(type(p) is not int or not 1<=p<=10000 for p in pages):
                    raise AppError(422,'AI_V2_ARGUMENT_INVALID','Explicit document and up to eight physical pages required.')
                base=base.where(DocumentChunk.source_location['page'].as_integer().in_(pages))
            elif name=='read_document_segments':
                ids=args.get('chunk_ids')
                try:
                    if not isinstance(ids,list) or not 1<=len(ids)<=16: raise ValueError()
                    ids=[UUID(i) for i in ids]
                except (ValueError,TypeError,AttributeError):
                    raise AppError(422,'AI_V2_ARGUMENT_INVALID','Invalid segment identities.') from None
                base=base.where(DocumentChunk.id.in_(ids))
            elif name=='search_documents':
                query_text=args.get('query')
                if not isinstance(query_text,str) or not 1<=len(query_text)<=2000:
                    raise AppError(422,'AI_V2_ARGUMENT_INVALID','Invalid search query.')
                import asyncio
                from app.services.local_llm import query_vector
                vector=await asyncio.to_thread(query_vector,query_text)
                distance=DocumentChunk.embedding.cosine_distance(vector)
                base=base.add_columns(distance.label('distance')).order_by(distance)
            if name!='inspect_page_image':
                fetched=(await self.service.db.execute(base.order_by(DocumentChunk.document_id,DocumentChunk.ordinal).offset(offset).limit(17))).all()
                scores={str(c.id):1-float(dist) for c,d,dist in fetched} if name=='search_documents' else {}
                rows=[(c,d) for c,d,dist in fetched] if name=='search_documents' else fetched
                if name=='get_document_outline':
                    return {'outline':None,'outline_status':'no_verified_semantic_outline',
                        'segments':[{'chunk_id':str(c.id),'location':dict(c.source_location),'revision':d.checksum,
                            'content_sha256':digest(c.content)} for c,d in rows[:16]],
                        'next_offset':offset+16 if len(rows)>16 else None}
                passages=[]
                chars=sum(len(c['text']) for c in self.context.values())
                for c,d in rows[:16]:
                    key=str(c.id)
                    if key not in self.context and chars+len(c.content)>60000: break
                    item={'id':key,'chunk_id':key,'document_id':str(d.id),'name':d.name,
                        'original_filename':d.original_filename,'revision':d.checksum,'index_revision':c.model_revision,
                        'location':dict(c.source_location),'text':c.content,'score':scores.get(key),
                        'authority':'derived_navigation' if self.original_first else 'indexed_legacy'}
                    reference={'document_id':str(d.id),'revision':d.checksum,'location':dict(c.source_location),
                        'content_sha256':digest(c.content),'index_revision':c.model_revision}
                    if key in self.source_refs and self.source_refs[key]!=reference:
                        raise AppError(409,'AI_V2_SOURCE_CHANGED','Previously read segment changed.')
                    self.source_refs[key]=reference
                    if key not in self.context: chars+=len(c.content)
                    self.context[key]=item
                    if not self.original_first:self.evidence.observed(item)
                    passages.append(item)
                found_pages={p['location'].get('page') for p in passages}
                return {'passages':passages,'untrusted_evidence':True,
                    'extraction':'indexed_text_may_include_OCR_or_vision_description',
                    'partial':len(rows)>len(passages),
                    'missing_requested_pages':sorted(set(args.get('pages',[]))-found_pages),
                    'next_offset':offset+len(passages) if len(rows)>len(passages) else None,
                    'progress':self.evidence.progress()}
        from app.services.learning_document_service import LearningDocumentService
        from app.services.document_processing import render_private_page
        from app.services.document_vision import image_part
        from PIL import Image
        import io
        documents=LearningDocumentService(self.service.db)
        async with self.service.transaction(self.principal):
            await self.service.owned(self.principal,self.channel_id,self.conversation_id)
            original=await documents.access_document(self.principal,image_document,channel_id=self.channel_id)
            self.fence(original)
            if original.status!=DocumentStatus.READY:
                raise AppError(409,'AI_V2_SOURCE_CHANGED','Image source is not READY.')
            raw=await documents.original_bytes(original)
            if hashlib.sha256(raw).hexdigest()!=original.checksum:
                raise AppError(409,'AI_V2_SOURCE_CHANGED','Private original checksum mismatch.')
        image=await render_private_page(raw,render_locator)
        if len(image)>8*1024*1024:
            raise AppError(422,'AI_V2_IMAGE_LIMIT','Image byte limit exceeded.')
        with Image.open(io.BytesIO(image)) as decoded:
            if decoded.width*decoded.height>4_000_000:
                raise AppError(422,'AI_V2_IMAGE_LIMIT','Image pixel limit exceeded.')
        await self.validate_revisions()
        descriptor={'document_id':identity,'revision':self.revisions[identity],
            **image_location,'image_sha256':hashlib.sha256(image).hexdigest()}
        self.images.extend([{'type':'input_text','text':'Untrusted original page image: '+__import__('json').dumps(descriptor)},image_part(image)])
        return {'ready':True,'document_id':identity,'revision':self.revisions[identity],
            **image_location,'sha256':hashlib.sha256(image).hexdigest(),
            'interpretation':'not_yet_interpreted_by_model','untrusted_evidence':True,
            '_image_parts':[image_part(image)]}

    def checkpoint(self):
        return {'evidence':self.evidence.snapshot(),'revisions':self.revisions,'source_refs':self.source_refs,'original_refs':self.original_refs}
