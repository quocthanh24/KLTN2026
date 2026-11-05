"""Scoped read/delete/cleanup evidence for Phase 5B browser-created fixtures only."""
import asyncio
import hashlib
import json
import re
import sys
from sqlalchemy import delete, select
from app.db.session import engine, session_factory
from app.models import AuthSession, Channel, User, Workspace, WorkspaceInvitation, WorkspaceMember
from app.models.learning_document import DocumentChunk, LearningDocument
from app.services.storage_service import get_storage_service
from botocore.exceptions import ClientError


async def main(data):
    marker = data['marker']
    assert re.fullmatch(r'phase5b-[0-9a-f-]{36}', marker)
    if data['action'] == 'word_fixture':
        import base64
        from tests.test_document_rag import word_bytes
        print(base64.b64encode(word_bytes()).decode())
        return
    storage = get_storage_service()
    async with session_factory() as db:
        workspace = await db.scalar(select(Workspace).where(Workspace.name == marker))
        channels = list(await db.scalars(select(Channel).where(Channel.workspace_id == workspace.id))) if workspace else []
        documents = list(await db.scalars(select(LearningDocument).where(LearningDocument.channel_id.in_([c.id for c in channels]))))
        if data['action'] == 'cleanup':
            for document in documents:
                await storage.delete(document.object_key)
            users = list(await db.scalars(select(User).where(User.email.in_([f'{marker}-{role}@example.com' for role in ('owner', 'member', 'outsider')]))))
            if workspace:
                await db.execute(delete(WorkspaceInvitation).where(WorkspaceInvitation.workspace_id == workspace.id))
                await db.execute(delete(WorkspaceMember).where(WorkspaceMember.workspace_id == workspace.id))
                await db.execute(delete(Workspace).where(Workspace.id == workspace.id))
            if users:
                await db.execute(delete(AuthSession).where(AuthSession.user_id.in_([u.id for u in users])))
                await db.execute(delete(User).where(User.id.in_([u.id for u in users])))
            await db.commit()
            print('PASS: scoped Phase 5B fixture cleanup; existing data preserved')
            return
        document = next(d for d in documents if str(d.id) == data['document_id'])
        if data['action'] == 'remove_original':
            await storage.delete(document.object_key)
            print('PASS: removed only the selected live fixture original')
            return
        chunks = list(await db.scalars(select(DocumentChunk).where(DocumentChunk.document_id == document.id).order_by(DocumentChunk.ordinal)))
        try:
            original = await storage.read_document(document.object_key, 25 * 1024 * 1024)
            original_sha = hashlib.sha256(original).hexdigest()
        except ClientError as error:
            assert error.response.get('Error', {}).get('Code') in {'NoSuchKey', 'NotFound', '404'}
            original_sha = None
        eligible = list(await db.scalars(select(DocumentChunk.document_id).join(LearningDocument).where(
            LearningDocument.channel_id == document.channel_id, LearningDocument.deleted_at.is_(None), LearningDocument.status == 'READY').distinct()))
        print(json.dumps({'name': document.name, 'description': document.description, 'status': document.status.value,
            'deleted': document.deleted_at is not None, 'object_cleaned': document.object_deleted_at is not None,
            'original_sha256': original_sha, 'checksum': document.checksum, 'chunk_ids': [str(c.id) for c in chunks],
            'eligible_documents': [str(d) for d in eligible]}))


if __name__ == '__main__':
    try:
        asyncio.run(main(json.load(sys.stdin)))
    except Exception as error:
        print(type(error).__name__)
        sys.exit(1)
