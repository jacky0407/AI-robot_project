"use client";

// 多機器人聊天室（#68），GPT 式對話介面
// - 每則訊息只由一個機器人回答：打 @名稱 指定，沒指定時系統依內容挑一個
// - @ 評分機器人或按「送出評分」：評分機器人評整段對話，評分結果直接出現在對話裡
// - 每日次數按每一則訊息計算
// 後端：GET /api/chat/steps/{step_id}、POST /api/chat/sessions/{session_id}/steps/{step_id}/messages（SSE）

import { useEffect, useRef, useState } from "react";
import { ApiError, apiFetch, streamSSE } from "@/utils/api";
import Composer, { ToolChip } from "./Composer";
import { BotTurn, EMPTY_RUN, RunResultCard, StudentTurn, applyRunEvent, type RunState } from "./RunResult";

export type ChatRoomInfo = {
  chat: true;
  step_id: string;
  bots: { tool_id: string; name: string; when_to_use: string }[];
  evaluator: { tool_id: string; name: string } | null;
  pass_mode: string;
  scoring: boolean;
  messages: ChatMessage[];
};

type ChatMessage = {
  id?: string;
  role: "student" | "bot";
  bot_name?: string | null;
  content: string;
  routed_by?: string | null;
  attempt_id?: string | null;
  // 學習助教引用的教材段落（#67）
  sources?: string[];
};

const MAX_CHARS = 2000;
// 評分相關的事件：在對話裡長成一張評分卡，也轉給頁面（下一題按鈕要知道有沒有通過）
const EVALUATION_EVENTS = new Set(["score_start", "analysis_start", "dimension_score", "score_complete", "feedback_complete", "pass_status", "errors_detected"]);

const ROUTED_LABEL: Record<string, string> = {
  mention: "你指定的",
  router: "依你的問題挑選",
  only_bot: "",
  evaluate: "評分",
};

// 對話裡的一個項目：一則訊息，或這次送出評分的結果卡
type Item = { kind: "msg"; message: ChatMessage } | { kind: "eval"; run: RunState; streaming: boolean; botName: string };

export async function loadChatRoom(stepId: string): Promise<ChatRoomInfo | null> {
  const res = await apiFetch<{ data: ChatRoomInfo | { chat: false } }>(`/api/chat/steps/${encodeURIComponent(stepId)}`);
  return res.data.chat ? (res.data as ChatRoomInfo) : null;
}

export default function ChatRoom({
  room,
  sessionId,
  disabledReason,
  footnote,
  onEvaluationStart,
  onEvaluationEvent,
  onFinished,
}: {
  room: ChatRoomInfo;
  sessionId: string;
  disabledReason?: string;
  footnote?: React.ReactNode;
  onEvaluationStart: () => void;
  onEvaluationEvent: (event: string, data: Record<string, unknown>) => void;
  onFinished: () => void;
}) {
  const [items, setItems] = useState<Item[]>(() => room.messages.map((m) => ({ kind: "msg", message: m })));
  const [text, setText] = useState("");
  const [busy, setBusy] = useState<string>("");
  const [warning, setWarning] = useState("");
  const endRef = useRef<HTMLDivElement>(null);
  const firstRender = useRef(true);

  // 有新訊息就捲到最下面（第一次載入不捲，讓學生先看到關卡說明）
  useEffect(() => {
    if (firstRender.current) {
      firstRender.current = false;
      return;
    }
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [items, busy]);

  const hasMessages = items.some((it) => it.kind === "msg");

  // 更新最後一張評分卡
  const updateEval = (fn: (it: Extract<Item, { kind: "eval" }>) => Extract<Item, { kind: "eval" }>) =>
    setItems((prev) => {
      const idx = prev.map((it) => it.kind).lastIndexOf("eval");
      if (idx < 0) return prev;
      const next = [...prev];
      next[idx] = fn(prev[idx] as Extract<Item, { kind: "eval" }>);
      return next;
    });

  const send = async (evaluate: boolean) => {
    const content = text.trim();
    if (!content && !evaluate) return;
    const mentionsEvaluator = !!room.evaluator && new RegExp(`[@＠]\\s*${room.evaluator.name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}`).test(content);
    const evaluating = evaluate || mentionsEvaluator;
    setBusy(evaluating ? `${room.evaluator?.name ?? "評分機器人"} 正在看整段對話…` : "思考中…");
    setWarning("");
    // 按下送出就清空輸入框；送不出去時再把內容放回來
    setText("");
    if (evaluating) onEvaluationStart();
    let evalAdded = false;
    let saved = false;
    try {
      await streamSSE(
        `/api/chat/sessions/${sessionId}/steps/${room.step_id}/messages`,
        { method: "POST", body: JSON.stringify({ content, evaluate }) },
        (event, data) => {
          if (event === "message_saved" && data.message) {
            setItems((prev) => [...prev, { kind: "msg", message: data.message as ChatMessage }]);
            saved = true;
          } else if (event === "bot_selected") {
            setBusy(`${String(data.bot_name ?? "機器人")} 思考中…`);
          } else if (event === "materials_used") {
            setBusy((b) => `${b.replace("思考中…", "")}查過教材，正在回覆…`);
          } else if (event === "bot_reply") {
            const message = data.message as ChatMessage;
            // 評分完後端會再存一則摘要；這次已經有完整的評分卡，摘要只在重新整理後的紀錄裡顯示
            if (message.routed_by === "evaluate" && evalAdded) updateEval((it) => ({ ...it, streaming: false }));
            else setItems((prev) => [...prev, { kind: "msg", message }]);
          } else if (event === "error") {
            setWarning(String(data.message ?? "回覆失敗，請稍後再試"));
          }
          if (EVALUATION_EVENTS.has(event) || (evaluating && event === "error")) {
            if (!evalAdded) {
              evalAdded = true;
              setBusy("");
              setItems((prev) => [...prev, { kind: "eval", run: { ...EMPTY_RUN }, streaming: true, botName: room.evaluator?.name ?? "評分機器人" }]);
            }
            updateEval((it) => ({ ...it, run: applyRunEvent(it.run, event, data) }));
            onEvaluationEvent(event, data);
          }
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
      // 訊息沒存進去（個資偵測、次數用完、連線失敗）：內容放回輸入框
      if (content && !saved) setText((cur) => cur || content);
      if (evalAdded) updateEval((it) => ({ ...it, streaming: false }));
      setBusy("");
      onFinished();
    }
  };

  const insertMention = (name: string) => setText((t) => `@${name} ${t.replace(/^[@＠]\S*\s*/, "")}`);
  const blocked = Boolean(disabledReason) || Boolean(busy);
  const everyone = [...room.bots.map((b) => b.name), ...(room.evaluator ? [room.evaluator.name] : [])];

  return (
    <>
      {!hasMessages && (
        <p style={{ margin: "4px 0 0 40px", fontSize: "13px", color: "var(--t-text-muted)", lineHeight: 1.7 }}>
          這一關一起討論的有：{everyone.join("、") || "（老師還沒設定機器人）"}。直接打字開始，或在下方點 @ 指定要問誰。
        </p>
      )}

      {items.map((it, i) => {
        if (it.kind === "eval") {
          return (
            <BotTurn key={`eval-${i}`} name={it.botName} note="評分" tone="warning">
              <RunResultCard run={it.run} streaming={it.streaming} />
            </BotTurn>
          );
        }
        const m = it.message;
        if (m.role === "student") return <StudentTurn key={m.id ?? i}>{m.content}</StudentTurn>;
        const isEval = m.routed_by === "evaluate";
        return (
          <BotTurn key={m.id ?? i} name={m.bot_name ?? "機器人"} note={m.routed_by ? ROUTED_LABEL[m.routed_by] || undefined : undefined} tone={isEval ? "warning" : "primary"}>
            <div
              style={{
                fontSize: "15px",
                lineHeight: 1.8,
                whiteSpace: "pre-wrap",
                wordBreak: "break-word",
                color: "var(--t-text)",
                ...(isEval ? { padding: "10px 14px", borderRadius: "12px", backgroundColor: "var(--t-warning-soft)" } : {}),
              }}
            >
              {m.content}
            </div>
            {m.sources && m.sources.length > 0 && (
              <div style={{ fontSize: "12px", color: "var(--t-text-muted)", marginTop: "6px" }}>參考教材：{m.sources.join("、")}</div>
            )}
          </BotTurn>
        );
      })}

      {busy && (
        <div style={{ display: "flex", gap: "10px", alignItems: "center", marginLeft: "40px", fontSize: "13px", color: "var(--t-text-muted)" }}>
          <span className="chat-dots" aria-hidden="true">
            <i />
            <i />
            <i />
          </span>
          {busy}
        </div>
      )}
      <div ref={endRef} style={{ scrollMarginBottom: "var(--composer-h, 200px)" }} />

      <Composer
        value={text}
        onChange={setText}
        onSend={() => send(false)}
        sendLabel="送出"
        sendDisabled={blocked || !text.trim()}
        enterToSend
        maxLength={MAX_CHARS}
        placeholder={disabledReason ?? "輸入訊息，Enter 送出、Shift+Enter 換行"}
        warning={warning}
        tools={
          <>
            {room.bots.map((b) => (
              <ToolChip key={b.tool_id} onClick={() => insertMention(b.name)} title={b.when_to_use || undefined}>
                @{b.name}
              </ToolChip>
            ))}
            {room.evaluator && (
              <ToolChip tone="warning" onClick={() => insertMention(room.evaluator!.name)} title="評分機器人：@ 它會評整段對話">
                @{room.evaluator.name}
              </ToolChip>
            )}
          </>
        }
        extraActions={
          room.evaluator ? (
            <button
              type="button"
              disabled={blocked || !hasMessages}
              onClick={() => send(true)}
              title="請評分機器人評整段對話"
              style={{
                padding: "8px 16px",
                borderRadius: "9999px",
                border: "1px solid var(--t-warning-border)",
                backgroundColor: "transparent",
                color: blocked || !hasMessages ? "var(--t-text-faint)" : "var(--t-warning)",
                fontSize: "13px",
                fontWeight: "bold",
                cursor: blocked || !hasMessages ? "not-allowed" : "pointer",
                whiteSpace: "nowrap",
              }}
            >
              送出評分
            </button>
          ) : undefined
        }
        footnote={footnote ?? `${text.length} / ${MAX_CHARS}・每則訊息扣一次今日次數`}
      />
    </>
  );
}
