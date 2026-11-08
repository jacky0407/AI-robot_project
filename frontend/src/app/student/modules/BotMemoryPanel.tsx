"use client";

// 「AI 助教記得你」：這支機器人（= 這一步）評分與給提示時會參考的記憶
// 資料：GET /api/practice/steps/{step_id}/memory（只讀，不呼叫 LLM、不扣額度）
// 規則見 docs/LLM_SYSTEM.md 6.3、backend/core/ai/memory.py：
//   - 記憶只用在整體回饋與提示，不影響逐構面評分
//   - 教授可以逐支機器人關掉記憶（enabled=false）

import { useEffect, useState } from "react";
import { ApiError, apiFetch } from "@/utils/api";

type AttemptMemory = {
  attempt_number: number;
  score: number | null; // null = 那次只拿到提示、沒有評分
  weakest_dimension: string | null;
  errors: string[];
  hints_given: number;
  teacher_score: number | null; // 只有已發布的教師複核
};

type BotMemory = {
  enabled: boolean;
  attempts: AttemptMemory[]; // 舊 → 新
  recurring_errors: string[];
  prior_steps: { step_order: number; step_title: string; passed: boolean }[];
};

type State = { memory: BotMemory } | { error: string } | null;

const chip = (color: string, bg: string): React.CSSProperties => ({
  fontSize: "11px",
  padding: "2px 8px",
  borderRadius: "9999px",
  color,
  backgroundColor: bg,
  whiteSpace: "nowrap",
});

export default function BotMemoryPanel({ stepId, refreshKey, panelStyle }: { stepId: string; refreshKey: number; panelStyle: React.CSSProperties }) {
  const [state, setState] = useState<State>(null);
  const [open, setOpen] = useState(true);

  // 切換步驟、送出作答後重新讀
  useEffect(() => {
    let active = true;
    apiFetch<{ data: BotMemory }>(`/api/practice/steps/${stepId}/memory`).then(
      (res) => active && setState({ memory: res.data }),
      (err) => {
        if (!active) return;
        // 沒被指派等情況由剩餘次數區塊說明，這裡只簡短帶過
        setState({ error: err instanceof ApiError ? err.message : "連不到後端" });
      }
    );
    return () => {
      active = false;
    };
  }, [stepId, refreshKey]);

  const memory = state && "memory" in state ? state.memory : null;
  const isEmpty = memory && memory.attempts.length === 0 && memory.prior_steps.length === 0;

  return (
    <div style={panelStyle}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        style={{ width: "100%", display: "flex", justifyContent: "space-between", alignItems: "center", gap: "8px", padding: 0, background: "none", border: "none", color: "inherit", font: "inherit", cursor: "pointer" }}
      >
        <h3 style={{ fontSize: "14px", fontWeight: "bold", margin: 0 }}>🧠 AI 助教記得你</h3>
        <span style={{ fontSize: "12px", color: "#64748b" }}>{open ? "收合 ▴" : "展開 ▾"}</span>
      </button>

      {open && (
        <div style={{ marginTop: "10px", display: "flex", flexDirection: "column", gap: "12px", fontSize: "13px" }}>
          {state === null && <p style={{ margin: 0, color: "#94a3b8" }}>讀取中…</p>}
          {state && "error" in state && <p style={{ margin: 0, color: "#64748b" }}>暫時讀不到記憶（{state.error}）。不影響作答與評分。</p>}

          {memory && !memory.enabled && <p style={{ margin: 0, color: "#94a3b8" }}>老師沒有為這支 AI 助教開啟記憶，每次作答都會重新開始。</p>}

          {memory?.enabled && isEmpty && (
            <p style={{ margin: 0, color: "#94a3b8" }}>這是你第一次練習這一關，AI 助教還沒有關於你的紀錄。送出作答後，它會記住你的進步與常犯的錯誤。</p>
          )}

          {memory?.enabled && !isEmpty && (
            <>
              {/* 重複出現的錯誤：最值得注意，放最上面 */}
              {memory.recurring_errors.length > 0 && (
                <div style={{ padding: "10px 12px", borderRadius: "8px", backgroundColor: "rgba(245,158,11,0.12)", border: "1px solid rgba(245,158,11,0.35)" }}>
                  <div style={{ fontSize: "12px", fontWeight: "bold", color: "#fbbf24", marginBottom: "4px" }}>你在多次作答都出現的問題</div>
                  <div style={{ display: "flex", gap: "6px", flexWrap: "wrap" }}>
                    {memory.recurring_errors.map((e) => (
                      <span key={e} style={chip("#fde68a", "rgba(245,158,11,0.2)")}>
                        {e}
                      </span>
                    ))}
                  </div>
                  <div style={{ fontSize: "12px", color: "#fcd34d", marginTop: "6px" }}>這次作答可以特別留意。</div>
                </div>
              )}

              {/* 這一關最近幾次 */}
              {memory.attempts.length > 0 && (
                <div>
                  <div style={{ fontSize: "12px", color: "#94a3b8", marginBottom: "6px" }}>它記得你這一關最近 {memory.attempts.length} 次的作答</div>
                  <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
                    {[...memory.attempts].reverse().map((a) => (
                      <div key={a.attempt_number} style={{ display: "flex", alignItems: "center", gap: "8px", flexWrap: "wrap", padding: "8px 10px", borderRadius: "8px", backgroundColor: "rgba(255,255,255,0.04)" }}>
                        <b style={{ flexShrink: 0 }}>第 {a.attempt_number} 次</b>
                        <span style={{ color: a.score == null ? "#64748b" : "#e2e8f0" }}>{a.score == null ? "只拿了提示" : `${Math.round(a.score)} 分`}</span>
                        {a.teacher_score != null && <span style={chip("#86efac", "rgba(34,197,94,0.15)")}>老師評 {a.teacher_score} 分</span>}
                        {a.weakest_dimension && <span style={chip("#fca5a5", "rgba(239,68,68,0.12)")}>最弱：{a.weakest_dimension}</span>}
                        {a.hints_given > 0 && <span style={chip("#c7d2fe", "rgba(129,140,248,0.15)")}>用了 {a.hints_given} 則提示</span>}
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* 前面步驟 */}
              {memory.prior_steps.length > 0 && (
                <div>
                  <div style={{ fontSize: "12px", color: "#94a3b8", marginBottom: "6px" }}>它也會參考你前面關卡寫的內容</div>
                  <div style={{ display: "flex", gap: "6px", flexWrap: "wrap" }}>
                    {memory.prior_steps.map((p) => (
                      <span key={p.step_order} style={chip(p.passed ? "#86efac" : "#cbd5e1", p.passed ? "rgba(34,197,94,0.12)" : "rgba(255,255,255,0.08)")}>
                        關卡 {p.step_order}：{p.step_title}
                        {p.passed ? " ✓" : "（未通過）"}
                      </span>
                    ))}
                  </div>
                </div>
              )}
            </>
          )}

          {memory?.enabled && (
            <p style={{ margin: 0, fontSize: "11px", color: "#64748b", lineHeight: 1.6 }}>
              記憶只用在整體回饋與提示，不影響逐構面評分：每次的分數都只看這次作答。
            </p>
          )}
        </div>
      )}
    </div>
  );
}
