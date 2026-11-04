"use client";
import { useEffect, useState } from "react";
import Image from "next/image";
import { chatAuth, clearAuth } from "@/features/auth/store";
import { createApiClient } from "@/lib/api";

const api = createApiClient({ ...chatAuth, clear: clearAuth });
type Props = { documentId: string; channelId: string; initialPage?: number; revision?: string };
export function OriginalPageViewer(props: Props) {
  return <PageViewer key={`${props.channelId}:${props.documentId}:${props.revision}:${props.initialPage}`} {...props} />;
}
function PageViewer({ documentId, channelId, initialPage = 1, revision }: Props) {
  const [count, setCount] = useState(0), [page, setPage] = useState(initialPage);
  const [sourceRevision, setSourceRevision] = useState(""), [attempt, setAttempt] = useState(0);
  const [url, setUrl] = useState(""), [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    void api<{ unit_count: number; revision: string }>(`/learning-documents/${documentId}/inventory?channel_id=${channelId}${revision ? `&revision=${revision}` : ""}`, { protected: true, timeoutMs: 45000 })
      .then(value => { if (active) { setCount(value.unit_count); setSourceRevision(value.revision); } })
      .catch(failure => { if (active) setError(failure instanceof Error ? failure.message : "Không thể kiểm kê bản gốc."); });
    return () => { active = false; };
  }, [documentId, channelId, revision, attempt]);
  useEffect(() => {
    if (!sourceRevision) return;
    let active = true, objectUrl = "";
    void api<File>(`/learning-documents/${documentId}/pages/${page}/image?channel_id=${channelId}&revision=${sourceRevision}`, { protected: true, download: true, timeoutMs: 45000 })
      .then(file => {
        if (!active) return;
        objectUrl = URL.createObjectURL(file); setUrl(objectUrl);
      }).catch(failure => { if (active) setError(failure instanceof Error ? failure.message : "Không thể mở trang gốc."); });
    return () => { active = false; if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [documentId, channelId, page, sourceRevision, attempt]);
  return <div aria-label="Trình xem từng trang gốc">
    <p>Đang xem từng trang từ bản gốc; không tải toàn bộ PDF vào trình duyệt. Việc xem trang không gọi AI/OCR.</p>
    <div className="actions">
      <button disabled={page <= 1 || !url} onClick={() => { setUrl(""); setError(""); setPage(page - 1); }}>Trang trước</button>
      <span aria-live="polite">Trang {page} / {count || "…"}</span>
      <button disabled={!count || page >= count || !url} onClick={() => { setUrl(""); setError(""); setPage(page + 1); }}>Trang sau</button>
    </div>
    {error ? <div role="alert">{error}<button onClick={() => { setError(""); setUrl(""); setAttempt(attempt + 1); }}>Thử mở lại trang</button></div> : !url ? <p role="status">Đang mở trang gốc…</p> :
      <Image src={url} alt={`Nội dung PDF trang ${page}`} width={1500} height={2200} unoptimized style={{ width: "100%", height: "auto" }} />}
  </div>;
}
