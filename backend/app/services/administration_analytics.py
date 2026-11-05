"""UC45: platform counts, and per-user interval union (never session wall-clock time)."""
from datetime import timedelta
from sqlalchemy import text
from app.core.exceptions import AppError
from app.services.administration_service import AdministrationService

PLATFORM_SQL="""
WITH bounds AS (
 SELECT CAST(:start AS date)::timestamp AT TIME ZONE 'Asia/Ho_Chi_Minh' lo,
 (CAST(:end AS date)+1)::timestamp AT TIME ZONE 'Asia/Ho_Chi_Minh' hi
), sessions AS MATERIALIZED (
 SELECT s.* FROM study_sessions s,bounds b WHERE s.status='COMPLETED' AND s.ended_at<=CURRENT_TIMESTAMP
 AND s.ended_at=s.last_activity_at AND s.ended_at>=s.started_at AND s.duration_us>=0
 AND s.duration_us<=EXTRACT(EPOCH FROM s.ended_at-s.started_at)*1000000
 AND s.started_at<b.hi AND s.ended_at>=b.lo
), ledger AS (
 SELECT s.id,s.user_id,s.duration_us,range_agg(tstzrange(i.started_at,i.ended_at,'[)')) spans
 FROM sessions s JOIN study_activity_intervals i ON i.session_id=s.id WHERE s.analytics_tracked
 GROUP BY s.id,s.user_id,s.duration_us,s.started_at,s.ended_at
 HAVING min(i.started_at)>=s.started_at AND max(i.ended_at)<=s.ended_at
 AND sum(EXTRACT(EPOCH FROM i.ended_at-i.started_at)*1000000)=s.duration_us
), valid AS MATERIALIZED (
 SELECT * FROM ledger l WHERE (SELECT sum(EXTRACT(EPOCH FROM upper(r)-lower(r))*1000000) FROM unnest(l.spans) r)=l.duration_us
), attendance AS (
 SELECT user_id,unnest(spans) r FROM valid
), merged AS (
 SELECT user_id,unnest(range_agg(r)) r FROM attendance GROUP BY user_id
), daily AS (
 SELECT (d AT TIME ZONE 'Asia/Ho_Chi_Minh')::date AS day,
 sum(EXTRACT(EPOCH FROM least(upper(r),d+interval '24 hours',b.hi)-greatest(lower(r),d,b.lo))*1000000)::bigint duration_us
 FROM merged,bounds b,LATERAL generate_series(
 (greatest(lower(r),b.lo) AT TIME ZONE 'Asia/Ho_Chi_Minh')::date::timestamp AT TIME ZONE 'Asia/Ho_Chi_Minh',
 ((least(upper(r),b.hi)-interval '1 microsecond') AT TIME ZONE 'Asia/Ho_Chi_Minh')::date::timestamp AT TIME ZONE 'Asia/Ho_Chi_Minh',
 interval '1 day','Asia/Ho_Chi_Minh') d WHERE r && tstzrange(b.lo,b.hi,'[)') GROUP BY day
)
SELECT jsonb_build_object(
 'users',jsonb_build_object('total',(SELECT count(*) FROM users),'active',(SELECT count(*) FROM users WHERE status='ACTIVE'),
 'locked',(SELECT count(*) FROM users WHERE status='LOCKED'),'created_in_period',(SELECT count(*) FROM users,bounds b WHERE created_at>=b.lo AND created_at<b.hi)),
 'workspaces',jsonb_build_object('total',(SELECT count(*) FROM workspaces WHERE deleted_at IS NULL),
 'active',(SELECT count(*) FROM workspaces WHERE deleted_at IS NULL AND disabled_at IS NULL),
 'disabled',(SELECT count(*) FROM workspaces WHERE deleted_at IS NULL AND disabled_at IS NOT NULL),
 'deleted',(SELECT count(*) FROM workspaces WHERE deleted_at IS NOT NULL),
 'created_in_period',(SELECT count(*) FROM workspaces,bounds b WHERE created_at>=b.lo AND created_at<b.hi)),
 'documents',jsonb_build_object('total',(SELECT count(*) FROM learning_documents),
 'available',(SELECT count(*) FROM learning_documents d JOIN channels c ON c.id=d.channel_id JOIN workspaces w ON w.id=c.workspace_id
 WHERE d.deleted_at IS NULL AND d.moderated_at IS NULL AND w.deleted_at IS NULL AND w.disabled_at IS NULL),
 'hidden',(SELECT count(*) FROM learning_documents WHERE moderated_at IS NOT NULL AND deleted_at IS NULL),
 'deleted',(SELECT count(*) FROM learning_documents WHERE deleted_at IS NOT NULL),
 'created_in_period',(SELECT count(*) FROM learning_documents,bounds b WHERE created_at>=b.lo AND created_at<b.hi)),
 'study',jsonb_build_object('duration_us',COALESCE((SELECT sum(duration_us) FROM daily),0),
 'completed_sessions',(SELECT count(*) FROM sessions),
 'excluded_sessions',((SELECT count(*) FROM sessions WHERE duration_us>0)-(SELECT count(*) FROM valid WHERE duration_us>0))),
 'reports',jsonb_build_object('pending',(SELECT count(*) FROM violation_reports WHERE status='PENDING'),
 'resolved',(SELECT count(*) FROM violation_reports WHERE status='RESOLVED'),
 'created_in_period',(SELECT count(*) FROM violation_reports,bounds b WHERE created_at>=b.lo AND created_at<b.hi),
 'violation_in_period',(SELECT count(*) FROM violation_reports,bounds b WHERE created_at>=b.lo AND created_at<b.hi AND outcome='VIOLATION'),
 'no_violation_in_period',(SELECT count(*) FROM violation_reports,bounds b WHERE created_at>=b.lo AND created_at<b.hi AND outcome='NO_VIOLATION')),
 'days',COALESCE((SELECT jsonb_agg(jsonb_build_object('day',day,'duration_us',duration_us) ORDER BY day) FROM daily),'[]'::jsonb)
) result
"""


class AdministrationAnalytics(AdministrationService):
    async def dates(self,start,end):
        today=await self.db.scalar(text("SELECT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Ho_Chi_Minh')::date"))
        end=end or today;start=start or end-timedelta(days=6)
        if start>end or (end-start).days>=366:
            raise AppError(422,'INVALID_ADMIN_RANGE','Khoảng ngày phải tăng dần và không quá 366 ngày')
        return start,end

    async def overview(self,principal,start=None,end=None):
        async with self.admin_transaction(principal):
            start,end=await self.dates(start,end)
            result=await self.db.scalar(text(PLATFORM_SQL),{'start':start,'end':end})
            daily={d['day']:d['duration_us'] for d in result['days']}
            result['days']=[{'day':str(start+timedelta(days=i)),'duration_us':daily.get(str(start+timedelta(days=i)),0)} for i in range((end-start).days+1)]
            result.update(start_date=str(start),end_date=str(end),timezone='Asia/Ho_Chi_Minh')
            return result

    async def details(self,principal,group,start=None,end=None,offset=0):
        if group=='study':
            data=await self.overview(principal,start,end)
            return {'items':data['days'][offset:offset+50],'next_offset':offset+50 if len(data['days'])>offset+50 else None}
        sources={
            'users':('users','full_name','status'),
            'workspaces':('workspaces','name',"CASE WHEN deleted_at IS NOT NULL THEN 'DELETED' WHEN disabled_at IS NOT NULL THEN 'DISABLED' ELSE 'ACTIVE' END"),
            'documents':('learning_documents','name',"CASE WHEN deleted_at IS NOT NULL THEN 'DELETED' WHEN moderated_at IS NOT NULL THEN 'HIDDEN' ELSE 'STORED' END"),
            'reports':('violation_reports','target_type','status')}
        if group not in sources:raise AppError(422,'ADMIN_GROUP_INVALID','Nhóm thống kê không hợp lệ')
        table,label,status=sources[group]
        async with self.admin_transaction(principal):
            start,end=await self.dates(start,end)
            # Dynamic fragments come ONLY from the fixed server whitelist above.
            query=f"SELECT id,{label} label,{status} status,created_at FROM {table} WHERE created_at>=CAST(:start AS date)::timestamp AT TIME ZONE 'Asia/Ho_Chi_Minh' AND created_at<(CAST(:end AS date)+1)::timestamp AT TIME ZONE 'Asia/Ho_Chi_Minh' ORDER BY created_at DESC,id OFFSET :offset LIMIT 51"
            rows=[dict(r) for r in (await self.db.execute(text(query),{'start':start,'end':end,'offset':offset})).mappings()]
            return {'items':rows[:50],'next_offset':offset+50 if len(rows)>50 else None}
