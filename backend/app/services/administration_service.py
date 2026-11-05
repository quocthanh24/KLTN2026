from contextlib import asynccontextmanager
from sqlalchemy import func,select,text
from app.core.exceptions import AppError
from app.models import User,Workspace,WorkspaceMember,ChatMessage,LearningDocument,Channel
from app.models.enums import AccountStatus,SystemRole,WorkspaceRole
from app.models.moderation import AdministrationAudit,ViolationReport
from app.services.auth_service import AuthService
from app.services.channel_service import ChannelService


class AdministrationService(ChannelService):
    @asynccontextmanager
    async def admin_transaction(self,principal):
        async with self.db.begin():
            # Serialize rare cross-user admin mutations before locking any target user.
            await self.db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended('system-administration',0))"))
            current=await AuthService(self.db).load_principal(principal.user.id,principal.session.id)
            if current.user.system_role!=SystemRole.ADMIN:
                raise AppError(403,'SYSTEM_ADMIN_REQUIRED','Chỉ quản trị viên hệ thống được truy cập')
            yield current

    def audit(self,actor,target_type,target_id,action,before,after,reason):
        self.db.add(AdministrationAudit(actor_id=actor,target_type=target_type,target_id=target_id,
            action=action,before=before,after=after,reason=reason))

    @staticmethod
    def user_public(row):
        return {k:getattr(row,k) for k in ('id','full_name','email','status','system_role','admin_version','created_at')}

    async def users_list(self,principal,search='',status=None,offset=0):
        async with self.admin_transaction(principal):
            query=select(User)
            if search:query=query.where(User.email.icontains(search,autoescape=True)|User.full_name.icontains(search,autoescape=True))
            if status:query=query.where(User.status==status)
            rows=list(await self.db.scalars(query.order_by(User.created_at.desc(),User.id).offset(offset).limit(51)))
            return {'items':[self.user_public(r) for r in rows[:50]],'next_offset':offset+50 if len(rows)>50 else None}

    async def user_detail(self,principal,identity):
        async with self.admin_transaction(principal):
            row=await self.db.get(User,identity,populate_existing=True)
            if row is None:raise AppError(404,'USER_NOT_FOUND','Không tìm thấy tài khoản')
            result=self.user_public(row)
            await self.attach_media_operation(result,'USER',row.id)
            return result

    async def attach_media_operation(self,result,scope,identity):
        from app.core.config import get_settings
        if get_settings().media_authority_enabled:
            from app.models.media_grant import MediaRevocation
            from app.services.media_authority import public_operation
            operation=await self.db.scalar(select(MediaRevocation).where(MediaRevocation.scope==scope,MediaRevocation.scope_id==identity).order_by(MediaRevocation.created_at.desc(),MediaRevocation.id).limit(1))
            if operation:result['media_revocation']=public_operation(operation)

    async def user_state(self,principal,identity,request):
        operation_id=None
        async with self.admin_transaction(principal) as current:
            row=await self.db.get(User,identity,with_for_update=True,populate_existing=True)
            if row is None:raise AppError(404,'USER_NOT_FOUND','Không tìm thấy tài khoản')
            if row.system_role==SystemRole.ADMIN or row.id==current.user.id:
                raise AppError(403,'ADMIN_ACCOUNT_PROTECTED','Không được khóa hoặc thay đổi tài khoản quản trị qua thao tác này')
            if row.admin_version!=request.version:raise AppError(409,'ADMIN_STATE_STALE','Trạng thái đã thay đổi. Hãy tải lại trước khi xác nhận')
            desired=AccountStatus.ACTIVE if request.enabled else AccountStatus.LOCKED
            if row.status!=desired:
                before={'status':str(row.status),'version':row.admin_version}
                row.status=desired;row.admin_version+=1
                self.audit(current.user.id,'USER',row.id,'USER_STATE',before,{'status':str(row.status),'version':row.admin_version},request.reason)
            result=self.user_public(row)
            if not request.enabled:
                from app.services.media_authority import revoke_in_transaction
                operation_id=await revoke_in_transaction(self.db,'USER',row.id,current.user.id)
        if not request.enabled:
            from app.realtime.moderation import enforce_revocations
            await enforce_revocations()
        if operation_id:
            result['media_revocation']={'id':operation_id,'state':'PENDING'}
        return result

    @staticmethod
    def report_public(row):
        return {k:getattr(row,k) for k in ('id','reporter_id','target_type','target_id','channel_id','workspace_id',
            'reason','description','status','version','outcome','action','resolution','resolved_by','resolved_at','created_at')}

    async def reports_list(self,principal,status='PENDING',offset=0):
        async with self.admin_transaction(principal):
            query=select(ViolationReport)
            if status:query=query.where(ViolationReport.status==status)
            rows=list(await self.db.scalars(query.order_by(ViolationReport.created_at.desc(),ViolationReport.id).offset(offset).limit(51)))
            return {'items':[self.report_public(r) for r in rows[:50]],'next_offset':offset+50 if len(rows)>50 else None}

    async def report_row(self,identity,lock=False):
        row=await self.db.get(ViolationReport,identity,populate_existing=True,with_for_update=lock)
        if row is None:raise AppError(404,'REPORT_NOT_FOUND','Không tìm thấy báo cáo')
        return row

    async def report_target(self,row,lock=False):
        model=ChatMessage if row.target_type=='MESSAGE' else LearningDocument
        # Same Channel->target order as ordinary message/document mutations.
        await self.db.get(Channel,row.channel_id,with_for_update=lock)
        target=await self.db.get(model,row.target_id,populate_existing=True,with_for_update=lock,execution_options={'include_moderated':True})
        if target is not None and target.channel_id!=row.channel_id:
            raise AppError(409,'REPORT_TARGET_CHANGED','Đối tượng không khớp phạm vi đã báo cáo')
        return target

    async def report_detail(self,principal,identity):
        async with self.admin_transaction(principal):
            row=await self.report_row(identity);target=await self.report_target(row)
            data=self.report_public(row)
            data['target_available']=bool(target is not None and target.deleted_at is None and target.moderated_at is None)
            data['target']=None if target is None or target.deleted_at else (
                {'content':target.content} if row.target_type=='MESSAGE' else
                {'name':target.name,'content_type':target.content_type,'size_bytes':target.size_bytes})
            return data

    async def evidence(self,principal,identity):
        async with self.admin_transaction(principal) as current:
            row=await self.report_row(identity);target=await self.report_target(row)
            if row.target_type!='DOCUMENT' or target is None or target.deleted_at:
                raise AppError(404,'REPORT_EVIDENCE_UNAVAILABLE','Tài liệu báo cáo không còn khả dụng')
            from app.services.learning_document_service import LearningDocumentService
            raw=await LearningDocumentService(self.db).original_bytes(target)
            self.audit(current.user.id,'REPORT',row.id,'EVIDENCE_VIEW',{}, {},'Viewed original evidence attached to report')
            return raw,target.content_type,target.original_filename

    async def resolve(self,principal,identity,request):
        message_event=None
        async with self.admin_transaction(principal) as current:
            row=await self.report_row(identity,True)
            if row.status!='PENDING' or row.version!=request.version:
                raise AppError(409,'REPORT_ALREADY_RESOLVED','Báo cáo đã thay đổi. Hãy tải lại kết quả mới nhất')
            target=await self.report_target(row,True)
            available=target is not None and target.deleted_at is None and target.moderated_at is None
            if (request.outcome=='NO_VIOLATION' and request.action!='NONE' or
                request.outcome=='VIOLATION' and (request.action=='NONE' or
                    request.action=='HIDE' and not available or request.action=='ALREADY_UNAVAILABLE' and available)):
                raise AppError(422,'REPORT_ACTION_INVALID','Biện pháp không phù hợp kết luận hoặc trạng thái đối tượng')
            now=await self.db.scalar(select(func.clock_timestamp()))
            if request.action=='HIDE':
                target.moderated_at=now
                if row.target_type=='MESSAGE':message_event=(row.channel_id,row.target_id)
                else:
                    # Fence old processing jobs; do not invoke physical deletion/MinIO cleanup.
                    target.processing_token=target.processing_lease_until=None
            row.status='RESOLVED';row.version+=1;row.outcome=request.outcome;row.action=request.action
            row.resolution=request.reason;row.resolved_by=current.user.id;row.resolved_at=now
            self.audit(current.user.id,'REPORT',row.id,'REPORT_RESOLVE',{'status':'PENDING'},
                {'status':'RESOLVED','outcome':row.outcome,'action':row.action,'target_type':row.target_type,'target_id':str(row.target_id)},request.reason)
            await self.db.flush();result=self.report_public(row)
        if message_event:
            from app.realtime.connection_manager import channel_connections
            await channel_connections.broadcast(message_event[0],{'type':'message.deleted','data':{'message_id':str(message_event[1])}})
        return result

    @staticmethod
    def workspace_public(row,owner=None):
        result={k:getattr(row,k) for k in ('id','name','description','disabled_at','deleted_at','admin_version','created_at')}
        result['owner']={'id':owner.id,'full_name':owner.full_name,'email':owner.email} if owner else None
        result['status']='DELETED' if row.deleted_at else 'DISABLED' if row.disabled_at else 'ACTIVE'
        return result

    async def workspaces_list(self,principal,search='',status=None,offset=0):
        async with self.admin_transaction(principal):
            query=select(Workspace,User).outerjoin(WorkspaceMember,(WorkspaceMember.workspace_id==Workspace.id)&(WorkspaceMember.role==WorkspaceRole.OWNER)).outerjoin(User,User.id==WorkspaceMember.user_id)
            if search:query=query.where(Workspace.name.icontains(search,autoescape=True))
            if status=='ACTIVE':query=query.where(Workspace.disabled_at.is_(None),Workspace.deleted_at.is_(None))
            elif status=='DISABLED':query=query.where(Workspace.disabled_at.is_not(None),Workspace.deleted_at.is_(None))
            rows=(await self.db.execute(query.order_by(Workspace.created_at.desc(),Workspace.id).offset(offset).limit(51).execution_options(include_moderated=True))).all()
            return {'items':[self.workspace_public(w,u) for w,u in rows[:50]],'next_offset':offset+50 if len(rows)>50 else None}

    async def workspace_detail(self,principal,identity):
        async with self.admin_transaction(principal):
            row=await self.db.get(Workspace,identity,populate_existing=True,execution_options={'include_moderated':True})
            if row is None or row.deleted_at:raise AppError(404,'WORKSPACE_NOT_FOUND','Workspace không còn tồn tại')
            owner=await self.db.scalar(select(User).join(WorkspaceMember,WorkspaceMember.user_id==User.id).where(WorkspaceMember.workspace_id==identity,WorkspaceMember.role==WorkspaceRole.OWNER))
            result=self.workspace_public(row,owner)
            await self.attach_media_operation(result,'WORKSPACE',row.id)
            return result

    async def workspace_state(self,principal,identity,request):
        operation_id=None
        async with self.admin_transaction(principal) as current:
            row=await self.db.get(Workspace,identity,populate_existing=True,with_for_update=True,execution_options={'include_moderated':True})
            if row is None or row.deleted_at:raise AppError(404,'WORKSPACE_NOT_FOUND','Workspace không còn tồn tại')
            if row.admin_version!=request.version:raise AppError(409,'ADMIN_STATE_STALE','Trạng thái đã thay đổi. Hãy tải lại trước khi xác nhận')
            if bool(row.disabled_at)==request.enabled:
                before={'disabled':bool(row.disabled_at),'version':row.admin_version}
                row.disabled_at=None if request.enabled else await self.db.scalar(select(func.clock_timestamp()))
                row.admin_version+=1
                self.audit(current.user.id,'WORKSPACE',row.id,'WORKSPACE_STATE',before,{'disabled':bool(row.disabled_at),'version':row.admin_version},request.reason)
            result=self.workspace_public(row)
            if not request.enabled:
                from app.services.media_authority import revoke_in_transaction
                operation_id=await revoke_in_transaction(self.db,'WORKSPACE',row.id,current.user.id)
        if not request.enabled:
            from app.realtime.moderation import enforce_revocations
            await enforce_revocations()
        if operation_id:
            result['media_revocation']={'id':operation_id,'state':'PENDING'}
        return result
