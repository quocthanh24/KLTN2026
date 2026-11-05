"""Real PostgreSQL deterministic history fixtures plus real-clock service integration.

No success mocks. Historical timestamps are explicit test data, NOT a live midnight claim.
"""
import asyncio
import unittest
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4
from sqlalchemy import delete, select, text
from sqlalchemy.exc import IntegrityError
from app.models import WorkspaceMember, Workspace, Channel
from app.models.enums import WorkspaceRole, ChannelType
from app.models.study_session import StudySession
from app.models.learning_activity import StudyActivityInterval, PomodoroFocusInterval, PomodoroFocusSegment
from app.models.pomodoro import PomodoroSession, PomodoroCompletion
from app.services.learning_analytics_service import LearningAnalyticsService, streaks
from app.services.study_session_service import StudySessionService, microseconds
from app.services.pomodoro_service import PomodoroService
from app.schemas.pomodoro import PomodoroMutation
from app.realtime.study_room import room_presence
from tests import test_chat_realtime as fixtures
from tests import test_study_sessions as attendance
from tests.test_channel_chat_hardening import HttpAssertions

setUpModule = fixtures.setUpModule
tearDownModule = fixtures.tearDownModule


def instant(value):
    return datetime.fromisoformat(value).astimezone(UTC)


class AnalyticsTests(HttpAssertions, unittest.IsolatedAsyncioTestCase):
    asyncSetUp = attendance.StudySessionTests.asyncSetUp
    asyncTearDown = attendance.StudySessionTests.asyncTearDown
    event = attendance.StudySessionTests.event
    disconnect = attendance.StudySessionTests.disconnect

    async def history(self, spans, room=None, principal=None, tracked=True, status='COMPLETED', duration=None):
        spans = [(instant(a), instant(b)) for a, b in spans]
        async with self.factory() as db, db.begin():
            row = StudySession(user_id=(principal or self.owner).user.id, channel_id=room or self.room,
                status=status, started_at=spans[0][0], last_activity_at=spans[-1][1],
                ended_at=spans[-1][1] if status == 'COMPLETED' else None,
                end_reason='LEFT' if status == 'COMPLETED' else None,
                duration_us=duration if duration is not None else sum(microseconds(b-a) for a,b in spans), analytics_tracked=tracked)
            db.add(row); await db.flush()
            if tracked:
                db.add_all([StudyActivityInterval(session_id=row.id, started_at=a, ended_at=b) for a,b in spans])
            return row.id

    async def overview(self, start='2026-01-01', end='2026-12-31', **kw):
        async with self.factory() as db:
            return await LearningAnalyticsService(db).overview(self.owner, date.fromisoformat(start), date.fromisoformat(end), **kw)

    async def focus(self, spans, status='COMPLETED', room=None):
        spans = [(instant(a), instant(b)) for a,b in spans]
        async with self.factory() as db, db.begin():
            session = PomodoroSession(channel_id=room or self.room, started_by=self.owner.user.id, started_at=spans[0][0])
            db.add(session); await db.flush()
            focus = PomodoroFocusInterval(session_id=session.id, interval=1,status=status,started_at=spans[0][0],ended_at=spans[-1][1])
            db.add(focus); await db.flush()
            db.add_all([PomodoroFocusSegment(focus_id=focus.id,started_at=a,ended_at=b) for a,b in spans])
            return focus.id

    async def test_empty_real_query(self):
        data = await self.overview()
        self.assertEqual(data['total_duration_us'], 0)
        self.assertEqual(data['streak']['longest'], 0)
        self.assertEqual(data['focus']['status'], 'insufficient_data')
        self.assertEqual(len(data['days']), 365)

    async def test_overlap_multi_room_union_and_room_scope(self):
        await self.history([('2026-01-02T10:00:00+07:00','2026-01-02T10:02:00+07:00')])
        await self.history([('2026-01-02T10:01:00+07:00','2026-01-02T10:03:00+07:00')], room=self.other_room)
        self.assertEqual((await self.overview())['total_duration_us'], 180_000_000)
        self.assertEqual((await self.overview(channel_id=self.room))['total_duration_us'], 120_000_000)

    async def test_midnight_reconnect_gap_exact_microseconds(self):
        await self.history([('2026-01-02T23:59:29.123456+07:00','2026-01-03T00:00:10.123456+07:00'),
                            ('2026-01-03T00:01:00+07:00','2026-01-03T00:01:35.000001+07:00')])
        data = await self.overview('2026-01-02','2026-01-03')
        self.assertEqual([d['duration_us'] for d in data['days']], [30_876_544,45_123_457])
        self.assertEqual(data['total_duration_us'],76_000_001)
        self.assertEqual(data['streak']['longest'],0)

    async def test_exact_midnight_not_credited_to_next_day(self):
        await self.history([('2026-01-02T23:59:00+07:00','2026-01-03T00:00:00+07:00')])
        data=await self.overview('2026-01-02','2026-01-03')
        self.assertEqual([d['duration_us'] for d in data['days']],[60_000_000,0])
        self.assertEqual(data['streak']['longest'],1)

    async def test_threshold_after_union_and_both_midnight_days_qualify(self):
        await self.history([('2026-01-02T23:59:00+07:00','2026-01-03T00:01:00+07:00')])
        await self.history([('2026-01-04T10:00:00+07:00','2026-01-04T10:00:59.999999+07:00')])
        data=await self.overview('2026-01-02','2026-01-04')
        self.assertEqual([d['qualified'] for d in data['days']],[True,True,False])
        self.assertEqual(data['streak']['longest'],2)

    async def test_legacy_active_and_corrupt_ledger_excluded(self):
        span=[('2026-01-02T10:00:00+07:00','2026-01-02T10:02:00+07:00')]
        await self.history(span,tracked=False)
        await self.history(span,status='ACTIVE',room=self.other_room)
        await self.history(span,duration=1)
        data=await self.overview(); self.assertEqual(data['total_duration_us'],0)
        self.assertEqual(data['coverage']['excluded_sessions'],2)

    async def test_overlap_inside_corrupt_session_fails_closed(self):
        await self.history([('2026-01-02T10:00:00+07:00','2026-01-02T10:01:00+07:00'),
                            ('2026-01-02T10:00:30+07:00','2026-01-02T10:01:30+07:00')],duration=90_000_000)
        self.assertEqual((await self.overview())['total_duration_us'],0)

    async def test_threshold_streak_global_history_and_today_yesterday(self):
        async with self.factory() as db:
            today=await db.scalar(text("SELECT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Ho_Chi_Minh')::date"))
        for offset in [5,4,3,1]:
            d=today-timedelta(days=offset)
            await self.history([(f'{d}T01:00:00+07:00', f'{d}T01:01:00+07:00')])
        data=await self.overview(str(today),str(today))
        self.assertEqual((data['streak']['current'],data['streak']['longest']),(1,3))
        self.assertEqual(data['total_duration_us'],0)
        await self.history([(f'{today}T00:00:00+07:00',f'{today}T00:01:00+07:00')])
        data=await self.overview(str(today),str(today)); self.assertEqual(data['streak']['current'],2)

    async def test_streak_leap_year_new_year_and_break(self):
        for today,days,expected in [
            (date(2026,1,1),[date(2025,12,30),date(2025,12,31)],(2,2)),
            (date(2024,3,1),[date(2024,2,28),date(2024,2,29),date(2024,3,1)],(3,3)),
            (date(2026,1,5),[date(2026,1,1),date(2026,1,2)],(0,2))]:
            self.assertEqual(streaks(set(days),today),expected)

    async def test_focus_pause_break_late_join_absent_and_room_stop(self):
        spans=[('2026-01-02T10:00:00+07:00','2026-01-02T10:01:00+07:00'),
               ('2026-01-02T10:02:00+07:00','2026-01-02T10:03:00+07:00')]
        await self.history([('2026-01-02T09:59:00+07:00','2026-01-02T10:04:00+07:00')])
        await self.focus(spans)
        data=await self.overview(); self.assertEqual(data['focus']['duration_us'],120_000_000)
        self.assertEqual(data['focus']['completed_intervals'],1)
        # No presence in other room: membership cannot give user a completion.
        await self.focus(spans,room=self.other_room)
        self.assertEqual((await self.overview())['focus']['completed_intervals'],1)
        await self.focus([('2026-01-02T10:03:00+07:00','2026-01-02T10:03:30+07:00')],status='ENDED')
        self.assertEqual((await self.overview())['focus']['early_ended_intervals'],1)

    async def test_focus_partial_participant_not_full_cycle_no_invented_interruption(self):
        await self.history([('2026-01-02T10:00:30+07:00','2026-01-02T10:01:30+07:00')])
        await self.focus([('2026-01-02T10:00:00+07:00','2026-01-02T10:02:00+07:00')])
        focus=(await self.overview())['focus']
        self.assertEqual((focus['duration_us'],focus['completed_intervals'],focus['early_ended_intervals']),(60_000_000,0,0))

    async def test_focus_multi_room_union_and_midnight(self):
        span=[('2026-01-02T23:59:00+07:00','2026-01-03T00:01:00+07:00')]
        for room in [self.room,self.other_room]:
            await self.history(span,room=room); await self.focus(span,room=room)
        data=await self.overview('2026-01-02','2026-01-03')
        self.assertEqual(data['focus']['duration_us'],120_000_000)
        self.assertEqual([d['focus_us'] for d in data['days']],[60_000_000,60_000_000])
        self.assertEqual(data['focus']['completed_intervals'],2)  # counts cycles; minutes unioned

    async def test_legacy_pomodoro_no_guessed_attribution(self):
        await self.history([('2026-01-02T09:00:00+07:00','2026-01-02T11:00:00+07:00')])
        async with self.factory() as db,db.begin():
            p=PomodoroSession(channel_id=self.room,started_at=instant('2026-01-02T10:00:00+07:00'))
            db.add(p); await db.flush()
            db.add(PomodoroCompletion(session_id=p.id,interval=1,focus_seconds=60,completed_at=instant('2026-01-02T10:01:00+07:00')))
        focus=(await self.overview())['focus']; self.assertEqual(focus['legacy_intervals'],1)
        self.assertEqual(focus['status'],'insufficient_data')

    async def test_real_events_concurrent_tabs_reconnect_interval_invariant(self):
        keys=[uuid4() for _ in range(4)]
        rows=await asyncio.gather(*(self.event(k,'join') for k in keys))
        self.assertEqual(len({r['id'] for r in rows}),1)
        await asyncio.gather(*(self.event(k,'state') for k in keys))
        for k in keys: await self.disconnect(k)
        await asyncio.sleep(.04)
        await self.event(keys[0],'join'); final=await self.event(keys[0],'leave')
        async with self.factory() as db:
            intervals=list(await db.scalars(select(StudyActivityInterval).where(StudyActivityInterval.session_id==final['id'])))
        self.assertEqual(len(intervals),2)
        self.assertEqual(sum(microseconds(i.ended_at-i.started_at) for i in intervals),final['duration_us'])
        self.assertEqual((await self.overview())['total_duration_us'],final['duration_us'])

    async def test_api_auth_ranges_timezone_scoping_and_revocation(self):
        await self.history([('2026-01-02T10:00:00+07:00','2026-01-02T10:01:00+07:00')])
        async with self.client() as client:
            path='/users/me/learning-analytics'
            self.assertEqual((await client.get(path)).status_code,401)
            r=await client.get(path,headers=self.headers(self.owner)); self.assertEqual(r.status_code,200,r.text)
            self.assertEqual(r.headers['cache-control'],'private, no-store')
            for query in ['start_date=oops','start_date=2026-02-30','start_date=2026-02-01&end_date=2026-01-01',
                          'start_date=2024-01-01&end_date=2026-01-01','timezone=UTC',"timezone=x%27%3Bselect%201"]:
                self.assertEqual((await client.get(path+'?'+query,headers=self.headers(self.owner))).status_code,422,query)
            r=await client.get(path+f'?workspace_id={self.workspace_id}',headers=self.headers(self.outsider));self.assertEqual(r.status_code,403)
            r=await client.get(path+f'?channel_id={uuid4()}',headers=self.headers(self.owner));self.assertEqual(r.status_code,404)
            r=await client.get(path+f'?user_id={self.owner.user.id}&start_date=2026-01-01&end_date=2026-01-31',headers=self.headers(self.member))
            self.assertEqual(r.json()['data']['total_duration_us'],0)
        async with self.factory() as db,db.begin():
            await db.execute(delete(WorkspaceMember).where(WorkspaceMember.workspace_id==self.workspace_id,WorkspaceMember.user_id==self.owner.user.id))
        self.assertEqual((await self.overview())['total_duration_us'],0)

    async def test_db_constraints_and_atomic_rollback(self):
        row=await self.history([('2026-01-02T10:00:00+07:00','2026-01-02T10:01:00+07:00')])
        for start,end in [('2026-01-02T10:00:00+07:00','2026-01-02T10:00:01+07:00'),
                          ('2026-01-02T11:00:00+07:00','2026-01-02T10:00:00+07:00')]:
            with self.assertRaises(IntegrityError):
                async with self.factory() as db,db.begin():
                    db.add(StudyActivityInterval(session_id=row,started_at=instant(start),ended_at=instant(end)))
        key=uuid4(); active=await self.event(key,'join')
        # Real PostgreSQL fault on this fixture: failed ledger CHECK rolls back duration too.
        async with self.factory() as db,db.begin():
            await db.execute(text("ALTER TABLE study_activity_intervals ADD CONSTRAINT uc38_test_fault CHECK(session_id<>'"+str(active['id'])+"'::uuid)"))
        try:
            with self.assertRaises(IntegrityError): await self.event(key,'state')
            async with self.factory() as db:
                self.assertEqual((await db.get(StudySession,active['id'])).duration_us,0)
        finally:
            async with self.factory() as db,db.begin():
                await db.execute(text('ALTER TABLE study_activity_intervals DROP CONSTRAINT uc38_test_fault'))
        await self.event(key,'leave')

    async def test_channel_workspace_pair_and_cross_workspace_permissions(self):
        async with self.factory() as db,db.begin():
            workspace=Workspace(name='UC38 scope '+uuid4().hex);db.add(workspace);await db.flush()
            self.extra_workspace_ids.append(workspace.id)
            channel=Channel(workspace_id=workspace.id,name='private',type=ChannelType.STUDY_ROOM)
            db.add_all([channel,WorkspaceMember(workspace_id=workspace.id,user_id=self.owner.user.id,role=WorkspaceRole.OWNER)])
            await db.flush(); channel_id=channel.id
        async with self.client() as client:
            r=await client.get(f'/users/me/learning-analytics?workspace_id={self.workspace_id}&channel_id={channel_id}',headers=self.headers(self.owner))
            self.assertEqual(r.status_code,404)
            r=await client.get(f'/users/me/learning-analytics?channel_id={channel_id}',headers=self.headers(self.member))
            self.assertEqual(r.status_code,403)

    async def test_real_clock_pomodoro_pause_resume_end_and_completion(self):
        key=uuid4(); await self.event(key,'join')
        presence=object(); room_presence.update(presence,self.room,SimpleNamespace(id=self.owner.user.id,full_name='UC38'),'join')
        async def mutate(action, revision, seconds=1):
            async with self.factory() as db:
                return await PomodoroService(db).current(self.owner,self.room,PomodoroMutation(action=action,revision=revision,focus_seconds=seconds,break_seconds=30))
        try:
            state=await mutate('start',0)
            await asyncio.sleep(.1); state=await mutate('pause',state['revision'])
            await asyncio.sleep(.1); state=await mutate('resume',state['revision'])
            await asyncio.sleep(1.1)
            async with self.factory() as db:
                state=await PomodoroService(db).current(self.owner,self.room)
            self.assertEqual(state['phase'],'break')
            state=await mutate('end',state['revision'])
            state=await mutate('start',state['revision'],30)
            await asyncio.sleep(.1); await mutate('end',state['revision'])
            await self.event(key,'leave')
            focus=(await self.overview())['focus']
            self.assertEqual((focus['completed_intervals'],focus['early_ended_intervals']),(1,1))
            self.assertGreaterEqual(focus['duration_us'],1_000_000)
            self.assertLess(focus['duration_us'],1_500_000)
        finally:
            room_presence.disconnect(presence)
