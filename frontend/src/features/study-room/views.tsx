"use client";
import { useEffect, useRef, useState } from "react";
import { chatAuth } from "@/features/auth/store";
import type { Channel } from "@/lib/chat-types";
import { connectRoom, type RoomState } from "./realtime";
import { MediaPanel } from "./media-view";
import styles from "./participants.module.css";
import { PomodoroCard } from "./pomodoro-view";

export function StudyRoom({ channel }: { channel: Channel }) {
  const [room, setRoom] = useState<RoomState | null>(null);
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);

  const [mediaActive, setMediaActive] = useState(false);
  const [mediaError, setMediaError] = useState("");
  const connection = useRef<ReturnType<typeof connectRoom> | null>(null);
  useEffect(() => {
    const client = connectRoom(channel.id, chatAuth, state => { setRoom(state); setPending(false); setError(""); }, message => { setPending(false); setError(message); });
    connection.current = client;
    return () => { connection.current = null; client.close(); };
  }, [channel.id, attempt]);
  return <section className="study-room" aria-label="Phòng tự học">
    <h3>Phòng tự học · {channel.name}</h3>
    <p className="lede">Bật micro, camera hoặc chia sẻ màn hình khi đã vào phòng. Trạng thái thiết bị được đồng bộ với nhóm.</p>
    {mediaError && <p role="alert">{mediaError}</p>}
    {room?.self_state === "IN_ROOM" && mediaActive && !error ? <MediaPanel id={channel.id}
      sync={devices => connection.current?.devices(devices)}
      onDisconnect={() => { setMediaActive(false); setMediaError("Media đã ngắt hoặc không thể kết nối. Hãy tham gia lại phòng."); connection.current?.command("leave"); }} /> :
      <div className="actions media-controls"><button disabled>Microphone: OFF</button><button disabled>Camera: OFF</button><button disabled>Chia sẻ màn hình</button></div>}
    {error ? <><p role="alert" className="error">{error}</p><button onClick={() => { setError(""); setRoom(null); setAttempt(n => n + 1); }}>Kết nối lại</button></> : !room ? <p role="status">Đang tải phòng…</p> : <>
      <div className="study-status">
        <p role="status" style={{ margin: 0 }}>{room.self_state === "IN_ROOM" ? "Bạn đang ở trong phòng." : "Bạn đang ở ngoài phòng."}</p>
        <button disabled={pending} onClick={() => { setPending(true); setMediaError(""); setMediaActive(room.self_state !== "IN_ROOM"); connection.current?.command(room.self_state === "IN_ROOM" ? "leave" : "join"); }}>{room.self_state === "IN_ROOM" ? "Rời phòng" : "Tham gia phòng"}</button>
      </div>
      <h4>Thành viên trong phòng ({room.participants.length})</h4>
      {!room.participants.length && <p>Chưa có thành viên trong phòng.</p>}
      <ul className="participant-list" aria-label="Thành viên trong phòng">{room.participants.map(person => <li key={person.user_id}>
        <strong>{person.full_name}</strong> · Đang trong phòng · Microphone: {person.microphone_enabled ? "ON" : "OFF"} · Camera: {person.camera_enabled ? "ON" : "OFF"} · Màn hình: {person.screen_sharing ? "ON" : "OFF"}
        <div className="device-pills">
          <span className={person.microphone_enabled ? "on" : ""}>Mic</span>
          <span className={person.camera_enabled ? "on" : ""}>Cam</span>
          <span className={person.screen_sharing ? "on" : ""}>Màn hình</span>
        </div>
      </li>)}</ul>
    </>}
    {room && <section aria-label="Trạng thái thành viên" className={styles.participants}>
      <h4>Thành viên trong phòng ({room.participants.length})</h4>
      <p role="status">{error ? "Mất kết nối · Đang hiển thị trạng thái gần nhất. Kết nối lại để đồng bộ." : "Trạng thái được đồng bộ từ phòng học."}</p>
      {!room.participants.length && <p>Chưa có thành viên trong phòng.</p>}
      <ul aria-label="Thành viên trong phòng">{[...room.participants].sort((a, b) => a.full_name.localeCompare(b.full_name, "vi") || a.user_id.localeCompare(b.user_id)).map(person => <li key={person.user_id}>
        <strong>{person.full_name}</strong>
        <div className={styles.statuses}>{([
          ["🎙", "Microphone", person.microphone_enabled],
          ["▣", "Camera", person.camera_enabled],
          ["▤", "Màn hình", person.screen_sharing],
        ] as const).map(([icon, label, enabled]) => <span key={label} data-enabled={enabled} title={`${label}: ${enabled ? "ON" : "OFF"}`}>
          <span aria-hidden="true">{icon}</span> {label}: {enabled ? "ON" : "OFF"}
        </span>)}</div>
      </li>)}</ul>
    </section>}
  </section>;
}
