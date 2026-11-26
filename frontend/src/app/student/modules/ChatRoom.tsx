"use client";

// 多機器人聊天室（#68）：取代原本「寫完一整份作答再送出」的關卡
// - 每則訊息只由一個機器人回答：打 @名稱 指定，沒指定時系統依內容挑一個
// - @ 評分機器人或按「送出評分」：評分機器人評整段對話，結果顯示在左邊的「評分與回饋」
// - 每日次數按每一則訊息計算
// - 一個模組一個聊天室（#108）：傳入 stages 時，對話依階段畫出分隔線；訊息送到目前階段（room.step_id）
// 後端：GET /api/chat/steps/{step_id}、POST /api/chat/sessions/{session_id}/steps/{step_id}/messages（SSE）

import { useEffect, useRef, useState } from "react";
import { ApiError, apiFetch, streamSSE } from "@/utils/api";

export type ChatRoomInfo = {
  chat: true;
  step_id: string;
  bots: { tool_id: string; name: string; when_to_use: string }[];
  evaluator: { tool_id: string; name: string } | null;
  pass_mode: string;
  scoring: boolean;
  messages?: ChatMessage[];
  // 這個階段沒有對話機器人：評分機器人陪學生對話（#108）
  evaluator_chats?: boolean;
};

// 模組聊天室的階段（GET /api/chat/modules/{module_id} 的 stages）
export type ChatStage = {
  step_id: string;
  step_order: number;
  step_title: string;
  intro?: string;
};

export type ChatMessage = {
  id?: string;
  role: "student" | "bot";
  bot_name?: string | null;
  content: string;
  routed_by?: string | null;
  attempt_id?: string | null;
  // 學習助教引用的教材段落（#67）
  sources?: string[];
  // 屬於哪個階段（#108）
  step_id?: string | null;
};

const MAX_CHARS = 2000;
// 評分相關的事件交給頁面原本的「評分與回饋」顯示
const EVALUATION_EVENTS = new Set(["score_start", "analysis_start", "dimension_score", "score_complete", "feedback_complete", "pass_status", "errors_detected"]);

const ROUTED_LABEL: Record<string, string> = {
  mention: "你指定的",
  router: "系統依內容挑選",
  only_bot: "",
  evaluate: "評分",
};

export async function loadChatRoom(stepId: string): Promise<ChatRoomInfo | null> {
  const res = await apiFetch<{ data: ChatRoomInfo | { chat: false } }>(`/api/chat/steps/${encodeURIComponent(stepId)}`);
  return res.data.chat ? (res.data as ChatRoomInfo) : null;
}

export default function ChatRoom({
  room,
  sessionId,
  disabledReason,
  onEvaluationStart,
  onEvaluationEvent,
  onFinished,
  messages: initialMessages,
  stages,
  header,
}: {
  room: ChatRoomInfo;
  sessionId: string;
  disabledReason?: string;
  onEvaluationStart: () => void;
  onEvaluationEvent: (event: string, data: Record<string, unknown>) => void;
  onFinished: (evaluated: boolean) => void;
  // 模組聊天室：整個模組的對話與階段（#108）；不傳就是單一關卡的聊天室
  messages?: ChatMessage[];
  stages?: ChatStage[];
  header?: React.ReactNode;
}) {
  const [messages, setMessages] = useState<ChatMessage[]>(initialMessages ?? room.messages ?? []);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState<string>("");
  const [warning, setWarning] = useState("");
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight });
  }, [messages, busy]);

  const everyone = [...room.bots.map((b) => b.name), ...(room.evaluator ? [room.evaluator.name] : [])];

  const send = async (evaluate: boolean) => {
    const content = text.trim();
    if (!content && !evaluate) return;
    const mentionsEvaluator = !!room.evaluator && new RegExp(`[@＠]\\s*${room.evaluator.name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}`).test(content);
    const evaluating = evaluate || mentionsEvaluator;
    setBusy(evaluating ? "評分機器人評分中…" : "機器人回覆中…");
    setWarning("");
    if (evaluating) onEvaluationStart();
    try {
      await streamSSE(
        `/api/chat/sessions/${sessionId}/steps/${room.step_id}/messages`,
        { method: "POST", body: JSON.stringify({ content, evaluate }) },
        (event, data) => {
          if (event === "message_saved" && data.message) {
            setMessages((prev) => [...prev, data.message as ChatMessage]);
            setText("");
          } else if (event === "bot_selected") {
            setBusy(`${String(data.bot_name ?? "機器人")} 回覆中…`);
          } else if (event === "materials_used") {
            setBusy((b) => `${b.replace("回覆中…", "")}查過教材，回覆中…`);
          } else if (event === "bot_reply") {
            setMessages((prev) => [...prev, data.message as ChatMessage]);
          } else if (event === "error") {
            setWarning(String(data.message ?? "回覆失敗，請稍後再試"));
          }
          if (EVALUATION_EVENTS.has(event) || (evaluating && event === "error")) onEvaluationEvent(event, data);
        }
      );
    } catch (err) {
      if (err instanceof ApiError && err.code === "PII_DETECTED") {
        const types = (err.detail as { detected_types?: string[] })?.detected_types ?? [];
        setWarning(`${err.message}${types.length ? `（偵測到：${types.join("、")}）` : ""}`);
      } else {
        setWarning(err instanceof ApiError ? (err.status === 401 ? "登入已過期，請重新登入。" : err.message) : "連不到後端，請稍後再試。");
      }
    } finally {
      setBusy("");
      onFinished(evaluating);
    }
  };

  const stageById = new Map((stages ?? []).map((st) => [st.step_id, st]));
  const currentStage = stageById.get(room.step_id);
  const lastStepId = messages.length ? messages[messages.length - 1].step_id ?? room.step_id : null;
  // 目前階段還沒有任何訊息：在最後面畫出這個階段的開場
  const showCurrentIntro = Boolean(stages && currentStage && lastStepId !== room.step_id && !messages.some((m) => m.step_id === room.step_id));

  const insertMention = (name: string) => setText((t) => `@${name} ${t.replace(/^[@＠]\S*\s*/, "")}`);
  const blocked = Boolean(disabledReason) || Boolean(busy);

  return (
    <div style={{ backgroundColor: "#1e293b", borderRadius: "14px", padding: "16px", border: "1px solid rgba(255,255,255,0.1)", display: "flex", flexDirection: "column", gap: "10px" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: "8px", flexWrap: "wrap" }}>
        <h3 style={{ fontSize: "14px", fontWeight: "bold", margin: 0 }}>{stages ? "模組聊天室" : "聊天室"}</h3>
        <span style={{ fontSize: "11px", color: "#94a3b8" }}>每則訊息只由一個機器人回答；打 @名稱 可以指定</span>
      </div>

      {header}

      <div style={{ display: "flex", gap: "6px", flexWrap: "wrap" }}>
        {room.bots.map((b) => (
          <button key={b.tool_id} type="button" onClick={() => insertMention(b.name)} title={b.when_to_use || undefined} style={chip(false)}>
            @{b.name}
          </button>
        ))}
        {room.evaluator && (
          <button type="button" onClick={() => insertMention(room.evaluator!.name)} title="評分機器人：@ 它會評這個階段的對話" style={chip(true)}>
            @{room.evaluator.name}（評分）
          </button>
        )}
        {room.evaluator_chats && (
          <span style={{ fontSize: "11px", color: "#94a3b8", alignSelf: "center" }}>這個階段直接打字就會由 {room.evaluator?.name} 回覆；@ 它代表送出評分</span>
        )}
      </div>

      <div ref={listRef} style={{ height: "360px", overflowY: "auto", display: "flex", flexDirection: "column", gap: "8px", padding: "4px 2px" }}>
        {messages.length === 0 && !showCurrentIntro && (
          <p style={{ margin: "auto", textAlign: "center", fontSize: "13px", color: "#64748b" }}>
            還沒有對話。先打個招呼，或用 @ 找特定的機器人。
            <br />
            聊天室裡有：{everyone.join("、") || "（老師還沒設定機器人）"}
          </p>
        )}
        {messages.map((m, i) => (
          <div key={m.id ?? i} style={{ display: "contents" }}>
          {stages && m.step_id && m.step_id !== messages[i - 1]?.step_id && <StageDivider stage={stageById.get(m.step_id)} showIntro={m.step_id === room.step_id} />}
          <div style={{ alignSelf: m.role === "student" ? "flex-end" : "flex-start", maxWidth: "85%" }}>
            {m.role === "bot" && (
              <div style={{ fontSize: "11px", color: m.routed_by === "evaluate" ? "#fbbf24" : "#7dd3fc", marginBottom: "2px" }}>
                {m.bot_name ?? "機器人"}
                {m.routed_by && ROUTED_LABEL[m.routed_by] ? <span style={{ color: "#64748b" }}>・{ROUTED_LABEL[m.routed_by]}</span> : null}
              </div>
            )}
            <div
              style={{
                padding: "9px 12px",
                borderRadius: "10px",
                fontSize: "13px",
                lineHeight: 1.6,
                whiteSpace: "pre-wrap",
                color: "#f1f5f9",
                backgroundColor: m.role === "student" ? "#2563eb" : m.routed_by === "evaluate" ? "rgba(245,158,11,0.15)" : "rgba(255,255,255,0.06)",
              }}
            >
              {m.content}
            </div>
            {m.sources && m.sources.length > 0 && (
              <div style={{ fontSize: "11px", color: "#94a3b8", marginTop: "3px" }}>參考教材：{m.sources.join("、")}</div>
            )}
          </div>
          </div>
        ))}
        {showCurrentIntro && <StageDivider stage={currentStage} showIntro />}
        {busy && <p style={{ margin: 0, fontSize: "12px", color: "#94a3b8" }}>{busy}</p>}
      </div>

      {warning && <div style={{ padding: "8px 10px", borderRadius: "8px", fontSize: "12px", color: "#fca5a5", backgroundColor: "rgba(239,68,68,0.12)" }}>{warning}</div>}

      <textarea
        value={text}
        maxLength={MAX_CHARS}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
            e.preventDefault();
            if (!blocked) send(false);
          }
        }}
        placeholder="輸入訊息，Enter 送出、Shift+Enter 換行。請勿輸入真實姓名、電話、身分證字號等個資"
        style={{ width: "100%", height: "80px", boxSizing: "border-box", padding: "10px", borderRadius: "8px", border: "1px solid rgba(255,255,255,0.15)", backgroundColor: "#0f172a", color: "#f8fafc", fontSize: "13px", lineHeight: 1.5, resize: "vertical", outline: "none" }}
      />
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: "8px", flexWrap: "wrap" }}>
        <span style={{ fontSize: "11px", color: "#64748b" }}>{disabledReason ?? `${text.length} / ${MAX_CHARS}・每則訊息扣一次今日次數`}</span>
        <span style={{ display: "flex", gap: "8px" }}>
          {room.evaluator && (
            <button type="button" disabled={blocked || messages.length === 0} onClick={() => send(true)} style={btn(blocked || messages.length === 0, "#b45309")}>
              送出評分
            </button>
          )}
          <button type="button" disabled={blocked || !text.trim()} onClick={() => send(false)} style={btn(blocked || !text.trim(), "#2563eb")}>
            送出訊息
          </button>
        </span>
      </div>
    </div>
  );
}

// 階段分隔線：「階段 2：現況描述」＋（新階段剛開始時）評分機器人的開場說明
function StageDivider({ stage, showIntro = false }: { stage?: ChatStage; showIntro?: boolean }) {
  if (!stage) return null;
  return (
    <div style={{ alignSelf: "stretch", margin: "6px 0", textAlign: "center" }}>
      <div style={{ display: "flex", alignItems: "center", gap: "8px", fontSize: "11px", color: "#94a3b8" }}>
        <span style={{ flex: 1, height: "1px", backgroundColor: "rgba(255,255,255,0.12)" }} />
        階段 {stage.step_order}：{stage.step_title}
        <span style={{ flex: 1, height: "1px", backgroundColor: "rgba(255,255,255,0.12)" }} />
      </div>
      {showIntro && stage.intro && (
        <p style={{ margin: "6px auto 0", maxWidth: "85%", fontSize: "12px", lineHeight: 1.6, color: "#cbd5e1", whiteSpace: "pre-wrap" }}>{stage.intro}</p>
      )}
    </div>
  );
}

function chip(evaluator: boolean): React.CSSProperties {
  return {
    padding: "3px 10px",
    fontSize: "12px",
    borderRadius: "9999px",
    border: `1px solid ${evaluator ? "rgba(245,158,11,0.5)" : "rgba(125,211,252,0.4)"}`,
    backgroundColor: "transparent",
    color: evaluator ? "#fbbf24" : "#7dd3fc",
    cursor: "pointer",
  };
}

function btn(disabled: boolean, color: string): React.CSSProperties {
  return {
    padding: "8px 16px",
    borderRadius: "8px",
    border: "none",
    backgroundColor: disabled ? "#475569" : color,
    color: "#fff",
    fontSize: "13px",
    fontWeight: "bold",
    cursor: disabled ? "not-allowed" : "pointer",
    whiteSpace: "nowrap",
  };
}
