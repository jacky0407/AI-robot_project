"use client";

// 學生：課程簡介與關卡列表
// 從「我的課程」點進來先看到這一頁：課程說明、老師指派的模組、每一關的狀態；
// 點選某一關才進入作答區（/student/modules?course=<id>&step=<step_id>）
// 資料來源：GET /api/courses/mine、GET /api/courses/{id}/modules、
//           GET /api/practice/modules/{id}/progress、learning_modules / step_attempts（Supabase）

import { useEffect, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { createClient } from "@/utils/supabase/client";
import { ApiError, apiFetch } from "@/utils/api";
import StudentModeTabs from "../../StudentModeTabs";

type MyCourse = {
  course_id: string;
  course_name: string | null;
  description: string | null;
  is_archived: boolean;
  status: "pending" | "active" | "rejected" | "removed";
  plan: "free" | "self_key" | null;
  daily_quota: number | null;
  access_until: string | null;
  access_expired: boolean;
};

type Step = {
  id: string;
  step_order: number;
  step_title: string;
  pass_score: number | null;
  ai_tools?: { title: string; target_competency: string | null } | null;
};

type Module = { id: string; title: string; description: string | null; steps: Step[] };

type AttemptStatus = "draft" | "submitted" | "passed" | "revision_required";
// 只列啟用中的步驟；locked = 前面的必修步驟還沒通過
type StepProgress = { step_id: string; passed: boolean; locked: boolean; locked_by: string | null };

const STEP_STATUS: Record<"todo" | "submitted" | "passed" | "revision_required" | "locked", { text: string; color: string; bg: string }> = {
  todo: { text: "未作答", color: "#94a3b8", bg: "rgba(255,255,255,0.06)" },
  submitted: { text: "評分中", color: "#fbbf24", bg: "rgba(245,158,11,0.15)" },
  passed: { text: "已通過", color: "#4ade80", bg: "rgba(34,197,94,0.15)" },
  revision_required: { text: "需修改", color: "#f87171", bg: "rgba(239,68,68,0.15)" },
  locked: { text: "未解鎖", color: "#64748b", bg: "rgba(255,255,255,0.04)" },
};

const panel: React.CSSProperties = {
  backgroundColor: "#1e293b",
  borderRadius: "14px",
  padding: "20px",
  border: "1px solid rgba(255,255,255,0.1)",
};

const MODULE_SELECT =
  "id, title, description, module_steps(id, step_order, step_title, pass_score, ai_tools(title, target_competency))";

export default function CourseOverviewPage() {
  const router = useRouter();
  const params = useParams<{ courseId: string }>();
  const courseId = params.courseId;
  const [supabase] = useState(() => createClient());

  const [course, setCourse] = useState<MyCourse | null>(null);
  const [modules, setModules] = useState<Module[]>([]);
  const [statusByStep, setStatusByStep] = useState<Record<string, AttemptStatus>>({});
  // module_id → (step_id → 進度)；讀不到某個模組的進度時不擋（後端送出時仍會檢查）
  const [progress, setProgress] = useState<Record<string, Record<string, StepProgress> | null>>({});
  const [loading, setLoading] = useState(true);
  const [errorMsg, setErrorMsg] = useState("");

  useEffect(() => {
    let active = true;
    loadOverview(supabase, courseId).then((r) => {
      if (!active) return;
      if (r.kind === "signed_out") {
        router.replace("/");
        return;
      }
      setCourse(r.course);
      setModules(r.modules);
      setStatusByStep(r.statusByStep);
      setProgress(r.progress);
      setErrorMsg(r.error);
      setLoading(false);
    });
    return () => {
      active = false;
    };
  }, [supabase, router, courseId]);

  const handleSignOut = async () => {
    await supabase.auth.signOut();
    router.push("/");
    router.refresh();
  };

  // 停用的步驟不在進度清單裡，不顯示；讀不到進度的模組全部顯示
  const visibleSteps = (m: Module) => {
    const p = progress[m.id];
    return p ? m.steps.filter((s) => s.id in p) : m.steps;
  };
  const stepState = (m: Module, s: Step): keyof typeof STEP_STATUS => {
    const p = progress[m.id]?.[s.id];
    if (p?.locked) return "locked";
    if (p?.passed) return "passed";
    const st = statusByStep[s.id];
    return st && st !== "draft" ? st : "todo";
  };

  const allSteps = modules.flatMap((m) => visibleSteps(m).map((s) => ({ m, s })));
  const passedCount = allSteps.filter(({ m, s }) => stepState(m, s) === "passed").length;
  // 「繼續練習」：第一個還沒通過、也沒被鎖住的關卡
  const nextUp = allSteps.find(({ m, s }) => !["passed", "locked"].includes(stepState(m, s)));

  const openStep = (s: Step) =>
    router.push(`/student/modules?course=${encodeURIComponent(courseId)}&step=${encodeURIComponent(s.id)}`);

  return (
    <div style={{ minHeight: "100vh", backgroundColor: "#0f172a", color: "#f8fafc", fontFamily: "sans-serif" }}>
      <header
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          padding: "14px 28px",
          borderBottom: "1px solid rgba(255,255,255,0.1)",
          backgroundColor: "#1e293b",
        }}
      >
        <StudentModeTabs />
        <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
          <Link href="/student/profile" style={{ fontSize: "12px", color: "#cbd5e1", textDecoration: "none" }}>
            個人資料
          </Link>
          <button
            onClick={handleSignOut}
            style={{ padding: "6px 12px", fontSize: "12px", borderRadius: "6px", border: "1px solid rgba(255,255,255,0.2)", background: "none", color: "#cbd5e1", cursor: "pointer" }}
          >
            登出
          </button>
        </div>
      </header>

      <main style={{ maxWidth: "960px", margin: "24px auto", padding: "0 20px", display: "flex", flexDirection: "column", gap: "16px" }}>
        <Link href="/student/courses" style={{ alignSelf: "flex-start", fontSize: "13px", color: "#38bdf8", textDecoration: "none" }}>
          ← 我的課程
        </Link>

        {loading && <p style={{ fontSize: "13px", color: "#94a3b8" }}>載入課程中…</p>}

        {/* 課程簡介 */}
        {!loading && course && (
          <section style={panel}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: "16px", flexWrap: "wrap" }}>
              <div style={{ minWidth: 0, flex: "1 1 360px" }}>
                <h1 style={{ fontSize: "20px", fontWeight: "bold", margin: 0, lineHeight: 1.4 }}>
                  {course.course_name || "（未命名課程）"}
                </h1>
                <p style={{ margin: "10px 0 0", fontSize: "14px", lineHeight: 1.7, color: course.description ? "#cbd5e1" : "#64748b", whiteSpace: "pre-wrap" }}>
                  {course.description || "老師還沒有填寫課程簡介。"}
                </p>
                <div style={{ display: "flex", gap: "6px", flexWrap: "wrap", marginTop: "12px" }}>
                  {course.access_until && <Tag>可用到 {course.access_until}</Tag>}
                  {course.plan === "free" && <Tag>每天可作答 {course.daily_quota} 次</Tag>}
                  {course.plan === "self_key" && <Tag>使用自己的金鑰</Tag>}
                  {!course.plan && <Tag>不限練習次數</Tag>}
                </div>
              </div>

              {allSteps.length > 0 && (
                <div className="ov-progress" style={{ flex: "0 0 auto", minWidth: "180px", textAlign: "right" }}>
                  <div style={{ fontSize: "12px", color: "#94a3b8" }}>完成進度</div>
                  <div style={{ fontSize: "24px", fontWeight: "bold", margin: "2px 0 6px" }}>
                    {passedCount} <span style={{ fontSize: "14px", color: "#94a3b8", fontWeight: "normal" }}>/ {allSteps.length} 關</span>
                  </div>
                  <div style={{ height: "6px", borderRadius: "9999px", backgroundColor: "rgba(255,255,255,0.08)", overflow: "hidden" }}>
                    <div style={{ width: `${(passedCount / allSteps.length) * 100}%`, height: "100%", backgroundColor: "#22c55e" }} />
                  </div>
                  {nextUp ? (
                    <button
                      onClick={() => openStep(nextUp.s)}
                      style={{ marginTop: "12px", padding: "9px 18px", borderRadius: "8px", border: "none", backgroundColor: "#2563eb", color: "#fff", fontSize: "13px", fontWeight: "bold", cursor: "pointer" }}
                    >
                      {passedCount === 0 && !statusByStep[nextUp.s.id] ? "開始第一關" : "繼續練習"}
                    </button>
                  ) : (
                    passedCount === allSteps.length && <p style={{ margin: "10px 0 0", fontSize: "12px", color: "#4ade80" }}>所有關卡都已通過</p>
                  )}
                </div>
              )}
            </div>
          </section>
        )}

        {!loading && errorMsg && (
          <section style={{ ...panel, textAlign: "center" }}>
            <p style={{ margin: 0, fontSize: "14px", color: "#fbbf24", lineHeight: 1.6 }}>{errorMsg}</p>
            <button
              onClick={() => router.push("/student/courses")}
              style={{ marginTop: "14px", padding: "8px 16px", borderRadius: "8px", border: "none", backgroundColor: "#2563eb", color: "#fff", fontSize: "13px", fontWeight: "bold", cursor: "pointer" }}
            >
              回到我的課程
            </button>
          </section>
        )}

        {/* 關卡列表：依模組分組 */}
        {!loading &&
          !errorMsg &&
          modules.map((m) => {
            const steps = visibleSteps(m);
            return (
              <section key={m.id} style={panel}>
                <h2 style={{ fontSize: "16px", fontWeight: "bold", margin: 0 }}>{m.title}</h2>
                {m.description && (
                  <p style={{ margin: "6px 0 0", fontSize: "13px", color: "#94a3b8", lineHeight: 1.6, whiteSpace: "pre-wrap" }}>{m.description}</p>
                )}

                {steps.length === 0 ? (
                  <p style={{ margin: "14px 0 0", fontSize: "13px", color: "#64748b" }}>這個模組目前沒有開放的關卡。</p>
                ) : (
                  <ol style={{ listStyle: "none", margin: "14px 0 0", padding: 0, display: "flex", flexDirection: "column", gap: "8px" }}>
                    {steps.map((s) => {
                      const state = stepState(m, s);
                      const badge = STEP_STATUS[state];
                      const locked = state === "locked";
                      const lockedBy = progress[m.id]?.[s.id]?.locked_by;
                      return (
                        <li key={s.id}>
                          <button
                            type="button"
                            disabled={locked}
                            onClick={() => openStep(s)}
                            className="step-row"
                            title={locked ? `請先通過「${lockedBy}」` : "進入作答"}
                            style={{
                              width: "100%",
                              display: "flex",
                              alignItems: "center",
                              gap: "14px",
                              padding: "12px 14px",
                              borderRadius: "10px",
                              border: "1px solid rgba(255,255,255,0.08)",
                              backgroundColor: "rgba(255,255,255,0.03)",
                              color: "inherit",
                              font: "inherit",
                              textAlign: "left",
                              cursor: locked ? "not-allowed" : "pointer",
                              opacity: locked ? 0.55 : 1,
                            }}
                          >
                            <span
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
                                color: state === "passed" ? "#0f172a" : "#cbd5e1",
                                backgroundColor: state === "passed" ? "#4ade80" : "rgba(255,255,255,0.08)",
                              }}
                            >
                              {state === "passed" ? "✓" : s.step_order}
                            </span>
                            <span style={{ flex: 1, minWidth: 0 }}>
                              <span style={{ display: "block", fontSize: "14px", fontWeight: "bold" }}>{s.step_title}</span>
                              <span style={{ display: "block", fontSize: "12px", color: "#94a3b8", marginTop: "2px" }}>
                                {locked
                                  ? `先通過「${lockedBy}」才會解鎖`
                                  : [
                                      s.ai_tools?.target_competency && `練習能力：${s.ai_tools.target_competency}`,
                                      s.pass_score != null && `通過標準 ${s.pass_score} 分`,
                                    ]
                                      .filter(Boolean)
                                      .join("・") || " "}
                              </span>
                            </span>
                            <span style={{ flexShrink: 0, fontSize: "11px", fontWeight: "bold", padding: "2px 8px", borderRadius: "9999px", color: badge.color, backgroundColor: badge.bg }}>
                              {badge.text}
                            </span>
                            {!locked && <span className="step-go" style={{ flexShrink: 0, fontSize: "13px", color: "#38bdf8" }}>作答 →</span>}
                          </button>
                        </li>
                      );
                    })}
                  </ol>
                )}
              </section>
            );
          })}
      </main>

      <style>{`
        .step-row { transition: border-color 0.15s, background-color 0.15s; }
        .step-row:not(:disabled):hover { border-color: #38bdf8 !important; background-color: rgba(56,189,248,0.08) !important; }
        .step-row:focus-visible { outline: 2px solid #38bdf8; outline-offset: 2px; }
        /* 手機：進度區塊改成整列靠左，關卡列拿掉「作答 →」讓標題有空間 */
        @media (max-width: 600px) {
          .ov-progress { flex: 1 1 100% !important; text-align: left !important; }
          .step-go { display: none; }
        }
      `}</style>
    </div>
  );
}

function Tag({ children }: { children: React.ReactNode }) {
  return (
    <span style={{ fontSize: "11px", padding: "2px 8px", borderRadius: "9999px", color: "#94a3b8", backgroundColor: "rgba(255,255,255,0.08)" }}>
      {children}
    </span>
  );
}

type Overview =
  | { kind: "signed_out" }
  | {
      kind: "ok";
      course: MyCourse | null;
      modules: Module[];
      statusByStep: Record<string, AttemptStatus>;
      progress: Record<string, Record<string, StepProgress> | null>;
      error: string;
    };

// 讀取課程簡介頁需要的所有資料；不碰 React state，由呼叫端一次套用
async function loadOverview(supabase: ReturnType<typeof createClient>, courseId: string): Promise<Overview> {
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) return { kind: "signed_out" };

  const result: Extract<Overview, { kind: "ok" }> = { kind: "ok", course: null, modules: [], statusByStep: {}, progress: {}, error: "" };
  const fail = (error: string) => ({ ...result, error });

  try {
    const mine = await apiFetch<{ data: MyCourse[] }>("/api/courses/mine");
    const c = mine.data.find((x) => x.course_id === courseId) ?? null;
    result.course = c;
    if (!c || c.status === "removed") return fail("你不在這門課程裡，請回到「我的課程」重新選擇。");
    if (c.status === "pending") return fail("老師還沒核准你的加入申請，核准後就能開始練習。");
    if (c.status === "rejected") return fail("加入申請未通過，請向老師索取新的邀請碼。");
    if (c.is_archived) return fail("這門課程已封存，無法再作答。");
    if (c.access_expired) return fail("這門課程的可用日期已過，請向老師索取新的邀請碼。");

    const assigned = await apiFetch<{ data: { module_id: string }[] }>(`/api/courses/${encodeURIComponent(courseId)}/modules`);
    const ids = assigned.data.map((a) => a.module_id);
    if (ids.length === 0) return fail("老師還沒有安排這門課程的練習內容，請之後再回來看看。");

    const { data: rows, error } = await supabase.from("learning_modules").select(MODULE_SELECT).in("id", ids);
    if (error) throw error;
    const byId = new Map(
      (rows ?? []).map((m) => [
        m.id,
        {
          id: m.id,
          title: m.title,
          description: m.description,
          steps: [...((m.module_steps as unknown as Step[]) ?? [])].sort((a, b) => a.step_order - b.step_order),
        } as Module,
      ])
    );
    // 依老師指派的順序排列
    const ordered = ids.map((id) => byId.get(id)).filter((m): m is Module => Boolean(m));
    if (ordered.length === 0) return fail("讀不到這門課程的練習內容，請確認老師已發布，或聯絡老師。");
    result.modules = ordered;

    // 每一關最新的作答狀態，與每個模組的解鎖狀態（讀不到某模組的進度就不擋，後端送出時仍會檢查）
    const [{ data: attempts }, progressList] = await Promise.all([
      supabase
        .from("step_attempts")
        .select("step_id, status, attempt_number")
        .eq("user_id", user.id)
        .order("attempt_number", { ascending: false }),
      Promise.all(
        ordered.map((m) =>
          apiFetch<{ data: StepProgress[] }>(`/api/practice/modules/${encodeURIComponent(m.id)}/progress`).then(
            (res) => [m.id, Object.fromEntries(res.data.map((p) => [p.step_id, p]))] as const,
            () => [m.id, null] as const
          )
        )
      ),
    ]);
    for (const a of attempts ?? []) {
      if (!(a.step_id in result.statusByStep)) result.statusByStep[a.step_id] = a.status as AttemptStatus;
    }
    result.progress = Object.fromEntries(progressList);
    return result;
  } catch (err) {
    return fail(
      err instanceof ApiError
        ? `無法讀取課程內容：${err.message}`
        : "無法連線到後端（請確認 FastAPI 已啟動，且 NEXT_PUBLIC_API_URL 設定正確）"
    );
  }
}
