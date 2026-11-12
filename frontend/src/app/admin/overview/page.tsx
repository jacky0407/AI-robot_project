"use client";

// 教師端：監控總覽（學生 × 步驟作答狀況）
// 選一門課 → 每個指派的模組一張表：一列一位學生、一欄一個步驟；點一格看那位學生在那一步的每次作答
// 後端：GET /api/courses、GET /api/admin/overview、GET /api/admin/students/{user_id}/steps/{step_id}（backend/api/admin.py）

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { createClient } from "@/utils/supabase/client";
import { ApiError, apiFetch } from "@/utils/api";
import AdminNav from "../AdminNav";

type CellStatus = "not_started" | "in_progress" | "passed" | "needs_review";
type ReviewReason = "escalated" | "low_confidence" | "step_requires_review";

type Cell = {
  status: CellStatus;
  attempt_count: number;
  evaluated_count: number;
  latest_ai_score: number | null;
  best_ai_score: number | null;
  teacher_score: number | null;
  passed: boolean;
  hints_used: number;
  needs_review_count: number;
  review_reasons: ReviewReason[];
  last_activity_at: string | null;
};

type Step = {
  step_id: string;
  step_order: number;
  step_title: string;
  tool_title: string;
  is_required: boolean;
  pass_score: number;
  require_teacher_review: boolean;
  summary: { started: number; passed: number; needs_review: number; avg_latest_ai_score: number | null };
};

type StudentRow = {
  user_id: string;
  full_name: string;
  email: string;
  required_passed: number;
  required_total: number;
  needs_review_count: number;
  last_activity_at: string | null;
  cells: Record<string, Cell>;
};

type ModuleOverview = { module_id: string; title: string; is_published: boolean; steps: Step[]; students: StudentRow[] };
type Overview = { course: { id: string; title: string }; student_count: number; modules: ModuleOverview[] };
type Course = { id: string; title: string; active_count: number };

type AttemptDetail = {
  submission_id: string;
  attempt_number: number;
  submitted_at: string;
  status: string;
  tutor_action: string | null;
  content: string;
  ai_evaluation: { total_score: number | null; confidence: number | null; feedback_text: string } | null;
  teacher_review: { decision: string; final_score: number | null; is_published: boolean } | null;
  hints: { hint_level: number }[];
  review_reasons: ReviewReason[];
};
// GET /api/admin/students/{user_id}/profile（backend/core/learner_profile.py 的 build_profile）
type LearnerProfile = {
  attempt_count: number;
  evaluated_count: number;
  dimensions: { name: string; mastery: number; latest: number; trend: "up" | "down" | "flat" | "new"; evaluations: number; step_orders: (number | null)[] }[];
  errors: { code: string; label: string; count: number; step_orders: (number | null)[]; last_seen_at: string | null; active: boolean; improved_at: string | null }[];
  steps: { step_id: string; step_order: number | null; step_title: string; attempts: number; evaluated: number; best_score: number | null; latest_score: number | null; passed: boolean }[];
  teacher_notes: { step_order: number | null; final_score: number | null; feedback: string; reviewed_at: string | null }[];
};
type ProfileResponse = { student: { full_name: string; email: string }; module: { title: string }; profile: LearnerProfile; saved_version: number | null };

type StepDetail = { student: { full_name: string; email: string }; step: { step_title: string; pass_score: number }; attempts: AttemptDetail[] };

const STATUS_STYLE: Record<CellStatus, { text: string; color: string; bg: string }> = {
  not_started: { text: "未開始", color: "#64748b", bg: "rgba(255,255,255,0.03)" },
  in_progress: { text: "進行中", color: "#93c5fd", bg: "rgba(59,130,246,0.12)" },
  needs_review: { text: "等老師", color: "#fbbf24", bg: "rgba(245,158,11,0.16)" },
  passed: { text: "已通過", color: "#4ade80", bg: "rgba(34,197,94,0.14)" },
};

const REASON_TEXT: Record<ReviewReason, string> = {
  escalated: "AI 建議找老師",
  low_confidence: "AI 信心不足",
  step_requires_review: "教師強制審核點",
};

const DECISION_TEXT: Record<string, string> = {
  accept_ai: "採用 AI 評分",
  modify: "調整分數",
  override: "推翻 AI 判定",
  request_retry: "要求重做",
};

const ATTEMPT_STATUS_TEXT: Record<string, string> = {
  passed: "通過",
  revision_required: "需修改",
  submitted: "已送出",
  draft: "草稿",
};

const panel: React.CSSProperties = { backgroundColor: "#1e293b", borderRadius: "12px", padding: "20px", border: "1px solid rgba(255,255,255,0.1)" };
const smallBtn: React.CSSProperties = { padding: "5px 12px", fontSize: "12px", borderRadius: "6px", border: "1px solid rgba(255,255,255,0.2)", backgroundColor: "transparent", color: "#cbd5e1", cursor: "pointer" };
const th: React.CSSProperties = { padding: "8px 10px", fontSize: "12px", fontWeight: "bold", color: "#cbd5e1", textAlign: "left", borderBottom: "1px solid rgba(255,255,255,0.1)", verticalAlign: "bottom" };
const td: React.CSSProperties = { padding: "6px", borderBottom: "1px solid rgba(255,255,255,0.06)", verticalAlign: "top" };

// 2026-11-08T10:00:00+00:00 → 11/8 18:00（台灣時間）
function shortTime(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso.slice(0, 10);
  return d.toLocaleString("zh-TW", { timeZone: "Asia/Taipei", month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false });
}

function errorText(err: unknown, what: string): string {
  return err instanceof ApiError
    ? `無法讀取${what}：${err.message}`
    : "無法連線到後端（請確認 FastAPI 已啟動，且 NEXT_PUBLIC_API_URL 設定正確）";
}

export default function AdminOverviewPage() {
  const router = useRouter();
  const [supabase] = useState(() => createClient());

  const [courses, setCourses] = useState<Course[] | null>(null);
  const [courseId, setCourseId] = useState("");
  const [overview, setOverview] = useState<Overview | null>(null);
  const [errorMsg, setErrorMsg] = useState("");
  const [selected, setSelected] = useState<{ student: StudentRow; step: Step } | null>(null);
  const [profileOf, setProfileOf] = useState<{ student: StudentRow; module: ModuleOverview } | null>(null);

  useEffect(() => {
    apiFetch<{ data: Course[] }>("/api/courses").then(
      (res) => {
        setCourses(res.data);
        if (res.data.length) setCourseId(res.data[0].id);
      },
      (err) => {
        setCourses([]);
        setErrorMsg(errorText(err, "課程"));
      }
    );
  }, []);

  // 重新整理 = tick + 1；loading 由「目前要的資料」和「已載入的資料」是否一致推出來，
  // 不在 effect 裡同步 setState（react-hooks/set-state-in-effect）
  const [tick, setTick] = useState(0);
  const [loadedKey, setLoadedKey] = useState("");
  const wantedKey = `${courseId}:${tick}`;
  const loading = Boolean(courseId) && loadedKey !== wantedKey;

  useEffect(() => {
    if (!courseId) return;
    let active = true;
    apiFetch<{ data: Overview }>(`/api/admin/overview?course_id=${encodeURIComponent(courseId)}`).then(
      (res) => {
        if (!active) return;
        setOverview(res.data);
        setErrorMsg("");
        setLoadedKey(`${courseId}:${tick}`);
      },
      (err) => {
        if (!active) return;
        setOverview(null);
        setErrorMsg(errorText(err, "監控總覽"));
        setLoadedKey(`${courseId}:${tick}`);
      }
    );
    return () => {
      active = false;
    };
  }, [courseId, tick]);

  const handleSignOut = async () => {
    await supabase.auth.signOut();
    router.push("/");
    router.refresh();
  };

  return (
    <div style={{ minHeight: "100vh", backgroundColor: "#0f172a", color: "#f8fafc", fontFamily: "sans-serif", colorScheme: "dark" }}>
      <header style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: "12px", padding: "14px 24px", backgroundColor: "#1e293b", borderBottom: "1px solid rgba(255,255,255,0.1)", flexWrap: "wrap" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "12px", flexWrap: "wrap" }}>
          <span style={{ backgroundColor: "#16a34a", color: "#fff", padding: "4px 8px", borderRadius: "6px", fontSize: "12px", fontWeight: "bold" }}>管理與複核後台</span>
          <AdminNav />
        </div>
        <button onClick={handleSignOut} style={{ ...smallBtn, padding: "6px 12px" }}>
          登出
        </button>
      </header>

      <main style={{ maxWidth: "1400px", margin: "24px auto", padding: "0 16px", display: "flex", flexDirection: "column", gap: "16px" }}>
        <div style={{ ...panel, display: "flex", justifyContent: "space-between", alignItems: "center", gap: "12px", flexWrap: "wrap" }}>
          <div>
            <h1 style={{ fontSize: "16px", fontWeight: "bold", margin: "0 0 4px 0" }}>監控總覽</h1>
            <p style={{ fontSize: "12px", color: "#94a3b8", margin: 0 }}>
              一列一位學生、一欄一個步驟。「等老師」的學生排在最上面；點一格看那位學生在那一步的每次作答。
            </p>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: "8px", flexWrap: "wrap" }}>
            <label style={{ fontSize: "12px", color: "#cbd5e1" }} htmlFor="course-select">
              課程
            </label>
            <select
              id="course-select"
              value={courseId}
              onChange={(e) => {
                setSelected(null);
                setCourseId(e.target.value);
              }}
              disabled={!courses?.length}
              style={{ padding: "6px 8px", borderRadius: "6px", border: "1px solid rgba(255,255,255,0.2)", backgroundColor: "#0f172a", color: "#fff", fontSize: "13px", maxWidth: "260px" }}
            >
              {courses?.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.title}（{c.active_count} 人）
                </option>
              ))}
            </select>
            <button onClick={() => setTick((t) => t + 1)} disabled={!courseId || loading} style={smallBtn}>
              {loading ? "載入中…" : "↻ 重新整理"}
            </button>
          </div>
        </div>

        {errorMsg && <p style={{ fontSize: "13px", color: "#fca5a5", margin: 0 }}>{errorMsg}</p>}
        {courses === null && <p style={{ fontSize: "13px", color: "#94a3b8" }}>載入中…（後端剛喚醒時可能要等 30 秒）</p>}
        {courses?.length === 0 && !errorMsg && (
          <p style={{ fontSize: "13px", color: "#94a3b8" }}>
            還沒有課程，請先到 <Link href="/admin/courses" style={{ color: "#86efac" }}>課程管理</Link> 建立。
          </p>
        )}

        {overview && <Legend />}

        {overview && overview.modules.length === 0 && (
          <div style={panel}>
            <p style={{ fontSize: "13px", color: "#94a3b8", margin: 0 }}>
              這門課還沒有指派模組，請到 <Link href="/admin/courses" style={{ color: "#86efac" }}>課程管理</Link> 指派。
            </p>
          </div>
        )}

        {overview?.modules.map((m) => (
          <ModuleTable
            key={m.module_id}
            module={m}
            onSelect={(student, step) => setSelected({ student, step })}
            onSelectStudent={(student) => setProfileOf({ student, module: m })}
          />
        ))}
      </main>

      {selected && <DetailDrawer student={selected.student} step={selected.step} onClose={() => setSelected(null)} />}
      {profileOf && <ProfileDrawer student={profileOf.student} module={profileOf.module} onClose={() => setProfileOf(null)} />}
    </div>
  );
}

function Legend() {
  return (
    <div style={{ display: "flex", gap: "12px", flexWrap: "wrap", fontSize: "12px", color: "#94a3b8" }}>
      {(Object.keys(STATUS_STYLE) as CellStatus[]).map((s) => (
        <span key={s} style={{ display: "flex", alignItems: "center", gap: "6px" }}>
          <span style={{ width: "12px", height: "12px", borderRadius: "3px", backgroundColor: STATUS_STYLE[s].bg, border: `1px solid ${STATUS_STYLE[s].color}` }} />
          {STATUS_STYLE[s].text}
        </span>
      ))}
      <span>・格子內：AI 最新分數（師＝已發布的教師分數）・次數・提示數</span>
    </div>
  );
}

function ModuleTable({
  module,
  onSelect,
  onSelectStudent,
}: {
  module: ModuleOverview;
  onSelect: (s: StudentRow, step: Step) => void;
  onSelectStudent: (s: StudentRow) => void;
}) {
  return (
    <section style={panel}>
      <div style={{ display: "flex", alignItems: "center", gap: "8px", marginBottom: "12px", flexWrap: "wrap" }}>
        <h2 style={{ fontSize: "15px", fontWeight: "bold", margin: 0 }}>{module.title || "（未命名模組）"}</h2>
        {!module.is_published && (
          <span style={{ fontSize: "11px", padding: "2px 8px", borderRadius: "9999px", color: "#fbbf24", backgroundColor: "rgba(245,158,11,0.15)" }}>未發布：學生看不到</span>
        )}
        <span style={{ fontSize: "12px", color: "#94a3b8" }}>
          {module.students.length} 位學生・{module.steps.length} 個步驟
        </span>
      </div>

      {module.steps.length === 0 ? (
        <p style={{ fontSize: "13px", color: "#94a3b8", margin: 0 }}>
          這個模組沒有啟用中的步驟，請到 <Link href="/admin/modules" style={{ color: "#86efac" }}>模組管理</Link> 新增。
        </p>
      ) : module.students.length === 0 ? (
        <p style={{ fontSize: "13px", color: "#94a3b8", margin: 0 }}>這門課還沒有已加入的學生。</p>
      ) : (
        <div style={{ overflowX: "auto" }}>
          <table style={{ borderCollapse: "collapse", minWidth: "100%" }}>
            <thead>
              <tr>
                <th style={{ ...th, position: "sticky", left: 0, backgroundColor: "#1e293b", minWidth: "160px" }}>學生</th>
                {module.steps.map((s) => (
                  <th key={s.step_id} style={{ ...th, minWidth: "120px" }}>
                    <div>
                      {s.step_order}. {s.step_title}
                    </div>
                    <div style={{ fontWeight: "normal", color: "#64748b", marginTop: "2px" }}>
                      門檻 {s.pass_score}
                      {!s.is_required && "・選修"}
                      {s.require_teacher_review && "・強制審核"}
                    </div>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {module.students.map((st) => (
                <tr key={st.user_id}>
                  <td style={{ ...td, position: "sticky", left: 0, backgroundColor: "#1e293b", padding: "8px 10px" }}>
                    <button
                      onClick={() => onSelectStudent(st)}
                      title="查看學生概述"
                      style={{ padding: 0, border: "none", background: "none", color: "#86efac", fontSize: "13px", fontWeight: "bold", cursor: "pointer", textAlign: "left" }}
                    >
                      {st.full_name || "（未填姓名）"} ›
                    </button>
                    <div style={{ fontSize: "11px", color: "#64748b" }}>{st.email}</div>
                    <div style={{ fontSize: "11px", color: "#94a3b8", marginTop: "2px" }}>
                      必修 {st.required_passed}/{st.required_total}・最後 {shortTime(st.last_activity_at)}
                    </div>
                  </td>
                  {module.steps.map((s) => (
                    <td key={s.step_id} style={td}>
                      <CellButton cell={st.cells[s.step_id]} onClick={() => onSelect(st, s)} />
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
            <tfoot>
              <tr>
                <td style={{ ...td, position: "sticky", left: 0, backgroundColor: "#1e293b", padding: "8px 10px", fontSize: "12px", color: "#94a3b8", fontWeight: "bold" }}>
                  全班
                </td>
                {module.steps.map((s) => (
                  <td key={s.step_id} style={{ ...td, fontSize: "11px", color: "#94a3b8", padding: "8px 10px", lineHeight: 1.6 }}>
                    開始 {s.summary.started}・通過 {s.summary.passed}
                    <br />
                    {s.summary.needs_review > 0 && <span style={{ color: "#fbbf24" }}>等老師 {s.summary.needs_review}・</span>}
                    平均 {s.summary.avg_latest_ai_score ?? "—"}
                  </td>
                ))}
              </tr>
            </tfoot>
          </table>
        </div>
      )}
    </section>
  );
}

function CellButton({ cell, onClick }: { cell: Cell | undefined; onClick: () => void }) {
  if (!cell) return null;
  const style = STATUS_STYLE[cell.status];
  const started = cell.attempt_count > 0;
  return (
    <button
      onClick={onClick}
      disabled={!started}
      title={cell.review_reasons.map((r) => REASON_TEXT[r]).join("、") || style.text}
      style={{
        width: "100%",
        minHeight: "52px",
        padding: "6px 8px",
        borderRadius: "8px",
        border: `1px solid ${started ? style.color : "rgba(255,255,255,0.08)"}`,
        backgroundColor: style.bg,
        color: "#f8fafc",
        textAlign: "left",
        cursor: started ? "pointer" : "default",
        fontSize: "12px",
      }}
    >
      <div style={{ color: style.color, fontWeight: "bold" }}>{style.text}</div>
      {started && (
        <div style={{ marginTop: "2px", color: "#cbd5e1" }}>
          {cell.teacher_score !== null ? `師 ${cell.teacher_score}` : cell.latest_ai_score ?? "未評分"}
          <span style={{ color: "#64748b" }}>
            ・{cell.attempt_count} 次・提示 {cell.hints_used}
          </span>
        </div>
      )}
    </button>
  );
}

function DetailDrawer({ student, step, onClose }: { student: StudentRow; step: Step; onClose: () => void }) {
  const [detail, setDetail] = useState<StepDetail | null>(null);
  const [errorMsg, setErrorMsg] = useState("");

  useEffect(() => {
    let active = true;
    apiFetch<{ data: StepDetail }>(
      `/api/admin/students/${encodeURIComponent(student.user_id)}/steps/${encodeURIComponent(step.step_id)}`
    ).then(
      (res) => active && setDetail(res.data),
      (err) => active && setErrorMsg(errorText(err, "作答紀錄"))
    );
    return () => {
      active = false;
    };
  }, [student.user_id, step.step_id]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div onClick={onClose} style={{ position: "fixed", inset: 0, backgroundColor: "rgba(0,0,0,0.5)", display: "flex", justifyContent: "flex-end", zIndex: 50 }}>
      <aside
        role="dialog"
        aria-modal="true"
        aria-labelledby="drawer-title"
        onClick={(e) => e.stopPropagation()}
        style={{ width: "min(560px, 100%)", height: "100%", overflowY: "auto", backgroundColor: "#0f172a", borderLeft: "1px solid rgba(255,255,255,0.1)", padding: "20px", boxSizing: "border-box" }}
      >
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: "12px", marginBottom: "16px" }}>
          <div>
            <h2 id="drawer-title" style={{ fontSize: "16px", fontWeight: "bold", margin: "0 0 4px 0" }}>
              {student.full_name || student.email}
            </h2>
            <p style={{ fontSize: "12px", color: "#94a3b8", margin: 0 }}>
              {step.step_order}. {step.step_title}・{step.tool_title}・門檻 {step.pass_score}
            </p>
          </div>
          <button onClick={onClose} style={smallBtn} aria-label="關閉">
            ✕
          </button>
        </div>

        {errorMsg && <p style={{ fontSize: "13px", color: "#fca5a5" }}>{errorMsg}</p>}
        {!detail && !errorMsg && <p style={{ fontSize: "13px", color: "#94a3b8" }}>載入中…</p>}
        {detail?.attempts.length === 0 && <p style={{ fontSize: "13px", color: "#94a3b8" }}>還沒有作答。</p>}

        <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
          {detail?.attempts.map((a) => (
            <div key={a.submission_id} style={{ padding: "12px", borderRadius: "10px", backgroundColor: "rgba(255,255,255,0.04)", border: "1px solid rgba(255,255,255,0.08)" }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: "8px", flexWrap: "wrap" }}>
                <span style={{ fontSize: "13px", fontWeight: "bold" }}>
                  第 {a.attempt_number} 次・{ATTEMPT_STATUS_TEXT[a.status] ?? a.status}
                </span>
                <span style={{ fontSize: "11px", color: "#64748b" }}>{shortTime(a.submitted_at)}</span>
              </div>

              <p style={{ fontSize: "12px", color: "#cbd5e1", margin: "6px 0 0" }}>
                AI：{a.ai_evaluation?.total_score ?? (a.tutor_action === "give_hint" ? "未評分（只給提示）" : a.tutor_action === "escalate" ? "未評分（建議找老師）" : "未評分")}
                {a.ai_evaluation?.confidence != null && `・信心 ${Math.round(a.ai_evaluation.confidence * 100)}%`}
                {a.hints.length > 0 && `・提示 ${a.hints.length} 則`}
              </p>
              {a.teacher_review && (
                <p style={{ fontSize: "12px", color: "#86efac", margin: "4px 0 0" }}>
                  教師：{DECISION_TEXT[a.teacher_review.decision] ?? a.teacher_review.decision}
                  {a.teacher_review.final_score !== null && ` ${a.teacher_review.final_score} 分`}
                  {!a.teacher_review.is_published && "（未發布）"}
                </p>
              )}
              {a.review_reasons.length > 0 && (
                <p style={{ fontSize: "12px", color: "#fbbf24", margin: "4px 0 0" }}>等老師：{a.review_reasons.map((r) => REASON_TEXT[r]).join("、")}</p>
              )}

              <p style={{ fontSize: "12px", color: "#94a3b8", margin: "8px 0 0", whiteSpace: "pre-wrap", lineHeight: 1.6, maxHeight: "120px", overflow: "hidden" }}>
                {a.content}
              </p>

              <div style={{ marginTop: "8px" }}>
                <Link href={`/admin/review/${a.submission_id}`} style={{ fontSize: "12px", color: "#86efac", textDecoration: "none" }}>
                  {a.teacher_review ? "查看複核 →" : "去複核 →"}
                </Link>
              </div>
            </div>
          ))}
        </div>
      </aside>
    </div>
  );
}

const TREND_TEXT: Record<string, { text: string; color: string }> = {
  up: { text: "▲ 進步", color: "#4ade80" },
  down: { text: "▼ 退步", color: "#f87171" },
  flat: { text: "— 持平", color: "#94a3b8" },
  new: { text: "首次", color: "#94a3b8" },
};

function ProfileDrawer({ student, module, onClose }: { student: StudentRow; module: ModuleOverview; onClose: () => void }) {
  const [data, setData] = useState<ProfileResponse | null>(null);
  const [errorMsg, setErrorMsg] = useState("");

  useEffect(() => {
    let active = true;
    apiFetch<{ data: ProfileResponse }>(
      `/api/admin/students/${encodeURIComponent(student.user_id)}/profile?module_id=${encodeURIComponent(module.module_id)}`
    ).then(
      (res) => active && setData(res.data),
      (err) => active && setErrorMsg(errorText(err, "學生概述"))
    );
    return () => {
      active = false;
    };
  }, [student.user_id, module.module_id]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const p = data?.profile;
  const activeErrors = p?.errors.filter((e) => e.active) ?? [];
  const improved = p?.errors.filter((e) => !e.active) ?? [];
  const section: React.CSSProperties = { marginTop: "16px" };
  const h3: React.CSSProperties = { fontSize: "13px", fontWeight: "bold", margin: "0 0 8px 0", color: "#cbd5e1" };

  return (
    <div onClick={onClose} style={{ position: "fixed", inset: 0, backgroundColor: "rgba(0,0,0,0.5)", display: "flex", justifyContent: "flex-end", zIndex: 50 }}>
      <aside
        role="dialog"
        aria-modal="true"
        aria-labelledby="profile-title"
        onClick={(e) => e.stopPropagation()}
        style={{ width: "min(560px, 100%)", height: "100%", overflowY: "auto", backgroundColor: "#0f172a", borderLeft: "1px solid rgba(255,255,255,0.1)", padding: "20px", boxSizing: "border-box" }}
      >
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: "12px" }}>
          <div>
            <h2 id="profile-title" style={{ fontSize: "16px", fontWeight: "bold", margin: "0 0 4px 0" }}>
              {student.full_name || student.email}・學生概述
            </h2>
            <p style={{ fontSize: "12px", color: "#94a3b8", margin: 0 }}>
              {module.title}
              {p && `・作答 ${p.attempt_count} 次（評分 ${p.evaluated_count} 次）`}
            </p>
          </div>
          <button onClick={onClose} style={smallBtn} aria-label="關閉">
            ✕
          </button>
        </div>

        <p style={{ fontSize: "11px", color: "#64748b", margin: "8px 0 0" }}>
          由作答紀錄即時計算，不使用 AI；只給教師看。熟練度是各構面得分率的加權平均，越新的作答權重越高。
        </p>

        {errorMsg && <p style={{ fontSize: "13px", color: "#fca5a5" }}>{errorMsg}</p>}
        {!data && !errorMsg && <p style={{ fontSize: "13px", color: "#94a3b8" }}>載入中…</p>}
        {p && p.evaluated_count === 0 && <p style={{ fontSize: "13px", color: "#94a3b8", marginTop: "16px" }}>還沒有被評分過的作答。</p>}

        {p && p.dimensions.length > 0 && (
          <div style={section}>
            <h3 style={h3}>各構面熟練度（弱 → 強）</h3>
            {p.dimensions.map((d) => {
              const pct = Math.round(d.mastery * 100);
              const trend = TREND_TEXT[d.trend] ?? TREND_TEXT.flat;
              return (
                <div key={d.name} style={{ marginBottom: "8px" }}>
                  <div style={{ display: "flex", justifyContent: "space-between", gap: "8px", fontSize: "12px" }}>
                    <span>{d.name}</span>
                    <span style={{ color: "#94a3b8", whiteSpace: "nowrap" }}>
                      {pct}%・<span style={{ color: trend.color }}>{trend.text}</span>
                    </span>
                  </div>
                  <div style={{ height: "6px", borderRadius: "3px", backgroundColor: "rgba(255,255,255,0.08)", marginTop: "4px" }}>
                    <div style={{ width: `${pct}%`, height: "100%", borderRadius: "3px", backgroundColor: pct < 60 ? "#f59e0b" : "#22c55e" }} />
                  </div>
                </div>
              );
            })}
          </div>
        )}

        {p && (activeErrors.length > 0 || improved.length > 0) && (
          <div style={section}>
            <h3 style={h3}>常犯錯誤</h3>
            {activeErrors.length === 0 && <p style={{ fontSize: "12px", color: "#94a3b8", margin: 0 }}>目前沒有仍在犯的錯誤。</p>}
            {activeErrors.map((e) => (
              <p key={e.code} style={{ fontSize: "12px", margin: "0 0 4px 0", color: "#fbbf24" }}>
                {e.label}・{e.count} 次
                <span style={{ color: "#94a3b8" }}>
                  （{e.step_orders.filter((o) => o != null).map((o) => `步驟 ${o}`).join("、")}・最後 {shortTime(e.last_seen_at)}）
                </span>
              </p>
            ))}
            {improved.length > 0 && (
              <p style={{ fontSize: "12px", margin: "8px 0 0", color: "#4ade80" }}>
                已改善：{improved.map((e) => `${e.label}（${shortTime(e.improved_at)}）`).join("、")}
              </p>
            )}
          </div>
        )}

        {p && p.steps.length > 0 && (
          <div style={section}>
            <h3 style={h3}>各步驟</h3>
            {p.steps.map((s) => (
              <p key={s.step_id} style={{ fontSize: "12px", margin: "0 0 4px 0" }}>
                {s.step_order}. {s.step_title}：{s.passed ? <span style={{ color: "#4ade80" }}>已通過</span> : "未通過"}
                <span style={{ color: "#94a3b8" }}>
                  ・{s.attempts} 次・最佳 {s.best_score ?? "—"}・最新 {s.latest_score ?? "—"}
                </span>
              </p>
            ))}
          </div>
        )}

        {p && p.teacher_notes.length > 0 && (
          <div style={section}>
            <h3 style={h3}>最近的教師意見（已發布）</h3>
            {p.teacher_notes.map((n, i) => (
              <p key={i} style={{ fontSize: "12px", margin: "0 0 6px 0", color: "#cbd5e1" }}>
                步驟 {n.step_order ?? "—"}
                {n.final_score !== null && `・${n.final_score} 分`}：{n.feedback || "（無文字）"}
              </p>
            ))}
          </div>
        )}

        {data && (
          <p style={{ fontSize: "11px", color: "#64748b", marginTop: "16px" }}>
            {data.saved_version ? `AI 助教目前參考的是第 ${data.saved_version} 版側寫。` : "尚未保存側寫：下一次評分後，AI 助教才會開始參考。"}
          </p>
        )}
      </aside>
    </div>
  );
}
