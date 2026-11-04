"use client";
import styles from "./assistant.module.css";
import { useCallback, useEffect, useRef, useState } from "react";
import { chatAuth, clearAuth } from "@/features/auth/store";
import { ApiFailure, createApiClient } from "@/lib/api";
import { OriginalPageViewer } from "@/features/learning-documents/original-page-viewer";
import { OriginalDocxViewer } from "@/features/learning-documents/original-docx-viewer";
import { PdfViewer } from "@/features/learning-documents/pdf-viewer";
import { AnswerMarkdown, CopyButton } from "./answer-markdown";
import { useAuth } from "@/features/auth/provider";
import Image from "next/image";
import { PilotStatus } from "./pilot-status";
import { OriginalDeliveryDry } from "./original-delivery-dry";

const api = createApiClient({ ...chatAuth, clear: clearAuth });
type Conversation = { id: string; title: string; updated_at: string };
type Source = { document_id: string; chunk_id: string; name: string; revision?: string; authority?: string; representation?: { kind: string }; location: { page?: number; body_block?: number; paragraph?: number; table_block?: number; row?: number; cell?: number; part?: string; section?: number; image_id?: string; embedded_image_id?: string }; quote: string | null; available: boolean; content_type?: string; size_bytes?: number };
function sourcePosition(location: Source["location"]) {
  if (location.page) return `Trang PDF vật lý ${location.page}`;
  if (location.body_block) return `Khối nội dung ${location.body_block} trong DOCX`;
  const part = location.part === "header" ? `Đầu trang · Section ${location.section} · ` : location.part === "footer" ? `Chân trang · Section ${location.section} · ` : "";
  return part + (location.paragraph ? `Đoạn ${location.paragraph}` : location.table_block ? `Bảng ${location.table_block} · Hàng ${location.row} · Ô ${location.cell}` : "");
}
type SourceSelection = { document_id: string; page_numbering: "unspecified" | "physical" | "printed" };
type V2Task = { id: string; status: string; stage: string; resumable: boolean; error_code?: string | null; reason_codes?: string[]; status_message?: string; source_selection?: SourceSelection | null; progress: { whole_scope?: boolean; unread_source_ids?: string[]; missing_child_ids?: string[] } };
type Turn = { id: string; request_id: string; question: string; answer: string | null; status: string; sources: Source[]; v2_task?: V2Task };
type Detail = Conversation & { turns: Turn[]; next_offset: number | null };
type Listing = { items: Conversation[]; next_offset: number | null };

export function AIAssistant({ channelId, channelName }: { channelId: string; channelName: string }) {
  const { user, authStatus } = useAuth();
  if (authStatus !== "authenticated" || !user) return null;
  return <AssistantPanel key={`${user.id}-${channelId}`} channelId={channelId} channelName={channelName} />;
}

function AssistantPanel({ channelId, channelName }: { channelId: string; channelName: string }) {
  const base = `/channels/${channelId}/ai/conversations`;
  const [open, setOpen] = useState(false), [list, setList] = useState<Listing>({ items: [], next_offset: null });
  const [detail, setDetail] = useState<Detail | null>(null), [error, setError] = useState("");
  const [question, setQuestion] = useState(""), [busy, setBusy] = useState(false), [denied, setDenied] = useState(false);
  const [sourceDocuments, setSourceDocuments] = useState<{ id: string; name: string }[]>([]);
  const [sourceSelection, setSourceSelection] = useState<SourceSelection | null>(null);
  useEffect(() => {
    if (!open) return;
    let active = true;
    void api<{ id: string; name: string }[]>(`/channels/${channelId}/learning-documents`, { protected: true })
      .then(documents => { if (active) setSourceDocuments(documents); })
      .catch(failure => { if (active) setError(failure instanceof Error ? failure.message : "Không thể tải danh sách tài liệu."); });
    return () => { active = false; };
  }, [channelId, open]);
  const [renaming, setRenaming] = useState<Conversation | null>(null), [title, setTitle] = useState(""), [deleting, setDeleting] = useState<Conversation | null>(null);
  const [history, setHistory] = useState(false), [menu, setMenu] = useState<string | null>(null);
  const menuBox = useRef<HTMLDivElement>(null), modal = useRef<HTMLDivElement>(null);
  const sourcePanel = useRef<HTMLDivElement>(null);
  const sourceTrigger = useRef<HTMLButtonElement | null>(null);
  const historyToggle = useRef<HTMLButtonElement>(null);
  const menuTrigger = useRef<HTMLButtonElement | null>(null);
  const [viewer, setViewer] = useState<{ source: Source; file: File | null; imageUrl?: string | null } | null>(null);
  useEffect(() => () => { if (viewer?.imageUrl) URL.revokeObjectURL(viewer.imageUrl); }, [viewer?.imageUrl]);
  const [pending, setPending] = useState<{ question: string; id: string } | null>(null);
  const [pendingTask, setPendingTask] = useState<V2Task | null>(null), [cancelling, setCancelling] = useState(false);
  const cancelledRequest = useRef<string | null>(null);
  const [phase, setPhase] = useState("retrieving");
  const composing = useRef(false), stickToBottom = useRef(true), scrollPosition = useRef(0);
  const messages = useRef<HTMLDivElement>(null), input = useRef<HTMLTextAreaElement>(null);
  const phaseLabels: Record<string, string> = { retrieving: "Đang tìm tài liệu liên quan…", generating: "Đang tạo câu trả lời…", verifying: "Đang đối chiếu câu trả lời với nguồn…", saving: "Đang lưu hội thoại…", completed: "Đang hoàn tất…" };
  const flight = useRef(false), alive = useRef(true), retry = useRef<{ question: string; id: string } | null>(null);
  const selected = useRef<string | null>(null);
  const displayedTurns = useRef(50);
  const launcher = useRef<HTMLButtonElement>(null), panel = useRef<HTMLDivElement>(null);
  useEffect(() => { if (open) panel.current?.focus(); }, [open]);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => {
    if (messages.current) messages.current.scrollTop = stickToBottom.current ? messages.current.scrollHeight : scrollPosition.current;
  }, [detail?.id, detail?.turns.length, pending?.id, history, open]);
  useEffect(() => {
    if (!pending || !detail) return;
    let active = true, checking = false;
    const timer = setInterval(() => {
      if (checking) return;
      checking = true;
      void api<{ stage: string; v2_task?: V2Task }>(`${base}/${detail.id}/requests/${pending.id}/progress`, { protected: true })
        .then(value => { if (active) { setPhase(value.stage); setPendingTask(value.v2_task ?? null); } })
        .catch(() => { /* The main request reports authorization/network errors. */ })
        .finally(() => { checking = false; });
    }, 1200);
    return () => { active = false; clearInterval(timer); };
  }, [base, detail, pending]);
  useEffect(() => {
    if (!open) return;
    const viewport = window.visualViewport;
    const update = () => {
      panel.current?.style.setProperty("--ai-viewport-height", `${viewport?.height ?? window.innerHeight}px`);
      panel.current?.style.setProperty("--ai-viewport-top", `${viewport?.offsetTop ?? 0}px`);
    };
    update(); viewport?.addEventListener("resize", update); viewport?.addEventListener("scroll", update);
    return () => { viewport?.removeEventListener("resize", update); viewport?.removeEventListener("scroll", update); };
  }, [open]);
  useEffect(() => {
    const el = input.current;
    if (el) { el.style.height = "0px"; el.style.height = `${Math.min(el.scrollHeight, 132)}px`; }
  }, [question, open, history, detail?.id]);
  useEffect(() => {
    if (!menu) return;
    menuBox.current?.querySelector<HTMLButtonElement>("[role=menuitem]")?.focus();
    const outside = (event: PointerEvent) => {
      if (!menuBox.current?.contains(event.target as Node) && !menuTrigger.current?.contains(event.target as Node)) setMenu(null);
    };
    document.addEventListener("pointerdown", outside);
    return () => document.removeEventListener("pointerdown", outside);
  }, [menu]);
  useEffect(() => {
    if (renaming || deleting) {
      const target = modal.current?.querySelector<HTMLInputElement | HTMLButtonElement>("input, button");
      target?.focus(); if (target instanceof HTMLInputElement) target.select();
    }
  }, [renaming, deleting]);
  useEffect(() => { if (viewer) sourcePanel.current?.focus(); }, [viewer]);
  function dismissModal() { setRenaming(null); setDeleting(null); requestAnimationFrame(() => menuTrigger.current?.focus()); }
  function dismissSource() { setViewer(null); requestAnimationFrame(() => sourceTrigger.current?.focus()); }
  function openSource(turn: Turn, index: number, trigger: HTMLButtonElement) {
    if (!detail || !turn.sources[index]?.available) return;
    sourceTrigger.current = trigger;
    void run(async () => {
            const checked = await api<Source>(`${base}/${detail.id}/turns/${turn.id}/sources/${index}`, { protected: true });
            const file = checked.content_type === "application/pdf" && (checked.size_bytes ?? 0) <= 32 * 1024 * 1024 ? await api<File>(`/learning-documents/${checked.document_id}/preview?channel_id=${channelId}`, { protected: true, download: true }) : null;
            const image = checked.location.embedded_image_id ? await api<File>(`/learning-documents/${checked.document_id}/regions/${encodeURIComponent(checked.location.embedded_image_id)}/image?channel_id=${channelId}`, { protected: true, download: true }) : checked.location.image_id === "original" ? await api<File>(`/learning-documents/${checked.document_id}/image?channel_id=${channelId}`, { protected: true, download: true }) : null;
            setViewer({ source: checked, file, imageUrl: image ? URL.createObjectURL(image) : null });
    });
  }
  const conversationDetail = useCallback(async (id: string, minimum = 50) => {
    let data = await api<Detail>(`${base}/${id}`, { protected: true });
    while (data.next_offset !== null && data.turns.length < minimum) {
      const more = await api<Detail>(`${base}/${id}?offset=${data.next_offset}`, { protected: true });
      data = { ...more, turns: [...data.turns, ...more.turns] };
    }
    return data;
  }, [base]);
  async function load(id?: string | null) {
    const listing = await api<Listing>(base, { protected: true });
    const target = id === undefined ? listing.items[0]?.id : id;
    if (target !== selected.current) stickToBottom.current = true;
    const data = target ? await conversationDetail(target) : null;
    if (alive.current) { displayedTurns.current = data?.turns.length ?? 50; setList(listing); setDetail(data); selected.current = data?.id ?? null; setDenied(false); setSourceSelection(data?.turns.at(-1)?.v2_task?.source_selection ?? null); }
  }
  async function run(work: () => Promise<void>) {
    if (flight.current) return;
    flight.current = true; setBusy(true); setError("");
    try { await work(); }
    catch (failure) {
      if (!alive.current) return;
      setError(failure instanceof Error ? failure.message : "Yêu cầu chưa hoàn tất.");
      if (failure instanceof ApiFailure && (failure.status === 403 || failure.status === 401)) {
        setDenied(true); setDetail(null); setList({ items: [], next_offset: null }); setViewer(null); setRenaming(null); setDeleting(null);
      } else if (failure instanceof ApiFailure && failure.status === 404) { setRenaming(null); setDeleting(null); await load().catch(() => {}); }
    } finally { flight.current = false; if (alive.current) setBusy(false); }
  }
  useEffect(() => {
    if (!open) return;
    let active = true;
    const timer = setInterval(() => {
      if (flight.current) return;
      void (async () => {
        await api<Listing>(base, { protected: true });
        const id = selected.current;
        if (id) {
          const current = await conversationDetail(id, displayedTurns.current);
          if (active && !flight.current && selected.current === id) {
            displayedTurns.current = current.turns.length;
            setDetail(current);
            setViewer(previous => previous && current.turns.some(t => t.sources.some(s => s.chunk_id === previous.source.chunk_id && s.available)) ? previous : null);
          }
        }
      })().catch(failure => {
        if (active && failure instanceof ApiFailure && (failure.status === 403 || failure.status === 401)) {
          setDenied(true); setDetail(null); setList({ items: [], next_offset: null }); setViewer(null); setRenaming(null); setDeleting(null);
        } else if (active && failure instanceof ApiFailure && failure.status === 404) {
          setDetail(null); selected.current = null; setViewer(null); setRenaming(null); setDeleting(null);
        }
      });
    }, 3000);
    return () => { active = false; clearInterval(timer); };
  }, [base, open, conversationDetail]);
  function close() { setOpen(false); setViewer(null); setMenu(null); setRenaming(null); setDeleting(null); launcher.current?.focus(); }
  async function cancelPending() {
    if (!pendingTask || !pending || !detail || cancelling) return;
    setCancelling(true);
    try {
      const task = await api<V2Task>(`${base}/${detail.id}/v2/tasks/${pendingTask.id}/cancel`, { method: "POST", protected: true });
      if (!alive.current) return;
      if (task.status === "cancelled") { cancelledRequest.current = pending.id; retry.current = null; }
      setPendingTask(task);
    } catch (failure) { if (alive.current) setError(failure instanceof Error ? failure.message : "Không thể dừng tác vụ."); }
    finally { if (alive.current) setCancelling(false); }
  }
  async function send() {
    if (!question.trim() || !detail || flight.current || composing.current) return;
    await run(async () => {
      if (!retry.current || retry.current.question !== question.trim()) retry.current = { question: question.trim(), id: crypto.randomUUID() };
      const attempt = retry.current;
      stickToBottom.current = true; setPhase("retrieving"); setPendingTask(null); setPending(attempt);
      performance.mark("ai:request-start");
      try {
        const turn = await api<Turn>(`${base}/${detail.id}/questions`, { method: "POST", protected: true, timeoutMs: 315000, body: { question: attempt.question, request_id: attempt.id, source_selection: sourceSelection } });
        performance.mark("ai:response"); performance.measure("ai:request", "ai:request-start", "ai:response");
        if (!alive.current) return;
        const newTitle = detail.title === "Hội thoại mới" ? attempt.question.slice(0, 120) : detail.title;
        displayedTurns.current = detail.turns.filter(t => t.request_id !== attempt.id).length + 1;
        setDetail(current => current ? { ...current, title: newTitle, turns: [...current.turns.filter(t => t.request_id !== attempt.id), turn] } : current);
        setList(current => { const active = current.items.find(c => c.id === detail.id); return { ...current, items: active ? [{ ...active, title: newTitle }, ...current.items.filter(c => c.id !== detail.id)] : current.items }; });
        retry.current = null; setQuestion("");
        requestAnimationFrame(() => { performance.mark("ai:render"); performance.measure("ai:response-render", "ai:response", "ai:render"); input.current?.focus(); });
      } catch (failure) {
        if (failure instanceof ApiFailure && ["AI_V2_JOB_TERMINAL", "AI_V2_SOURCE_CHANGED", "AI_SOURCE_UNAVAILABLE"].includes(failure.error.code)) retry.current = { question: attempt.question, id: crypto.randomUUID() };
        if (cancelledRequest.current !== attempt.id) {
          if (!(failure instanceof ApiFailure) || failure.status === 0) {
            await load(detail.id).catch(() => {});
            setSourceSelection(sourceSelection);
            throw new Error("Kết nối bị gián đoạn trước khi nhận đủ câu trả lời. Hãy kiểm tra lượt đã lưu hoặc gửi lại cùng câu hỏi; hệ thống sẽ kiểm tra tác vụ trước khi chạy lại.");
          }
          throw failure;
        }
        await load(detail.id);
      } finally { if (alive.current) { setPending(null); setPendingTask(null); } }
    });
  }
  return <section aria-label="Trợ lý AI" className={styles.widget}>
    <button ref={launcher} className={styles.launcher} aria-label={open ? "Đóng trợ lý AI" : "Mở trợ lý AI"} aria-expanded={open} aria-controls={`ai-panel-${channelId}`} onClick={() => { if (open) close(); else { setOpen(true); void run(() => load(selected.current ?? undefined)); } }}>
      {open ? <span aria-hidden="true">×</span> : <svg aria-hidden="true" width="25" height="25" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"><path d="M20 11.5a8 8 0 0 1-8 8H4l-2 3V11.5a9 9 0 0 1 18 0Z"/><path d="M7 10h8M7 14h5"/></svg>}
    </button>
    {open && <div ref={panel} tabIndex={-1} id={`ai-panel-${channelId}`} role="dialog" aria-label={`Trợ lý AI · ${channelName}`} className={styles.panel} onKeyDown={event => { if (event.nativeEvent.isComposing || composing.current) return;
      if (event.key === "Escape") { event.stopPropagation(); if (renaming || deleting) { if (!busy) dismissModal(); } else if (viewer) dismissSource(); else if (history) { setHistory(false); historyToggle.current?.focus(); } else close(); }
      if (event.key === "Tab") {
        const container = modal.current ?? sourcePanel.current ?? panel.current;
        const nodes = Array.from(container?.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), textarea:not(:disabled), a[href], [tabindex="0"]') ?? []).filter(el => el.getClientRects().length && !el.closest('[inert]'));
        const first = nodes[0], last = nodes[nodes.length - 1];
        if (event.shiftKey && (document.activeElement === first || document.activeElement === container)) { event.preventDefault(); last?.focus(); } else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
      } }}>
      <header className={styles.header} inert={!!(renaming || deleting || viewer)}>
        <div className={styles.identity}><span className={styles.avatar} aria-hidden="true">✦</span><div><h3>Trợ lý AI</h3><span title={channelName}># {channelName}</span></div></div>
        <div className={styles.headerActions}>
          <button ref={historyToggle} aria-label="Lịch sử hội thoại" title="Lịch sử hội thoại" aria-expanded={history} aria-controls={`ai-history-${channelId}`} onClick={() => { setHistory(!history); setMenu(null); }}>☷</button>
          <button aria-label="Hội thoại mới" title="Hội thoại mới" disabled={busy || denied} onClick={() => void run(async () => { const created = await api<Conversation>(base, { method: "POST", protected: true }); retry.current = null; setQuestion(""); setHistory(false); await load(created.id); requestAnimationFrame(() => input.current?.focus()); })}>＋</button>
          <button aria-label="Thu gọn trợ lý AI" title="Thu gọn" onClick={close}>−</button>
        </div>
      </header>
      <PilotStatus channelId={channelId} select={id => void run(async () => { await load(id); setHistory(false); })} />
      <OriginalDeliveryDry channelId={channelId} />
      {error && <div role="alert" className={styles.error}>{error}<button disabled={busy} onClick={() => void (retry.current && detail && !denied ? send() : run(() => load()))}>Thử lại</button></div>}
      {denied && <p className={styles.notice}>Bạn không còn quyền xem nội dung kênh này.</p>}
      {!denied && <div className={styles.body}>
      {history ? <div id={`ai-history-${channelId}`} className={styles.history} aria-label="Danh sách hội thoại" inert={!!(renaming || deleting || viewer)}>
        <div className={styles.historyHeading}><h4>Lịch sử hội thoại</h4><button aria-label="Đóng lịch sử" onClick={() => { setHistory(false); historyToggle.current?.focus(); }}>×</button></div>
        {!list.items.length && <p className={styles.notice}>Chưa có lịch sử hỏi đáp trong kênh này.</p>}
        <ul>{list.items.map(c => <li key={c.id} className={styles.historyRow} data-selected={detail?.id === c.id}>
          <button className={styles.historyTitle} title={c.title} aria-current={detail?.id === c.id ? "true" : undefined} disabled={busy} onClick={() => void run(async () => { retry.current = null; setQuestion(""); setViewer(null); setMenu(null); await load(c.id); setHistory(false); })}>{c.title}</button>
          <button className={styles.more} aria-label={`Thao tác: ${c.title}`} aria-haspopup="menu" aria-expanded={menu === c.id} disabled={busy} onClick={event => { menuTrigger.current = event.currentTarget; setMenu(menu === c.id ? null : c.id); }}>⋯</button>
          {menu === c.id && <div ref={menuBox} className={styles.menu} role="menu" aria-label={`Quản lý ${c.title}`} onKeyDown={event => {
            const items = Array.from(event.currentTarget.querySelectorAll<HTMLButtonElement>("[role=menuitem]"));
            const index = items.indexOf(document.activeElement as HTMLButtonElement);
            if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) { event.preventDefault(); items[event.key === "Home" ? 0 : event.key === "End" ? items.length - 1 : (index + (event.key === "ArrowDown" ? 1 : -1) + items.length) % items.length]?.focus(); }
            if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); setMenu(null); menuTrigger.current?.focus(); }
            if (event.key === "Tab") { setMenu(null); menuTrigger.current?.focus(); }
          }}>
            <button role="menuitem" onClick={() => { setTitle(c.title); setRenaming(c); setMenu(null); }}>Đổi tên</button>
            <button role="menuitem" className={styles.danger} onClick={() => { setDeleting(c); setMenu(null); }}>Xóa hội thoại</button>
          </div>}
        </li>)}</ul>
        {list.next_offset !== null && <button className={styles.secondary} disabled={busy} onClick={() => void run(async () => { const more = await api<Listing>(`${base}?offset=${list.next_offset}`, { protected: true }); setList(current => ({ items: [...current.items, ...more.items], next_offset: more.next_offset })); })}>Thêm lịch sử</button>}
      </div> : detail ? <>
        <div ref={messages} inert={!!(renaming || deleting || viewer)} onScroll={event => { const el = event.currentTarget; scrollPosition.current = el.scrollTop; stickToBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 72; }} className={styles.messages} role="log" aria-label="Tin nhắn trợ lý AI" aria-live="polite">
        {!detail.turns.length && !pending && <div className={styles.empty}><span aria-hidden="true">✦</span><h4>Bạn muốn tìm hiểu điều gì?</h4><p>Hỏi chi tiết, nhờ giải thích hoặc tóm tắt tài liệu sẵn sàng trong #{channelName}.</p><button onClick={() => { setQuestion("Kênh này đang học về nội dung gì?"); input.current?.focus(); }}>Kênh này đang học gì?</button><button onClick={() => { setQuestion("Tóm tắt nội dung tài liệu trong kênh"); input.current?.focus(); }}>Tóm tắt tài liệu</button></div>}
        {detail.turns.map(turn => <article key={turn.id} className={styles.turn}>
          <div className={styles.userMessage}><span className={styles.srOnly}>Bạn: </span>{turn.question}</div><div className={styles.aiMessage}><span className={styles.aiAvatar} aria-label="Trợ lý AI">✦</span><div className={styles.answer}>
          {["answered", "partial"].includes(turn.status) ? <AnswerMarkdown text={turn.answer ?? ""} onSource={(index, trigger) => openSource(turn, index, trigger)} sourceAvailable={index => !busy && !!turn.sources[index]?.available} /> : <p className={styles.statusText}>{turn.status === "processing" ? "Yêu cầu đang được xử lý…" : turn.status === "failed" ? "Yêu cầu thất bại. Có thể gửi lại câu hỏi." : turn.status === "unverified" ? turn.answer ?? "Chưa kiểm chứng được nội dung trả lời. Bạn có thể thử lại; yêu cầu trước vẫn được giữ nguyên." : turn.answer ?? "Chưa tìm thấy đủ bằng chứng trong phạm vi tài liệu được yêu cầu."}</p>}
          {["answered", "partial"].includes(turn.status) && turn.answer && <CopyButton text={turn.answer} label="Sao chép câu trả lời" />}
          {(turn.status === "failed" || (["partial", "unverified"].includes(turn.status) && !turn.v2_task?.resumable)) && <button disabled={busy} onClick={() => { retry.current = { question: turn.question, id: crypto.randomUUID() }; setSourceSelection(turn.v2_task?.source_selection ?? null); setQuestion(turn.question); }}>Soạn lại câu hỏi</button>}
          {turn.v2_task && (turn.v2_task.status !== "complete" || turn.v2_task.progress.whole_scope) && <div className={styles.notice} role="status">
            {turn.v2_task.status === "complete" ? "Đã hoàn tất tác vụ." : turn.v2_task.status === "cancelled" ? "Đã dừng tác vụ." : turn.v2_task.status === "stale" ? "Nguồn của tác vụ đã thay đổi. Hãy soạn lại câu hỏi để đọc nguồn hiện tại." : turn.v2_task.status === "expired" ? "Tác vụ đã hết hạn. Hãy soạn lại câu hỏi để bắt đầu lượt mới." : turn.v2_task.status_message ?? (turn.v2_task.reason_codes?.includes("REVIEW_PROTOCOL_ERROR") ? "Bước kiểm chứng chưa hoàn tất. Hãy soạn lại câu hỏi; không có tác vụ chạy nền." : turn.v2_task.error_code ? "Lượt xử lý đã dừng; phần đã kiểm chứng và tiến độ đã được lưu." : "Tác vụ đang xử lý.")}
            {turn.v2_task.resumable && <>
              <button disabled={busy} onClick={() => void run(async () => {
                const updated = await api<Turn>(`${base}/${detail.id}/v2/tasks/${turn.v2_task!.id}/resume`, { method: "POST", protected: true, timeoutMs: 315000 });
                if (alive.current) setDetail(current => current ? { ...current, turns: current.turns.map(t => t.id === updated.id ? updated : t) } : current);
              })}>Tiếp tục phần còn lại</button>
              <button disabled={busy} onClick={() => void run(async () => {
                const task = await api<V2Task>(`${base}/${detail.id}/v2/tasks/${turn.v2_task!.id}/cancel`, { method: "POST", protected: true });
                if (alive.current) setDetail(current => current ? { ...current, turns: current.turns.map(t => t.id === turn.id ? { ...t, v2_task: task } : t) } : current);
              })}>Dừng tác vụ</button>
            </>}
          </div>}
          <div className={styles.citations}>{turn.sources.map((source, index) => <div key={`${source.chunk_id}-${index}`}><button className={styles.citation} title={source.available ? `Mở nguồn: ${source.name}` : "Nguồn không còn khả dụng"} disabled={busy || !source.available} onClick={event => openSource(turn, index, event.currentTarget)}>▤ {source.name}{sourcePosition(source.location) ? ` · ${sourcePosition(source.location)}` : ""}</button>{!source.available && <span> — Nguồn không còn khả dụng</span>}</div>)}</div></div></div>
        </article>)}
        {pending && <article className={styles.turn} aria-busy="true"><div className={styles.userMessage}>{pending.question}</div><p role="status" className={styles.thinking}><span className={styles.dots} aria-hidden="true">•••</span>{pendingTask?.status === "cancelled" ? "Đã yêu cầu dừng; đang chờ lượt xử lý hiện tại kết thúc." : phaseLabels[phase] ?? "Đang xử lý yêu cầu…"}</p>{pendingTask && ["running", "ready"].includes(pendingTask.status) && <button disabled={cancelling} onClick={() => void cancelPending()}>Dừng tác vụ đang chạy</button>}</article>}
        {detail.next_offset !== null && <button disabled={busy} onClick={() => void run(async () => { const more = await api<Detail>(`${base}/${detail.id}?offset=${detail.next_offset}`, { protected: true }); const turns = [...new Map([...detail.turns, ...more.turns].map(turn => [turn.id, turn])).values()]; displayedTurns.current = turns.length; setDetail(current => current ? { ...current, turns, next_offset: more.next_offset } : current); })}>Thêm lượt hỏi đáp</button>}
        </div>
        <form className={styles.composer} inert={!!(renaming || deleting || viewer)} onSubmit={event => { event.preventDefault(); void send(); }}>
          <div className={styles.sourceSelection}>
            <label>Tài liệu cho câu hỏi<select aria-label="Tài liệu cho câu hỏi" disabled={busy} value={sourceSelection?.document_id ?? ""} onChange={event => { retry.current = null; setSourceSelection(event.target.value ? { document_id: event.target.value, page_numbering: "unspecified" } : null); }}>
              <option value="">Theo ngữ cảnh hội thoại</option>{sourceDocuments.map(doc => <option key={doc.id} value={doc.id}>{doc.name}</option>)}
            </select></label>
            {sourceSelection && <label>Số trang trong yêu cầu<select aria-label="Số trang trong yêu cầu" disabled={busy} value={sourceSelection.page_numbering} onChange={event => { retry.current = null; setSourceSelection(current => current ? { ...current, page_numbering: event.target.value as SourceSelection["page_numbering"] } : null); }}>
              <option value="unspecified">Chưa xác định</option><option value="physical">Vị trí trang trong PDF (gồm bìa)</option><option value="printed">Số trang in trên tài liệu</option>
            </select></label>}
          </div>
          <label className={styles.srOnly} htmlFor={`ai-input-${channelId}`}>Câu hỏi cho trợ lý AI</label><div className={styles.inputBox}><textarea id={`ai-input-${channelId}`} ref={input} placeholder="Hỏi hoặc tóm tắt tài liệu…" rows={1} value={question} maxLength={2000} disabled={busy} onCompositionStart={() => { composing.current = true; }} onCompositionEnd={() => { composing.current = false; }} onKeyDown={event => { if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing && event.nativeEvent.keyCode !== 229 && !composing.current) { event.preventDefault(); void send(); } }} onChange={event => setQuestion(event.target.value)} /><button className={styles.send} aria-label="Gửi câu hỏi" title="Gửi câu hỏi" disabled={busy || !question.trim()}><svg aria-hidden="true" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="m5 12 7-7 7 7M12 5v15" /></svg></button></div><div className={styles.composerHint}><small>Enter gửi · Shift+Enter xuống dòng</small><small>{question.length}/2000</small></div>
        </form>
      </> : <div className={styles.empty}><span aria-hidden="true">✦</span><h4>Học cùng trợ lý AI</h4><p>Tạo hội thoại mới để hỏi và khám phá tài liệu trong kênh.</p><p>Dùng nút ＋ phía trên để bắt đầu.</p></div>}
      {busy && !pending && <p role="status" className={styles.notice}>Đang tải hội thoại…</p>}
      {(renaming || deleting) && <div className={styles.backdrop}><div ref={modal} role="dialog" aria-modal="true" aria-label={renaming ? "Đổi tên hội thoại" : "Xác nhận xóa hội thoại"} className={styles.modal}>
        {error && <p role="alert" className={styles.error}>{error}</p>}
        {renaming ? <form onSubmit={event => { event.preventDefault(); if (!title.trim()) return; void run(async () => { const target = renaming.id; await api(`${base}/${target}`, { method: "PATCH", protected: true, body: { title: title.trim() } }); setList(current => ({ ...current, items: current.items.map(c => c.id === target ? { ...c, title: title.trim() } : c) })); setDetail(current => current?.id === target ? { ...current, title: title.trim() } : current); dismissModal(); }); }}><h4>Đổi tên hội thoại</h4><label>Tên hội thoại<input value={title} maxLength={120} required disabled={busy} onChange={event => setTitle(event.target.value)} /></label><div className={styles.modalActions}><button type="button" className={styles.secondary} disabled={busy} onClick={dismissModal}>Hủy</button><button disabled={busy || !title.trim()}>Lưu tên</button></div></form> : <><h4>Xóa hội thoại?</h4><p>“{deleting?.title}” và lịch sử hỏi đáp sẽ bị xóa. Tài liệu gốc vẫn được giữ.</p><div className={styles.modalActions}><button className={styles.secondary} disabled={busy} onClick={dismissModal}>Hủy</button><button className={styles.deleteConfirm} disabled={busy} onClick={() => void run(async () => { const target = deleting!.id; await api(`${base}/${target}`, { method: "DELETE", protected: true }); retry.current = null; setViewer(null); await load(selected.current === target ? undefined : selected.current); dismissModal(); })}>Xác nhận xóa hội thoại</button></div></>}
      </div></div>}
      {viewer && <div ref={sourcePanel} tabIndex={-1} role="dialog" aria-modal="true" aria-label="Nguồn tham khảo" className={styles.source}>
        <div className={styles.sourceHeader}><h4>{viewer.source.name}</h4><button aria-label="Đóng nguồn" onClick={dismissSource}>×</button></div><p>{sourcePosition(viewer.source.location) || "Nguồn tham khảo"}</p><p>{viewer.source.authority === "original" ? (viewer.source.representation?.kind === "image" ? "Nguồn ảnh từ bản gốc; lời trích do AI đọc, hãy đối chiếu trực quan." : "Nguồn cấu trúc từ DOCX gốc; chưa phải bản render bố cục.") : "Tham chiếu từ chỉ mục dẫn xuất; chưa xác nhận đã đọc bản gốc."}</p><blockquote>{viewer.source.quote}</blockquote>
        {viewer.file ? <PdfViewer file={viewer.file} initialPage={viewer.source.location.page ?? 1} /> : viewer.source.content_type === "application/pdf" ? <OriginalPageViewer key={viewer.source.document_id} documentId={viewer.source.document_id} channelId={channelId} initialPage={viewer.source.location.page ?? 1} revision={viewer.source.revision} /> : viewer.imageUrl ? <Image src={viewer.imageUrl} width={900} height={900} style={{ width: "100%", height: "auto" }} unoptimized alt="Ảnh gốc của tài liệu" /> : <OriginalDocxViewer documentId={viewer.source.document_id} channelId={channelId} initialUnit={viewer.source.location.body_block ?? 1} revision={viewer.source.revision} />}
        <button disabled={busy} onClick={() => void run(async () => { const file = await api<File>(`/learning-documents/${viewer.source.document_id}/download?channel_id=${channelId}`, { protected: true, download: true }); const url = URL.createObjectURL(file); const link = document.createElement("a"); link.href = url; link.download = file.name; link.click(); setTimeout(() => URL.revokeObjectURL(url), 30000); })}>Tải nguồn gốc</button>
      </div>}
    </div>}
    </div>}
  </section>;
}
