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
  task_desc?: string;
  case_content?: string;
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

// ─── 評分規準維度型別 ───
interface DimensionScore {
  name: string;
  score: number;
  max_score?: number;
  feedback?: string;
}

interface EvaluationResult {
  passed?: boolean;
  score?: number;
  percentage?: number;
  overall_comment?: string;
  dimensions: DimensionScore[];
  suggestions: string[];
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
  const [sessionId, setSessionId] = useState<string>("demo-session");
  const [studentAnswer, setStudentAnswer] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [quotaState, setQuotaState] = useState<QuotaState>(null);
  const [quotaKey, setQuotaKey] = useState(0); // 送出後 +1，重新查剩餘次數
  const [statusMessage, setStatusMessage] = useState("");
  const [piiWarning, setPiiWarning] = useState("");
  const [hintText, setHintText] = useState("");
  const [isLoadingHint, setIsLoadingHint] = useState(false);
  const [evalResult, setEvalResult] = useState<EvaluationResult | null>(null);

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

      const MODULE_SELECT = "id, title, description, module_steps(id, module_id, step_order, step_title, pass_score)";
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

  // 2. 索取鷹架指引提示 (Scaffolding Hints)
  // 後端需要 step_id；reveal=true 才會真的取出下一層提示並記錄使用次數
  const handleFetchHint = async () => {
    if (!selectedStep) return;
    setIsLoadingHint(true);
    try {
      const params = new URLSearchParams({ step_id: selectedStep.id, reveal: "true" }).toString();
      const json = await apiFetch<{ data: { hint: { content?: string } | null; message?: string } }>(
        `/api/practice/sessions/${sessionId}/hints?${params}`
      );
      setHintText(json.data.hint?.content || json.data.message || "目前沒有可用的提示。");
    } catch (err) {
      setHintText(`⚠️ 取得提示失敗：${err instanceof ApiError ? err.message : "連不到後端，請確認 FastAPI 已啟動後再試一次。"}`);
    } finally {
      setIsLoadingHint(false);
    }
  };

  // 3. 提交作答並接收 SSE 串流評分（apiFetch / streamSSE 會自動帶登入 token）
  const handleSubmit = async () => {
    if (!studentAnswer.trim()) {
      setStatusMessage("請先輸入作答內容再送出！");
      return;
    }

    setIsSubmitting(true);
    setStatusMessage("");
    setPiiWarning("");
    setEvalResult({ dimensions: [], suggestions: [] });

    try {
      await streamSSE(
        `/api/practice/sessions/${sessionId}/submit`,
        {
          method: "POST",
          body: JSON.stringify({
            module_id: selectedStep?.module_id, // 後端必填（SubmitAnswerRequest.module_id）
            step_id: selectedStep?.id,
            content: studentAnswer,
            version_note: "學生工作台提交",
          }),
        },
        (eventType, parsed) => {
          if (eventType === "score_start") {
            setStatusMessage("AI 正在閱讀並根據評分規準分析中...");
          } else if (eventType === "dimension_score") {
            setEvalResult((prev) => ({
              ...prev,
              dimensions: [...(prev?.dimensions || []), parsed as unknown as DimensionScore],
              suggestions: prev?.suggestions || [],
            }));
          } else if (eventType === "score_complete") {
            const passed = Boolean(parsed.passed);
            setEvalResult((prev) => ({
              ...prev,
              passed,
              percentage: parsed.percentage as number | undefined,
              overall_comment: parsed.overall_comment as string | undefined,
              suggestions: (parsed.suggestions as string[] | undefined) || [],
              dimensions: prev?.dimensions || [],
            }));
            setStatusMessage("評分完成！");
            if (selectedStep) {
              setStatusByStep((prev) => ({
                ...prev,
                [selectedStep.id]: passed ? "passed" : "revision_required",
              }));
            }
          } else if (eventType === "error") {
            setStatusMessage(`⚠️ ${String(parsed.message ?? "AI 評分失敗")}，請稍後再試。`);
          }
        }
      );
    } catch (err) {
      // 顯示真正原因（個資偵測、未登入、沒被指派 403、次數用完 429…），不用假資料蓋掉
      if (err instanceof ApiError && err.code === "PII_DETECTED") {
        setPiiWarning(err.message);
      } else if (err instanceof ApiError && err.status === 401) {
        setStatusMessage("⚠️ 登入已過期，請重新登入後再送出。");
      } else if (err instanceof ApiError) {
        setStatusMessage(`⚠️ 送出失敗：${err.message}`);
      } else {
        console.error("送出作答失敗：", err);
        setStatusMessage("⚠️ 連不到後端，請確認 FastAPI 已啟動後再試一次。");
      }
      setEvalResult(null);
    } finally {
      setIsSubmitting(false);
      setQuotaKey((k) => k + 1);
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
  const submitDisabled = isSubmitting || Boolean(quotaBlockReason);

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
                  setSelectedStep(next.steps[0] || null);
                  setStudentAnswer("");
                  setEvalResult(null);
                  setPiiWarning("");
                  setHintText("");
                  setStatusMessage("");
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
              {currentMod?.steps.map((step) => {
                const isCurrent = selectedStep?.id === step.id;
                const status = statusByStep[step.id] || "draft";
                const badge = STATUS_LABEL[status];

                return (
                  <div
                    key={step.id}
                    onClick={() => {
                      setSelectedStep(step);
                      setStudentAnswer("");
                      setEvalResult(null);
                      setPiiWarning("");
                      setHintText("");
                      setStatusMessage("");
                    }}
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

        {/* ─── 右側欄：題目卡片、作答輸入與即時評分回饋 ─── */}
        <section
          style={{
            display: "grid",
            gridTemplateColumns: "1.1fr 0.9fr",
            gap: "20px",
            alignItems: "start",
          }}
        >
          {/* 左半邊：案例題目與輸入框 */}
          <div style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
            {/* 個案卡片 */}
            <div
              style={{
                backgroundColor: "#1e293b",
                borderRadius: "14px",
                padding: "20px",
                border: "1px solid rgba(255,255,255,0.1)",
              }}
            >
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "10px" }}>
                <span
                  style={{
                    fontSize: "12px",
                    fontWeight: "bold",
                    color: "#38bdf8",
                    backgroundColor: "rgba(56,189,248,0.12)",
                    padding: "4px 10px",
                    borderRadius: "6px",
                  }}
                >
                  個案情境：小翔（4 歲 2 個月）
                </span>
                <button
                  onClick={handleFetchHint}
                  disabled={isLoadingHint}
                  style={{
                    fontSize: "12px",
                    padding: "5px 12px",
                    borderRadius: "6px",
                    border: "1px solid #38bdf8",
                    backgroundColor: "transparent",
                    color: "#38bdf8",
                    cursor: "pointer",
                  }}
                >
                  {isLoadingHint ? "提示生成中..." : "💡 索取鷹架提示"}
                </button>
              </div>

              <p style={{ fontSize: "14px", lineHeight: "1.6", color: "#cbd5e1", margin: "0 0 12px 0" }}>
                在自由角落活動時，小翔經常獨自玩積木並排列特定顏色。當同儕靠近拿積木時會大叫推人，無法以語言或手勢表達意圖。
              </p>
              <div
                style={{
                  fontSize: "13px",
                  color: "#94a3b8",
                  backgroundColor: "rgba(255,255,255,0.03)",
                  padding: "10px 14px",
                  borderRadius: "8px",
                }}
              >
                📌 <strong>當前關卡任務：</strong> {selectedStep?.step_title || "擬定介入策略"}
              </div>

              {hintText && (
                <div
                  style={{
                    marginTop: "12px",
                    padding: "10px 14px",
                    borderRadius: "8px",
                    backgroundColor: "rgba(56, 189, 248, 0.12)",
                    border: "1px solid rgba(56, 189, 248, 0.3)",
                    color: "#7dd3fc",
                    fontSize: "13px",
                  }}
                >
                  {hintText}
                </div>
              )}
            </div>

            {/* 個資攔截警示 */}
            {piiWarning && (
              <div
                style={{
                  padding: "12px 16px",
                  borderRadius: "8px",
                  backgroundColor: "rgba(239, 68, 68, 0.15)",
                  border: "1px solid rgba(239, 68, 68, 0.5)",
                  color: "#fca5a5",
                  fontSize: "13px",
                  lineHeight: "1.5",
                }}
              >
                {piiWarning}
              </div>
            )}

            {/* 作答區 */}
            <div
              style={{
                backgroundColor: "#1e293b",
                borderRadius: "14px",
                padding: "20px",
                border: "1px solid rgba(255,255,255,0.1)",
                display: "flex",
                flexDirection: "column",
                gap: "12px",
              }}
            >
              <label style={{ fontSize: "14px", fontWeight: "bold" }}>
                請撰寫您的個別化教育計畫策略：
              </label>
              <textarea
                rows={9}
                value={studentAnswer}
                onChange={(e) => setStudentAnswer(e.target.value)}
                placeholder="請具體撰寫行為功能分析、前事介入安排、正向替代行為訓練與評量檢核標準..."
                style={{
                  width: "100%",
                  padding: "12px",
                  borderRadius: "8px",
                  border: "1px solid rgba(255,255,255,0.2)",
                  backgroundColor: "rgba(0,0,0,0.25)",
                  color: "#fff",
                  fontSize: "14px",
                  lineHeight: "1.6",
                  resize: "vertical",
                  boxSizing: "border-box",
                  outline: "none",
                }}
              />

              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: "12px" }}>
                <QuotaBadge state={quotaState} />
                <button
                  onClick={handleSubmit}
                  disabled={submitDisabled}
                  title={quotaBlockReason}
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
                  {isSubmitting ? "AI 評分串流中..." : "送出作答並評分"}
                </button>
              </div>
            </div>
          </div>

          {/* 右半邊：即時評分結果反饋 */}
          <div
            style={{
              backgroundColor: "#1e293b",
              borderRadius: "14px",
              padding: "20px",
              border: "1px solid rgba(255,255,255,0.1)",
              minHeight: "480px",
            }}
          >
            <h2
              style={{
                fontSize: "15px",
                fontWeight: "bold",
                margin: "0 0 14px 0",
                borderBottom: "1px solid rgba(255,255,255,0.1)",
                paddingBottom: "10px",
              }}
            >
              📊 AI 即時評分診斷回饋
            </h2>

            {statusMessage && (
              <div
                style={{
                  marginBottom: "14px",
                  padding: "8px 12px",
                  borderRadius: "6px",
                  backgroundColor: "rgba(56, 189, 248, 0.1)",
                  color: "#38bdf8",
                  fontSize: "13px",
                }}
              >
                {statusMessage}
              </div>
            )}

            {!evalResult?.percentage && !evalResult?.dimensions?.length && !isSubmitting && (
              <div style={{ textAlign: "center", color: "#64748b", marginTop: "120px", fontSize: "13px" }}>
                尚未送出作答。請在左側輸入介入方案後送出評估。
              </div>
            )}

            {/* 成績與達成度徽章 */}
            {evalResult?.percentage !== undefined && (
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  padding: "14px 16px",
                  borderRadius: "10px",
                  backgroundColor: evalResult.passed ? "rgba(34, 197, 94, 0.12)" : "rgba(239, 68, 68, 0.12)",
                  border: `1px solid ${evalResult.passed ? "rgba(34, 197, 94, 0.4)" : "rgba(239, 68, 68, 0.4)"}`,
                  marginBottom: "16px",
                }}
              >
                <div>
                  <div style={{ fontSize: "12px", color: "#94a3b8" }}>綜合達成度</div>
                  <div style={{ fontSize: "26px", fontWeight: "bold", color: evalResult.passed ? "#4ade80" : "#f87171" }}>
                    {evalResult.percentage}%
                  </div>
                </div>
                <span
                  style={{
                    padding: "6px 14px",
                    borderRadius: "9999px",
                    fontSize: "12px",
                    fontWeight: "bold",
                    backgroundColor: evalResult.passed ? "#22c55e" : "#ef4444",
                    color: "#fff",
                  }}
                >
                  {evalResult.passed ? "✓ 通過標準" : "✕ 建議修訂"}
                </span>
              </div>
            )}

            {/* 規準分維度評量 */}
            {evalResult?.dimensions && evalResult.dimensions.length > 0 && (
              <div style={{ display: "flex", flexDirection: "column", gap: "10px", marginBottom: "16px" }}>
                <h3 style={{ fontSize: "13px", color: "#cbd5e1", margin: 0 }}>維度規準評量：</h3>
                {evalResult.dimensions.map((dim, idx) => (
                  <div
                    key={idx}
                    style={{
                      backgroundColor: "rgba(255,255,255,0.03)",
                      padding: "10px 12px",
                      borderRadius: "8px",
                      border: "1px solid rgba(255,255,255,0.05)",
                    }}
                  >
                    <div style={{ display: "flex", justifyContent: "space-between", marginBottom: "3px" }}>
                      <span style={{ fontSize: "13px", fontWeight: "bold", color: "#e2e8f0" }}>{dim.name}</span>
                      <span style={{ fontSize: "13px", color: "#38bdf8", fontWeight: "bold" }}>{dim.score} 分</span>
                    </div>
                    {dim.feedback && <p style={{ fontSize: "12px", color: "#94a3b8", margin: 0 }}>{dim.feedback}</p>}
                  </div>
                ))}
              </div>
            )}

            {/* 整體評語與建議 */}
            {evalResult?.overall_comment && (
              <div style={{ marginBottom: "14px" }}>
                <h3 style={{ fontSize: "13px", color: "#cbd5e1", margin: "0 0 6px 0" }}>整體回饋意見：</h3>
                <p style={{ fontSize: "12px", color: "#94a3b8", lineHeight: "1.6", margin: 0 }}>
                  {evalResult.overall_comment}
                </p>
              </div>
            )}

            {evalResult?.suggestions && evalResult.suggestions.length > 0 && (
              <div>
                <h3 style={{ fontSize: "13px", color: "#cbd5e1", margin: "0 0 6px 0" }}>修訂建議：</h3>
                <ul style={{ margin: 0, paddingLeft: "18px", fontSize: "12px", color: "#cbd5e1", lineHeight: "1.6" }}>
                  {evalResult.suggestions.map((sug, idx) => (
                    <li key={idx}>{sug}</li>
                  ))}
                </ul>
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
