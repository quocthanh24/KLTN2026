"use client";
import { useEffect, useState } from "react";
import { chatAuth, clearAuth } from "@/features/auth/store";
import { createApiClient } from "@/lib/api";
const api = createApiClient({ ...chatAuth, clear: clearAuth });
type Status = { engine: string; model: string; channel_id: string; conversation_id: string; cap_usd: number; remaining_usd: number; calls: number; max_calls: number; paused: boolean; manual: boolean; blocked: boolean; expires_at?: string; source_scope?: string; acceptance_warning?: string };
export function PilotStatus({ channelId, select }: { channelId: string; select: (id: string) => void }) {
  const [status, setStatus] = useState<Status | null>(null), [error, setError] = useState("");
  const enabled = process.env.NEXT_PUBLIC_KLTN_PILOT === "true";
  const path = `/channels/${channelId}/ai/pilot`;
  useEffect(() => {
    if (!enabled) return;
    let alive = true;
    const refresh = () => { void api<Status>(path, { protected: true }).then(s => { if (alive) { setStatus(s); setError(""); } }).catch(() => { if (alive) { setStatus(null); setError("Channel này chưa xác nhận V2 pilot; không gửi câu hỏi."); } }); };
    refresh(); const timer = setInterval(refresh, 5000);
    return () => { alive = false; clearInterval(timer); };
  }, [enabled, path]);
  if (!enabled) return null;
  async function mode(action: string) {
    try { setStatus(await api<Status>(`${path}/${action}`, { method: "POST", protected: true })); setError(""); }
    catch (e) { setError(e instanceof Error ? e.message : "Không thể đổi trạng thái pilot."); }
  }
  return <aside role="status" aria-label="Trạng thái AI Chat V2 pilot" style={{ padding: 12, background: "#e5f2ff", color: "#102e53" }}>
    {status ? <><strong>{status.engine} · {status.model}</strong><div>Channel: {status.channel_id}</div>
      <div>Trần chung ${status.cap_usd.toFixed(2)} · Còn theo ledger ${status.remaining_usd.toFixed(6)} · {status.calls}/{status.max_calls} calls</div>
      <div>{status.blocked ? "Đã chặn API: kiểm tra ngân sách/usage/thời hạn; không tự cấp thêm." : status.paused ? "Đang tạm dừng API; kết quả vẫn được giữ." : status.manual ? "Bạn đang tự nghiệm thu với cùng ngân sách còn lại." : "Đang chạy bộ ca nghiệm thu."}</div>
      {status.acceptance_warning && <div role="alert">{status.acceptance_warning}</div>}
      <button onClick={() => select(status.conversation_id)}>Mở hội thoại nghiệm thu V2</button>{" "}
      {status.paused && !status.blocked && <button onClick={() => void mode("manual")}>Tự chat tiếp trong grant hiện hành</button>}
      {!status.paused && <button onClick={() => void mode("pause")}>Tạm dừng API</button>}
      <div>{status.source_scope}</div>{status.expires_at && <div>Grant expires: {new Date(status.expires_at).toLocaleString("vi-VN")}</div>}</> : <strong>V2 pilot unavailable</strong>}
    {error && <div role="alert">{error}</div>}
  </aside>;
}
