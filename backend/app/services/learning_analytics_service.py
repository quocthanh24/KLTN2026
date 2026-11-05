"""Personal analytics over reconciled, completed attendance. PostgreSQL interval union."""
from datetime import date, timedelta
from sqlalchemy import text
from app.core.exceptions import AppError
from app.services.channel_service import ChannelService

DEFAULT_TIMEZONE = 'Asia/Ho_Chi_Minh'
DAILY_QUALIFYING_US = 60_000_000
MAX_RANGE_DAYS = 366

# One MVCC statement: permissions, attendance, focus and historical coverage agree.
# range_agg/unnest uses PostgreSQL 14+ multiranges; all parameters remain bound.
ANALYTICS_SQL = """
WITH permitted AS MATERIALIZED (
 SELECT c.id FROM channels c JOIN workspace_members m ON m.workspace_id=c.workspace_id
 JOIN workspaces w ON w.id=c.workspace_id AND w.deleted_at IS NULL AND w.disabled_at IS NULL
 WHERE m.user_id=:user_id AND (CAST(:workspace_id AS uuid) IS NULL OR c.workspace_id=:workspace_id)
 AND (CAST(:channel_id AS uuid) IS NULL OR c.id=:channel_id)
), sessions AS MATERIALIZED (
 SELECT s.* FROM study_sessions s JOIN permitted p ON p.id=s.channel_id
 WHERE s.user_id=:user_id AND s.status='COMPLETED' AND s.ended_at<=CURRENT_TIMESTAMP
 AND s.ended_at=s.last_activity_at AND s.ended_at>=s.started_at AND s.duration_us>=0
 AND s.duration_us<=EXTRACT(EPOCH FROM s.ended_at-s.started_at)*1000000
), ledger AS (
 SELECT s.id,s.channel_id,s.duration_us,range_agg(tstzrange(i.started_at,i.ended_at,'[)')) spans
 FROM sessions s JOIN study_activity_intervals i ON i.session_id=s.id
 WHERE s.analytics_tracked
 GROUP BY s.id,s.channel_id,s.duration_us,s.started_at,s.ended_at
 HAVING min(i.started_at)>=s.started_at AND max(i.ended_at)<=s.ended_at
 AND sum(EXTRACT(EPOCH FROM i.ended_at-i.started_at)*1000000)=s.duration_us
), valid AS MATERIALIZED (
 SELECT * FROM ledger l WHERE
 (SELECT sum(EXTRACT(EPOCH FROM upper(r)-lower(r))*1000000) FROM unnest(l.spans) r)=l.duration_us
), attendance AS MATERIALIZED (
 SELECT channel_id,r FROM valid CROSS JOIN LATERAL unnest(spans) r
), attendance_by_channel AS (
 SELECT channel_id,range_agg(r) spans FROM attendance GROUP BY channel_id
), merged AS (
 SELECT unnest(range_agg(r)) r FROM attendance
), daily AS MATERIALIZED (
 SELECT (d AT TIME ZONE :timezone)::date AS day,
 sum(EXTRACT(EPOCH FROM least(upper(r),d+interval '1 day')-greatest(lower(r),d))*1000000)::bigint duration_us
 FROM merged CROSS JOIN LATERAL generate_series(
   (lower(r) AT TIME ZONE :timezone)::date::timestamp AT TIME ZONE :timezone,
   ((upper(r)-interval '1 microsecond') AT TIME ZONE :timezone)::date::timestamp AT TIME ZONE :timezone,
   interval '1 day', :timezone) d
 GROUP BY day
), focus_segments AS MATERIALIZED (
 SELECT f.id,f.status,f.ended_at,p.channel_id,tstzrange(g.started_at,g.ended_at,'[)') r
 FROM pomodoro_focus_intervals f JOIN pomodoro_sessions p ON p.id=f.session_id
 JOIN permitted a ON a.id=p.channel_id JOIN pomodoro_focus_segments g ON g.focus_id=f.id
 WHERE f.status IN ('COMPLETED','ENDED') AND f.ended_at<=CURRENT_TIMESTAMP
 AND f.started_at < (CAST(:end_date AS date)+1)::timestamp AT TIME ZONE :timezone
 AND f.ended_at >= CAST(:start_date AS date)::timestamp AT TIME ZONE :timezone
 AND g.ended_at>g.started_at
), focus_overlap AS MATERIALIZED (
 SELECT f.id,f.status,f.ended_at,unnest(a.spans*tstzmultirange(f.r)) r FROM focus_segments f
 JOIN attendance_by_channel a ON a.channel_id=f.channel_id
), focus_merged AS (
 SELECT unnest(range_agg(r)) r FROM focus_overlap
), focus_days AS (
 SELECT (d AT TIME ZONE :timezone)::date AS day,
 sum(EXTRACT(EPOCH FROM least(upper(r),d+interval '1 day')-greatest(lower(r),d))*1000000)::bigint duration_us
 FROM focus_merged CROSS JOIN LATERAL generate_series(
   (lower(r) AT TIME ZONE :timezone)::date::timestamp AT TIME ZONE :timezone,
   ((upper(r)-interval '1 microsecond') AT TIME ZONE :timezone)::date::timestamp AT TIME ZONE :timezone,
   interval '1 day', :timezone) d
 GROUP BY day
), cycle_totals AS (
 SELECT id,sum(EXTRACT(EPOCH FROM upper(r)-lower(r))*1000000) duration_us
 FROM focus_segments GROUP BY id
), user_cycles AS (
 SELECT id,status,ended_at,sum(EXTRACT(EPOCH FROM upper(r)-lower(r))*1000000) duration_us
 FROM (SELECT id,status,ended_at,unnest(range_agg(r)) r FROM focus_overlap GROUP BY id,status,ended_at) u
 GROUP BY id,status,ended_at
)
SELECT
 COALESCE((SELECT jsonb_agg(jsonb_build_object('day',day,'duration_us',duration_us) ORDER BY day) FROM daily),'[]'::jsonb) days,
 COALESCE((SELECT jsonb_agg(jsonb_build_object('day',day,'duration_us',duration_us) ORDER BY day) FROM focus_days
 WHERE day BETWEEN :start_date AND :end_date),'[]'::jsonb) focus_days,
 (SELECT count(*) FROM user_cycles u JOIN cycle_totals t USING(id)
 WHERE u.status='COMPLETED' AND u.duration_us=t.duration_us AND t.duration_us>0
 AND (u.ended_at AT TIME ZONE :timezone)::date BETWEEN :start_date AND :end_date) completed_focus,
 (SELECT count(*) FROM user_cycles WHERE status='ENDED'
 AND (ended_at AT TIME ZONE :timezone)::date BETWEEN :start_date AND :end_date) early_ended_focus,
 (SELECT count(*) FROM sessions s WHERE s.duration_us>0 AND NOT EXISTS(SELECT 1 FROM valid v WHERE v.id=s.id)
 AND s.started_at < (CAST(:end_date AS date)+1)::timestamp AT TIME ZONE :timezone
 AND s.ended_at >= CAST(:start_date AS date)::timestamp AT TIME ZONE :timezone) excluded_sessions,
 (SELECT count(*) FROM sessions s WHERE s.duration_us>0 AND NOT EXISTS(SELECT 1 FROM valid v WHERE v.id=s.id)) excluded_history_sessions,
 (SELECT count(*) FROM pomodoro_completions pc JOIN pomodoro_sessions p ON p.id=pc.session_id
 JOIN permitted a ON a.id=p.channel_id WHERE NOT EXISTS(SELECT 1 FROM pomodoro_focus_intervals f
 WHERE f.session_id=pc.session_id AND f.interval=pc.interval)
 AND EXISTS(SELECT 1 FROM sessions s WHERE s.channel_id=p.channel_id
 AND s.started_at<pc.completed_at AND s.ended_at>p.started_at)
 AND (pc.completed_at AT TIME ZONE :timezone)::date BETWEEN :start_date AND :end_date) legacy_focus,
 (CURRENT_TIMESTAMP AT TIME ZONE :timezone)::date today
"""


def streaks(qualified, today):
    longest = run = 0
    previous = None
    for day in sorted(qualified):
        run = run + 1 if previous and day == previous + timedelta(days=1) else 1
        longest = max(longest, run)
        previous = day
    anchor = today if today in qualified else today - timedelta(days=1)
    current = 0
    while anchor in qualified:
        current += 1
        anchor -= timedelta(days=1)
    return current, longest


class LearningAnalyticsService(ChannelService):
    async def overview(self, principal, start_date=None, end_date=None, timezone=DEFAULT_TIMEZONE,
                       workspace_id=None, channel_id=None):
        # Fixed IANA timezone in this phase; accepting arbitrary offsets/DST silently is unsafe.
        if timezone != DEFAULT_TIMEZONE:
            raise AppError(422, 'UNSUPPORTED_TIMEZONE', 'Thống kê sử dụng Asia/Ho_Chi_Minh')
        async with self.transaction(principal):
            if channel_id:
                channel = await self.channel_access(channel_id, principal.user.id)
                if workspace_id and channel.workspace_id != workspace_id:
                    raise AppError(404, 'CHANNEL_NOT_FOUND', 'Channel không thuộc Workspace')
            elif workspace_id:
                await self.workspace_access(workspace_id, principal.user.id)
            today = await self.db.scalar(text('SELECT (CURRENT_TIMESTAMP AT TIME ZONE :tz)::date'), {'tz': timezone})
            end_date = end_date or today
            start_date = start_date or end_date - timedelta(days=6)
            if start_date > end_date or (end_date - start_date).days >= MAX_RANGE_DAYS:
                raise AppError(422, 'INVALID_ANALYTICS_RANGE', 'Khoảng ngày phải tăng dần và không quá 366 ngày')
            row = (await self.db.execute(text(ANALYTICS_SQL), dict(user_id=principal.user.id,
                workspace_id=workspace_id, channel_id=channel_id, timezone=timezone,
                start_date=start_date, end_date=end_date))).mappings().one()
            daily = {date.fromisoformat(d['day']): d['duration_us'] for d in row['days']}
            focus = {date.fromisoformat(d['day']): d['duration_us'] for d in row['focus_days']}
            qualified = {day for day, us in daily.items() if us >= DAILY_QUALIFYING_US and day <= today}
            current, longest = streaks(qualified, today)
            days = [start_date + timedelta(days=i) for i in range((end_date-start_date).days+1)]
            items = [dict(day=d.isoformat(), duration_us=daily.get(d, 0), focus_us=focus.get(d, 0),
                          qualified=d in qualified) for d in days]
            focus_us = sum(d['focus_us'] for d in items)
            return dict(timezone=timezone, start_date=start_date, end_date=end_date,
                total_duration_us=sum(d['duration_us'] for d in items), days=items,
                streak=dict(current=current, longest=longest, threshold_us=DAILY_QUALIFYING_US,
                            scope='all_accessible_history', today=today),
                focus=dict(status='available' if focus_us else 'insufficient_data', duration_us=focus_us,
                           completed_intervals=row['completed_focus'], early_ended_intervals=row['early_ended_focus'],
                           legacy_intervals=row['legacy_focus']),
                coverage=dict(excluded_sessions=row['excluded_sessions'],
                    excluded_history_sessions=row['excluded_history_sessions'], completed_only=True))
