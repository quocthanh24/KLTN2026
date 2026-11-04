"""Real DB snapshots/strictly scoped fixture cleanup for Phase 7B Chrome runs."""
import asyncio
import json
import re
import sys
from sqlalchemy import delete, select, text
from app.db.session import session_factory, engine
from app.models import AuthSession, Channel, User, Workspace, WorkspaceInvitation, WorkspaceMember


async def main(data):
    marker = data['marker']
    assert re.fullmatch(r'phase7b-[0-9a-f-]{36}', marker)
    async with session_factory() as db, db.begin():
        workspace = await db.scalar(select(Workspace).where(Workspace.name == marker))
        assert workspace is not None
        channels = list(await db.scalars(select(Channel.id).where(Channel.workspace_id == workspace.id)))
        emails = [f'{marker}-{role}@example.com' for role in ('owner','member','outsider')]
        users = list(await db.scalars(select(User.id).where(User.email.in_(emails))))
        if data['action'] == 'cleanup':
            await db.execute(delete(WorkspaceInvitation).where(WorkspaceInvitation.workspace_id == workspace.id))
            await db.execute(delete(WorkspaceMember).where(WorkspaceMember.workspace_id == workspace.id))
            await db.execute(delete(Workspace).where(Workspace.id == workspace.id))
            await db.execute(delete(AuthSession).where(AuthSession.user_id.in_(users)))
            await db.execute(delete(User).where(User.id.in_(users),User.email.in_(emails)))
            print('Scoped Phase 7B fixtures cleaned; pre-existing data preserved')
        else:
            assert data['action'] == 'inspect'
            result = {}
            queries = {
                'sessions': 'SELECT s.*, COALESCE((SELECT sum(EXTRACT(EPOCH FROM ended_at-started_at)*1000000)::bigint FROM study_activity_intervals WHERE session_id=s.id),0) ledger_duration_us FROM study_sessions s WHERE channel_id=ANY(:channels) ORDER BY started_at,id',
                'intervals': 'SELECT i.*,s.user_id,s.channel_id FROM study_activity_intervals i JOIN study_sessions s ON s.id=i.session_id WHERE s.channel_id=ANY(:channels) ORDER BY i.started_at,i.id',
                'focus': 'SELECT f.*,p.channel_id FROM pomodoro_focus_intervals f JOIN pomodoro_sessions p ON p.id=f.session_id WHERE p.channel_id=ANY(:channels) ORDER BY f.started_at,f.id',
                'focus_segments': 'SELECT g.* FROM pomodoro_focus_segments g JOIN pomodoro_focus_intervals f ON f.id=g.focus_id JOIN pomodoro_sessions p ON p.id=f.session_id WHERE p.channel_id=ANY(:channels) ORDER BY g.started_at,g.id',
                'completions': 'SELECT c.* FROM pomodoro_completions c JOIN pomodoro_sessions p ON p.id=c.session_id WHERE p.channel_id=ANY(:channels) ORDER BY c.completed_at,c.id',
            }
            for name, sql in queries.items():
                result[name] = [dict(r) for r in (await db.execute(text(sql), {'channels':channels})).mappings()]
            print(json.dumps(result,default=str))
    await engine.dispose()


if __name__ == '__main__':
    asyncio.run(main(json.load(sys.stdin)))
