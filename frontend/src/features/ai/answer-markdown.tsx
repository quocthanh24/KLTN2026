"use client";

import { Children, isValidElement, memo, useEffect, useRef, useState, type ReactNode } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import styles from "./assistant.module.css";

export function CopyButton({ text, label }: { text: string; label: string }) {
  const [status, setStatus] = useState("");
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);
  return <button type="button" className={styles.copy} aria-label={label} onClick={async () => {
    try { await navigator.clipboard.writeText(text); setStatus("Đã sao chép"); }
    catch { setStatus("Không thể sao chép"); }
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setStatus(""), 2200);
  }}><span aria-hidden="true">▢</span> <span role="status">{status || label}</span></button>;
}

function codeText(children: ReactNode): string {
  return Children.toArray(children).map(child => typeof child === "string" || typeof child === "number" ? String(child) : isValidElement<{ children?: ReactNode }>(child) ? codeText(child.props.children) : "").join("");
}

// No raw HTML plugins: Markdown becomes React elements, never arbitrary HTML.
export const AnswerMarkdown = memo(function AnswerMarkdown({ text, onSource, sourceAvailable }: { text: string; onSource?: (index: number, trigger: HTMLButtonElement) => void; sourceAvailable?: (index: number) => boolean }) {
  return <div className={styles.markdown}><Markdown skipHtml remarkPlugins={[remarkGfm]} components={{
    a: ({ href, children }) => href?.match(/^#source-(\d+)$/) ? <button type="button" data-source-index={Number(href.slice(8))} className={styles.citation} disabled={!onSource || !sourceAvailable?.(Number(href.slice(8)))} onClick={event => onSource?.(Number(href.slice(8)), event.currentTarget)}>{children}</button> : href ? <a href={href} target="_blank" rel="noopener noreferrer">{children}</a> : <span>{children}</span>,
    img: ({ alt }) => <span>{alt}</span>,
    pre: ({ children }) => <div className={styles.codeBlock}><div className={styles.codeToolbar}><span>Mã</span><CopyButton text={codeText(children)} label="Sao chép mã" /></div><pre tabIndex={0} aria-label="Khối mã">{children}</pre></div>,
    table: ({ children }) => <div className={styles.tableWrap} tabIndex={0} role="region" aria-label="Bảng trong câu trả lời"><table>{children}</table></div>,
  }}>{text}</Markdown></div>;
});
