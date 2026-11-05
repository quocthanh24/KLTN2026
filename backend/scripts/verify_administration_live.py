"""Scoped Phase 7C fixture provisioning, real MinIO evidence and independent SQL checks."""
import asyncio
import hashlib
import json
import re
import sys
from collections import defaultdict
from datetime import datetime,timedelta,timezone
from sqlalchemy import select,delete,text,or_
from app.db.session import session_factory,engine
from app.models import User,Workspace,WorkspaceMember,WorkspaceInvitation,Channel,AuthSession,LearningDocument,DocumentChunk,ChatAttachment,ChatMessage
from app.models.moderation import ViolationReport,AdministrationAudit


def union_duration(spans):
    merged=[]
    for start,end in sorted(spans):
        if merged and start<=merged[-1][1]:merged[-1][1]=max(merged[-1][1],end)
        elif start<end:merged.append([start,end])
    return sum((b-a)//timedelta(microseconds=1) for a,b in merged)


async def main(data):
    marker=data['marker'];assert re.fullmatch(r'phase7c-[0-9a-f-]{36}',marker)
    roles=('admin','admin2','owner','wadmin','member','outsider')
    emails=[f'{marker}-{role}@example.com' for role in roles]
    async with session_factory() as db,db.begin():
        users=list(await db.scalars(select(User).where(User.email.in_(emails))))
        workspace=await db.scalar(select(Workspace).where(Workspace.name==marker).execution_options(include_moderated=True))
        if data['action']=='grant':
            assert data['role'] in ('admin','admin2')
            user=next(u for u in users if u.email==f"{marker}-{data['role']}@example.com")
            identity=user.id
        else:
            assert workspace is not None
            channel_ids=list(await db.scalars(select(Channel.id).where(Channel.workspace_id==workspace.id)))
            docs=list(await db.scalars(select(LearningDocument).where(LearningDocument.channel_id.in_(channel_ids)).execution_options(include_moderated=True)))
            user_ids=[u.id for u in users]
            attachment_keys=list(await db.scalars(select(ChatAttachment.storage_key).join(ChatMessage,ChatMessage.id==ChatAttachment.message_id).where(ChatMessage.channel_id.in_(channel_ids)).execution_options(include_moderated=True)))
    if data['action']=='grant':
        from scripts.manage_system_admin import change
        await change(identity,True,'Phase 7C isolated live fixture administrator')
        print('Scoped fixture system administrator provisioned by audited trusted CLI');return
    from app.services.storage_service import get_storage_service
    storage=get_storage_service()
    if data['action']=='cleanup':
        for doc in docs:await storage.delete(doc.object_key)
        for key in attachment_keys:await storage.delete(key)
        async with session_factory() as db,db.begin():
            report_ids=list(await db.scalars(select(ViolationReport.id).where(ViolationReport.workspace_id==workspace.id)))
            await db.execute(delete(AdministrationAudit).where(or_(AdministrationAudit.actor_id.in_(user_ids),AdministrationAudit.target_id.in_([workspace.id,*user_ids,*report_ids]))))
            await db.execute(delete(ViolationReport).where(ViolationReport.workspace_id==workspace.id))
            await db.execute(delete(WorkspaceInvitation).where(WorkspaceInvitation.workspace_id==workspace.id))
            await db.execute(delete(WorkspaceMember).where(WorkspaceMember.workspace_id==workspace.id))
            await db.execute(delete(Workspace).where(Workspace.id==workspace.id))
            await db.execute(delete(AuthSession).where(AuthSession.user_id.in_(user_ids)))
            await db.execute(delete(User).where(User.id.in_(user_ids),User.email.in_(emails)))
        print('Scoped Phase 7C DB/MinIO fixtures cleaned; other records unchanged')
    elif data['action']=='rag':
        from app.services.auth_service import AuthService
        from app.services.document_retrieval import DocumentRetrieval
        async with session_factory() as db:
            principal=await AuthService(db).authenticate(data['token'])
            # Read actual existing embedding, then exercise authorized retrieval; no provider calls.
            async with db.begin():
                chunk=await db.scalar(select(DocumentChunk).where(DocumentChunk.document_id==__import__('uuid').UUID(data['document_id'])))
                vector=list(chunk.embedding) if chunk is not None else [0.0]*384
            hits=await DocumentRetrieval(db).search(principal,__import__('uuid').UUID(data['channel_id']),vector)
            print(json.dumps({'hits':[str(c.document_id) for c in hits],'real_postgres_vector_query':True}))
    else:
        assert data['action']=='inspect'
        result={'documents':[]}
        for doc in docs:
            raw=await storage.read_document(doc.object_key,200*1024*1024)
            result['documents'].append({'id':str(doc.id),'stored_sha256':hashlib.sha256(raw).hexdigest(),'metadata_sha256':doc.checksum,'moderated':bool(doc.moderated_at),'deleted':bool(doc.deleted_at),'original_retained':True})
        async with session_factory() as db,db.begin():
            result['users']=[{k:str(getattr(u,k)) for k in ('id','email','status','system_role')} for u in users]
            result['reports']=[{k:getattr(r,k) for k in ('id','target_type','target_id','reason','status','outcome','action','resolved_by','resolved_at')} for r in await db.scalars(select(ViolationReport).where(ViolationReport.workspace_id==workspace.id))]
            result['audit']=[{k:getattr(a,k) for k in ('id','target_type','target_id','action','before','after','created_at')} for a in await db.scalars(select(AdministrationAudit).where(or_(AdministrationAudit.actor_id.in_(user_ids),AdministrationAudit.target_id.in_(user_ids))))]
            lo,hi=(await db.execute(text("SELECT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Ho_Chi_Minh')::date::timestamp AT TIME ZONE 'Asia/Ho_Chi_Minh',((CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Ho_Chi_Minh')::date+1)::timestamp AT TIME ZONE 'Asia/Ho_Chi_Minh'"))).one()
            # Independent arithmetic, no production analytics SQL/helper reuse.
            sessions=[dict(r) for r in (await db.execute(text("SELECT * FROM study_sessions WHERE status='COMPLETED' AND ended_at<=CURRENT_TIMESTAMP AND started_at<:hi AND ended_at>=:lo"),{'lo':lo,'hi':hi})).mappings()]
            intervals=defaultdict(list)
            for r in (await db.execute(text("SELECT i.* FROM study_activity_intervals i JOIN study_sessions s ON s.id=i.session_id WHERE s.status='COMPLETED' AND s.started_at<:hi AND s.ended_at>=:lo"),{'lo':lo,'hi':hi})).mappings():intervals[r['session_id']].append((r['started_at'],r['ended_at']))
            by_user=defaultdict(list);excluded=0
            for s in sessions:
                spans=intervals[s['id']]
                duration=sum((b-a)//timedelta(microseconds=1) for a,b in spans)
                valid=(s['analytics_tracked'] and s['ended_at']==s['last_activity_at'] and s['duration_us']==duration==union_duration(spans) and all(a>=s['started_at'] and b<=s['ended_at'] for a,b in spans))
                if valid:by_user[s['user_id']].extend((max(a,lo),min(b,hi)) for a,b in spans if a<hi and b>lo)
                elif s['duration_us']>0:excluded+=1
            result['independent_sql']={
                'users':await db.scalar(text('SELECT count(*) FROM users')),
                'workspaces':await db.scalar(text('SELECT count(*) FROM workspaces WHERE deleted_at IS NULL')),
                'documents':await db.scalar(text('SELECT count(*) FROM learning_documents')),
                'reports_pending':await db.scalar(text("SELECT count(*) FROM violation_reports WHERE status='PENDING'")),
                'reports_resolved':await db.scalar(text("SELECT count(*) FROM violation_reports WHERE status='RESOLVED'")),
                'study_duration_us_today':sum(union_duration(v) for v in by_user.values()),'study_excluded_today':excluded}
        print(json.dumps(result,default=str))
    await engine.dispose()


if __name__=='__main__':asyncio.run(main(json.load(sys.stdin)))
