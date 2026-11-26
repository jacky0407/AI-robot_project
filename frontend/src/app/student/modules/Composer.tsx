"use client";

// 對話底部的輸入框（像 ChatGPT）：固定在畫面底部，輸入框會隨內容長高
// - 聊天室：Enter 送出、Shift+Enter 換行
// - 一般作答：作答通常很長，Enter 換行、Ctrl／⌘+Enter 送出
// 上方工具列放 @ 機器人、我需要提示等按鈕；右下是送出按鈕

import { useEffect, useRef } from "react";

export default function Composer({
  value,
  onChange,
  onSend,
  sendLabel,
  sendDisabled,
  enterToSend,
  placeholder,
  maxLength,
  minHeight = 56,
  tools,
  extraActions,
  footnote,
  warning,
}: {
  value: string;
  onChange: (v: string) => void;
  onSend: () => void;
  sendLabel: string;
  sendDisabled: boolean;
  enterToSend: boolean;
  placeholder: string;
  maxLength: number;
  minHeight?: number;
  tools?: React.ReactNode;
  extraActions?: React.ReactNode;
  footnote?: React.ReactNode;
  warning?: string;
}) {
  const ref = useRef<HTMLTextAreaElement>(null);
  const boxRef = useRef<HTMLDivElement>(null);

  // 把輸入框的實際高度記在 --composer-h，對話捲到底時用來留空間（輸入框會隨內容長高）
  useEffect(() => {
    const el = boxRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => document.documentElement.style.setProperty("--composer-h", `${el.offsetHeight + 12}px`));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // 隨內容長高，最多到畫面的 40%
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(Math.max(el.scrollHeight, minHeight), Math.round(window.innerHeight * 0.4))}px`;
  }, [value, minHeight]);

  return (
    <div ref={boxRef} style={{ position: "sticky", bottom: 0, zIndex: 5, padding: "12px 0 16px", backgroundColor: "var(--t-bg)" }}>
      {warning && (
        <div style={{ marginBottom: "8px", padding: "8px 12px", borderRadius: "10px", fontSize: "13px", color: "var(--t-danger)", backgroundColor: "var(--t-danger-soft)", border: "1px solid var(--t-danger-border)" }}>
          {warning}
        </div>
      )}
      <div style={{ borderRadius: "18px", border: "1px solid var(--t-border-strong)", backgroundColor: "var(--t-surface)", padding: "10px 12px 10px 16px" }}>
        {tools && <div style={{ display: "flex", gap: "6px", flexWrap: "wrap", marginBottom: "6px" }}>{tools}</div>}
        <textarea
          ref={ref}
          value={value}
          maxLength={maxLength}
          rows={1}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={(e) => {
            if (e.key !== "Enter" || e.nativeEvent.isComposing) return;
            const send = enterToSend ? !e.shiftKey : e.ctrlKey || e.metaKey;
            if (send) {
              e.preventDefault();
              if (!sendDisabled) onSend();
            }
          }}
          placeholder={placeholder}
          style={{
            display: "block",
            width: "100%",
            boxSizing: "border-box",
            minHeight: `${minHeight}px`,
            padding: "4px 0",
            border: "none",
            outline: "none",
            resize: "none",
            backgroundColor: "transparent",
            color: "var(--t-text)",
            fontSize: "15px",
            lineHeight: 1.7,
            fontFamily: "inherit",
          }}
        />
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: "10px", marginTop: "6px", flexWrap: "wrap" }}>
          <span style={{ fontSize: "12px", color: "var(--t-text-faint)", minWidth: 0 }}>{footnote}</span>
          <span style={{ display: "flex", gap: "8px", marginLeft: "auto" }}>
            {extraActions}
            <button
              type="button"
              onClick={onSend}
              disabled={sendDisabled}
              style={{
                padding: "8px 18px",
                borderRadius: "9999px",
                border: "none",
                backgroundColor: sendDisabled ? "var(--t-surface-3)" : "var(--t-primary)",
                color: sendDisabled ? "var(--t-text-faint)" : "var(--t-on-primary)",
                fontSize: "13px",
                fontWeight: "bold",
                cursor: sendDisabled ? "not-allowed" : "pointer",
                whiteSpace: "nowrap",
              }}
            >
              {sendLabel}
            </button>
          </span>
        </div>
      </div>
    </div>
  );
}

// 輸入框上方的小按鈕（@機器人、我需要提示）
export function ToolChip({
  children,
  onClick,
  title,
  tone = "link",
  disabled,
}: {
  children: React.ReactNode;
  onClick: () => void;
  title?: string;
  tone?: "link" | "warning" | "indigo";
  disabled?: boolean;
}) {
  const color = tone === "warning" ? "var(--t-warning)" : tone === "indigo" ? "var(--t-indigo)" : "var(--t-link)";
  const border = tone === "warning" ? "var(--t-warning-border)" : tone === "indigo" ? "var(--t-indigo-border)" : "var(--t-link-border)";
  return (
    <button
      type="button"
      onClick={onClick}
      title={title}
      disabled={disabled}
      style={{
        padding: "3px 10px",
        fontSize: "12px",
        borderRadius: "9999px",
        border: `1px solid ${border}`,
        backgroundColor: "transparent",
        color,
        cursor: disabled ? "not-allowed" : "pointer",
        opacity: disabled ? 0.45 : 1,
      }}
    >
      {children}
    </button>
  );
}
