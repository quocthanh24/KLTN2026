"""Single-worker socket authorization registry. No token or private content is logged."""
import logging
import time
import jwt
import httpx
from app.core.exceptions import AppError

connections={}
logger=logging.getLogger(__name__)


def register(socket,token,channel_id,user_id,study=False,factory=None):
    connections[socket]=(token,channel_id,user_id,study,factory)


async def permitted(socket):
    entry=connections.get(socket)
    if entry is None:return True  # non-production test transports are not registered
    from app.db.session import session_factory
    from app.services.auth_service import AuthService
    from app.services.channel_service import ChannelService
    try:
        async with (entry[4] or session_factory)() as db:
            principal=await AuthService(db).authenticate(entry[0])
            async with ChannelService(db).transaction(principal):
                await ChannelService(db).channel_access(entry[1],principal.user.id)
        return True
    except AppError as error:
        code=4401 if error.status_code==401 else 4404 if error.status_code==404 else 4403
        try:await socket.close(code=code)
        except Exception:pass
        return False


async def remove_media(channel_id,user_id):
    from app.core.config import get_settings
    settings=get_settings()
    key,secret=settings.livekit_api_key.get_secret_value(),settings.livekit_api_secret.get_secret_value()
    if not key or not secret:return
    room=f'study-{channel_id}'
    token=jwt.encode({'iss':key,'exp':int(time.time())+30,'video':{'roomAdmin':True,'room':room}},secret,algorithm='HS256')
    base=settings.livekit_url.replace('wss://','https://').replace('ws://','http://').rstrip('/')
    async with httpx.AsyncClient(timeout=5) as client:
        response=await client.post(base+'/twirp/livekit.RoomService/RemoveParticipant',
            headers={'Authorization':'Bearer '+token},json={'room':room,'identity':str(user_id)})
        if response.status_code not in (200,404):
            raise AppError(503,'MEDIA_REVOCATION_PENDING','Trạng thái đã lưu; chưa hoàn tất ngắt media. Hãy tải lại và thử lại')


async def enforce_revocations():
    from app.core.config import get_settings
    for socket,entry in list(connections.items()):
        if not await permitted(socket) and entry[3] and not get_settings().media_authority_enabled:
            await remove_media(entry[1],entry[2])
