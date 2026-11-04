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
  }, [channel.id]);
  return <section aria-label="Phòng tự học">
    <h3>Phòng tự học · {channel.name}</h3>
    {room?.pomodoro && <PomodoroCard key={channel.id} id={channel.id} snapshot={room.pomodoro} participating={!error && room.self_state === "IN_ROOM"} />}
    {mediaError && <p role="alert">{mediaError}</p>}
    {room?.self_state === "IN_ROOM" && mediaActive && !error ? <MediaPanel id={channel.id}
      sync={devices => connection.current?.devices(devices)}
      onDisconnect={() => { setMediaActive(false); setMediaError("Media đã ngắt. Bạn có thể khôi phục kết nối khi còn quyền tham gia."); }} /> :
      <div className="actions"><button disabled>Microphone: OFF</button><button disabled>Camera: OFF</button><button disabled>Chia sẻ màn hình</button></div>}
    {error && <><p role="alert" className="error">{error}</p><button onClick={() => { setError("Đang kết nối lại…"); connection.current?.reconnect(); }}>Kết nối lại</button></>}
    {room?.self_state === "IN_ROOM" && !mediaActive && !error && <button onClick={() => { setMediaError(""); setMediaActive(true); }}>Khôi phục media</button>}
    {!room ? <p role="status">Đang tải phòng…</p> : <>
      <p role="status">{room.self_state === "IN_ROOM" ? "Bạn đang ở trong phòng." : "Bạn đang ở ngoài phòng."}</p>
      <button disabled={pending || !!error} onClick={() => { setPending(true); setMediaError(""); setMediaActive(room.self_state !== "IN_ROOM"); connection.current?.command(room.self_state === "IN_ROOM" ? "leave" : "join"); }}>{room.self_state === "IN_ROOM" ? "Rời phòng" : "Tham gia phòng"}</button>
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
