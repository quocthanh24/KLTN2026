export type PomodoroState = {
  channel_id: string; revision: number; status: "idle" | "running" | "paused" | "ended";
  phase: "focus" | "break"; focus_seconds: number; break_seconds: number;
  deadline: string | null; remaining_seconds: number | null; server_now: string;
  session_id: string | null; completed_focus: number; can_control: boolean;
};
export function remaining(state: PomodoroState, elapsedSeconds: number): number {
  if (state.status === "paused") return state.remaining_seconds ?? 0;
  if (state.status !== "running" || !state.deadline) return 0;
  return Math.max(0, (Date.parse(state.deadline) - Date.parse(state.server_now)) / 1000 - elapsedSeconds);
}
export function formatTime(seconds: number): string {
  const value = Math.max(0, Math.ceil(seconds));
  return `${Math.floor(value / 60).toString().padStart(2, "0")}:${(value % 60).toString().padStart(2, "0")}`;
}
