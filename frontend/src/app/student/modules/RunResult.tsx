"use client";

// AI 評分結果：一般作答關卡與聊天室關卡共用
// - RunState：一次送出的串流結果（逐事件累積），applyRunEvent 把一個 SSE 事件併進去
// - RunResultCard：在對話裡顯示成一則「AI 回覆」卡片（分數、逐項評分、常見錯誤、回饋、提示）
// 事件欄位與 backend/core/ai/agents 的 evaluator_agent、tutor_agent 一致

export type DimensionScore = { dimension: string; score: number; max_score: number; reason?: string; evidence?: string };
export type ScoreComplete = {
  total_score: number;
  percentage?: number;
  passed: boolean;
  overall_feedback: string;
  confidence?: number;
  needs_teacher_review?: boolean;
};
export type Hint = { level: number; content: string; source?: "teacher" | "generated"; is_example?: boolean; warning?: string };
export type DetectedError = { code: string; label: string; severity?: string; evidence?: string; explanation?: string };

export type RunState = {
  decision?: string;
  dimensions: DimensionScore[];
  totalDimensions?: number;
  score?: ScoreComplete;
  hint?: Hint;
  encouragement?: string;
  escalation?: string;
  errors: DetectedError[];
  failure?: string;
  // 沒有評分規準的機器人只給文字回饋、不評分（#65）
  feedback?: string;
  // 依關卡通過方式算出的最終結果（score_complete.passed 只代表 AI 分數有沒有過門檻）
  pass?: { pass_mode: string; status: string; passed: boolean; awaiting_teacher: boolean };
};
export const EMPTY_RUN: RunState = { dimensions: [], errors: [] };

const DECISION_LABEL: Record<string, string> = {
  evaluate: "依評分規準逐項評分",
  encourage: "接近通過門檻，評分並給你鼓勵",
  give_hint: "先給你一則提示，修改後再送出",
  escalate: "建議你先和老師討論",
};

export function applyRunEvent(r: RunState, event: string, data: Record<string, unknown>): RunState {
  switch (event) {
    case "tutor_decision":
      return { ...r, decision: String(data.action ?? "") };
    case "analysis_start":
      return { ...r, totalDimensions: Number(data.total_dimensions ?? 0) };
    case "dimension_score":
      return { ...r, dimensions: [...r.dimensions, data as unknown as DimensionScore] };
    case "score_complete":
      return { ...r, score: data as unknown as ScoreComplete };
    case "hint":
      return { ...r, hint: data as unknown as Hint };
    case "encouragement":
      return { ...r, encouragement: String(data.message ?? "") };
    case "escalation":
      return { ...r, escalation: String(data.message ?? "") };
    case "errors_detected":
      return { ...r, errors: (data.errors as DetectedError[]) ?? [] };
    case "feedback_complete":
      return { ...r, feedback: String(data.feedback ?? "") };
    case "pass_status":
      return { ...r, pass: data as unknown as NonNullable<RunState["pass"]> };
    case "error":
      return { ...r, failure: `${String(data.message ?? "AI 評分失敗")}，請稍後再試。` };
    default:
      return r;
  }
}

// 對話裡的 AI 頭像（圓形，取名稱第一個字）
export function BotAvatar({ name, tone = "primary" }: { name: string; tone?: "primary" | "warning" | "indigo" }) {
  const bg = tone === "warning" ? "var(--t-warning-soft)" : tone === "indigo" ? "var(--t-indigo-soft)" : "var(--t-link-soft)";
  const fg = tone === "warning" ? "var(--t-warning)" : tone === "indigo" ? "var(--t-indigo)" : "var(--t-link)";
  return (
    <span
      aria-hidden="true"
      style={{
        flexShrink: 0,
        width: "30px",
        height: "30px",
        borderRadius: "9999px",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        fontSize: "13px",
        fontWeight: "bold",
        color: fg,
        backgroundColor: bg,
      }}
    >
      {name.slice(0, 1)}
    </span>
  );
}

// 一則「機器人說的話」：左邊頭像、上面名稱，內容放在 children
export function BotTurn({
  name,
  note,
  tone,
  children,
}: {
  name: string;
  note?: string;
  tone?: "primary" | "warning" | "indigo";
  children: React.ReactNode;
}) {
  return (
    <div style={{ display: "flex", gap: "10px", alignItems: "flex-start" }}>
      <BotAvatar name={name} tone={tone} />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: "12px", fontWeight: "bold", color: "var(--t-text-2)", margin: "5px 0 6px" }}>
          {name}
          {note && <span style={{ fontWeight: "normal", color: "var(--t-text-faint)" }}>・{note}</span>}
        </div>
        {children}
      </div>
    </div>
  );
}

// 學生說的話：靠右的主色氣泡
export function StudentTurn({ children, label }: { children: React.ReactNode; label?: string }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end" }}>
      {label && <div style={{ fontSize: "11px", color: "var(--t-text-faint)", marginBottom: "4px" }}>{label}</div>}
      <div
        style={{
          maxWidth: "85%",
          padding: "10px 14px",
          borderRadius: "16px 16px 4px 16px",
          fontSize: "14px",
          lineHeight: 1.7,
          whiteSpace: "pre-wrap",
          wordBreak: "break-word",
          color: "var(--t-on-primary)",
          backgroundColor: "var(--t-primary)",
        }}
      >
        {children}
      </div>
    </div>
  );
}

// 提示卡（分層提示或評分後 AI 生成的提示）
export function HintCard({ hint }: { hint: Hint }) {
  return (
    <div style={{ padding: "10px 14px", borderRadius: "12px", backgroundColor: "var(--t-indigo-soft)", border: "1px solid var(--t-indigo-border)" }}>
      <div style={{ fontSize: "11px", fontWeight: "bold", color: "var(--t-indigo)" }}>
        第 {hint.level} 層提示{hint.source === "generated" ? "（依評分結果產生）" : ""}
      </div>
      <p style={{ fontSize: "14px", color: "var(--t-text)", margin: "4px 0 0", whiteSpace: "pre-wrap", lineHeight: 1.7 }}>{hint.content}</p>
      {hint.warning && <p style={{ fontSize: "12px", color: "var(--t-indigo)", margin: "4px 0 0" }}>{hint.warning}</p>}
    </div>
  );
}

// 評分結果卡：串流中逐項長出來；streaming = 還在評分
export function RunResultCard({ run, streaming }: { run: RunState; streaming?: boolean }) {
  const passModeOnly = run.pass && run.pass.pass_mode !== "score";
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
      {run.failure && (
        <div style={{ padding: "10px 12px", borderRadius: "10px", fontSize: "13px", color: "var(--t-danger)", backgroundColor: "var(--t-danger-soft)" }}>{run.failure}</div>
      )}

      {run.decision && !run.score && !run.feedback && <p style={{ margin: 0, fontSize: "13px", color: "var(--t-text-muted)" }}>{DECISION_LABEL[run.decision] ?? run.decision}</p>}
      {run.encouragement && (
        <div style={{ padding: "10px 12px", borderRadius: "10px", fontSize: "14px", lineHeight: 1.7, color: "var(--t-success)", backgroundColor: "var(--t-success-soft)" }}>{run.encouragement}</div>
      )}
      {run.escalation && (
        <div style={{ padding: "10px 12px", borderRadius: "10px", fontSize: "14px", lineHeight: 1.7, color: "var(--t-orange)", backgroundColor: "var(--t-orange-soft)" }}>{run.escalation}</div>
      )}

      {run.score && (
        <div
          style={{
            padding: "14px 16px",
            borderRadius: "12px",
            backgroundColor: run.score.passed ? "var(--t-success-soft)" : "var(--t-danger-soft)",
            border: `1px solid ${run.score.passed ? "var(--t-success-border)" : "var(--t-danger-border)"}`,
          }}
        >
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: "10px", flexWrap: "wrap" }}>
            <span style={{ fontSize: "24px", fontWeight: "bold", color: run.score.passed ? "var(--t-success)" : "var(--t-danger)" }}>
              {Math.round(run.score.percentage ?? run.score.total_score)} 分
            </span>
            <span
              style={{
                fontSize: "12px",
                fontWeight: "bold",
                padding: "4px 10px",
                borderRadius: "9999px",
                color: "var(--t-on-primary)",
                backgroundColor: run.score.passed ? "var(--t-success-strong)" : "var(--t-danger-strong)",
              }}
            >
              {passModeOnly ? "分數僅供參考" : run.score.passed ? "已達通過門檻" : "尚未達標，請依回饋修改"}
            </span>
          </div>
          {run.score.overall_feedback && (
            <p style={{ margin: "10px 0 0", fontSize: "14px", lineHeight: 1.7, color: "var(--t-text)", whiteSpace: "pre-wrap" }}>{run.score.overall_feedback}</p>
          )}
          {run.score.needs_teacher_review && <p style={{ margin: "8px 0 0", fontSize: "12px", color: "var(--t-warning)" }}>這次評分比較沒把握，老師會再複核。</p>}
        </div>
      )}

      {run.feedback && (
        <div style={{ fontSize: "14px", lineHeight: 1.7, color: "var(--t-text)", whiteSpace: "pre-wrap" }}>
          <span style={{ display: "block", fontSize: "12px", color: "var(--t-text-muted)", marginBottom: "4px" }}>這個關卡不評分，以下是回饋</span>
          {run.feedback}
        </div>
      )}

      {passModeOnly && run.pass && (
        <p style={{ margin: 0, fontSize: "13px", fontWeight: "bold", color: run.pass.passed ? "var(--t-success)" : "var(--t-warning)" }}>
          {run.pass.passed ? "這個關卡送出即通過" : run.pass.awaiting_teacher ? "已送出，等老師複核後才會通過" : "這次還沒通過，請修改後再送出"}
        </p>
      )}

      {run.dimensions.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
          {run.dimensions.map((d) => (
            <div key={d.dimension} style={{ padding: "10px 12px", borderRadius: "10px", backgroundColor: "var(--t-surface-2)", border: "1px solid var(--t-border)", fontSize: "13px" }}>
              <div style={{ display: "flex", justifyContent: "space-between", gap: "8px", fontWeight: "bold" }}>
                <span>{d.dimension}</span>
                <span style={{ flexShrink: 0, color: "var(--t-link)" }}>
                  {d.score} / {d.max_score}
                </span>
              </div>
              {d.reason && <p style={{ margin: "4px 0 0", color: "var(--t-text-2)", lineHeight: 1.6 }}>{d.reason}</p>}
              {d.evidence && <p style={{ margin: "4px 0 0", fontSize: "12px", color: "var(--t-text-muted)", fontStyle: "italic" }}>引用：{d.evidence}</p>}
            </div>
          ))}
        </div>
      )}

      {run.errors.length > 0 && (
        <div style={{ padding: "10px 12px", borderRadius: "10px", fontSize: "13px", color: "var(--t-orange)", backgroundColor: "var(--t-orange-soft)", border: "1px solid var(--t-orange-border)" }}>
          <div style={{ fontWeight: "bold" }}>需要留意的常見錯誤</div>
          {run.errors.map((err) => (
            <div key={err.code} style={{ marginTop: "6px" }}>
              <b>{err.label}</b>
              {err.explanation && <span>：{err.explanation}</span>}
              {err.evidence && <div style={{ fontSize: "12px", fontStyle: "italic" }}>原文：{err.evidence}</div>}
            </div>
          ))}
        </div>
      )}

      {run.hint && <HintCard hint={run.hint} />}

      {streaming && (
        <p style={{ margin: 0, fontSize: "13px", color: "var(--t-text-muted)" }}>
          評分中{run.totalDimensions ? `（${run.dimensions.length} / ${run.totalDimensions} 項）` : "…"}
        </p>
      )}
    </div>
  );
}
