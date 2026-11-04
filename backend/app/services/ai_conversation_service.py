import asyncio
import hashlib
import json
import logging
import re
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4
from time import perf_counter
from sqlalchemy import select, func
from app.core.exceptions import AppError
from app.models.ai_conversation import AIConversation, AITurn
from app.models.learning_document import LearningDocument, DocumentChunk, DocumentStatus
from app.services.channel_service import ChannelService
from app.services.ai_observability import stage, progress, trace, mark
from app.services.ai_observability import call_budget,TurnBudget
from app.services.ai_provider import complete
from app.services.document_pipeline import MODEL_REVISION

logger = logging.getLogger('uvicorn.error.ai_retrieval')


class AIConversationService(ChannelService):
    async def owned(self, principal, channel_id, conversation_id):
        await self.channel_access(channel_id,principal.user.id)
        row=await self.db.scalar(select(AIConversation).where(AIConversation.id==conversation_id,
            AIConversation.channel_id==channel_id,AIConversation.user_id==principal.user.id)
            .with_for_update().execution_options(populate_existing=True))
        if row is None: raise AppError(404,'AI_CONVERSATION_NOT_FOUND','Hội thoại không còn tồn tại.')
        return row

    @staticmethod
    def summary(row):
        return {k:getattr(row,k) for k in ('id','channel_id','title','created_at','updated_at')}

    async def listing(self,principal,channel_id,offset=0):
        async with self.transaction(principal):
            await self.channel_access(channel_id,principal.user.id)
            rows=list(await self.db.scalars(select(AIConversation).where(AIConversation.channel_id==channel_id,
                AIConversation.user_id==principal.user.id).order_by(AIConversation.updated_at.desc(),AIConversation.id).offset(offset).limit(21)))
            return {'items':[self.summary(r) for r in rows[:20]],'next_offset':offset+20 if len(rows)>20 else None}

    async def create(self,principal,channel_id):
        async with self.transaction(principal):
            await self.channel_access(channel_id,principal.user.id)
            row=AIConversation(channel_id=channel_id,user_id=principal.user.id,title='Hội thoại mới')
            self.db.add(row); await self.db.flush(); return self.summary(row)

    async def rename(self,principal,channel_id,conversation_id,title):
        async with self.transaction(principal):
            row=await self.owned(principal,channel_id,conversation_id)
            row.title=title; row.updated_at=datetime.now(UTC); await self.db.flush(); return self.summary(row)

    async def remove(self,principal,channel_id,conversation_id):
        async with self.transaction(principal):
            row=await self.owned(principal,channel_id,conversation_id)
            await self.db.delete(row)

    async def turn_response(self,row,documents=None):
        sources=[]
        for source in row.sources:
            doc=(documents.get(UUID(source['document_id'])) if documents is not None else
                 await self.db.get(LearningDocument,UUID(source['document_id']),populate_existing=True))
            available=(doc is not None and doc.deleted_at is None and (source.get('authority')=='original' or doc.status==DocumentStatus.READY)
                and (not source.get('revision') or source['revision']==doc.checksum))
            sources.append({**{k:v for k,v in source.items() if k not in {'quote','_coverage_manifest','_unit_coverage_manifest','_unit_keys'}},'available':available,
                            'quote':source['quote'] if available else None})
        response={'id':row.id,'request_id':row.request_id,'question':row.question,'answer':row.answer,'status':row.status,
                  'created_at':row.created_at,'sources':sources}
        if row.sources and row.sources[0].get('_coverage_manifest'):
            response['coverage_manifest']=row.sources[0]['_coverage_manifest']
        elif row.sources and row.sources[0].get('_unit_coverage_manifest'):
            response['coverage_manifest']=row.sources[0]['_unit_coverage_manifest']
        if row.sources and row.sources[0].get('_unit_keys'):response['unit_keys']=row.sources[0]['_unit_keys']
        # V2 tasks are additive; historical turns without jobs remain readable.
        from app.core.config import get_settings
        from app.services.ai_chat_v2 import enabled
        channel_id=getattr(row,'_v2_channel_id',None)
        if channel_id is None:
            channel_id=await self.db.scalar(select(AIConversation.channel_id).where(AIConversation.id==row.conversation_id))
        if enabled(get_settings(),channel_id):
            from app.models.ai_chat_v2_job import AIChatV2Job
            from app.services.ai_chat_v2_jobs import JobStore
            job=await self.db.scalar(select(AIChatV2Job).where(AIChatV2Job.turn_id==row.id))
            if job is not None:response['v2_task']=JobStore.public(job)
        return response

    async def detail(self,principal,channel_id,conversation_id,offset=0):
        async with self.transaction(principal):
            row=await self.owned(principal,channel_id,conversation_id)
            turns=list(await self.db.scalars(select(AITurn).where(AITurn.conversation_id==row.id)
                .order_by(AITurn.created_at,AITurn.id).offset(offset).limit(51)))
            document_ids={UUID(s['document_id']) for t in turns[:50] for s in t.sources}
            documents={d.id:d for d in await self.db.scalars(select(LearningDocument).where(
                LearningDocument.id.in_(document_ids),LearningDocument.channel_id==channel_id)
                .execution_options(populate_existing=True))}
            return {**self.summary(row),'turns':[await self.turn_response(t,documents) for t in turns[:50]],
                    'next_offset':offset+50 if len(turns)>50 else None}

    async def source(self,principal,channel_id,conversation_id,turn_id,index):
        async with self.transaction(principal):
            await self.owned(principal,channel_id,conversation_id)
            turn=await self.db.scalar(select(AITurn).where(AITurn.id==turn_id,AITurn.conversation_id==conversation_id))
            if turn is None or index<0 or index>=len(turn.sources): raise AppError(404,'AI_SOURCE_NOT_FOUND','Không tìm thấy nguồn.')
            source=turn.sources[index]
            doc=await self.db.get(LearningDocument,UUID(source['document_id']),populate_existing=True)
            if source.get('authority')=='original':
                from app.services.ai_chat_v2_original import validate_original_reference
                doc=await validate_original_reference(self,principal,channel_id,source)
                return {**source,'name':doc.name,'content_type':doc.content_type,'size_bytes':doc.size_bytes,'provenance':'original_revision_unit','quote_verification':'visual_review' if source.get('representation',{}).get('kind')=='image' else 'original_structure_substring'}
            chunk=await self.db.get(DocumentChunk,UUID(source['chunk_id']),populate_existing=True)
            if (doc is None or doc.channel_id!=channel_id or doc.deleted_at is not None or doc.status!=DocumentStatus.READY
                or chunk is None or chunk.document_id!=doc.id or chunk.model_revision!=MODEL_REVISION
                or (source.get('revision') and source['revision']!=doc.checksum)
                or dict(chunk.source_location)!=source['location']):
                raise AppError(410,'AI_SOURCE_UNAVAILABLE','Nguồn tài liệu không còn khả dụng.')
            if source['quote'] not in chunk.content: raise AppError(410,'AI_SOURCE_UNAVAILABLE','Nguồn đã thay đổi.')
            return {**{k:v for k,v in source.items() if k not in {'_coverage_manifest','_unit_coverage_manifest','_unit_keys'}},'name':doc.name,'content_type':doc.content_type,'size_bytes':doc.size_bytes}

    async def ask(self,principal,channel_id,conversation_id,question,request_id,*,resume_v2=False,source_selection=None):
        started = perf_counter()
        timing = {}
        progress_key = (conversation_id, request_id)
        mode = 'detail'
        token=uuid4()
        async with self.transaction(principal):
            conversation=await self.owned(principal,channel_id,conversation_id)
            if source_selection:
                from app.services.learning_document_service import LearningDocumentService
                await LearningDocumentService(self.db).access_document(principal,UUID(source_selection['document_id']),channel_id=channel_id)
            turn=await self.db.scalar(select(AITurn).where(AITurn.conversation_id==conversation_id,AITurn.request_id==request_id).with_for_update())
            if turn:
                if turn.question!=question: raise AppError(409,'AI_REQUEST_CONFLICT','Mã yêu cầu đã dùng cho câu hỏi khác.')
                if not resume_v2:
                    from app.models.ai_chat_v2_job import AIChatV2Job
                    job=await self.db.scalar(select(AIChatV2Job).where(AIChatV2Job.turn_id==turn.id))
                    if job and job.checkpoint.get('request_source_selection')!=source_selection:
                        raise AppError(409,'AI_REQUEST_CONFLICT','Mã yêu cầu đã dùng với lựa chọn tài liệu khác.')
                if turn.status in {'answered','partial','unverified','no_information','clarification'} and not resume_v2: return await self.turn_response(turn)
                if turn.status=='processing' and turn.created_at>datetime.now(UTC)-timedelta(minutes=5):
                    raise AppError(409,'AI_REQUEST_PENDING','Câu hỏi đang được xử lý.')
            if await self.db.scalar(select(AITurn.id).where(AITurn.conversation_id==conversation_id,
                AITurn.status=='processing',AITurn.created_at>datetime.now(UTC)-timedelta(minutes=5),
                AITurn.request_id!=request_id).limit(1)):
                raise AppError(409,'AI_REQUEST_PENDING','Hội thoại đang xử lý một câu hỏi.')
            prior_turns=list(await self.db.scalars(select(AITurn).where(AITurn.conversation_id==conversation_id,
                AITurn.request_id!=request_id,AITurn.status.in_(['answered','partial','no_information','unverified','clarification']))
                .order_by(AITurn.created_at.desc(),AITurn.id.desc()).limit(64)))
            history=[{'question':t.question,'answer':t.answer,'sources':t.sources,'status':t.status,
                      'unit_keys':t.sources[0].get('_unit_keys',[]) if t.sources else []} for t in prior_turns]
            documents=list(await self.db.scalars(select(LearningDocument).where(
                LearningDocument.channel_id==channel_id,LearningDocument.deleted_at.is_(None))
                .order_by(LearningDocument.created_at,LearningDocument.id)))
            if turn is None:
                turn=AITurn(conversation_id=conversation_id,request_id=request_id,question=question,status='processing',sources=[])
                self.db.add(turn)
            turn.status='processing'; turn.processing_token=token; turn.created_at=datetime.now(UTC)
            await self.db.flush(); turn_id=turn.id
        trace_token = trace.set(timing)
        from app.core.config import get_settings
        from time import monotonic
        limits=get_settings()
        budget_token=call_budget.set(TurnBudget(limits.ai_turn_max_calls,monotonic()+limits.ai_turn_timeout_seconds))
        context=[]
        mark('authorization_and_history_ms', started)
        stage(progress_key, 'retrieving')
        try:
            from app.services.ai_chat_v2 import run as run_v2
            from types import SimpleNamespace
            mode = 'v2'
            result, context = await run_v2(self, principal, channel_id, conversation_id,
                question, history, complete, limits,turn_id=turn_id,source_selection=source_selection)
            mode = 'v2'
            plan = SimpleNamespace(unit_scope=(), reason='model_tool_orchestration',
                diagnostics=lambda: {'version': 'v2', 'scope': 'current_channel'})
            diagnostics = {'version': 'v2', 'outcome': result['status']}
            stage(progress_key,'saving')
            before=perf_counter()
            async with self.transaction(principal):
                conversation=await self.owned(principal,channel_id,conversation_id)
                current=await self.db.get(AITurn,turn_id,with_for_update=True,populate_existing=True)
                if current is None or current.processing_token!=token: raise AppError(409,'AI_REQUEST_SUPERSEDED','Yêu cầu đã thay đổi.')
                for source in context:
                    if mode=='v2' and source.get('authority')=='original':
                        from app.services.ai_chat_v2_original import validate_original_reference
                        await validate_original_reference(self,principal,channel_id,source)
                        continue
                    doc=await self.db.get(LearningDocument,UUID(source['document_id']),populate_existing=True)
                    chunk=await self.db.get(DocumentChunk,UUID(source['chunk_id']),populate_existing=True)
                    if doc is None or doc.deleted_at is not None or doc.status!=DocumentStatus.READY or doc.channel_id!=channel_id or chunk is None or chunk.document_id!=doc.id:
                        raise AppError(409,'AI_SOURCE_CHANGED','Nguồn đã thay đổi. Vui lòng gửi lại câu hỏi.')
                    if (source.get('revision') and source['revision'] != doc.checksum) or chunk.content != source['text'] or chunk.model_revision!=MODEL_REVISION or dict(chunk.source_location)!=source['location']:
                        raise AppError(409,'AI_SOURCE_CHANGED','Nội dung nguồn đã thay đổi. Vui lòng gửi lại câu hỏi.')
                mapped={c['id']:c for c in context}
                sources=[]
                for claim in result['sources']:
                    src=mapped[claim['source_id']]
                    if mode=='v2' and src.get('representation',{}).get('kind')!='image' and claim['quote'] not in src['text']:
                        raise AppError(409,'AI_V2_SOURCE_CHANGED','Trích dẫn đã thay đổi trước khi lưu.')
                    citation={k:src[k] for k in ('document_id','chunk_id','name','location')} | {'quote':claim['quote']}
                    if mode=='v2':
                        citation['revision']=src['revision']
                        if src.get('authority')=='original':
                            citation.update({k:src[k] for k in ('authority','adapter_revision','unit','representation_sha256','source_sha256','representation')})
                    if citation not in sources:
                        sources.append(citation)
                if sources and result.get('coverage_manifest'):
                    if result['coverage_manifest'].get('scope','whole_documents')!='whole_documents':
                        sources[0]['_unit_coverage_manifest']=result['coverage_manifest']
                        sources[0]['_unit_keys']=[u['key'] for u in plan.unit_scope]
                    else:
                        sources[0]['_coverage_manifest']=result['coverage_manifest']
                        sources[0]['_unit_keys']=[result['coverage_manifest']['map_id']+':'+u['id']
                            for u in result['coverage_manifest']['units'] if u['status']!='administrative']
                elif sources and plan.unit_scope:sources[0]['_unit_keys']=[u['key'] for u in plan.unit_scope]
                current.answer=result['answer'];current.status=result['status'];current.sources=sources;current.processing_token=None
                if mode=='v2' and result.get('_v2_job'):
                    from app.models.ai_chat_v2_job import AIChatV2Job
                    fence=result['_v2_job']
                    job=await self.db.get(AIChatV2Job,UUID(fence['id']),with_for_update=True,populate_existing=True)
                    if (job is None or job.lease_token!=UUID(fence['token']) or job.status!='ready'
                            or job.lease_until is None or job.lease_until<=datetime.now(UTC)):
                        raise AppError(409,'AI_V2_JOB_FENCED','Tác vụ đã bị hủy hoặc thay thế trước khi công bố.')
                    job.status='complete' if result['status']=='answered' else 'partial'
                    job.stage='published';job.lease_token=job.lease_until=None;job.version+=1
                    from app.services.ai_chat_v2_jobs import JobStore
                    result['v2_task']=JobStore.public(job)
                conversation.updated_at=datetime.now(UTC)
                if conversation.title=='Hội thoại mới': conversation.title=question[:120]
                await self.db.flush()
                response=await self.turn_response(current)
            mark('save_ms', before)
            mark('total_service_ms', started)
            stage(progress_key,'completed')
            timing['call_budget']={'used':call_budget.get().calls,'limit':limits.ai_turn_max_calls,
                                   'timeout_seconds':limits.ai_turn_timeout_seconds}
            response['timings_ms']=timing.copy()
            response['context_resolution']=plan.diagnostics()
            if result.get('v2_task'):response['v2_task']=result['v2_task']
            if mode=='v2':response['reason_codes']=result.get('reason_codes',[])
            if result.get('coverage_manifest'):response['coverage_manifest']=result['coverage_manifest']
            response['retrieval']={'mode':mode,'resolution':plan.reason,'diagnostics':diagnostics,'chunks':len(context),'documents':len({c['document_id'] for c in context}),
                                   'sources':[{'chunk_id':c['chunk_id'],'document_id':c['document_id'],
                                               'score':c['score'],'location':c['location']} for c in context]}
            return response
        except asyncio.CancelledError:
            if mode=='v2':
                await self.db.rollback()
                async with self.db.begin():
                    current=await self.db.get(AITurn,turn_id,with_for_update=True,populate_existing=True)
                    if current is not None and current.processing_token==token:
                        current.status='partial' if current.answer else 'failed'
                        current.processing_token=None
            raise
        except Exception as error:
            if not isinstance(error,AppError):
                import traceback
                logger.error('ai_request_failure %s',json.dumps({'exception_type':type(error).__name__,
                    'frames':[{'function':f.name,'line':f.lineno} for f in traceback.extract_tb(error.__traceback__)]}))
            stage(progress_key,'failed')
            # Failed attempts are retryable, never successful partial answers.
            await self.db.rollback()
            async with self.db.begin():
                current=await self.db.get(AITurn,turn_id,with_for_update=True,populate_existing=True)
                if current is not None and current.processing_token==token:
                    if mode=='v2' and current.answer:
                        current.status='partial';current.processing_token=None
                    else:
                        current.status='failed';current.processing_token=None;current.answer=None;current.sources=[]
            if isinstance(error,AppError): raise
            raise AppError(503,'AI_RETRIEVAL_FAILED','Không thể truy xuất tài liệu. Vui lòng thử lại.') from None
        finally:
            trace.reset(trace_token)
            call_budget.reset(budget_token)

    async def request_progress(self,principal,channel_id,conversation_id,request_id):
        async with self.transaction(principal):
            await self.owned(principal,channel_id,conversation_id)
            value=progress.get((conversation_id,request_id))
            result={'stage':value[0] if value else 'retrieving'}
            from app.core.config import get_settings
            from app.services.ai_chat_v2 import enabled
            if enabled(get_settings(),channel_id):
                from app.models.ai_chat_v2_job import AIChatV2Job
                from app.services.ai_chat_v2_jobs import JobStore
                job=await self.db.scalar(select(AIChatV2Job).join(AITurn).where(
                    AITurn.conversation_id==conversation_id,AITurn.request_id==request_id,
                    AIChatV2Job.user_id==principal.user.id,AIChatV2Job.channel_id==channel_id))
                if job is not None:result['v2_task']=JobStore.public(job)
            return result
