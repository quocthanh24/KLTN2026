"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { createApiClient } from "@/lib/api";
import { chatAuth, clearAuth } from "@/features/auth/store";
import { durationLabel, recentRange, type LearningAnalytics } from "./contracts";
import styles from "./analytics.module.css";

const api = createApiClient({ ...chatAuth, clear: clearAuth });
export function LearningAnalyticsView() {
  const [range, setRange] = useState(() => recentRange(7));
  const [preset, setPreset] = useState("7");
  const [draft, setDraft] = useState(range);
  const [attempt, setAttempt] = useState(0);
  const [result, setResult] = useState<{ key: string; data?: LearningAnalytics; error?: string } | null>(null);
  const key = `${range.start}/${range.end}/${attempt}`;
  useEffect(() => {
    let active = true;
    api<LearningAnalytics>(`/users/me/learning-analytics?start_date=${range.start}&end_date=${range.end}`, { protected: true })
      .then(data => { if (active) setResult({ key, data }); })
      .catch(error => { if (active) setResult({ key, error: error instanceof Error ? error.message : "Không thể tải thống kê." }); });
    return () => { active = false; };
  }, [key, range.start, range.end]);
  useEffect(() => {
    const refresh = () => setAttempt(n => n + 1);
    window.addEventListener("focus", refresh);
    return () => window.removeEventListener("focus", refresh);
  }, []);
  const current = result?.key === key ? result : null;
  const data = current?.data;
  const maximum = Math.max(60_000_000, ...data?.days.map(d => d.duration_us) ?? []);
  return <div className={styles.dashboard}>
    <nav><Link href="/app">Trang chính</Link><Link href="/workspaces">Workspace</Link></nav>
    <h1>Thống kê học tập</h1>
    <p>Thời gian tham gia đã ghi nhận từ các phiên hoàn thành. Múi giờ: Asia/Ho_Chi_Minh (UTC+7).</p>
    <label htmlFor="analytics-range">Khoảng thời gian</label>
    <select id="analytics-range" value={preset} onChange={e => {
      setPreset(e.target.value);
      if (e.target.value !== "custom") { const next = recentRange(Number(e.target.value)); setRange(next); setDraft(next); }
    }}><option value="7">7 ngày gần nhất</option><option value="30">30 ngày gần nhất</option><option value="custom">Chọn khoảng ngày</option></select>
    {preset === "custom" && <form onSubmit={e => { e.preventDefault(); setRange(draft); setAttempt(n => n + 1); }} className={styles.filters}>
      <label>Từ ngày<input type="date" required value={draft.start} max={draft.end} onChange={e => setDraft({ ...draft, start: e.target.value })} /></label>
      <label>Đến ngày<input type="date" required value={draft.end} min={draft.start} onChange={e => setDraft({ ...draft, end: e.target.value })} /></label>
      <button>Áp dụng</button>
    </form>}
    <button className={styles.refresh} onClick={() => setAttempt(n => n + 1)}>Cập nhật</button>
    {!current && <p role="status">Đang tải thống kê…</p>}
    {current?.error && <div role="alert"><p>{current.error}</p><button onClick={() => setAttempt(n => n + 1)}>Thử lại</button></div>}
    {data && <>
      {!!data.coverage.excluded_sessions && <p role="note">Có {data.coverage.excluded_sessions} phiên cũ hoặc thiếu dữ liệu chi tiết chưa được đưa vào thống kê theo ngày.</p>}
      {!!data.coverage.excluded_history_sessions && <p>Chuỗi ngày học chỉ dựa trên lịch sử đủ chi tiết; có {data.coverage.excluded_history_sessions} phiên trong lịch sử chưa thể đối soát.</p>}
      <section aria-label="Thời gian tự học"><h2>Thời gian tự học</h2><strong data-testid="study-total" className={styles.metric}>{durationLabel(data.total_duration_us)}</strong>
        <p>{data.start_date} – {data.end_date}. Khoảng trùng giữa các phòng chỉ tính một lần.</p>
        {!data.total_duration_us && <p>Chưa có thời gian tự học đã hoàn thành trong khoảng này.</p>}
        <div className={styles.chart} aria-label="Biểu đồ thời gian tự học theo ngày">{data.days.map(day => <div key={day.day} className={styles.barRow}>
          <time dateTime={day.day}>{day.day.slice(5)}</time><div className={styles.track}><div style={{ width: `${day.duration_us / maximum * 100}%` }} /></div><span>{durationLabel(day.duration_us)}</span>
        </div>)}</div>
      </section>
      <section aria-label="Chuỗi ngày học"><h2>Chuỗi ngày học liên tục</h2>
        <p className={styles.metric}><span data-testid="streak-current">{data.streak.current}</span> ngày hiện tại</p><p>Dài nhất: {data.streak.longest} ngày.</p>
        <p>Một ngày hợp lệ cần ít nhất 1 phút tham gia đã ghi nhận. Chuỗi tính trên toàn bộ lịch sử còn quyền truy cập; hôm nay chưa học vẫn giữ chuỗi đến hôm qua.</p>
        {!data.streak.longest && <p>Bạn chưa có ngày học đạt điều kiện.</p>}
        <ul className={styles.calendar} aria-label="Lịch ngày học trong khoảng đã chọn">{data.days.map(day => <li key={day.day} className={day.qualified ? styles.qualified : ""} title={`${day.day}: ${durationLabel(day.duration_us)}`}><time dateTime={day.day}>{day.day.slice(5)}</time><span>{day.qualified ? "Đạt" : "—"}</span></li>)}</ul>
      </section>
      <section aria-label="Hoạt động Pomodoro"><h2>Hoạt động tập trung Pomodoro</h2><p>Chỉ số hoạt động Pomodoro, không đo sự chú ý tinh thần. Loại thời gian nghỉ, tạm dừng và mất kết nối.</p>
        {data.focus.status === "insufficient_data" ? <p>Chưa đủ dữ liệu để thống kê.</p> : <>
          <p className={styles.metric} data-testid="focus-total">{durationLabel(data.focus.duration_us)}</p><p>Focus có bằng chứng tham gia trong khoảng ngày đã chọn.</p>
          <p>Hoàn thành đủ chu kỳ: <strong data-testid="focus-completed">{data.focus.completed_intervals}</strong></p>
          <p>Chu kỳ phòng dừng sớm có tham gia: <strong>{data.focus.early_ended_intervals}</strong></p>
          <p>Hoàn thành cần tham gia đủ mọi đoạn chạy của chu kỳ. Số chu kỳ tính theo ngày kết thúc; không quy việc rời phòng thành dừng chu kỳ.</p>
        </>}
        {!!data.focus.legacy_intervals && <p>Một số chu kỳ cũ chưa có đủ dữ liệu tham gia để thống kê.</p>}
      </section>
    </>}
  </div>;
}
