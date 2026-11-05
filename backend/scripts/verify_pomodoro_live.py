"""Read interval evidence or remove only a uniquely named UC27 live fixture."""
import asyncio
import json
import re
import sys
from sqlalchemy import delete, func, select
from app.db.session import session_factory, engine
from app.models import User, Workspace, WorkspaceMember, WorkspaceInvitation, AuthSession
from app.models.pomodoro import PomodoroCompletion, PomodoroSession


async def main(data):
    marker = data["marker"]
    assert re.fullmatch(r"phase4e-[0-9a-f-]{36}", marker)
    emails = [f"{marker}-{role}@example.com" for role in ("owner", "member")]
    async with session_factory() as db, db.begin():
        if data["action"] == "count":
            from uuid import UUID
            session_id = UUID(data["session_id"])
            session = await db.get(PomodoroSession, session_id)
            from app.models import Channel
            channel = await db.get(Channel, session.channel_id)
            workspace = await db.get(Workspace, channel.workspace_id)
            assert workspace.name == marker
            print(await db.scalar(select(func.count()).select_from(PomodoroCompletion).where(PomodoroCompletion.session_id == session_id)))
        else:
            assert data["action"] == "cleanup"
            users = list(await db.scalars(select(User.id).where(User.email.in_(emails))))
            workspaces = list(await db.scalars(select(Workspace.id).where(Workspace.name == marker)))
            assert len(workspaces) <= 1
            await db.execute(delete(WorkspaceInvitation).where(WorkspaceInvitation.workspace_id.in_(workspaces)))
            await db.execute(delete(WorkspaceMember).where(WorkspaceMember.workspace_id.in_(workspaces)))
            await db.execute(delete(Workspace).where(Workspace.id.in_(workspaces), Workspace.name == marker))
            await db.execute(delete(AuthSession).where(AuthSession.user_id.in_(users)))
            await db.execute(delete(User).where(User.id.in_(users), User.email.in_(emails)))
            print("Scoped Phase 4E cleanup PASS")
    await engine.dispose()


if __name__ == "__main__":
    try:
        asyncio.run(main(json.load(sys.stdin)))
    except Exception as error:
        print(type(error).__name__)
        sys.exit(1)
