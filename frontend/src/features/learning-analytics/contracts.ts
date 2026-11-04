export type AnalyticsDay = { day: string; duration_us: number; focus_us: number; qualified: boolean };
export type LearningAnalytics = {
  timezone: string; start_date: string; end_date: string; total_duration_us: number; days: AnalyticsDay[];
  streak: { current: number; longest: number; threshold_us: number; scope: string; today: string };
  focus: { status: "available" | "insufficient_data"; duration_us: number; completed_intervals: number; early_ended_intervals: number; legacy_intervals: number };
  coverage: { excluded_sessions: number; excluded_history_sessions: number; completed_only: boolean };
};
export function durationLabel(us: number) {
  const minutes = Math.floor(us / 60_000_000);
  return us > 0 && minutes === 0 ? "Dưới 1 phút" : `${Math.floor(minutes / 60)} giờ ${minutes % 60} phút`;
}
export function calendarDate(now: Date) {
  const parts = new Intl.DateTimeFormat("en", { timeZone: "Asia/Ho_Chi_Minh", year: "numeric", month: "2-digit", day: "2-digit" }).formatToParts(now);
  return ["year", "month", "day"].map(type => parts.find(p => p.type === type)!.value).join("-");
}
export function recentRange(days: number, now = new Date()) {
  const end = calendarDate(now);
  const start = new Date(end + "T00:00:00Z"); start.setUTCDate(start.getUTCDate() - days + 1);
  return { start: start.toISOString().slice(0, 10), end };
}
