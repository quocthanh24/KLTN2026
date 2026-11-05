"""Remove only the disposable multimodal workspace/account and its objects."""
import asyncio
import json
from pathlib import Path
from uuid import UUID
from scripts import run_multimodal_verification_server
from sqlalchemy import select,delete,func
from app.db.session import session_factory
from app.models import Workspace,Channel,User,AuthSession,WorkspaceMember
from app.models.learning_document import LearningDocument
from app.models.ai_conversation import AIConversation
from app.services.storage_service import get_storage_service


async def main():
    state_path=Path('D:/Temp/kltn-multimodal/resume.json')
    state=json.loads(state_path.read_text(encoding='utf-8'))
    keys=[]
    async with session_factory() as db,db.begin():
        workspace=await db.get(Workspace,UUID(state['workspace']))
        user=await db.get(User,UUID(state['user']))
        if workspace is None or not workspace.name.startswith('multimodal-'):
            raise RuntimeError('Refusing cleanup: unexpected workspace')
        if user is None or user.email!=state['email'] or not user.email.startswith('multimodal-'):
            raise RuntimeError('Refusing cleanup: unexpected account')
        documents=list(await db.scalars(select(LearningDocument).join(Channel).where(Channel.workspace_id==workspace.id)))
        keys=[d.object_key for d in documents]
        channel_ids=select(Channel.id).where(Channel.workspace_id==workspace.id)
        await db.execute(delete(AIConversation).where(AIConversation.channel_id.in_(channel_ids)))
        await db.execute(delete(LearningDocument).where(LearningDocument.channel_id.in_(channel_ids)))
        await db.execute(delete(Channel).where(Channel.workspace_id==workspace.id))
        await db.execute(delete(WorkspaceMember).where(WorkspaceMember.workspace_id==workspace.id))
        await db.execute(delete(Workspace).where(Workspace.id==workspace.id))
        remaining=await db.scalar(select(func.count()).select_from(WorkspaceMember).where(WorkspaceMember.user_id==user.id))
        if remaining:
            raise RuntimeError('Unexpected membership; rollback cleanup')
        await db.execute(delete(AuthSession).where(AuthSession.user_id==user.id))
        await db.execute(delete(User).where(User.id==user.id))
    for key in keys:
        await get_storage_service().delete(key)
    result={'workspace_removed':state['workspace'],'test_objects_removed':len(keys),'account_removed':True}
    out=Path(__file__).resolve().parents[2]/'docs/progress/evidence/multimodal-document-rag/cleanup.json'
    out.write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result))
    # Explicit temporary artifacts only; preserve durable usage ledgers.
    for name in ('resume.json','actual-scan.pdf','actual-page1.jpg','actual-page13.jpg',
                 'page1-extracted.json','page13-warning.txt','native.pdf','native.docx',
                 'chart-scan.pdf','resume-debug.png','debug.cjs'):
        candidate=state_path.parent/name
        if candidate.is_file():
            candidate.unlink()


if __name__=='__main__':
    asyncio.run(main())
