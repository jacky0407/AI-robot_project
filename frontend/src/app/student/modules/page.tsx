"use client";

import { Suspense, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { createClient } from "@/utils/supabase/client";
import { ApiError, apiFetch, streamSSE } from "@/utils/api";

// ─── 步驟與模組型別定義 ───
type Step = {
  id: string;
  module_id: string;
  step_order: number;
  step_title: string;
  pass_score: number | null;
  ai_tools?: { title: string; target_competency: string | null } | null;
};

type Module = {
  id: string;
  title: string;
  description: string | null;
  steps: Step[];
};

type AttemptStatus = "draft" | "submitted" | "passed" | "revision_required";

type CoherenceIssue = { check_name: string; problem: string; suggestion: string; go_to_step_order: number };
type CoherenceResult = {
  overall_coherent: boolean | null;
  summary: string;
  strengths: string[];
  issues: CoherenceIssue[];
};

// ─── AI 評分串流事件（欄位與 backend/core/ai/agents 的 evaluator_agent、tutor_agent 一致） ───
type DimensionScore = { dimension: string; score: number; max_score: number; reason?: string; evidence?: string };
type ScoreComplete = {
  total_score: number;
  percentage?: number;
  passed: boolean;
  overall_feedback: string;
  confidence?: number;
  needs_teacher_review?: boolean;
};
type Hint = { level: number; content: string; source?: "teacher" | "generated"; is_example?: boolean; warning?: string };
type DetectedError = { code: string; label: string; severity?: string; evidence?: string; explanation?: string };
type HintStatus = { hints_used: number; total_levels: number | null; next_level_available: boolean };
// GET /api/practice/modules/{id}/progress：只列啟用中的步驟；locked = 前面的必修步驟還沒通過
type StepProgress = { step_id: string; passed: boolean; locked: boolean; locked_by: string | null };
type CaseData = { id: string; title: string; case_background: string };

type Submission = {
  id: string;
  attempt_number: number;
  status: string;
  created_at: string;
  user_input_content?: string;
  ai_evaluations: { total_score: number | null }[] | { total_score: number | null } | null;
};

// 一次送出的串流結果（逐事件累積）
type RunState = {
  decision?: string;
  dimensions: DimensionScore[];
  totalDimensions?: number;
  score?: ScoreComplete;
  hint?: Hint;
  encouragement?: string;
  escalation?: string;
  errors: DetectedError[];
  failure?: string;
};
const EMPTY_RUN: RunState = { dimensions: [], errors: [] };

const DECISION_LABEL: Record<string, string> = {
  evaluate: "AI 正在依評分規準評分",
  encourage: "接近通過門檻，AI 會評分並給你鼓勵",
  give_hint: "AI 判斷先給你一則提示，修改後再送出",
  escalate: "AI 建議你先和老師討論",
};

const panel: React.CSSProperties = {
  backgroundColor: "#1e293b",
  borderRadius: "14px",
  padding: "20px",
  border: "1px solid rgba(255,255,255,0.1)",
};

function attemptScore(s: Submission): number | null {
  const ev = Array.isArray(s.ai_evaluations) ? s.ai_evaluations[0] : s.ai_evaluations;
  return ev?.total_score ?? null;
}

// GET /api/practice/steps/{step_id}/quota 的回應（backend/core/access.py 的 quota_status）
type Quota = {
  mode: "staff" | "course" | "permission";
  used: number | null;
  quota: number | null;
  remaining: number | null;
  expire_date: string | null;
};
// null = 尚未載入；{ blocked } = 沒被指派或期限已過（後端回 403），不能送出
type QuotaState = { quota: Quota } | { blocked: string } | null;

const LOCKED_LABEL = { text: "🔒 未解鎖", color: "#94a3b8", bg: "rgba(255,255,255,0.06)" };

const STATUS_LABEL: Record<AttemptStatus, { text: string; color: string; bg: string }> = {
  draft: { text: "草稿", color: "#94a3b8", bg: "rgba(255,255,255,0.08)" },
  submitted: { text: "評分中", color: "#fbbf24", bg: "rgba(245,158,11,0.15)" },
  passed: { text: "已通過", color: "#4ade80", bg: "rgba(34,197,94,0.15)" },
  revision_required: { text: "需修改", color: "#f87171", bg: "rgba(239,68,68,0.15)" },
};

// useSearchParams() 必須包在 <Suspense> 裡，否則 next build 預先產生頁面時會失敗
export default function StudentIntegratedWorkspacePage() {
  return (
    <Suspense fallback={null}>
      <StudentWorkspace />
    </Suspense>
  );
}

function StudentWorkspace() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [supabase] = useState(() => createClient());

  // 學生與課程模組資料
  const [modules, setModules] = useState<Module[]>([]);
  const [currentModuleId, setCurrentModuleId] = useState<string>("");
  const [selectedStep, setSelectedStep] = useState<Step | null>(null);
  const [statusByStep, setStatusByStep] = useState<Record<string, AttemptStatus>>({});
  const [loading, setLoading] = useState(true);
  const [errorMsg, setErrorMsg] = useState("");
  const [courseName, setCourseName] = useState("");
  // 從「我的課程」進來時帶 ?course=<id>，只顯示該課程指派的模組；沒帶則維持原本顯示全部可見模組
  const courseId = searchParams.get("course");

  // 一致性檢核 (Coherence)
  const [checkingModule, setCheckingModule] = useState<string | null>(null);
  const [coherence, setCoherence] = useState<Record<string, CoherenceResult | string>>({});

  // 練習工作區狀態
  // 後端目前不保存 session（KNOWN_GAPS #3），這裡只需要一個這次練習用的識別碼
  const [sessionId] = useState(() => crypto.randomUUID());
  const [studentAnswer, setStudentAnswer] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [quotaState, setQuotaState] = useState<QuotaState>(null);
  const [quotaKey, setQuotaKey] = useState(0); // 送出後 +1，重新查剩餘次數
  // 解鎖狀態（step_id → 進度），記下是哪個模組的；讀取失敗時不擋（後端送出時仍會檢查）
  const [unlockState, setUnlockState] = useState<{ moduleId: string; byStep: Record<string, StepProgress> } | null>(null);
  const [piiWarning, setPiiWarning] = useState("");
  const [run, setRun] = useState<RunState | null>(null);
  const [caseData, setCaseData] = useState<CaseData | null>(null);

  // 分層提示與作答紀錄（GET /hints、GET /history）；送出或取用提示後 progressKey +1 重新抓
  const [hintStatus, setHintStatus] = useState<HintStatus | null>(null);
  const [hintStatusError, setHintStatusError] = useState(false);
  const [revealedHints, setRevealedHints] = useState<Hint[]>([]);
  const [hintMessage, setHintMessage] = useState("");
  const [history, setHistory] = useState<Submission[]>([]);
  const [progressKey, setProgressKey] = useState(0);
  const [isLoadingHint, setIsLoadingHint] = useState(false);

  // 切換步驟或模組時清空這一步的作答狀態
  const selectStep = (step: Step | null) => {
    setSelectedStep(step);
    setStudentAnswer("");
    setRun(null);
    setPiiWarning("");
    setRevealedHints([]);
    setHintMessage("");
    setHintStatus(null);
    setHintStatusError(false);
    setHistory([]);
  };

  // 1. 初始化讀取使用者與模組步驟清單
  useEffect(() => {
    const load = async () => {
      const {
        data: { user },
      } = await supabase.auth.getUser();
      if (!user) {
        router.replace("/");
        return;
      }

      // 讀取學生作答最新紀錄狀態
      const loadAttemptStatus = async (uid: string) => {
        const { data: attempts } = await supabase
          .from("step_attempts")
          .select("step_id, status, attempt_number")
          .eq("user_id", uid)
          .order("attempt_number", { ascending: false });

        const latest: Record<string, AttemptStatus> = {};
        for (const a of attempts ?? []) {
          if (!(a.step_id in latest)) latest[a.step_id] = a.status as AttemptStatus;
        }
        setStatusByStep(latest);
      };

      const MODULE_SELECT = "id, title, description, module_steps(id, module_id, step_order, step_title, pass_score, ai_tools(title, target_competency))";
      const toModule = (m: { id: string; title: string; description: string | null; module_steps: unknown }): Module => ({
        id: m.id,
        title: m.title,
        description: m.description,
        steps: [...((m.module_steps as Step[]) ?? [])].sort((a, b) => a.step_order - b.step_order),
      });

      if (courseId) {
        // 從課程進來：只顯示老師指派給這門課、且已發布的模組（GET /api/courses/{id}/modules）
        try {
          const [assigned, mine] = await Promise.all([
            apiFetch<{ data: { module_id: string }[] }>(`/api/courses/${encodeURIComponent(courseId)}/modules`),
            apiFetch<{ data: { course_id: string; course_name: string | null }[] }>("/api/courses/mine"),
          ]);
          setCourseName(mine.data.find((c) => c.course_id === courseId)?.course_name ?? "");

          const ids = assigned.data.map((a) => a.module_id);
          if (ids.length === 0) {
            setErrorMsg("這門課程目前還沒有指派訓練模組，請等老師安排後再回來。");
          } else {
            const { data: rows, error } = await supabase.from("learning_modules").select(MODULE_SELECT).in("id", ids);
            if (error) throw error;
            // 依老師指派的順序排列
            const byId = new Map((rows ?? []).map((r) => [r.id, toModule(r)]));
            const ordered = ids.map((id) => byId.get(id)).filter((m): m is Module => Boolean(m));
            if (ordered.length === 0) {
              setErrorMsg("讀不到這門課程的模組內容，請確認模組已發布，或聯絡老師。");
            } else {
              setModules(ordered);
              setCurrentModuleId(ordered[0].id);
              setSelectedStep(ordered[0].steps[0] || null);
            }
          }
        } catch (err) {
          setErrorMsg(
            err instanceof ApiError
              ? `無法讀取課程模組：${err.message}`
              : "無法讀取課程模組，請確認後端已啟動後重新整理。"
          );
        }
        await loadAttemptStatus(user.id);
        setLoading(false);
        return;
      }

      const { data: moduleRows, error: moduleError } = await supabase
        .from("learning_modules")
        .select(MODULE_SELECT)
        .eq("is_published", true)
        .order("created_at");

      if (moduleError) {
        setErrorMsg(`無法讀取訓練模組：${moduleError.message}`);
      } else if (!moduleRows || moduleRows.length === 0) {
        setErrorMsg("目前沒有可以練習的訓練模組。請在「自主探索」申請機器人並等老師核准，或從「正式課程」加入課程。");
      } else {
        const formatted: Module[] = moduleRows.map(toModule);
        setModules(formatted);
        setCurrentModuleId(formatted[0].id);
        setSelectedStep(formatted[0].steps[0] || null);
      }

      await loadAttemptStatus(user.id);
      setLoading(false);
    };

    load();
  }, [supabase, router, courseId]);

  // 教學案例
  // ⚠️ tool_cases 與步驟之間還沒有關聯欄位（schema 尚未定義），暫時取第一筆案例；送出時把 case_id 一起帶給後端
  useEffect(() => {
    let active = true;
    supabase
      .from("tool_cases")
      .select("id, title, case_background")
      .order("created_at")
      .limit(1)
      .then(({ data }) => {
        if (active) setCaseData((data?.[0] as CaseData | undefined) ?? null);
      });
    return () => {
      active = false;
    };
  }, [supabase]);

  // 分層提示狀態（只查不取用）與這一步的作答紀錄
  useEffect(() => {
    if (!selectedStep) return;
    let active = true;
    const params = new URLSearchParams({ step_id: selectedStep.id }).toString();
    Promise.all([
      apiFetch<{ data: HintStatus }>(`/api/practice/sessions/${sessionId}/hints?${params}`),
      apiFetch<{ data: { submissions: Submission[] } }>(`/api/practice/sessions/${sessionId}/history?${params}`),
    ]).then(
      ([hints, hist]) => {
        if (!active) return;
        setHintStatus(hints.data);
        setHintStatusError(false);
        setHistory(hist.data.submissions);
      },
      () => {
        // 提示與紀錄是輔助資訊，讀不到不影響作答（沒被指派等原因會由剩餘次數區塊說明）；
        // 但要讓畫面離開「讀取中」，學生才知道該重新整理
        if (active) setHintStatusError(true);
      }
    );
    return () => {
      active = false;
    };
  }, [selectedStep, sessionId, progressKey]);

  // 模組裡每一步的解鎖狀態。切換模組、送出作答後重新查（通過一步就可能解鎖下一步）
  const progressModuleId = currentModuleId || modules[0]?.id;
  useEffect(() => {
    if (!progressModuleId) return;
    let active = true;
    apiFetch<{ data: StepProgress[] }>(`/api/practice/modules/${encodeURIComponent(progressModuleId)}/progress`).then(
      (res) => active && setUnlockState({ moduleId: progressModuleId, byStep: Object.fromEntries(res.data.map((p) => [p.step_id, p])) }),
      () => active && setUnlockState(null)
    );
    return () => {
      active = false;
    };
  }, [progressModuleId, progressKey]);
  // 只用目前模組的資料；剛切換模組、還沒讀到時為 null（先全部顯示、不擋）
  const unlock = unlockState?.moduleId === progressModuleId ? unlockState.byStep : null;

  // 今天還能作答幾次（只查不扣）。切換步驟、送出作答後重新查
  useEffect(() => {
    if (!selectedStep) return;
    let active = true;
    apiFetch<{ data: Quota }>(`/api/practice/steps/${selectedStep.id}/quota`).then(
      (res) => active && setQuotaState({ quota: res.data }),
      (err) => {
        if (!active) return;
        // 403：沒被指派或期限已過，送出也會被拒絕，直接停用送出按鈕
        if (err instanceof ApiError && err.status === 403) setQuotaState({ blocked: err.message });
        else setQuotaState(null); // 其他錯誤不擋作答，交給送出時的錯誤訊息
      }
    );
    return () => {
      active = false;
    };
  }, [selectedStep, quotaKey]);

  // 登出
  const handleSignOut = async () => {
    await supabase.auth.signOut();
    router.push("/");
  };

  // 2. 分層提示：先顯示還有沒有下一層，學生按下去才真的取用（reveal=true 會記錄使用次數）
  const handleRevealHint = async () => {
    if (!selectedStep) return;
    setIsLoadingHint(true);
    setHintMessage("");
    try {
      const params = new URLSearchParams({ step_id: selectedStep.id, reveal: "true" }).toString();
      const res = await apiFetch<{ data: HintStatus & { hint: Hint | null; message?: string } }>(
        `/api/practice/sessions/${sessionId}/hints?${params}`
      );
      if (res.data.hint) setRevealedHints((prev) => [...prev, res.data.hint as Hint]);
      if (res.data.message) setHintMessage(res.data.message);
      setHintStatus(res.data);
    } catch (err) {
      setHintMessage(`取得提示失敗：${err instanceof ApiError ? err.message : "連不到後端，請確認 FastAPI 已啟動。"}`);
    } finally {
      setIsLoadingHint(false);
    }
  };

  // 3. 送出作答並逐事件接收 SSE 評分（streamSSE 會自動帶登入 token）
  const handleEvent = (event: string, data: Record<string, unknown>) => {
    setRun((prev) => {
      const r = prev ?? EMPTY_RUN;
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
        case "error":
          return { ...r, failure: `${String(data.message ?? "AI 評分失敗")}，請稍後再試。` };
        default:
          return r;
      }
    });
    if (event === "score_complete" && selectedStep) {
      const passed = Boolean(data.passed);
      setStatusByStep((prev) => ({ ...prev, [selectedStep.id]: passed ? "passed" : "revision_required" }));
    }
  };

  const handleSubmit = async () => {
    if (!selectedStep) return;
    if (!studentAnswer.trim()) {
      setRun({ ...EMPTY_RUN, failure: "請先輸入作答內容再送出。" });
      return;
    }

    setIsSubmitting(true);
    setPiiWarning("");
    setRun({ ...EMPTY_RUN });

    try {
      await streamSSE(
        `/api/practice/sessions/${sessionId}/submit`,
        {
          method: "POST",
          body: JSON.stringify({
            module_id: selectedStep.module_id,
            step_id: selectedStep.id,
            case_id: caseData?.id ?? null,
            content: studentAnswer,
            version_note: "學生工作台提交",
          }),
        },
        handleEvent
      );
    } catch (err) {
      // 顯示真正原因（個資偵測、未登入、沒被指派 403、次數用完 429…），不用假資料蓋掉
      if (err instanceof ApiError && err.code === "PII_DETECTED") {
        const types = (err.detail as { detected_types?: string[] })?.detected_types ?? [];
        setPiiWarning(`${err.message}${types.length ? `（偵測到：${types.join("、")}）` : ""}`);
        setRun(null);
      } else {
        const message =
          err instanceof ApiError
            ? err.status === 401
              ? "登入已過期，請重新登入後再送出。"
              : `送出失敗：${err.message}`
            : "連不到後端，請確認 FastAPI 已啟動後再試一次。";
        if (!(err instanceof ApiError)) console.error("送出作答失敗：", err);
        setRun({ ...EMPTY_RUN, failure: message });
      }
    } finally {
      setIsSubmitting(false);
      setQuotaKey((k) => k + 1);
      setProgressKey((k) => k + 1);
    }
  };

  // 4. 跨步驟一致性檢核 (Coherence Check)
  const runCoherenceCheck = async (modId: string) => {
    setCheckingModule(modId);
    setCoherence((prev) => ({ ...prev, [modId]: "跨步驟檢核分析中…" }));
    try {
      await streamSSE(
        `/api/practice/modules/${modId}/coherence-check`,
        { method: "POST" },
        (event, data) => {
          if (event === "coherence_complete") {
            setCoherence((prev) => ({ ...prev, [modId]: data as unknown as CoherenceResult }));
          } else if (event === "error") {
            setCoherence((prev) => ({ ...prev, [modId]: `檢核失敗：${String(data.message ?? "")}` }));
          }
        }
      );
    } catch (err) {
      setCoherence((prev) => ({
        ...prev,
        [modId]: `檢核失敗：${err instanceof ApiError ? err.message : "連不到後端，請確認 FastAPI 已啟動後再試一次。"}`,
      }));
    } finally {
      setCheckingModule(null);
    }
  };

  const quotaBlockReason =
    quotaState && "blocked" in quotaState
      ? quotaState.blocked
      : quotaState?.quota.remaining === 0
        ? "今天的作答次數已用完，明天（台灣時間 0 點）會恢復"
        : undefined;
  // 從課程進來回課程頁；沒帶 course（自主探索的「開始練習」）回自主探索頁
  const backHref = courseId ? "/student/courses" : "/student/explore";
  // 停用的步驟不在進度清單裡；前面的必修步驟沒通過就鎖住
  const selectedProgress = selectedStep && unlock ? unlock[selectedStep.id] : undefined;
  const lockReason =
    selectedStep && unlock
      ? !selectedProgress
        ? "這個步驟已停用，請選擇其他步驟"
        : selectedProgress.locked
          ? `請先通過「${selectedProgress.locked_by}」，這一步才會解鎖`
          : undefined
      : undefined;
  const submitDisabled = isSubmitting || Boolean(quotaBlockReason) || Boolean(lockReason);

  const currentMod = modules.find((m) => m.id === currentModuleId) || modules[0];
  const coherenceData = currentMod ? coherence[currentMod.id] : null;

  return (
    <div style={{ minHeight: "100vh", backgroundColor: "#0f172a", color: "#f8fafc", fontFamily: "sans-serif" }}>
      {/* 頂部導航 */}
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
        <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
          <button
            onClick={() => router.push(backHref)}
            style={{
              padding: "6px 12px",
              borderRadius: "6px",
              backgroundColor: "rgba(255,255,255,0.08)",
              border: "none",
              color: "#38bdf8",
              cursor: "pointer",
              fontSize: "13px",
            }}
          >
            {courseId ? "← 返回課程選單" : "← 返回自主探索"}
          </button>
          <div>
            <h1 style={{ fontSize: "16px", fontWeight: "bold", margin: 0 }}>
              學生實踐工作台・{currentMod?.title || "訓練模組"}
            </h1>
            <p style={{ fontSize: "12px", color: "#94a3b8", margin: 0 }}>
              {courseName ? `課程：${courseName}・` : ""}循序闖關練習、即時 AI 評分與跨步驟邏輯一致性診斷
            </p>
          </div>
        </div>
        <button
          onClick={handleSignOut}
          style={{
            padding: "6px 14px",
            borderRadius: "6px",
            border: "1px solid rgba(255,255,255,0.2)",
            backgroundColor: "transparent",
            color: "#cbd5e1",
            fontSize: "12px",
            cursor: "pointer",
          }}
        >
          登出
        </button>
      </header>

      {(loading || modules.length === 0) && (
        <div style={{ maxWidth: "640px", margin: "48px auto", padding: "24px", borderRadius: "14px", backgroundColor: "#1e293b", border: "1px solid rgba(255,255,255,0.1)", textAlign: "center" }}>
          <p style={{ margin: 0, fontSize: "14px", color: loading ? "#94a3b8" : "#fbbf24", lineHeight: 1.6 }}>
            {loading ? "載入訓練模組中…" : errorMsg || "目前沒有可以練習的訓練模組。"}
          </p>
          {!loading && (
            <button
              onClick={() => router.push(backHref)}
              style={{ marginTop: "16px", padding: "8px 16px", borderRadius: "8px", border: "none", backgroundColor: "#2563eb", color: "#fff", fontSize: "13px", fontWeight: "bold", cursor: "pointer" }}
            >
              {courseId ? "回到我的課程" : "回到自主探索"}
            </button>
          )}
        </div>
      )}

      {/* 主體兩欄佈局 */}
      {!loading && modules.length > 0 && (
      <main
        style={{
          maxWidth: "1500px",
          margin: "0 auto",
          padding: "24px",
          display: "grid",
          gridTemplateColumns: "340px 1fr",
          gap: "24px",
        }}
      >
        {/* ─── 左側欄：步驟清單與跨步驟檢核 ─── */}
        <aside style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
          <div
            style={{
              backgroundColor: "#1e293b",
              borderRadius: "14px",
              padding: "18px",
              border: "1px solid rgba(255,255,255,0.1)",
            }}
          >
            {modules.length > 1 && (
              <select
                value={currentMod?.id}
                onChange={(e) => {
                  const next = modules.find((m) => m.id === e.target.value);
                  if (!next) return;
                  setCurrentModuleId(next.id);
                  selectStep(next.steps[0] || null);
                }}
                style={{ width: "100%", marginBottom: "14px", padding: "8px 10px", borderRadius: "8px", border: "1px solid rgba(255,255,255,0.2)", backgroundColor: "#0f172a", color: "#f8fafc", fontSize: "13px", colorScheme: "dark" }}
              >
                {modules.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.title}
                  </option>
                ))}
              </select>
            )}
            <h2 style={{ fontSize: "15px", fontWeight: "bold", margin: "0 0 4px 0", color: "#38bdf8" }}>
              📋 模組關卡進度
            </h2>
            <p style={{ fontSize: "12px", color: "#94a3b8", margin: "0 0 14px 0" }}>
              點選步驟切換練習，完成後請進行跨步驟檢核
            </p>

            <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
              {currentMod?.steps
                .filter((step) => !unlock || step.id in unlock)
                .map((step) => {
                const isCurrent = selectedStep?.id === step.id;
                const status = statusByStep[step.id] || "draft";
                const stepLock = unlock?.[step.id];
                const badge = stepLock?.locked ? LOCKED_LABEL : STATUS_LABEL[status];

                return (
                  <div
                    key={step.id}
                    onClick={() => selectStep(step)}
                    style={{
                      padding: "12px 14px",
                      borderRadius: "10px",
                      border: `1.5px solid ${isCurrent ? "#38bdf8" : "rgba(255,255,255,0.08)"}`,
                      backgroundColor: isCurrent ? "rgba(56, 189, 248, 0.12)" : "rgba(255,255,255,0.03)",
                      cursor: "pointer",
                      transition: "all 0.15s ease",
                    }}
                  >
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "4px" }}>
                      <span style={{ fontSize: "12px", color: isCurrent ? "#38bdf8" : "#94a3b8", fontWeight: "bold" }}>
                        關卡 {step.step_order}
                      </span>
                      <span
                        style={{
                          fontSize: "11px",
                          padding: "2px 8px",
                          borderRadius: "9999px",
                          color: badge.color,
                          backgroundColor: badge.bg,
                          fontWeight: "bold",
                        }}
                      >
                        {badge.text}
                      </span>
                    </div>
                    <div style={{ fontSize: "13px", fontWeight: "bold", color: "#f8fafc" }}>
                      {step.step_title}
                    </div>
                    {step.pass_score && (
                      <div style={{ fontSize: "11px", color: "#64748b", marginTop: "4px" }}>
                        通過標準：{step.pass_score} 分
                      </div>
                    )}
                    {stepLock?.locked && (
                      <div style={{ fontSize: "11px", color: "#94a3b8", marginTop: "4px" }}>
                        先通過「{stepLock.locked_by}」才會解鎖
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </div>

          {/* 跨步驟一致性檢核區塊 */}
          <div
            style={{
              backgroundColor: "#1e293b",
              borderRadius: "14px",
              padding: "18px",
              border: "1px solid rgba(255,255,255,0.1)",
            }}
          >
            <h3 style={{ fontSize: "14px", fontWeight: "bold", margin: "0 0 6px 0", color: "#a5b4fc" }}>
              🔍 跨步驟一致性檢核
            </h3>
            <p style={{ fontSize: "12px", color: "#94a3b8", margin: "0 0 12px 0", lineHeight: "1.5" }}>
              當完成多個步驟後，AI 將比對你的前事處遇、替代行為與學期目標是否存在矛盾。
            </p>
            <button
              onClick={() => currentMod && runCoherenceCheck(currentMod.id)}
              disabled={checkingModule !== null}
              style={{
                width: "100%",
                padding: "8px 0",
                borderRadius: "8px",
                border: "none",
                backgroundColor: "#4338ca",
                color: "#fff",
                fontSize: "13px",
                fontWeight: "bold",
                cursor: checkingModule ? "not-allowed" : "pointer",
                opacity: checkingModule ? 0.6 : 1,
              }}
            >
              {checkingModule ? "跨步驟檢核中…" : "執行前後邏輯檢核"}
            </button>

            {typeof coherenceData === "string" && (
              <p style={{ fontSize: "12px", color: "#cbd5e1", marginTop: "10px" }}>{coherenceData}</p>
            )}

            {coherenceData && typeof coherenceData === "object" && (
              <div
                style={{
                  marginTop: "12px",
                  padding: "12px",
                  borderRadius: "8px",
                  fontSize: "12px",
                  lineHeight: 1.5,
                  backgroundColor: coherenceData.overall_coherent ? "rgba(34, 197, 94, 0.12)" : "rgba(239, 68, 68, 0.12)",
                  border: `1px solid ${coherenceData.overall_coherent ? "rgba(34, 197, 94, 0.3)" : "rgba(239, 68, 68, 0.3)"}`,
                }}
              >
                <div style={{ fontWeight: "bold", color: coherenceData.overall_coherent ? "#4ade80" : "#fca5a5" }}>
                  {coherenceData.summary}
                </div>
                {coherenceData.issues.map((iss, i) => (
                  <div key={i} style={{ marginTop: "8px", borderTop: "1px solid rgba(255,255,255,0.08)", paddingTop: "6px" }}>
                    <strong>{iss.check_name}</strong>（建議修改關卡 {iss.go_to_step_order}）
                    <div style={{ color: "#cbd5e1" }}>{iss.problem}</div>
                    <div style={{ color: "#38bdf8", marginTop: "2px" }}>建議：{iss.suggestion}</div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </aside>

        {/* ─── 右側欄：案例、作答、提示與紀錄｜AI 評分回饋 ─── */}
        <section
          style={{
            display: "grid",
            gridTemplateColumns: "1.1fr 0.9fr",
            gap: "20px",
            alignItems: "start",
          }}
        >
          <div style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
            {/* 本關任務與教學案例（從資料庫讀，不再寫死） */}
            <div style={panel}>
              <div style={{ fontSize: "12px", color: "#94a3b8", marginBottom: "6px" }}>
                📌 關卡 {selectedStep?.step_order}：{selectedStep?.step_title}
                {selectedStep?.ai_tools?.title && <span>・AI 助教：{selectedStep.ai_tools.title}</span>}
              </div>
              {selectedStep?.ai_tools?.target_competency && (
                <div style={{ fontSize: "13px", color: "#cbd5e1", marginBottom: "12px" }}>練習能力：{selectedStep.ai_tools.target_competency}</div>
              )}
              {caseData ? (
                <>
                  <span style={{ fontSize: "12px", fontWeight: "bold", color: "#38bdf8", backgroundColor: "rgba(56,189,248,0.12)", padding: "4px 10px", borderRadius: "6px" }}>
                    教學案例：{caseData.title}
                  </span>
                  <p style={{ fontSize: "14px", lineHeight: 1.7, color: "#e2e8f0", margin: "10px 0 0", whiteSpace: "pre-wrap" }}>{caseData.case_background}</p>
                </>
              ) : (
                <p style={{ fontSize: "13px", color: "#fbbf24", margin: 0 }}>讀不到教學案例，請聯絡老師確認案例已建立。</p>
              )}
            </div>

            {/* 作答 */}
            <div style={panel}>
              <h3 style={{ fontSize: "14px", fontWeight: "bold", margin: "0 0 10px 0" }}>你的作答</h3>
              {piiWarning && (
                <div style={{ marginBottom: "10px", padding: "10px 12px", borderRadius: "8px", fontSize: "13px", color: "#fca5a5", backgroundColor: "rgba(239,68,68,0.12)", border: "1px solid rgba(239,68,68,0.35)" }}>
                  ⚠️ {piiWarning}
                </div>
              )}
              <textarea
                value={studentAnswer}
                onChange={(e) => setStudentAnswer(e.target.value)}
                placeholder="請依案例情境完成本關卡的撰寫…"
                style={{ width: "100%", height: "220px", boxSizing: "border-box", padding: "12px", borderRadius: "8px", border: "1px solid rgba(255,255,255,0.15)", backgroundColor: "#0f172a", color: "#f8fafc", fontSize: "14px", lineHeight: 1.6, resize: "vertical", outline: "none" }}
              />
              <div style={{ fontSize: "11px", color: "#64748b", margin: "6px 0 12px" }}>
                字數：{studentAnswer.length}・請勿輸入真實姓名、電話、身分證字號等個資
              </div>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: "12px" }}>
                {lockReason ? (
                  <span style={{ fontSize: "12px", color: "#94a3b8" }}>🔒 {lockReason}</span>
                ) : (
                  <QuotaBadge state={quotaState} />
                )}
                <button
                  onClick={handleSubmit}
                  disabled={submitDisabled}
                  title={lockReason ?? quotaBlockReason}
                  style={{
                    flexShrink: 0,
                    whiteSpace: "nowrap",
                    padding: "10px 22px",
                    borderRadius: "8px",
                    border: "none",
                    backgroundColor: submitDisabled ? "#475569" : "#2563eb",
                    color: "#fff",
                    fontSize: "13px",
                    fontWeight: "bold",
                    cursor: submitDisabled ? "not-allowed" : "pointer",
                  }}
                >
                  {isSubmitting ? "AI 評分中…" : "送出作答並評分"}
                </button>
              </div>
            </div>

            {/* 分層提示：先顯示還有沒有下一層，按下去才真的取用 */}
            <div style={panel}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: "12px" }}>
                <h3 style={{ fontSize: "14px", fontWeight: "bold", margin: 0 }}>💡 分層提示</h3>
                <button
                  onClick={handleRevealHint}
                  // 還沒送出過作答時提示無處記錄，先送出一次再索取
                  disabled={isLoadingHint || !hintStatus?.next_level_available || history.length === 0}
                  style={{ flexShrink: 0, padding: "6px 12px", borderRadius: "8px", border: "1px solid #38bdf8", background: "none", color: "#38bdf8", fontSize: "12px", fontWeight: "bold", cursor: "pointer", opacity: isLoadingHint || !hintStatus?.next_level_available || history.length === 0 ? 0.4 : 1 }}
                >
                  {isLoadingHint ? "取得中…" : "我需要提示"}
                </button>
              </div>
              <p style={{ fontSize: "12px", color: "#94a3b8", margin: "8px 0 0" }}>
                {hintStatus
                  ? `已使用 ${hintStatus.hints_used} 則${hintStatus.total_levels != null ? ` / 共 ${hintStatus.total_levels} 層` : ""}${!hintStatus.next_level_available ? "・目前沒有可用的下一層提示" : ""}`
                  : hintStatusError
                    ? "讀不到提示狀態，請稍後重新整理頁面"
                    : "讀取提示狀態中…"}
                {history.length === 0 && "・先送出一次作答後才能索取提示"}
              </p>
              {hintMessage && <p style={{ fontSize: "12px", color: "#cbd5e1", margin: "8px 0 0" }}>{hintMessage}</p>}
              {[...revealedHints, ...(run?.hint ? [run.hint] : [])].map((h, i) => (
                <div key={`${h.level}-${i}`} style={{ marginTop: "10px", padding: "10px 12px", borderRadius: "8px", backgroundColor: "rgba(129,140,248,0.12)", border: "1px solid rgba(129,140,248,0.3)" }}>
                  <div style={{ fontSize: "11px", fontWeight: "bold", color: "#a5b4fc" }}>
                    第 {h.level} 層{h.source === "generated" ? "（AI 依評分結果生成）" : ""}
                  </div>
                  <p style={{ fontSize: "13px", color: "#e0e7ff", margin: "4px 0 0", whiteSpace: "pre-wrap", lineHeight: 1.6 }}>{h.content}</p>
                  {h.warning && <p style={{ fontSize: "11px", color: "#c7d2fe", margin: "4px 0 0" }}>{h.warning}</p>}
                </div>
              ))}
            </div>

            {/* 作答版本紀錄 */}
            {history.length > 0 && (
              <div style={panel}>
                <h3 style={{ fontSize: "14px", fontWeight: "bold", margin: "0 0 10px 0" }}>📜 作答紀錄</h3>
                <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
                  {[...history].reverse().map((sub) => {
                    const status = STATUS_LABEL[sub.status as AttemptStatus];
                    const score = attemptScore(sub);
                    return (
                      <details key={sub.id} style={{ padding: "8px 10px", borderRadius: "8px", backgroundColor: "rgba(255,255,255,0.04)", fontSize: "13px" }}>
                        <summary style={{ cursor: "pointer", display: "flex", alignItems: "center", gap: "8px", flexWrap: "wrap" }}>
                          <b>第 {sub.attempt_number} 次</b>
                          {status && (
                            <span style={{ fontSize: "11px", padding: "1px 8px", borderRadius: "9999px", color: status.color, backgroundColor: status.bg }}>{status.text}</span>
                          )}
                          <span style={{ color: "#cbd5e1" }}>{score == null ? "未評分" : `${Math.round(score)} 分`}</span>
                          <span style={{ fontSize: "11px", color: "#64748b" }}>{new Date(sub.created_at).toLocaleString()}</span>
                        </summary>
                        {sub.user_input_content && (
                          <div style={{ marginTop: "8px", display: "flex", gap: "8px", alignItems: "flex-start" }}>
                            <p style={{ flex: 1, margin: 0, whiteSpace: "pre-wrap", color: "#cbd5e1", lineHeight: 1.6 }}>{sub.user_input_content}</p>
                            <button
                              onClick={() => setStudentAnswer(sub.user_input_content ?? "")}
                              style={{ flexShrink: 0, padding: "4px 10px", borderRadius: "6px", border: "1px solid rgba(255,255,255,0.2)", background: "none", color: "#cbd5e1", fontSize: "11px", cursor: "pointer" }}
                            >
                              帶入修改
                            </button>
                          </div>
                        )}
                      </details>
                    );
                  })}
                </div>
              </div>
            )}
          </div>

          {/* AI 評分回饋 */}
          <div style={{ ...panel, minHeight: "480px", display: "flex", flexDirection: "column", gap: "12px" }}>
            <h3 style={{ fontSize: "15px", fontWeight: "bold", margin: 0, paddingBottom: "12px", borderBottom: "1px solid rgba(255,255,255,0.08)" }}>
              📊 AI 評分回饋
            </h3>

            {!run && !isSubmitting && (
              <p style={{ margin: "auto", textAlign: "center", fontSize: "13px", color: "#64748b" }}>尚未送出作答。完成撰寫後按「送出作答並評分」。</p>
            )}

            {run?.failure && (
              <div style={{ padding: "10px 12px", borderRadius: "8px", fontSize: "13px", color: "#fca5a5", backgroundColor: "rgba(239,68,68,0.12)" }}>⚠️ {run.failure}</div>
            )}

            {run?.decision && <p style={{ margin: 0, fontSize: "13px", color: "#94a3b8" }}>{DECISION_LABEL[run.decision] ?? run.decision}</p>}
            {run?.encouragement && <div style={{ padding: "10px 12px", borderRadius: "8px", fontSize: "13px", color: "#86efac", backgroundColor: "rgba(34,197,94,0.12)" }}>{run.encouragement}</div>}
            {run?.escalation && <div style={{ padding: "10px 12px", borderRadius: "8px", fontSize: "13px", color: "#fdba74", backgroundColor: "rgba(249,115,22,0.12)" }}>{run.escalation}</div>}
            {run?.hint && <p style={{ margin: 0, fontSize: "12px", color: "#a5b4fc" }}>AI 給了一則提示，請看左側「分層提示」。</p>}

            {run?.score && (
              <div
                style={{
                  padding: "16px",
                  borderRadius: "10px",
                  backgroundColor: run.score.passed ? "rgba(34,197,94,0.12)" : "rgba(239,68,68,0.12)",
                  border: `1px solid ${run.score.passed ? "rgba(34,197,94,0.4)" : "rgba(239,68,68,0.4)"}`,
                }}
              >
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                  <span style={{ fontSize: "26px", fontWeight: "bold", color: run.score.passed ? "#4ade80" : "#f87171" }}>
                    {Math.round(run.score.percentage ?? run.score.total_score)} 分
                  </span>
                  <span style={{ fontSize: "12px", fontWeight: "bold", padding: "4px 10px", borderRadius: "9999px", color: "#fff", backgroundColor: run.score.passed ? "#22c55e" : "#ef4444" }}>
                    {run.score.passed ? "✓ 已達通過門檻" : "✕ 尚未達標，請依回饋修改"}
                  </span>
                </div>
                {run.score.overall_feedback && (
                  <p style={{ margin: "10px 0 0", fontSize: "13px", lineHeight: 1.6, color: "#e2e8f0", whiteSpace: "pre-wrap" }}>{run.score.overall_feedback}</p>
                )}
                {run.score.needs_teacher_review && <p style={{ margin: "8px 0 0", fontSize: "12px", color: "#fbbf24" }}>這次 AI 評分較沒把握，老師會再複核。</p>}
              </div>
            )}

            {run && run.dimensions.length > 0 && (
              <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
                {run.dimensions.map((d) => (
                  <div key={d.dimension} style={{ padding: "10px 12px", borderRadius: "8px", backgroundColor: "rgba(255,255,255,0.04)", fontSize: "13px" }}>
                    <div style={{ display: "flex", justifyContent: "space-between", fontWeight: "bold" }}>
                      <span>{d.dimension}</span>
                      <span style={{ color: "#38bdf8" }}>
                        {d.score} / {d.max_score}
                      </span>
                    </div>
                    {d.reason && <p style={{ margin: "4px 0 0", color: "#cbd5e1", lineHeight: 1.5 }}>{d.reason}</p>}
                    {d.evidence && <p style={{ margin: "4px 0 0", fontSize: "12px", color: "#94a3b8", fontStyle: "italic" }}>引用：{d.evidence}</p>}
                  </div>
                ))}
              </div>
            )}
            {isSubmitting && (
              <p style={{ margin: 0, fontSize: "12px", color: "#94a3b8" }}>
                AI 評分中{run?.totalDimensions ? `（${run.dimensions.length} / ${run.totalDimensions} 個構面）` : "…"}
              </p>
            )}

            {run && run.errors.length > 0 && (
              <div style={{ padding: "10px 12px", borderRadius: "8px", fontSize: "13px", color: "#fdba74", backgroundColor: "rgba(249,115,22,0.1)", border: "1px solid rgba(249,115,22,0.3)" }}>
                <div style={{ fontWeight: "bold", marginBottom: "4px" }}>需要留意的常見錯誤</div>
                {run.errors.map((err) => (
                  <div key={err.code} style={{ marginTop: "6px" }}>
                    <b>{err.label}</b>
                    {err.explanation && <span style={{ color: "#fed7aa" }}>：{err.explanation}</span>}
                    {err.evidence && <div style={{ fontSize: "12px", fontStyle: "italic", color: "#fdba74" }}>原文：{err.evidence}</div>}
                  </div>
                ))}
              </div>
            )}
          </div>
        </section>
      </main>
      )}
    </div>
  );
}

// 送出按鈕旁的剩餘次數：課程模式不限次數；自主模式顯示今天剩幾次與使用期限
function QuotaBadge({ state }: { state: QuotaState }) {
  const base: React.CSSProperties = { fontSize: "12px", lineHeight: 1.5 };
  if (!state) return <span style={{ ...base, color: "#64748b" }}>送出後將由 AI 規準引擎串流評估</span>;
  if ("blocked" in state) return <span style={{ ...base, color: "#f87171" }}>⚠️ {state.blocked}</span>;

  const q = state.quota;
  if (q.mode === "staff") return <span style={{ ...base, color: "#64748b" }}>教師帳號：不限次數</span>;
  if (q.mode === "course") return <span style={{ ...base, color: "#64748b" }}>正式課程：不限練習次數</span>;

  const color = q.remaining === 0 ? "#f87171" : (q.remaining ?? 0) <= 2 ? "#fbbf24" : "#86efac";
  return (
    <span style={{ ...base, color }}>
      今天還可以作答 <b>{q.remaining}</b> / {q.quota} 次
      <span style={{ color: "#64748b" }}>
        {q.expire_date ? `・可使用到 ${q.expire_date}` : "・不限期"}・自主練習，AI 回饋僅供參考
      </span>
    </span>
  );
}
