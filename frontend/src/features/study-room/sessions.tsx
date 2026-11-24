"use client";
import { useEffect, useState } from "react";
import { createApiClient } from "@/lib/api";
import { chatAuth, clearAuth } from "@/features/auth/store";
import { durationLabel } from "@/features/learning-analytics/contracts";
import styles from "./participants.module.css";

const api = createApiClient({ ...chatAuth, clear: clearAuth });
type StudySession = {
  id: string; channel_id: string; status: string; started_at: string; last_activity_at: string;
  ended_at: string | null; duration_us: number; grace_expires_at: string | null; end_reason: string | null;
};
type Page = { items: StudySession[]; next_offset: number | null };
const statusLabel: Record<string, string> = {
  ACTIVE: "Đang học", DISCONNECTED: "Mất kết nối", COMPLETED: "Đã kết thúc", INVALID: "Không hợp lệ",
};
const reasonLabel: Record<string, string> = {
  LEFT: "Rời phòng", DISCONNECT_TIMEOUT: "Hết thời gian kết nối lại", INVALID_TIME: "Thời gian không hợp lệ",
};

export function StudySessions({ channelId }: { channelId: string }) {
  const [offset, setOffset] = useState(0);
  const [attempt, setAttempt] = useState(0);
  const [state, setState] = useState<{ key: string; data?: Page; error?: string } | null>(null);
  const key = `${channelId}/${offset}/${attempt}`;
  useEffect(() => {
    let active = true;
    api<Page>(`/channels/${channelId}/study-room/study-sessions?offset=${offset}`, { protected: true })
      .then(data => { if (active) setState({ key, data }); })
      .catch(error => { if (active) setState({ key, error: error instanceof Error ? error.message : "Không thể tải phiên học." }); });
    const timer = setInterval(() => setAttempt(n => n + 1), 4000);
    return () => { active = false; clearInterval(timer); };
  }, [key, channelId, offset]);
  const current = state?.key === key ? state : null;
  return <section className={styles.participants} aria-label="Phiên tự học của bạn">
    <h4>Phiên tự học của bạn</h4>
    <p>Thời gian được ghi từ máy chủ khi bạn tham gia phòng. Khoảng mất kết nối không được tính.</p>
    {!current && <p role="status">Đang tải phiên học…</p>}
    {current?.error && <p role="alert">{current.error} <button type="button" onClick={() => setAttempt(n => n + 1)}>Thử lại</button></p>}
    {current?.data && <>
      {!current.data.items.length && <p>Chưa có phiên tự học trong phòng này.</p>}
      <ul aria-label="Danh sách phiên tự học">{current.data.items.map(session => (
        <li key={session.id}>
          <strong>{statusLabel[session.status] ?? session.status}</strong>
          <p>Bắt đầu: {new Date(session.started_at).toLocaleString("vi-VN")}</p>
          {session.ended_at && <p>Kết thúc: {new Date(session.ended_at).toLocaleString("vi-VN")}</p>}
          <p>Thời gian ghi nhận: {durationLabel(session.duration_us)}</p>
          {session.end_reason && <p>Lý do: {reasonLabel[session.end_reason] ?? session.end_reason}</p>}
        </li>
      ))}</ul>
      <div className="actions">
        <button type="button" disabled={!offset} onClick={() => setOffset(value => Math.max(0, value - 50))}>Trang trước</button>
        <button type="button" disabled={current.data.next_offset === null} onClick={() => setOffset(current.data!.next_offset!)}>Trang sau</button>
      </div>
    </>}
  </section>;
}
