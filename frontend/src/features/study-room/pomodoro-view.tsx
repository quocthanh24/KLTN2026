"use client";
import { useEffect, useRef, useState } from "react";
import { chatAuth, clearAuth } from "@/features/auth/store";
import { createApiClient } from "@/lib/api";
import { formatTime, remaining, type PomodoroState } from "./pomodoro";
import styles from "./pomodoro.module.css";

const api = createApiClient({ ...chatAuth, clear: clearAuth });
export function PomodoroCard({ id, snapshot, participating }: { id: string; snapshot: PomodoroState; participating: boolean }) {
  const [state, setState] = useState(snapshot);
  const current = useRef(snapshot);
  const anchor = useRef(0);
  const [elapsed, setElapsed] = useState(0);
  const [focus, setFocus] = useState(snapshot.focus_seconds / 60);
  const [pause, setPause] = useState(snapshot.break_seconds / 60);
  const [pending, setPending] = useState(false);
  const flight = useRef(false);
  const [message, setMessage] = useState("");
  const [confirm, setConfirm] = useState(false);
  function apply(next: PomodoroState) {
    const previous = current.current;
    if (next.revision < previous.revision || next.revision === previous.revision && Date.parse(next.server_now) < Date.parse(previous.server_now)) return;
    current.current = next;
    anchor.current = performance.now();
    setState(next);
    setElapsed(0);
  }
  useEffect(() => { apply(snapshot); }, [snapshot]);
  useEffect(() => {
    anchor.current = performance.now();
    const timer = setInterval(() => setElapsed((performance.now() - anchor.current) / 1000), 200);
    return () => clearInterval(timer);
  }, []);
  async function mutate(action: "start" | "pause" | "resume" | "end") {
    if (flight.current) return;
    flight.current = true; setPending(true); setMessage("");
    try {
      apply(await api<PomodoroState>(`/channels/${id}/study-room/pomodoro`, { method: "POST", protected: true,
        body: { action, revision: state.revision, ...(action === "start" ? { focus_seconds: Math.round(focus * 60), break_seconds: Math.round(pause * 60) } : {}) } }));
      setConfirm(false);
    } catch (error) {
      setConfirm(false);
      setMessage(error instanceof Error ? error.message : "Không thể cập nhật Pomodoro.");
      try { apply(await api<PomodoroState>(`/channels/${id}/study-room/pomodoro`, { protected: true })); } catch { /* Await authenticated realtime recovery. */ }
    } finally { flight.current = false; setPending(false); }
  }
  const active = state.status === "running" || state.status === "paused";
  const valid = [focus, pause].every(value => Number.isFinite(value) && value >= 1 / 60 && value <= 240);
  const controller = state.can_control && participating;
  return <section className={`${styles.card} ${state.phase === "break" ? styles.break : ""}`} aria-label="Pomodoro chung" data-status={state.status} data-phase={state.phase} data-revision={state.revision}>
    <div className={styles.heading}><h4>Pomodoro chung</h4><span>{state.status === "running" ? "Đang chạy" : state.status === "paused" ? "Tạm dừng" : state.status === "ended" ? "Đã kết thúc" : "Sẵn sàng"}</span></div>
    <p className={styles.phase}>{state.phase === "focus" ? "Tập trung" : "Nghỉ ngơi"}</p>
    <div className={styles.clock} aria-label="Thời gian còn lại">{formatTime(active ? remaining(state, elapsed) : state.status === "ended" ? 0 : state.focus_seconds)}</div>
    <p className={styles.caption}>Một nhịp học chung · Đồng bộ từ máy chủ</p>
    <p>{state.completed_focus} khoảng tập trung hoàn thành trong phiên</p>
    {!active && state.can_control && <div className={styles.settings}>
      <label>Tập trung (phút)<input type="number" min={1 / 60} max={240} step="any" value={focus} disabled={pending} onChange={event => setFocus(Number(event.target.value))} /></label>
      <label>Nghỉ (phút)<input type="number" min={1 / 60} max={240} step="any" value={pause} disabled={pending} onChange={event => setPause(Number(event.target.value))} /></label>
    </div>}
    {controller ? <div className={styles.controls}>
      {!active && <button disabled={pending || !valid} onClick={() => void mutate("start")}>Bắt đầu Pomodoro</button>}
      {state.status === "running" && <button disabled={pending} onClick={() => void mutate("pause")}>Tạm dừng Pomodoro</button>}
      {state.status === "paused" && <button disabled={pending} onClick={() => void mutate("resume")}>Tiếp tục Pomodoro</button>}
      {active && <button className={styles.end} disabled={pending} onClick={() => setConfirm(true)}>Kết thúc Pomodoro</button>}
    </div> : <p className={styles.caption}>{state.can_control ? "Tham gia phòng để điều khiển Pomodoro." : "Chế độ theo dõi · Chủ sở hữu và quản trị viên Workspace điều khiển phiên."}</p>}
    {pending && <p role="status">Đang cập nhật…</p>}
    {message && <p role="alert">{message}</p>}
    {confirm && controller && active && <div role="dialog" aria-label="Xác nhận kết thúc Pomodoro" className={styles.confirm}>
      <p>Kết thúc phiên chung? Khoảng tập trung chưa hoàn thành sẽ không được ghi nhận.</p>
      <button disabled={pending} onClick={() => void mutate("end")}>Xác nhận kết thúc</button>
      <button disabled={pending} onClick={() => setConfirm(false)}>Hủy</button>
    </div>}
  </section>;
}
