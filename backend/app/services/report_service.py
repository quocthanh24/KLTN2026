from sqlalchemy import select
from app.core.exceptions import AppError
from app.models import ChatMessage, Channel, LearningDocument
from app.models.moderation import ViolationReport
from app.services.channel_service import ChannelService


class ReportService(ChannelService):
    async def create(self,principal,request):
        async with self.transaction(principal):
            model=ChatMessage if request.target_type=='MESSAGE' else LearningDocument
            candidate=await self.db.get(model,request.target_id,populate_existing=True)
            if candidate is None:raise AppError(404,'REPORT_TARGET_UNAVAILABLE','Nội dung không còn khả dụng')
            channel=await self.channel_access(candidate.channel_id,principal.user.id)
            target=await self.db.scalar(select(model).where(model.id==request.target_id).with_for_update().execution_options(populate_existing=True))
            if target is None or target.deleted_at is not None or target.moderated_at is not None:
                raise AppError(404,'REPORT_TARGET_UNAVAILABLE','Nội dung không còn khả dụng')
            # User lock in transaction serializes duplicate submissions, unique index is backstop.
            existing=await self.db.scalar(select(ViolationReport).where(ViolationReport.reporter_id==principal.user.id,
                ViolationReport.target_type==request.target_type,ViolationReport.target_id==target.id,ViolationReport.status=='PENDING'))
            if existing:return self.receipt(existing)
            row=ViolationReport(reporter_id=principal.user.id,target_type=request.target_type,target_id=target.id,
                channel_id=channel.id,workspace_id=channel.workspace_id,reason=request.reason,description=request.description)
            self.db.add(row);await self.db.flush()
            return self.receipt(row)

    @staticmethod
    def receipt(row):return {k:getattr(row,k) for k in ('id','status','created_at')}
