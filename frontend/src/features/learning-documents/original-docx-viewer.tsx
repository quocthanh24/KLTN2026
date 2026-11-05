"use client";
import { useEffect, useState } from "react";
import { chatAuth, clearAuth } from "@/features/auth/store";
import { createApiClient } from "@/lib/api";

const api = createApiClient({ ...chatAuth, clear: clearAuth });
const namespace = "http://schemas.openxmlformats.org/wordprocessingml/2006/main";
type Block = { unit: number; kind: string; text: string; original_xml: string; needs_visual: boolean; revision: string };
type Props = { documentId: string; channelId: string; initialUnit?: number; revision?: string };

export function OriginalDocxViewer(props: Props) {
  return <BlockViewer key={`${props.channelId}:${props.documentId}:${props.revision}:${props.initialUnit}`} {...props} />;
}
function BlockViewer({ documentId, channelId, initialUnit = 1, revision }: Props) {
  const [unit, setUnit] = useState(initialUnit), [count, setCount] = useState(0);
  const [block, setBlock] = useState<Block | null>(null), [error, setError] = useState("");
  const [sourceRevision, setSourceRevision] = useState(""), [attempt, setAttempt] = useState(0);
  const scope = `channel_id=${channelId}`;
  useEffect(() => {
    let active = true;
    void api<{ unit_count: number; revision: string }>(`/learning-documents/${documentId}/inventory?${scope}${revision ? `&revision=${revision}` : ""}`, { protected: true, timeoutMs: 45000 })
      .then(value => { if (active) { setCount(value.unit_count); setSourceRevision(value.revision); } })
      .catch(failure => { if (active) setError(failure instanceof Error ? failure.message : "Không thể mở DOCX gốc."); });
    return () => { active = false; };
  }, [documentId, scope, revision, attempt]);
  useEffect(() => {
    if (!sourceRevision) return;
    let active = true;
    void api<Block>(`/learning-documents/${documentId}/blocks/${unit}?${scope}&revision=${sourceRevision}`, { protected: true, timeoutMs: 45000 })
      .then(value => { if (active) setBlock(value); })
      .catch(failure => { if (active) setError(failure instanceof Error ? failure.message : "Không thể mở khối DOCX gốc."); });
    return () => { active = false; };
  }, [documentId, scope, sourceRevision, unit, attempt]);
  const xml = block ? new DOMParser().parseFromString(block.original_xml, "application/xml") : null;
  const rows = xml ? Array.from(xml.documentElement.children).filter(node => node.localName === "tr") : [];
  return <div aria-label="Trình xem cấu trúc DOCX gốc">
    <p>Nội dung từ cấu trúc DOCX gốc theo thứ tự khối, không phải bố cục trang. Hình ảnh, trường động và bố cục phức tạp cần đối chiếu bằng file tải xuống.</p>
    <div className="actions">
      <button disabled={unit <= 1 || !block} onClick={() => { setBlock(null); setError(""); setUnit(unit - 1); }}>Khối trước</button>
      <span aria-live="polite">Khối {unit} / {count || "…"}</span>
      <button disabled={!count || unit >= count || !block} onClick={() => { setBlock(null); setError(""); setUnit(unit + 1); }}>Khối sau</button>
    </div>
    {error ? <div role="alert">{error}<button onClick={() => { setError(""); setBlock(null); setAttempt(attempt + 1); }}>Thử mở lại khối</button></div> : !block ? <p role="status">Đang mở khối gốc…</p> : <>
      {block.needs_visual && <p role="status">Khối có hình hoặc bố cục chưa hiển thị được đầy đủ. Hãy tải bản gốc để đối chiếu.</p>}
      {block.kind === "tbl" ? <table aria-label="Bảng từ DOCX gốc"><tbody>{rows.map((row, rowIndex) => <tr key={rowIndex}>{Array.from(row.children).filter(node => node.localName === "tc").map((cell, cellIndex) => {
        const span = Number(cell.getElementsByTagNameNS(namespace, "gridSpan")[0]?.getAttributeNS(namespace, "val") || 1);
        const merge = cell.getElementsByTagNameNS(namespace, "vMerge")[0];
        return <td key={cellIndex} colSpan={Math.min(100, Math.max(1, span || 1))} style={{ border: "1px solid", padding: 8, whiteSpace: "pre-wrap" }}>{Array.from(cell.getElementsByTagNameNS(namespace, "t")).map(text => text.textContent).join(" ")}{merge && <small> [ô gộp dọc — xem cấu trúc gốc]</small>}</td>;
      })}</tr>)}</tbody></table> : <p style={{ whiteSpace: "pre-wrap" }}>{block.text || "Khối không chứa văn bản thuần."}</p>}
      <details><summary>Cấu trúc XML nguyên bản của khối</summary><pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{block.original_xml}</pre></details>
    </>}
  </div>;
}
