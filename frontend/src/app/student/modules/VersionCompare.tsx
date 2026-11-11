"use client";

// 作答版本比對（需求書 P0）
// 從 GET /api/practice/sessions/{id}/history 拿到的歷次作答中挑兩版：
//   - 文字差異（並排／合併標示）
//   - 總分、各構面分數的變化
//   - 常見錯誤：已改善／新出現／仍存在

import { useEffect, useMemo, useState } from "react";
import { diffStats, diffText, type DiffPart } from "@/utils/textDiff";

type DimensionScore = { dimension: string; score: number; max_score?: number };
type DetectedError = { code: string; label: string };
type Evaluation = {
  total_score: number | null;
  dimension_scores?: DimensionScore[] | null;
  detected_errors?: DetectedError[] | null;
  feedback_text?: string | null;
};

export type HistoryAttempt = {
  id: string;
  attempt_number: number;
  status: string;
  created_at: string;
  user_input_content?: string;
  ai_evaluations: Evaluation[] | Evaluation | null;
};

const STATUS_TEXT: Record<string, string> = {
  passed: "已通過",
  revision_required: "需修改",
  pending_review: "待老師複核",
  draft: "草稿",
  submitted: "已送出",
};

const evaluationOf = (a: HistoryAttempt): Evaluation | null =>
  (Array.isArray(a.ai_evaluations) ? a.ai_evaluations[0] : a.ai_evaluations) ?? null;

const fmt = (n: number | null | undefined) => (n == null ? "—" : Number.isInteger(n) ? String(n) : n.toFixed(1));

function Delta({ from, to }: { from: number | null | undefined; to: number | null | undefined }) {
  if (from == null || to == null) return <span style={{ color: "#64748b" }}>—</span>;
  const d = to - from;
  const color = d > 0 ? "#4ade80" : d < 0 ? "#f87171" : "#94a3b8";
  return <span style={{ color, fontWeight: "bold" }}>{d > 0 ? `▲ +${fmt(d)}` : d < 0 ? `▼ ${fmt(d)}` : "持平"}</span>;
}

const ADDED: React.CSSProperties = { backgroundColor: "rgba(34,197,94,0.25)", color: "#bbf7d0", borderRadius: "2px" };
const REMOVED: React.CSSProperties = { backgroundColor: "rgba(239,68,68,0.25)", color: "#fecaca", textDecoration: "line-through", borderRadius: "2px" };

function DiffText({ parts, show }: { parts: DiffPart[]; show: Array<DiffPart["type"]> }) {
  return (
    <p style={{ margin: 0, whiteSpace: "pre-wrap", lineHeight: 1.9, fontSize: "14px", color: "#e2e8f0" }}>
      {parts
        .filter((p) => show.includes(p.type))
        .map((p, i) => (
          <span key={i} style={p.type === "added" ? ADDED : p.type === "removed" ? REMOVED : undefined}>
            {p.text}
          </span>
        ))}
    </p>
  );
}

const box: React.CSSProperties = { padding: "14px", borderRadius: "10px", backgroundColor: "rgba(255,255,255,0.04)", border: "1px solid rgba(255,255,255,0.08)" };
const select: React.CSSProperties = { padding: "6px 10px", borderRadius: "8px", border: "1px solid rgba(255,255,255,0.2)", backgroundColor: "#0f172a", color: "#f8fafc", fontSize: "13px" };

export default function VersionCompare({ attempts, onClose }: { attempts: HistoryAttempt[]; onClose: () => void }) {
  const sorted = useMemo(() => [...attempts].sort((a, b) => a.attempt_number - b.attempt_number), [attempts]);
  // 預設：上一版 vs 最新版
  const [oldId, setOldId] = useState(sorted[sorted.length - 2]?.id ?? sorted[0]?.id);
  const [newId, setNewId] = useState(sorted[sorted.length - 1]?.id);
  const [view, setView] = useState<"side" | "inline">("side");

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const older = sorted.find((a) => a.id === oldId);
  const newer = sorted.find((a) => a.id === newId);

  const parts = useMemo(() => diffText(older?.user_input_content ?? "", newer?.user_input_content ?? ""), [older, newer]);
  const stats = diffStats(parts);

  const evOld = older ? evaluationOf(older) : null;
  const evNew = newer ? evaluationOf(newer) : null;

  // 各構面：以名稱對齊兩版
  const dims = useMemo(() => {
    const names: string[] = [];
    for (const d of [...(evOld?.dimension_scores ?? []), ...(evNew?.dimension_scores ?? [])]) if (!names.includes(d.dimension)) names.push(d.dimension);
    return names.map((name) => ({
      name,
      before: evOld?.dimension_scores?.find((d) => d.dimension === name),
      after: evNew?.dimension_scores?.find((d) => d.dimension === name),
    }));
  }, [evOld, evNew]);

  // 常見錯誤：以 code（沒有就用 label）比對
  const errKey = (e: DetectedError) => e.code || e.label;
  const errsOld = evOld?.detected_errors ?? [];
  const errsNew = evNew?.detected_errors ?? [];
  const fixed = errsOld.filter((e) => !errsNew.some((n) => errKey(n) === errKey(e)));
  const added = errsNew.filter((e) => !errsOld.some((o) => errKey(o) === errKey(e)));
  const still = errsNew.filter((e) => errsOld.some((o) => errKey(o) === errKey(e)));

  const option = (a: HistoryAttempt) => (
    <option key={a.id} value={a.id}>
      第 {a.attempt_number} 次（{fmt(evaluationOf(a)?.total_score)} 分・{new Date(a.created_at).toLocaleDateString("zh-TW")}）
    </option>
  );

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="compare-title"
      onClick={onClose}
      style={{ position: "fixed", inset: 0, zIndex: 50, display: "flex", alignItems: "flex-start", justifyContent: "center", padding: "32px 16px", overflowY: "auto", backgroundColor: "rgba(2,6,23,0.75)", colorScheme: "dark" }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{ width: "100%", maxWidth: "1100px", padding: "24px", borderRadius: "16px", backgroundColor: "#1e293b", border: "1px solid rgba(255,255,255,0.12)", display: "flex", flexDirection: "column", gap: "16px" }}
      >
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: "12px", flexWrap: "wrap" }}>
          <h2 id="compare-title" style={{ fontSize: "17px", fontWeight: "bold", margin: 0 }}>
            作答版本比對
          </h2>
          <button onClick={onClose} aria-label="關閉" style={{ padding: "6px 12px", borderRadius: "8px", border: "1px solid rgba(255,255,255,0.2)", background: "none", color: "#cbd5e1", fontSize: "13px", cursor: "pointer" }}>
            關閉
          </button>
        </div>

        {/* 選兩個版本 */}
        <div style={{ display: "flex", alignItems: "center", gap: "10px", flexWrap: "wrap", fontSize: "13px", color: "#cbd5e1" }}>
          <select aria-label="較早的版本" value={oldId} onChange={(e) => setOldId(e.target.value)} style={select}>
            {sorted.map(option)}
          </select>
          <span style={{ color: "#64748b" }}>→</span>
          <select aria-label="較新的版本" value={newId} onChange={(e) => setNewId(e.target.value)} style={select}>
            {sorted.map(option)}
          </select>
          {older && newer && older.attempt_number > newer.attempt_number && (
            <span style={{ fontSize: "12px", color: "#fbbf24" }}>左邊選的是較新的版本，紅綠標示會反過來</span>
          )}
        </div>

        {older && newer && older.id === newer.id ? (
          <p style={{ margin: 0, fontSize: "13px", color: "#94a3b8" }}>請選兩個不同的版本。</p>
        ) : (
          older &&
          newer && (
            <>
              {/* 摘要 */}
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: "10px" }}>
                <div style={box}>
                  <div style={{ fontSize: "12px", color: "#94a3b8", marginBottom: "6px" }}>AI 總分</div>
                  <div style={{ fontSize: "20px", fontWeight: "bold" }}>
                    {fmt(evOld?.total_score)} → {fmt(evNew?.total_score)}
                  </div>
                  <div style={{ fontSize: "13px", marginTop: "4px" }}>
                    <Delta from={evOld?.total_score} to={evNew?.total_score} />
                  </div>
                </div>
                <div style={box}>
                  <div style={{ fontSize: "12px", color: "#94a3b8", marginBottom: "6px" }}>狀態</div>
                  <div style={{ fontSize: "15px", fontWeight: "bold" }}>
                    {STATUS_TEXT[older.status] ?? older.status} → {STATUS_TEXT[newer.status] ?? newer.status}
                  </div>
                </div>
                <div style={box}>
                  <div style={{ fontSize: "12px", color: "#94a3b8", marginBottom: "6px" }}>文字修改</div>
                  <div style={{ fontSize: "15px", fontWeight: "bold" }}>
                    <span style={{ color: "#4ade80" }}>+{stats.added} 字</span>　<span style={{ color: "#f87171" }}>−{stats.removed} 字</span>
                  </div>
                </div>
              </div>

              {/* 文字差異 */}
              <div>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: "8px", marginBottom: "8px", flexWrap: "wrap" }}>
                  <h3 style={{ fontSize: "14px", fontWeight: "bold", margin: 0 }}>作答內容</h3>
                  <div style={{ display: "flex", gap: "4px", padding: "3px", borderRadius: "8px", backgroundColor: "rgba(255,255,255,0.06)" }}>
                    {(
                      [
                        ["side", "並排"],
                        ["inline", "合併標示"],
                      ] as const
                    ).map(([v, label]) => (
                      <button
                        key={v}
                        onClick={() => setView(v)}
                        style={{ padding: "4px 12px", borderRadius: "6px", border: "none", fontSize: "12px", fontWeight: "bold", cursor: "pointer", backgroundColor: view === v ? "#2563eb" : "transparent", color: view === v ? "#fff" : "#94a3b8" }}
                      >
                        {label}
                      </button>
                    ))}
                  </div>
                </div>
                {stats.added === 0 && stats.removed === 0 ? (
                  <p style={{ ...box, margin: 0, fontSize: "13px", color: "#94a3b8" }}>兩個版本的文字相同。</p>
                ) : view === "side" ? (
                  <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(300px, 1fr))", gap: "10px" }}>
                    <div style={box}>
                      <div style={{ fontSize: "12px", color: "#f87171", marginBottom: "8px" }}>第 {older.attempt_number} 次（刪掉的字標紅）</div>
                      <DiffText parts={parts} show={["same", "removed"]} />
                    </div>
                    <div style={box}>
                      <div style={{ fontSize: "12px", color: "#4ade80", marginBottom: "8px" }}>第 {newer.attempt_number} 次（新增的字標綠）</div>
                      <DiffText parts={parts} show={["same", "added"]} />
                    </div>
                  </div>
                ) : (
                  <div style={box}>
                    <DiffText parts={parts} show={["same", "removed", "added"]} />
                  </div>
                )}
              </div>

              {/* 各構面 */}
              {dims.length > 0 && (
                <div>
                  <h3 style={{ fontSize: "14px", fontWeight: "bold", margin: "0 0 8px 0" }}>各構面分數</h3>
                  <div style={{ ...box, padding: 0, overflowX: "auto" }}>
                    <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "13px" }}>
                      <thead>
                        <tr style={{ color: "#94a3b8", textAlign: "left" }}>
                          <th style={{ padding: "10px 14px", fontWeight: "normal" }}>構面</th>
                          <th style={{ padding: "10px 14px", fontWeight: "normal" }}>第 {older.attempt_number} 次</th>
                          <th style={{ padding: "10px 14px", fontWeight: "normal" }}>第 {newer.attempt_number} 次</th>
                          <th style={{ padding: "10px 14px", fontWeight: "normal" }}>變化</th>
                        </tr>
                      </thead>
                      <tbody>
                        {dims.map((d) => (
                          <tr key={d.name} style={{ borderTop: "1px solid rgba(255,255,255,0.06)" }}>
                            <td style={{ padding: "10px 14px" }}>{d.name}</td>
                            <td style={{ padding: "10px 14px" }}>
                              {fmt(d.before?.score)}
                              {d.before?.max_score != null && <span style={{ color: "#64748b" }}> / {d.before.max_score}</span>}
                            </td>
                            <td style={{ padding: "10px 14px" }}>
                              {fmt(d.after?.score)}
                              {d.after?.max_score != null && <span style={{ color: "#64748b" }}> / {d.after.max_score}</span>}
                            </td>
                            <td style={{ padding: "10px 14px" }}>
                              <Delta from={d.before?.score} to={d.after?.score} />
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}

              {/* 常見錯誤 */}
              {(fixed.length > 0 || added.length > 0 || still.length > 0) && (
                <div>
                  <h3 style={{ fontSize: "14px", fontWeight: "bold", margin: "0 0 8px 0" }}>常見錯誤</h3>
                  <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: "10px" }}>
                    {(
                      [
                        ["✓ 已改善", fixed, "#4ade80"],
                        ["⚠ 新出現", added, "#fbbf24"],
                        ["… 仍存在", still, "#f87171"],
                      ] as const
                    ).map(([title, list, color]) => (
                      <div key={title} style={box}>
                        <div style={{ fontSize: "12px", fontWeight: "bold", color, marginBottom: "6px" }}>
                          {title}（{list.length}）
                        </div>
                        {list.length === 0 ? (
                          <span style={{ fontSize: "12px", color: "#64748b" }}>無</span>
                        ) : (
                          list.map((e) => (
                            <div key={errKey(e)} style={{ fontSize: "13px", color: "#e2e8f0", marginTop: "4px" }}>
                              {e.label}
                            </div>
                          ))
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {evNew?.feedback_text && (
                <div>
                  <h3 style={{ fontSize: "14px", fontWeight: "bold", margin: "0 0 8px 0" }}>第 {newer.attempt_number} 次的 AI 總評</h3>
                  <p style={{ ...box, margin: 0, fontSize: "13px", lineHeight: 1.7, color: "#cbd5e1", whiteSpace: "pre-wrap" }}>{evNew.feedback_text}</p>
                </div>
              )}
            </>
          )
        )}
      </div>
    </div>
  );
}
