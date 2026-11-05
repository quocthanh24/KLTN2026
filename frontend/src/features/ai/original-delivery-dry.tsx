"use client";
import { useState } from "react";
import { chatAuth, clearAuth } from "@/features/auth/store";
import { createApiClient } from "@/lib/api";
const api = createApiClient({ ...chatAuth, clear: clearAuth });
export function OriginalDeliveryDry({ channelId }: { channelId: string }) {
  const [result, setResult] = useState(""), [busy, setBusy] = useState(false);
  if (process.env.NEXT_PUBLIC_KLTN_ORIGINAL_DRY_RUN !== "true") return null;
  async function check(page: number) {
    setBusy(true);
    try { const proof = await api<unknown>(`/channels/${channelId}/ai/original-delivery-dry-run`, { method: "POST", protected: true, body: { page }, timeoutMs: 90000 }); setResult(JSON.stringify(proof, null, 2)); }
    catch (e) { setResult(e instanceof Error ? e.message : "Dry run failed."); }
    finally { setBusy(false); }
  }
  return <aside aria-label="V2 Original image delivery dry run" style={{ padding: 12, background: "#fff2cb", color: "#382d00", maxHeight: 320, overflow: "auto" }}>
    <strong>V2 ORIGINAL IMAGE DELIVERY — OFFLINE DRY-RUN</strong>
    <p>PDF thật từ MinIO → V2 → SDK serialization. Không gọi GPT thật, không tính phí, không lưu câu trả lời giả.</p>
    {[5, 6, 7].map(page => <button key={page} disabled={busy} onClick={() => void check(page)}>Kiểm tra payload trang {page}</button>)}
    <pre aria-label="Bằng chứng payload SDK" style={{ whiteSpace: "pre-wrap" }}>{result}</pre>
  </aside>;
}
