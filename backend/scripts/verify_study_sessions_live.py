"""Read-only UC37 DB evidence and strictly scoped cleanup of this run's fixtures."""
import asyncio
import json
import re
import sys
from sqlalchemy import delete, func, select
from app.db.session import session_factory, engine
from app.models import AuthSession, Channel, User, Workspace, WorkspaceInvitation, WorkspaceMember
from app.models.study_session import StudySession, StudySessionConnection
from app.models.pomodoro import PomodoroCompletion, PomodoroSession
from app.services.study_session_service import StudySessionService, microseconds


async def main(data):
    marker = data['marker']
    assert re.fullmatch(r'phase7a-[0-9a-f-]{36}', marker)
    async with session_factory() as db, db.begin():
        workspace = await db.scalar(select(Workspace).where(Workspace.name == marker))
        assert workspace is not None
        channels = list(await db.scalars(select(Channel.id).where(Channel.workspace_id == workspace.id)))
        emails = [f'{marker}-{role}@example.com' for role in ('owner', 'member', 'outsider')]
        users = list(await db.scalars(select(User.id).where(User.email.in_(emails))))
        if data['action'] == 'cleanup':
            await db.execute(delete(WorkspaceInvitation).where(WorkspaceInvitation.workspace_id == workspace.id))
            await db.execute(delete(WorkspaceMember).where(WorkspaceMember.workspace_id == workspace.id))
            await db.execute(delete(Workspace).where(Workspace.id == workspace.id))
            await db.execute(delete(AuthSession).where(AuthSession.user_id.in_(users)))
            await db.execute(delete(User).where(User.id.in_(users), User.email.in_(emails)))
            print('Scoped UC37 fixtures cleaned; existing data preserved')
        else:
            assert data['action'] == 'inspect'
            rows = list(await db.scalars(select(StudySession).where(StudySession.channel_id.in_(channels))
                                        .order_by(StudySession.started_at)))
            result = {'sessions': [StudySessionService.public(r) | {'user_id': r.user_id,
                'wall_span_us': microseconds(r.ended_at-r.started_at) if r.ended_at else None} for r in rows],
                'connections': await db.scalar(select(func.count()).select_from(StudySessionConnection)
                    .join(StudySession).where(StudySession.channel_id.in_(channels))),
                'pomodoro_completions': await db.scalar(select(func.count()).select_from(PomodoroCompletion)
                    .join(PomodoroSession, PomodoroCompletion.session_id == PomodoroSession.id)
                    .where(PomodoroSession.channel_id.in_(channels)))}
            print(json.dumps(result, default=str))
    await engine.dispose()


if __name__ == '__main__': asyncio.run(main(json.load(sys.stdin)))
