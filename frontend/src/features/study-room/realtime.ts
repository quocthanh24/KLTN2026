import type { Channel } from "../../lib/chat-types";
import type { PomodoroState } from "./pomodoro";

export type Devices = { microphone_enabled: boolean; camera_enabled: boolean; screen_sharing: boolean };
export type RoomState = { channel: Channel; pomodoro?: PomodoroState; self_state: "IN_ROOM" | "LEFT"; self_devices: Devices; participants: ({ user_id: string; full_name: string; state: "IN_ROOM"; joined_at: string } & Devices)[] };

export function connectRoom(id: string, auth: { getToken: () => string | null; refresh: () => Promise<string> }, receive: (state: RoomState) => void, failure: (message: string) => void) {
  let disposed = false;
  let socket: WebSocket | undefined;
  let heartbeat: ReturnType<typeof setInterval> | undefined;
  let watchdog: ReturnType<typeof setTimeout> | undefined;
  let refreshed = false;
  let latest: RoomState | undefined;
  let participating = false;
  let devices: Partial<Devices> = {};
  let restoring = false;
  function connect() {
    if (disposed) return;
    try {
      const base = process.env.NEXT_PUBLIC_API_BASE_URL;
      if (!base) throw new Error("Chưa cấu hình kết nối phòng.");
      const url = new URL(`${base.replace(/\/$/, "")}/api/v1/ws/study-rooms/${id}`);
      url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
      const ws = new WebSocket(url); socket = ws;
      const arm = () => { clearTimeout(watchdog); watchdog = setTimeout(() => { ws.close(); if (!disposed) failure("Mất kết nối phòng. Vui lòng kết nối lại."); }, 12000); };
      arm();
      ws.onopen = () => { if (!disposed) ws.send(JSON.stringify({ type: "auth", access_token: auth.getToken() })); };
      ws.onmessage = event => {
        if (disposed || socket !== ws) return;
        try {
          const frame = JSON.parse(event.data);
          if (restoring && frame.type === "pomodoro.state") return;
          if (frame.type === "pomodoro.state" && latest?.pomodoro) {
            if (frame.data.revision >= latest.pomodoro.revision) {
              latest = { ...latest, pomodoro: { ...frame.data, can_control: latest.pomodoro.can_control } };
              receive(latest);
            }
            return;
          }
          if (frame.type !== "room.state") return;
          arm();
          if (latest?.pomodoro && frame.data.pomodoro?.revision < latest.pomodoro.revision) {
            frame.data.pomodoro = { ...latest.pomodoro, can_control: frame.data.pomodoro.can_control };
          }
          if (restoring && participating && frame.data.self_state === "LEFT") {
            ws.send(JSON.stringify({ type: "join" }));
            return;
          }
          if (restoring && participating) {
            restoring = false;
            if (Object.keys(devices).length) {
              ws.send(JSON.stringify({ type: "devices", ...devices }));
              return;
            }
          }
          latest = frame.data;
          arm(); receive(frame.data);
          if (!heartbeat) heartbeat = setInterval(() => { if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "state" })); }, 2000);
        } catch { ws.close(); failure("Phản hồi phòng không hợp lệ."); }
      };
      ws.onclose = async event => {
        clearInterval(heartbeat); heartbeat = undefined; clearTimeout(watchdog);
        if (disposed) return;
        devices = { microphone_enabled: false, camera_enabled: false, screen_sharing: false };
        restoring = participating;
        if (event.code === 4401 && !refreshed) {
          failure("Phiên kết nối phòng đã hết hạn. Đang xác thực lại…");
          refreshed = true;
          try { await auth.refresh(); connect(); return; } catch { /* Show login failure below. */ }
        }
        failure([4403, 4404, 1008].includes(event.code) ? "Không có quyền truy cập hoặc phòng không còn tồn tại." : "Đã ngắt kết nối phòng. Vui lòng kết nối lại.");
      };
    } catch (error) { failure(error instanceof Error ? error.message : "Không thể kết nối phòng."); }
  }
  connect();
  return {
    reconnect() { if (disposed) return; clearInterval(heartbeat); heartbeat = undefined; clearTimeout(watchdog); if (socket) { socket.onclose = null; socket.onmessage = null; socket.close(); } restoring = participating; connect(); },
    command(type: "join" | "leave") { participating = type === "join"; if (!participating) devices = {}; if (!disposed && socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type })); },
    devices(state: Partial<Devices>) { devices = { ...devices, ...state }; if (!disposed && socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: "devices", ...state })); },
    close() { disposed = true; clearInterval(heartbeat); clearTimeout(watchdog); socket?.close(); },
  };
}
