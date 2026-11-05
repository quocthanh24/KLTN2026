"""Ephemeral single-worker presence; durable UC37 history lives in PostgreSQL."""
from datetime import UTC, datetime
from time import monotonic
from app.core.exceptions import AppError


class RoomPresence:
    def __init__(self):
        self.connections = {}

    def update(self, key, channel_id, user, action, devices=None):
        self.prune()
        if action == "join" and key not in self.connections:
            self.connections[key] = [channel_id, {
                "user_id": str(user.id), "full_name": user.full_name,
                "state": "IN_ROOM", "joined_at": datetime.now(UTC).isoformat(),
                "microphone_enabled": False, "camera_enabled": False, "screen_sharing": False,
            }, monotonic()]
        elif action == "leave":
            self.connections.pop(key, None)
        elif action == "devices":
            if key not in self.connections:
                raise AppError(403, "ROOM_PARTICIPATION_REQUIRED", "Bạn chưa tham gia phòng")
            self.connections[key][1].update(devices)
        if key in self.connections:
            self.connections[key][2] = monotonic()

    def prune(self):
        for key, (_, _, seen) in list(self.connections.items()):
            if monotonic() - seen >= 15:
                self.connections.pop(key, None)

    def disconnect(self, key):
        self.connections.pop(key, None)

    def snapshot(self, channel_id, key=None):
        self.prune()
        participants = {}
        for room, user, _ in self.connections.values():
            if room == channel_id:
                participant = participants.setdefault(user["user_id"], dict(user))
                for field in ("microphone_enabled", "camera_enabled", "screen_sharing"):
                    participant[field] = participant[field] or user[field]
        own = self.connections[key][1] if key in self.connections else {}
        return {"participants": list(participants.values()),
                "self_devices": {field: own.get(field, False) for field in ("microphone_enabled", "camera_enabled", "screen_sharing")},
                "self_state": "IN_ROOM" if key in self.connections else "LEFT"}


room_presence = RoomPresence()

# Meaningful timer snapshots are pushed immediately. Existing heartbeat snapshots
# additionally recover missed events and converge across backend workers.
pomodoro_sockets = {}


async def publish_pomodoro(channel_id, snapshot):
    import asyncio
    data = {key: value for key, value in snapshot.items() if key != "can_control"}
    sockets = [socket for socket, room in list(pomodoro_sockets.items()) if room == channel_id]
    from app.realtime.moderation import permitted
    async def send(socket):
        if await permitted(socket):await socket.send_json({"type":"pomodoro.state","data":data})
    await asyncio.gather(*(send(socket) for socket in sockets),return_exceptions=True)
