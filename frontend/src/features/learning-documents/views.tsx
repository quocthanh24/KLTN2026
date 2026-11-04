"use client";
import { ReportButton } from "@/features/administration/report-button";
import { useEffect, useRef, useState } from "react";
import { chatAuth, clearAuth } from "@/features/auth/store";
import { ApiFailure, createApiClient } from "@/lib/api";
import { documentFileError, DOCUMENT_TYPES, type LearningDocument } from "./contracts";
import styles from "./documents.module.css";
import Image from "next/image";
import { PdfViewer } from "./pdf-viewer";
import { OriginalPageViewer } from "./original-page-viewer";
import { OriginalDocxViewer } from "./original-docx-viewer";

const api = createApiClient({ ...chatAuth, clear: clearAuth });
export function LearningDocuments({ channelId, canUpload }: { channelId: string; canUpload: boolean }) {
  const [documents, setDocuments] = useState<LearningDocument[] | null>(null);
  const [form, setForm] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [error, setError] = useState("");
  const [listError, setListError] = useState("");
  const [notice, setNotice] = useState("");
  const [pending, setPending] = useState(false);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const flight = useRef(false);
  const uploadId = useRef("");
  const revision = useRef(0);
  const mounted = useRef(false);
  const [viewer, setViewer] = useState<{ document: LearningDocument; file: File | null; imageUrl?: string | null } | null>(null);
  useEffect(() => () => { if (viewer?.imageUrl) URL.revokeObjectURL(viewer.imageUrl); }, [viewer?.imageUrl]);
  const [editing, setEditing] = useState<LearningDocument | null>(null);
  const [editName, setEditName] = useState("");
  const [editDescription, setEditDescription] = useState("");
  const [deleting, setDeleting] = useState<LearningDocument | null>(null);
  const [busy, setBusy] = useState(false);
  const [denied, setDenied] = useState(false);
  const actionFlight = useRef(false);
  const reload = useRef<() => Promise<void>>(async () => {});
  useEffect(() => {
    mounted.current = true;
    let active = true, polling = false;
    async function refresh() {
      if (polling) return;
      polling = true;
      const started = revision.current;
      try {
        const result = await api<LearningDocument[]>(`/channels/${channelId}/learning-documents`, { protected: true });
        if (active && started === revision.current) {
          setDocuments(result); setListError(""); setDenied(false);
          setViewer(current => current && result.some(d => d.id === current.document.id) ? current : null);
          setEditing(current => current && result.some(d => d.id === current.id && d.can_manage !== false) ? current : null);
          setDeleting(current => current && result.some(d => d.id === current.id && d.can_manage !== false) ? current : null);
        }
      } catch (failure) {
        if (active) setListError(failure instanceof Error ? failure.message : "Không thể tải tài liệu.");
        if (active && failure instanceof ApiFailure && failure.status === 403) {
          setDenied(true); setDocuments(null); setViewer(null); setEditing(null); setDeleting(null); setForm(false);
        }
      } finally { polling = false; }
    }
    void refresh();
    reload.current = refresh;
    const timer = setInterval(() => void refresh(), 2000);
    return () => { active = false; mounted.current = false; clearInterval(timer); };
  }, [channelId]);
  const scoped = (document: LearningDocument) => `/learning-documents/${document.id}?channel_id=${channelId}`;
  async function perform(action: () => Promise<void>) {
    if (actionFlight.current) return;
    actionFlight.current = true; setBusy(true); setError(""); setNotice("");
    try { await action(); }
    catch (failure) {
      if (!mounted.current) return;
      setError(failure instanceof Error ? failure.message : "Thao tác tài liệu thất bại.");
      if (failure instanceof ApiFailure && [403, 404].includes(failure.status)) {
        setViewer(null); setEditing(null); setDeleting(null); await reload.current();
      }
    } finally { actionFlight.current = false; if (mounted.current) setBusy(false); }
  }
  async function openDocument(document: LearningDocument) {
    const detail = await api<LearningDocument>(scoped(document), { protected: true });
    const file = detail.content_type === "application/pdf" && detail.size_bytes <= 32 * 1024 * 1024 ? await api<File>(`/learning-documents/${detail.id}/preview?channel_id=${channelId}`, { protected: true, download: true }) : null;
    const image = detail.content_type.startsWith("image/") ? await api<File>(`/learning-documents/${detail.id}/image?channel_id=${channelId}`, { protected: true, download: true }) : null;
    if (mounted.current) setViewer({ document: detail, file, imageUrl: image ? URL.createObjectURL(image) : null });
  }
  async function downloadDocument(document: LearningDocument) {
    const file = await api<File>(`/learning-documents/${document.id}/download?channel_id=${channelId}`, { protected: true, download: true, downloadFilename: document.original_filename });
    if (!mounted.current) return;
    const url = URL.createObjectURL(file);
    const link = window.document.createElement("a"); link.href = url; link.download = file.name;
    window.document.body.appendChild(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  function reset() { setForm(false); setFile(null); setName(""); setDescription(""); uploadId.current = ""; setError(""); }
  async function upload() {
    if (flight.current || !file) return;
    const validation = documentFileError(file);
    if (validation) { setError(validation); return; }
    flight.current = true; setPending(true); setError(""); setNotice("");
    if (!uploadId.current) uploadId.current = crypto.randomUUID();
    const body = new FormData();
    // Browsers can omit MIME for a supported extension; server validates bytes too.
    const mime = DOCUMENT_TYPES[file.name.split(".").pop()?.toLowerCase() ?? ""];
    body.append("file", new File([file], file.name, { type: file.type || mime }));
    body.append("name", name); body.append("description", description); body.append("upload_id", uploadId.current);
    try {
      const result = await api<LearningDocument>(`/channels/${channelId}/learning-documents`, { method: "POST", protected: true, body });
      if (!mounted.current) return;
      revision.current++;
      setDocuments(current => [result, ...(current ?? []).filter(d => d.id !== result.id)]);
      reset(); setNotice("Đã tải tài liệu lên.");
    } catch (failure) { if (mounted.current) setError(failure instanceof Error ? failure.message : "Không thể tải tài liệu lên."); }
    finally { flight.current = false; if (mounted.current) setPending(false); }
  }
  if (denied) return <section className={styles.area} aria-label="Tài liệu học tập"><p role="alert">Bạn không còn quyền truy cập khu vực tài liệu. {listError}</p></section>;
  return <section className={styles.area} aria-label="Tài liệu học tập">
    <h3>Tài liệu học tập</h3>
    <p>PDF, Word DOCX, JPG hoặc PNG · Tối đa 200 MiB mỗi tệp.</p>
    {canUpload && !form && <button onClick={() => { reset(); setNotice(""); setForm(true); }}>Tải tài liệu lên</button>}
    {!canUpload && <p>Chủ sở hữu và quản trị viên Workspace có quyền tải tài liệu lên.</p>}
    {form && canUpload && <form onSubmit={event => { event.preventDefault(); void upload(); }}>
      <fieldset disabled={pending}>
        <label htmlFor={`document-file-${channelId}`}>Chọn tài liệu</label>
        <input id={`document-file-${channelId}`} type="file" accept=".pdf,.docx,.jpg,.jpeg,.png" onChange={event => {
          const selected = event.target.files?.[0] ?? null; setFile(selected); uploadId.current = ""; setError(selected ? documentFileError(selected) : "");
        }} />
        {file && <p>{file.name} · {(file.size / 1024).toFixed(1)} KiB</p>}
        <label htmlFor={`document-name-${channelId}`}>Tên tài liệu (tùy chọn)</label>
        <input id={`document-name-${channelId}`} value={name} maxLength={255} placeholder="Để trống để dùng tên tệp" onChange={event => { setName(event.target.value); uploadId.current = ""; }} />
        <label htmlFor={`document-description-${channelId}`}>Mô tả tài liệu (tùy chọn)</label>
        <textarea id={`document-description-${channelId}`} value={description} maxLength={2000} onChange={event => { setDescription(event.target.value); uploadId.current = ""; }} />
        <div className="actions"><button disabled={!file || !!(file && documentFileError(file))}>{pending ? "Đang tải tài liệu…" : "Xác nhận tải lên"}</button>
          <button type="button" onClick={reset}>Hủy</button></div>
      </fieldset>
    </form>}
    {error && <p role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
    {listError && <p role="alert">{listError}</p>}
    {!documents && !listError && <p role="status">Đang tải danh sách tài liệu…</p>}
    {documents?.length === 0 && <p>Chưa có tài liệu học tập trong kênh.</p>}
    {viewer && <section className={styles.panel} role="dialog" aria-modal="true" aria-label="Xem tài liệu">
      <h4>{viewer.document.name}</h4><p>{viewer.document.description}</p>
      <p>{viewer.document.original_filename} · {viewer.document.uploader_name}</p>
      <button onClick={() => setViewer(null)}>Đóng tài liệu</button>
      <button disabled={busy} onClick={() => void perform(() => downloadDocument(viewer.document))}>Tải xuống</button>
      {viewer.file ? <PdfViewer file={viewer.file} /> : viewer.document.content_type === "application/pdf" ? <OriginalPageViewer key={viewer.document.id} documentId={viewer.document.id} channelId={channelId} revision={viewer.document.checksum} /> : viewer.imageUrl ? <Image src={viewer.imageUrl} width={900} height={900} style={{ width: "100%", height: "auto" }} unoptimized alt="Ảnh tài liệu gốc" /> : <OriginalDocxViewer documentId={viewer.document.id} channelId={channelId} revision={viewer.document.checksum} />}
    </section>}
    {editing && <form className={styles.panel} aria-label="Chỉnh sửa tài liệu" onSubmit={event => { event.preventDefault(); void perform(async () => {
      const result = await api<LearningDocument>(scoped(editing), { method: "PATCH", protected: true, body: { name: editName.trim(), description: editDescription.trim() || null } });
      if (mounted.current) { revision.current++; setDocuments(current => current?.map(d => d.id === result.id ? result : d) ?? null); setEditing(null); setNotice("Đã cập nhật tài liệu."); }
    }); }}><fieldset disabled={busy}>
      <label htmlFor={`edit-name-${channelId}`}>Tên tài liệu</label><input id={`edit-name-${channelId}`} value={editName} required maxLength={255} onChange={event => setEditName(event.target.value)} />
      <label htmlFor={`edit-description-${channelId}`}>Mô tả</label><textarea id={`edit-description-${channelId}`} value={editDescription} maxLength={2000} onChange={event => setEditDescription(event.target.value)} />
      <button disabled={!editName.trim()}>Lưu thay đổi</button><button type="button" onClick={() => setEditing(null)}>Hủy chỉnh sửa</button>
    </fieldset></form>}
    {deleting && <section className={styles.panel} role="dialog" aria-modal="true" aria-label="Xác nhận xóa tài liệu">
      <p>Xóa “{deleting.name}”? Tài liệu sẽ không còn được truy cập hoặc sử dụng làm nguồn kiến thức cho trợ lý AI.</p>
      <button disabled={busy} onClick={() => void perform(async () => {
        await api<void>(scoped(deleting), { method: "DELETE", protected: true });
        if (mounted.current) { revision.current++; setDocuments(current => current?.filter(d => d.id !== deleting.id) ?? null); setViewer(null); setDeleting(null); setNotice("Đã xóa tài liệu."); }
      })}>Xác nhận xóa</button><button disabled={busy} onClick={() => setDeleting(null)}>Hủy xóa</button>
    </section>}
    <ul className={styles.list} aria-label="Danh sách tài liệu học tập">{documents?.map(document => <li key={document.id} data-document-id={document.id}>
      <strong>{document.name}</strong>{document.description && <p>{document.description}</p>}
      <p>{document.content_type === "application/pdf" ? "PDF" : document.content_type.startsWith("image/") ? "Ảnh" : "Word DOCX"} · {(document.size_bytes / 1024).toFixed(1)} KiB</p>
      <p>Người tải: {document.uploader_name || "Không rõ"} · {new Date(document.created_at).toLocaleString("vi-VN")}</p>
      <div className="actions"><button disabled={busy} onClick={() => void perform(() => openDocument(document))}>Xem tài liệu</button>
      <button disabled={busy} onClick={() => void perform(() => downloadDocument(document))}>Tải tệp gốc</button>
      <ReportButton targetType="DOCUMENT" targetId={document.id} />
      {(document.can_manage ?? canUpload) && <><button disabled={busy} onClick={() => void perform(async () => {
        const detail = await api<LearningDocument>(scoped(document), { protected: true });
        if (mounted.current) { setEditing(detail); setEditName(detail.name); setEditDescription(detail.description ?? ""); }
      })}>Chỉnh sửa</button><button disabled={busy} onClick={() => setDeleting(document)}>Xóa tài liệu</button></>}
      </div>
    </li>)}</ul>
  </section>;
}
