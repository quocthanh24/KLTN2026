"""Durable bounded steps, scoped to conversation owner and current authorization.

Checkpoints contain provenance references, obligations and policy-accepted natural
answers. Document assertions are source-reviewed; general knowledge is not falsely
claimed to be verified against documents. Expiry invalidates, not deletes data.
"""
import copy
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4
from sqlalchemy import select,func
from app.core.exceptions import AppError
from app.models.ai_chat_v2_job import AIChatV2Job
from app.models.ai_conversation import AITurn


class JobStore:
    def __init__(self, service, principal, channel, conversation):
        self.service,self.principal,self.channel,self.conversation = service,principal,channel,conversation
        self.identity = self.token = None
        self.request_id = None

    async def row(self, identity):
        await self.service.owned(self.principal,self.channel,self.conversation)
        row = await self.service.db.scalar(select(AIChatV2Job).join(AITurn).where(
            AIChatV2Job.id==identity,AIChatV2Job.user_id==self.principal.user.id,
            AIChatV2Job.channel_id==self.channel,AITurn.conversation_id==self.conversation)
            .with_for_update(of=AIChatV2Job).execution_options(populate_existing=True))
        if row is None: raise AppError(404,'AI_V2_JOB_NOT_FOUND','Không tìm thấy tác vụ.')
        return row

    @staticmethod
    def public(row):
        checkpoint = row.checkpoint or {}
        value={'id':str(row.id),'status':row.status,'stage':row.stage,'version':row.version,
            'expires_at':row.expires_at.isoformat(),'error_code':row.error_code,
            'progress':checkpoint.get('progress',{}),'resumable':row.status in {'pending','partial','interrupted'}}
        value['reason_codes']=checkpoint.get('reason_codes',[])
        value['source_selection']=checkpoint.get('request_source_selection')
        if row.status=='partial':
            from app.services.ai_source_status import source_failure_message
            value['status_message']=source_failure_message(checkpoint)
        if (row.status=='partial' and row.error_code is None and value['reason_codes']==['UNVERIFIED']
                and not checkpoint.get('response_kind') and not checkpoint.get('accepted')):
            # Older review-parse failures did not persist a distinct reason.
            # They have no verified answer or missing-reading continuation.
            value['reason_codes']=['REVIEW_PROTOCOL_ERROR']
        if 'REVIEW_PROTOCOL_ERROR' in value['reason_codes']:value['resumable']=False
        if set(checkpoint.get('tool_errors',[])) & {'AI_PAGE_NOT_FOUND','AI_V2_ORIGINAL_CAPABILITY','AI_V2_ORIGINAL_LAYOUT'}:
            value['resumable']=False
        if row.status not in {'complete','cancelled','stale'} and row.expires_at<=datetime.now(UTC):
            value.update(status='expired',resumable=False)
        elif row.status in {'running','ready'} and row.lease_until and row.lease_until<=datetime.now(UTC):
            value.update(status='interrupted',resumable=True)
        return value

    async def acquire(self, turn_id, ttl_hours=24):
        now = datetime.now(UTC)
        async with self.service.transaction(self.principal):
            await self.service.owned(self.principal,self.channel,self.conversation)
            turn=await self.service.db.scalar(select(AITurn).where(AITurn.id==turn_id,
                AITurn.conversation_id==self.conversation))
            if turn is None:
                raise AppError(404,'AI_V2_JOB_NOT_FOUND','Không tìm thấy lượt hội thoại của tác vụ.')
            self.request_id=turn.request_id
            row = await self.service.db.scalar(select(AIChatV2Job).where(AIChatV2Job.turn_id==turn_id)
                .with_for_update().execution_options(populate_existing=True))
            if row is None:
                row = AIChatV2Job(turn_id=turn_id,user_id=self.principal.user.id,channel_id=self.channel,
                    expires_at=now+timedelta(hours=ttl_hours),checkpoint={})
                self.service.db.add(row)
                await self.service.db.flush()
            if row.user_id!=self.principal.user.id or row.channel_id!=self.channel:
                raise AppError(404,'AI_V2_JOB_NOT_FOUND','Không tìm thấy tác vụ.')
            if row.expires_at<=now or row.status in {'complete','cancelled','stale'}:
                raise AppError(409,'AI_V2_JOB_TERMINAL','Tác vụ đã hết hạn, bị hủy hoặc nguồn đã thay đổi.')
            if row.lease_until and row.lease_until>now:
                raise AppError(409,'AI_V2_JOB_BUSY','Tác vụ đang được xử lý.')
            from app.core.config import get_settings
            # Cross-process cap for this PostgreSQL deployment, not just an in-memory semaphore.
            await self.service.db.execute(select(func.pg_advisory_xact_lock(780620261)))
            active=await self.service.db.scalar(select(func.count()).select_from(AIChatV2Job).where(
                AIChatV2Job.status.in_(['running','ready']),AIChatV2Job.lease_until>now))
            if active>=get_settings().ai_chat_v2_worker_concurrency:
                raise AppError(429,'AI_V2_WORKER_CAP','Đã đạt giới hạn tác vụ đồng thời; tiến độ cũ được giữ.')
            self.identity,self.token = row.id,uuid4()
            row.lease_token=self.token
            row.lease_until=now+timedelta(seconds=get_settings().ai_turn_timeout_seconds+15)
            row.status='running';row.error_code=None;row.version+=1
            await self.service.db.flush()
            return copy.deepcopy(row.checkpoint)

    async def save(self, checkpoint, stage, status='running', error=None, release=False):
        if len(json.dumps(checkpoint,ensure_ascii=False).encode('utf8'))>2_000_000:
            raise AppError(422,'AI_V2_CHECKPOINT_LIMIT','Checkpoint exceeds the private task size limit.')
        async with self.service.transaction(self.principal):
            row = await self.row(self.identity)
            if (row.lease_token!=self.token or row.status=='cancelled' or not row.lease_until
                    or row.lease_until<=datetime.now(UTC) or row.expires_at<=datetime.now(UTC)):
                raise AppError(409,'AI_V2_JOB_FENCED','Tác vụ đã được thay thế, hủy hoặc hết hạn.')
            row.checkpoint=copy.deepcopy(checkpoint)
            row.stage,row.status,row.error_code=stage,status,error
            row.version+=1
            if release: row.lease_token=row.lease_until=None
            await self.service.db.flush()
            return self.public(row)

    async def inspect(self, identity):
        async with self.service.transaction(self.principal):
            row=await self.row(identity)
            value=self.public(row)
            if row.expires_at<=datetime.now(UTC):
                value.update(status='expired',resumable=False)
            if row.status=='running' and row.lease_until and row.lease_until<=datetime.now(UTC):
                value.update(status='interrupted',resumable=True)
            return value

    async def cancel(self, identity):
        async with self.service.transaction(self.principal):
            row=await self.row(identity)
            if row.status not in {'complete','stale'}:
                row.status='cancelled';row.lease_token=row.lease_until=None;row.version+=1
            return self.public(row)

    async def resume_request(self, identity):
        async with self.service.transaction(self.principal):
            row=await self.row(identity)
            if row.status in {'complete','cancelled','stale'} or row.expires_at<=datetime.now(UTC):
                raise AppError(409,'AI_V2_JOB_TERMINAL','Tác vụ không thể tiếp tục.')
            if row.lease_until and row.lease_until>datetime.now(UTC):
                raise AppError(409,'AI_V2_JOB_BUSY','Tác vụ đang được xử lý.')
            turn=await self.service.db.get(AITurn,row.turn_id,populate_existing=True)
            if turn.status=='processing':
                # Expired job lease fences the old worker; do not also wait for legacy's 5m gate.
                turn.status='partial' if turn.answer else 'failed'
                turn.processing_token=None
            return turn.question,turn.request_id
