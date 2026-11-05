"use client";
import { useEffect, useRef, useState } from "react";
import type { PDFDocumentProxy } from "pdfjs-dist";

export function PdfViewer({ file, initialPage = 1 }: { file: File; initialPage?: number }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const [pdf, setPdf] = useState<PDFDocumentProxy | null>(null);
  const [page, setPage] = useState(1);
  const [error, setError] = useState("");
  const [rendering, setRendering] = useState(false);
  useEffect(() => {
    let active = true;
    let document: PDFDocumentProxy | null = null;
    void (async () => {
      const library = await import("pdfjs-dist");
      library.GlobalWorkerOptions.workerSrc = "/pdf.worker.min.mjs";
      const bytes = new Uint8Array(await file.arrayBuffer());
      document = await library.getDocument({ data: bytes, stopAtErrors: true }).promise;
      if (active) { setPdf(document); setPage(Math.min(Math.max(initialPage, 1), document.numPages)); setRendering(true); }
      else await document.loadingTask.destroy();
    })().catch(() => { if (active) setError("Không thể hiển thị PDF. Bạn có thể tải bản gốc xuống hoặc thử lại."); });
    return () => { active = false; if (document) void document.loadingTask.destroy(); };
  }, [file, initialPage]);
  useEffect(() => {
    if (!pdf) return;
    let active = true;
    let task: ReturnType<Awaited<ReturnType<PDFDocumentProxy["getPage"]>>["render"]> | null = null;
    void (async () => {
      const content = await pdf.getPage(page);
      if (!active || !canvas.current) return;
      const viewport = content.getViewport({ scale: 1.1 });
      const node = canvas.current;
      node.width = viewport.width; node.height = viewport.height;
      task = content.render({ canvas: node, canvasContext: node.getContext("2d")!, viewport });
      await task.promise;
      if (active) setRendering(false);
    })().catch(() => { if (active) { setError("Không thể hiển thị trang PDF."); setRendering(false); } });
    return () => { active = false; task?.cancel(); };
  }, [pdf, page]);
  return <div aria-label="Trình xem PDF">
    {error && <p role="alert">{error}</p>}
    {!pdf && !error && <p role="status">Đang mở PDF…</p>}
    {pdf && <><div className="actions">
      <button disabled={page === 1 || rendering} onClick={() => { setRendering(true); setPage(page - 1); }}>Trang trước</button>
      <span aria-live="polite">Trang {page} / {pdf.numPages}</span>
      <button disabled={page === pdf.numPages || rendering} onClick={() => { setRendering(true); setPage(page + 1); }}>Trang sau</button>
    </div><canvas ref={canvas} aria-label={`Nội dung PDF trang ${page}`} style={{ maxWidth: "100%", height: "auto" }} /></>}
  </div>;
}
