from app.core.exceptions import AppError
from app.models.enums import ChannelType
from app.services.channel_service import ChannelService


class StudyRoomService(ChannelService):
    async def media_token(self, principal, channel_id):
        from datetime import UTC, datetime, timedelta
        import jwt
        from app.core.config import get_settings

        settings = get_settings()
        media_grant = None
        if settings.media_authority_enabled:
            from sqlalchemy import select, func
            from app.models import Workspace, WorkspaceMember, AuthSession
            from app.models.media_grant import MediaGrant
            async with self.transaction(principal):
                channel = await self.channel_access(channel_id, principal.user.id)
                if channel.type != ChannelType.STUDY_ROOM:
                    raise AppError(422, "CHANNEL_NOT_STUDY_ROOM", "Channel không phải phòng tự học")
                workspace = await self.db.get(Workspace, channel.workspace_id)
                member = await self.db.scalar(select(WorkspaceMember).where(WorkspaceMember.workspace_id==workspace.id,WorkspaceMember.user_id==principal.user.id))
                session = await self.db.get(AuthSession, principal.session.id)
                now = await self.db.scalar(select(func.clock_timestamp()))
                if session.expires_at <= now:
                    raise AppError(401, "SESSION_EXPIRED", "Phiên xác thực đã hết hạn")
                media_grant = await self.db.scalar(select(MediaGrant).where(MediaGrant.auth_session_id==session.id,MediaGrant.channel_id==channel_id,
                    MediaGrant.revoked_at.is_(None),MediaGrant.expires_at>now,MediaGrant.membership_id==member.id,
                    MediaGrant.user_version==principal.user.admin_version,MediaGrant.workspace_version==workspace.admin_version).with_for_update())
                if media_grant is None:
                    media_grant = MediaGrant(user_id=principal.user.id,auth_session_id=session.id,workspace_id=workspace.id,channel_id=channel_id,
                        membership_id=member.id,user_version=principal.user.admin_version,workspace_version=workspace.admin_version,issued_at=now,
                        expires_at=min(session.expires_at,now+timedelta(hours=1)))
                    self.db.add(media_grant)
                else: media_grant.expires_at=min(session.expires_at,now+timedelta(hours=1))
                await self.db.flush()
        else:
            await self.authorize(principal, channel_id)
        key = settings.livekit_api_key.get_secret_value()
        secret = settings.livekit_api_secret.get_secret_value()
        if not settings.livekit_url.startswith(("ws://", "wss://")) or not key or len(secret) < 32:
            raise AppError(503, "MEDIA_NOT_CONFIGURED", "Máy chủ media chưa được cấu hình")
        now = datetime.now(UTC)
        room = f"study-{channel_id}"
        identity = str(principal.user.id)
        claims = {
            "iss": key, "sub": identity, "name": principal.user.full_name,
            "nbf": now, "exp": now + timedelta(minutes=5),
            "video": {"roomJoin": True, "room": room, "canPublish": True,
                      "canSubscribe": True, "canPublishData": False,
                      "canPublishSources": ["microphone", "camera", "screen_share"]},
        }
        if media_grant:
            import json
            claims['metadata']=json.dumps({'kltn_grant':str(media_grant.id)})
            claims['video']['canUpdateOwnMetadata']=False
        token = jwt.encode(claims, secret, algorithm="HS256")
        result = {"url": settings.livekit_url, "token": token, "room": room, "identity": identity}
        if media_grant:result.update(grant_id=media_grant.id,expires_at=media_grant.expires_at)
        return result

    async def authorize(self, principal, channel_id):
        async with self.transaction(principal):
            channel = await self.channel_access(channel_id, principal.user.id)
            if channel.type != ChannelType.STUDY_ROOM:
                raise AppError(422, "CHANNEL_NOT_STUDY_ROOM", "Channel không phải phòng tự học")
            return self.response(channel)
