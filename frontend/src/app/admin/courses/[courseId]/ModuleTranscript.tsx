"use client";

// 教師監控：某位學生在整個模組聊天室的對話，標出每段屬於哪個階段（#108）
// 後端：GET /api/chat/modules/{module_id}/users/{user_id}（只讀，不扣次數、不呼叫 AI）

import { useEffect, useState } from "react";
import { ApiError, apiFetch } from "@/utils/api";

type Message = {
  id: string;
  role: "student" | "bot";
  bot_name: string | null;
  content: string;
  routed_by: string | null;
  step_id: string | null;
  stage: string;
  created_at: string | null;
};

export default function ModuleTranscript({ moduleId, userId }: { moduleId: string; userId: string }) {
  const [messages, setMessages] = useState<Message[] | null>(null);
  const [error, setError] = useState("");
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (!open || messages) return;
    let active = true;
    apiFetch<{ data: { messages: Message[] } }>(
      `/api/chat/modules/${encodeURIComponent(moduleId)}/users/${encodeURIComponent(userId)}`
    ).then(
      (res) => active && setMessages(res.data.messages),
      (err) => active && setError(err instanceof ApiError ? err.message : "讀不到對話")
    );
    return () => {
      active = false;
    };
  }, [open, messages, moduleId, userId]);

  return (
    <div style={{ marginTop: "16px" }}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        style={{ padding: 0, border: "none", background: "none", color: "#cbd5e1", fontSize: "13px", fontWeight: "bold", cursor: "pointer" }}
      >
        {open ? "▾" : "▸"} 模組聊天室的整段對話
      </button>
      {open && error && <p style={{ fontSize: "12px", color: "#fca5a5" }}>{error}</p>}
      {open && !messages && !error && <p style={{ fontSize: "12px", color: "#94a3b8" }}>載入中…</p>}
      {open && messages?.length === 0 && <p style={{ fontSize: "12px", color: "#94a3b8" }}>還沒有對話。</p>}
      {open && messages && messages.length > 0 && (
        <div style={{ marginTop: "8px", display: "flex", flexDirection: "column", gap: "6px" }}>
          {messages.map((m, i) => (
            <div key={m.id ?? i}>
              {m.stage && m.step_id !== messages[i - 1]?.step_id && (
                <div style={{ margin: "8px 0 4px", fontSize: "11px", color: "#7dd3fc", fontWeight: "bold" }}>{m.stage}</div>
              )}
              <p style={{ margin: 0, fontSize: "12px", lineHeight: 1.6, whiteSpace: "pre-wrap", color: m.role === "student" ? "#e2e8f0" : "#94a3b8" }}>
                <b style={{ color: m.role === "student" ? "#93c5fd" : m.routed_by === "evaluate" ? "#fbbf24" : "#86efac" }}>
                  {m.role === "student" ? "學生" : m.bot_name ?? "機器人"}：
                </b>
                {m.content}
              </p>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
