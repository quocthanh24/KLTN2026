"""Inspect/revoke/clean only exact-run invitation verification fixtures."""
import asyncio
import hashlib
import json
import re
import sys
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.models import AuthSession, User, Workspace, WorkspaceInvitation, WorkspaceMember
from app.models.enums import InvitationStatus


async def main(data):
    marker = data['marker']
    assert re.fullmatch(r'invitation-[0-9a-f-]{36}', marker)
    emails = [f'{marker}-{role}@example.com' for role in ('owner', 'member', 'outsider')]
    engine = create_async_engine(str(get_settings().database_url))
    try:
        async with async_sessionmaker(engine)() as db, db.begin():
            users = list(await db.scalars(select(User.id).where(User.email.in_(emails))))
            rooms = list(await db.scalars(select(Workspace.id).where(Workspace.name == marker)))
            assert len(rooms) <= 1
            if data['action'] == 'cleanup':
                await db.execute(delete(WorkspaceInvitation).where(WorkspaceInvitation.workspace_id.in_(rooms)))
                await db.execute(delete(WorkspaceMember).where(WorkspaceMember.workspace_id.in_(rooms)))
                await db.execute(delete(Workspace).where(Workspace.id.in_(rooms), Workspace.name == marker))
                await db.execute(delete(AuthSession).where(AuthSession.user_id.in_(users)))
                await db.execute(delete(User).where(User.id.in_(users), User.email.in_(emails)))
                print('Scoped invitation fixture cleanup PASS')
                return
            invitation = await db.scalar(select(WorkspaceInvitation).where(
                WorkspaceInvitation.id == UUID(data['id']), WorkspaceInvitation.workspace_id.in_(rooms)))
            assert invitation is not None
            if data['action'] == 'revoke':
                invitation.status = InvitationStatus.REVOKED
                print('Revoked exact-run invitation fixture')
                return
            assert data['action'] == 'inspect'
            assert invitation.token_hash == hashlib.sha256(data['token'].encode()).hexdigest()
            assert invitation.invitee_user_id is None and invitation.expires_at is None
            assert invitation.status == data.get('status', 'PENDING')
            print('DB PASS: exact token SHA-256, general recipient NULL, expiry NULL, status ' + str(invitation.status))
    finally:
        await engine.dispose()


if __name__ == '__main__':
    try:
        asyncio.run(main(json.load(sys.stdin)))
    except Exception as error:
        print(type(error).__name__)
        sys.exit(1)
