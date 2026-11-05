import asyncio
import logging
from uuid import UUID, uuid4
from types import SimpleNamespace

from fastapi import APIRouter, Query, Response, WebSocket, WebSocketDisconnect

from app.api.dependencies import CurrentPrincipal, DbSession
from app.api.v1.chat_websocket import origin_allowed, AUTH_TIMEOUT_SECONDS
from app.core.exceptions import AppError
from app.db.session import session_factory
from app.realtime.study_room import room_presence, pomodoro_sockets
from app.services.auth_service import AuthService
from app.services.study_room_service import StudyRoomService
from app.schemas.common import DataResponse, ErrorResponse
from app.schemas.study_room import RoomDetails, MediaCredential
from app.schemas.pomodoro import PomodoroMutation
from app.services.pomodoro_service import PomodoroService
from app.services.study_session_service import StudySessionService

router = APIRouter(tags=["study-room"], responses={
    status: {"model": ErrorResponse} for status in (401, 403, 404, 422)
})


@router.get('/channels/{channel_id}/study-room/study-sessions')
async def study_sessions(channel_id: UUID, principal: CurrentPrincipal, db: DbSession,
                         response: Response, offset: int = Query(0, ge=0, le=100000)):
    response.headers['Cache-Control'] = 'private, no-store'
    return {'data': await StudySessionService(db).recent(principal, channel_id, offset)}


@router.get("/channels/{channel_id}/study-room/pomodoro")
async def pomodoro_current(channel_id: UUID, principal: CurrentPrincipal, db: DbSession):
    return {"data": await PomodoroService(db).current(principal, channel_id)}


@router.post("/channels/{channel_id}/study-room/pomodoro")
async def pomodoro_mutate(channel_id: UUID, request: PomodoroMutation, principal: CurrentPrincipal, db: DbSession):
    return {"data": await PomodoroService(db).current(principal, channel_id, request)}


@router.post("/channels/{channel_id}/study-room/media-token", response_model=DataResponse[MediaCredential], responses={503: {"model": ErrorResponse}})
async def media_token(channel_id: UUID, principal: CurrentPrincipal, db: DbSession, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return {"data": await StudyRoomService(db).media_token(principal, channel_id)}


@router.get("/channels/{channel_id}/study-room", response_model=DataResponse[RoomDetails])
async def detail(channel_id: UUID, principal: CurrentPrincipal, db: DbSession):
    channel = await StudyRoomService(db).authorize(principal, channel_id)
    return {"data": {"channel": channel, "participants": room_presence.snapshot(channel_id)["participants"]}}


@router.websocket("/ws/study-rooms/{channel_id}")
async def room_socket(websocket: WebSocket, channel_id: UUID):
    if not origin_allowed(websocket.headers.get("origin")):
        await websocket.close(code=1008)
        return
    await websocket.accept()
    key = uuid4()
    try:
        frame = await asyncio.wait_for(websocket.receive_json(), AUTH_TIMEOUT_SECONDS)
        if not isinstance(frame, dict) or frame.get("type") != "auth" or not isinstance(frame.get("access_token"), str):
            await websocket.close(code=4401)
            return
        token = frame["access_token"]
        action = "state"
        devices = None
        while True:
            async with session_factory() as db:
                principal = await AuthService(db).authenticate(token)
                channel = await StudyRoomService(db).authorize(principal, channel_id)
                pomodoro = await PomodoroService(db).current(principal, channel_id)
                presence_user = SimpleNamespace(id=principal.user.id, full_name=principal.user.full_name)
                from app.realtime.moderation import register
                register(websocket,token,channel_id,principal.user.id,study=True,factory=session_factory)
                if action == 'join' or key in room_presence.connections:
                    try:
                        attendance = await StudySessionService(db).event(principal, channel_id, key, action)
                    except AppError:
                        raise
                    except Exception:
                        if action != 'leave':
                            raise
                        # UC23 E2: local/media departure must still succeed; lease recovery
                        # conservatively closes the retained DB row if storage was unavailable.
                        logging.getLogger(__name__).exception('Study session leave pending recovery: connection_id=%s', key)
                        attendance = None
                    if attendance is None and action != 'join':
                        room_presence.disconnect(key)
            room_presence.update(key, channel_id, presence_user, action, devices)
            await websocket.send_json({"type": "room.state", "data": {
                "channel": channel.model_dump(mode="json"), **room_presence.snapshot(channel_id, key),
                "pomodoro": pomodoro,
            }})
            pomodoro_sockets[websocket] = channel_id
            frame = await asyncio.wait_for(websocket.receive_json(), 15)
            if not isinstance(frame, dict) or frame.get("type") not in ("join", "leave", "state", "devices"):
                await websocket.close(code=1008)
                return
            action = frame["type"]
            devices = {field: value for field, value in frame.items() if field != "type"}
            if action == "devices" and (
                not devices or not devices.keys() <= {"microphone_enabled", "camera_enabled", "screen_sharing"}
                or any(type(value) is not bool for value in devices.values())
            ):
                await websocket.close(code=1008)
                return
    except AppError as exc:
        await websocket.close(code=4403 if exc.status_code == 403 else 4404 if exc.status_code == 404 else 4401)
    except asyncio.TimeoutError:
        await websocket.close(code=4408)
    except (ValueError, TypeError):
        await websocket.close(code=1008)
    except WebSocketDisconnect:
        pass
    except Exception:
        logging.getLogger(__name__).exception('Study room event unavailable; attendance lease retained')
        await websocket.close(code=1011)
    finally:
        from app.realtime.moderation import connections
        connections.pop(websocket,None)
        pomodoro_sockets.pop(websocket, None)
        room_presence.disconnect(key)
        try:
            async with session_factory() as db:
                await StudySessionService(db).disconnect(key)
        except Exception:
            logging.getLogger(__name__).exception('Study session disconnect pending lease recovery: connection_id=%s', key)
