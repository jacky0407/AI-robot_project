"use client";

import { Suspense, useEffect, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { createClient } from "@/utils/supabase/client";
import { ApiError, apiFetch, streamSSE } from "@/utils/api";
import VersionCompare, { type HistoryAttempt } from "./VersionCompare";
import BotMemoryPanel from "./BotMemoryPanel";
import ChatRoom, { loadChatRoom, type ChatRoomInfo } from "./ChatRoom";
import Composer, { ToolChip } from "./Composer";
import { BotTurn, EMPTY_RUN, HintCard, RunResultCard, StudentTurn, applyRunEvent, type Hint, type RunState } from "./RunResult";

// ─── 步驟與模組型別定義 ───

// 介面風格預覽（#64）：value 對應 themes.css 的 data-theme；空字串 = 目前的深色
const THEME_OPTIONS = [
  { value: "", label: "目前深色" },
  { value: "b", label: "B 嫩芽溫暖" },
  { value: "b-dark", label: "B 嫩芽溫暖・深色" },
  { value: "f", label: "F 溫柔藍紫" },
  { value: "f-dark", label: "F 溫柔藍紫・深色" },
];

// 與 backend/api/practice.py 的 MAX_ANSWER_CHARS 一致：超過會被後端拒絕（422）
const MAX_ANSWER_CHARS = 8000;
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

// AI 評分串流的型別與處理在 RunResult.tsx（一般作答與聊天室共用）
type HintStatus = { hints_used: number; total_levels: number | null; next_level_available: boolean };
// GET /api/practice/modules/{id}/progress：只列啟用中的步驟；locked = 前面的必修步驟還沒通過
type StepProgress = { step_id: string; passed: boolean; locked: boolean; locked_by: string | null };
type CaseData = { id: string; title: string; case_background: string };

// 歷次作答（GET /history），型別與版本比對共用
type Submission = HistoryAttempt;

// 一般作答關卡的對話紀錄（這次進到關卡後的：取用的提示、送出的作答、之前幾次的評分結果）
// 正在評分的那一次放在 run，送出下一次時才收進這裡
type TimelineItem = { kind: "hint"; hint: Hint } | { kind: "answer"; text: string } | { kind: "result"; run: RunState };

const panel: React.CSSProperties = {
  backgroundColor: "var(--t-surface)",
  borderRadius: "14px",
  padding: "20px",
  border: "1px solid var(--t-border)",
};

function attemptScore(s: Submission): number | null {
  const ev = Array.isArray(s.ai_evaluations) ? s.ai_evaluations[0] : s.ai_evaluations;
  return ev?.total_score ?? null;
}

type NavItem = { moduleId: string; step: Step };

// GET /api/practice/steps/{step_id}/quota 的回應（backend/core/access.py 的 quota_status）
type Quota = {
  mode: "staff" | "course" | "permission" | "self_key";
  used: number | null;
  quota: number | null;
  remaining: number | null;
  expire_date: string | null;
};
// null = 尚未載入；{ blocked } = 沒被指派或期限已過（後端回 403），不能送出
type QuotaState = { quota: Quota } | { blocked: string } | null;

const STATUS_LABEL: Record<AttemptStatus, { text: string; color: string; bg: string }> = {
  draft: { text: "草稿", color: "var(--t-text-muted)", bg: "var(--t-surface-3)" },
  submitted: { text: "評分中", color: "var(--t-warning)", bg: "var(--t-warning-soft)" },
  passed: { text: "已通過", color: "var(--t-success)", bg: "var(--t-success-soft)" },
  revision_required: { text: "需修改", color: "var(--t-danger)", bg: "var(--t-danger-soft)" },
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
  // 介面風格預覽（#64）：?theme=b 嫩芽溫暖、f 溫柔藍紫、b-dark／f-dark 各自的深色版；沒帶時用目前的深色
  const themeParam = searchParams.get("theme");
  const theme = themeParam && THEME_OPTIONS.some((o) => o.value && o.value === themeParam) ? themeParam : undefined;
  // 右上角切換風格：用 replaceState 改網址（不重新載入），useSearchParams 會跟著更新
  const changeTheme = (value: string) => {
    const q = new URLSearchParams(window.location.search);
    if (value) q.set("theme", value);
    else q.delete("theme");
    window.history.replaceState(null, "", `?${q.toString()}`);
  };
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
  // 從課程簡介頁點選關卡時帶 ?step=<step_id>；只在進頁時讀一次（之後切換關卡用 replaceState 同步網址，不重新載入）
  const [initialStepId] = useState(() => searchParams.get("step"));

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
  const [timeline, setTimeline] = useState<TimelineItem[]>([]);
  // 上一次送出的作答：送出後輸入框會清空，可以按「帶入上一次作答」接著改
  const [lastAnswer, setLastAnswer] = useState("");
  // 對話最底部：送出作答、評分逐項出來、取用提示時捲到這裡（像 ChatGPT）
  const threadEndRef = useRef<HTMLDivElement>(null);
  // 右側抽屜（AI 助教記得你、作答紀錄、跨步驟檢核）：平常收起，按右上角的圖示才滑出
  const [sideOpen, setSideOpen] = useState(false);
  const [hintMessage, setHintMessage] = useState("");
  const [history, setHistory] = useState<Submission[]>([]);
  const [showCompare, setShowCompare] = useState(false);
  const [progressKey, setProgressKey] = useState(0);
  // 聊天室關卡（#68）：選到的關卡是聊天室時，中間的作答區換成聊天室；null = 一般作答關卡或還沒讀到
  const [chatRoom, setChatRoom] = useState<{ stepId: string; room: ChatRoomInfo } | null>(null);
  const [isLoadingHint, setIsLoadingHint] = useState(false);

  // 切換步驟或模組時清空這一步的作答狀態
  const selectStep = (step: Step | null) => {
    setSelectedStep(step);
    // 網址跟著目前的關卡，重新整理或分享連結時會回到同一關
    if (courseId && step) {
      const q = new URLSearchParams({ course: courseId, step: step.id });
      if (theme) q.set("theme", theme);
      window.history.replaceState(null, "", `?${q.toString()}`);
    }
    setStudentAnswer("");
    setRun(null);
    setPiiWarning("");
    setTimeline([]);
    setLastAnswer("");
    setHintMessage("");
    setHintStatus(null);
    setHintStatusError(false);
    setHistory([]);
    setShowCompare(false);
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

      if (courseId && !initialStepId) {
        // 沒指定關卡：先到課程簡介頁選關卡
        router.replace(`/student/courses/${encodeURIComponent(courseId)}`);
        return;
      }

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
            const target = ordered.find((m) => m.steps.some((st) => st.id === initialStepId));
            if (ordered.length === 0) {
              setErrorMsg("讀不到這門課程的模組內容，請確認模組已發布，或聯絡老師。");
            } else if (!target) {
              setErrorMsg("找不到這個關卡，可能已被老師移除。請回到課程簡介重新選擇。");
            } else {
              setModules(ordered);
              setCurrentModuleId(target.id);
              setSelectedStep(target.steps.find((st) => st.id === initialStepId) ?? null);
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
        setErrorMsg("目前沒有可以練習的訓練模組。請在「自主探索」申請模組並等老師核准，或從「正式課程」加入課程。");
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
  }, [supabase, router, courseId, initialStepId]);

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

  // 抽屜開著時按 Esc 收起
  useEffect(() => {
    if (!sideOpen) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setSideOpen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [sideOpen]);

  // 對話有新東西就捲到最下面；沒有新東西（剛進關卡）時不捲，讓學生先看關卡說明
  const runProgress = run ? run.dimensions.length + (run.score ? 1 : 0) + run.errors.length + (run.feedback ? 1 : 0) + (run.failure ? 1 : 0) : -1;
  useEffect(() => {
    if (timeline.length === 0 && runProgress < 0) return;
    threadEndRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [timeline.length, runProgress]);

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

  // 這一關是不是聊天室（後端回 chat: false 就維持原本的作答區；讀不到也當作一般關卡）
  useEffect(() => {
    if (!selectedStep) return;
    let active = true;
    const stepId = selectedStep.id;
    loadChatRoom(stepId).then(
      (room) => active && setChatRoom(room ? { stepId, room } : null),
      () => active && setChatRoom(null)
    );
    return () => {
      active = false;
    };
  }, [selectedStep]);
  const chat = chatRoom && selectedStep && chatRoom.stepId === selectedStep.id ? chatRoom.room : null;

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
  // 注意：模組清單還沒載入時 progressModuleId 是 undefined，不能只寫 unlockState?.moduleId === progressModuleId
  // （undefined === undefined 會成立，接著讀 null.byStep 整頁崩潰，2026-11-04 部署測試）
  const unlock = unlockState && unlockState.moduleId === progressModuleId ? unlockState.byStep : null;

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
      if (res.data.hint) setTimeline((prev) => [...prev, { kind: "hint", hint: res.data.hint as Hint }]);
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
    setRun((prev) => applyRunEvent(prev ?? EMPTY_RUN, event, data));
    // 是否通過以 pass_status 為準：關卡可能是「作答即通過」或「教師複核後通過」（#65）
    if (event === "pass_status" && selectedStep) {
      const status = String(data.status ?? "submitted");
      setStatusByStep((prev) => ({ ...prev, [selectedStep.id]: status as AttemptStatus }));
    }
  };

  const handleSubmit = async () => {
    if (!selectedStep) return;
    const answer = studentAnswer;
    if (!answer.trim()) {
      setPiiWarning("請先輸入作答內容再送出。");
      return;
    }

    setIsSubmitting(true);
    setPiiWarning("");
    // 對話裡先出現學生的作答，上一次的評分結果收進紀錄，這次的評分接在後面長出來
    setTimeline((prev) => [...prev, ...(run ? [{ kind: "result" as const, run }] : []), { kind: "answer", text: answer }]);
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
            content: answer,
            version_note: "學生工作台提交",
          }),
        },
        handleEvent
      );
      setLastAnswer(answer);
      setStudentAnswer("");
    } catch (err) {
      // 顯示真正原因（個資偵測、未登入、沒被指派 403、次數用完 429…），不用假資料蓋掉
      if (err instanceof ApiError && err.code === "PII_DETECTED") {
        const types = (err.detail as { detected_types?: string[] })?.detected_types ?? [];
        setPiiWarning(`${err.message}${types.length ? `（偵測到：${types.join("、")}）` : ""}`);
        // 被個資偵測擋下：沒有送出，把剛剛的作答氣泡拿掉，內容留在輸入框
        setTimeline((prev) => prev.slice(0, -1));
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
  // 從課程進來回課程簡介頁；沒帶 course（自主探索的「開始練習」）回自主探索頁
  const backHref = courseId ? `/student/courses/${encodeURIComponent(courseId)}` : "/student/explore";
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

  // 「上一題／下一題」：依模組順序把關卡攤平（目前模組裡已停用的關卡不列入）
  const flatSteps: NavItem[] = modules.flatMap((m) =>
    m.steps
      .filter((st) => m.id !== progressModuleId || !unlock || st.id in unlock)
      .map((st) => ({ moduleId: m.id, step: st }))
  );
  const stepIdx = selectedStep ? flatSteps.findIndex((it) => it.step.id === selectedStep.id) : -1;
  const stepPos = stepIdx + 1;
  const prevItem = stepIdx > 0 ? flatSteps[stepIdx - 1] : null;
  const nextItem = stepIdx >= 0 && stepIdx < flatSteps.length - 1 ? flatSteps[stepIdx + 1] : null;
  // 只知道目前模組的解鎖狀態；跨到下一個模組時不擋（後端送出時仍會檢查）
  const nextLock = nextItem && nextItem.moduleId === progressModuleId ? unlock?.[nextItem.step.id] : undefined;
  const nextLockedReason = nextLock?.locked ? `先通過「${nextLock.locked_by}」才會解鎖下一題` : undefined;

  const goToStep = (moduleId: string, step: Step) => {
    setCurrentModuleId(moduleId);
    selectStep(step);
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  // 對話裡 AI 的名稱：用這一關的機器人名稱
  const botName = selectedStep?.ai_tools?.title || "AI 助教";
  const stepPassed = (run?.pass ? run.pass.passed : Boolean(run?.score?.passed)) || (selectedStep ? statusByStep[selectedStep.id] === "passed" : false);
  // 還沒送出過作答時提示無處記錄，先送出一次再索取
  const hintDisabled = isLoadingHint || !hintStatus?.next_level_available || history.length === 0;
  const hintTitle = !hintStatus
    ? hintStatusError
      ? "讀不到提示狀態，請稍後重新整理頁面"
      : "讀取提示狀態中…"
    : history.length === 0
      ? "先送出一次作答後才能索取提示"
      : !hintStatus.next_level_available
        ? "目前沒有可用的下一層提示"
        : `已使用 ${hintStatus.hints_used} 則提示`;

  return (
    <div data-theme={theme} style={{ minHeight: "100vh", backgroundColor: "var(--t-bg)", color: "var(--t-text)", fontFamily: "sans-serif" }}>
      {/* 頂部導航 */}
      <header
        className="ws-header"
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          gap: "10px 16px",
          flexWrap: "wrap",
          padding: "14px 28px",
          borderBottom: "1px solid var(--t-border)",
          backgroundColor: "var(--t-surface)",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: "12px", minWidth: 0, flex: "1 1 320px" }}>
          <button
            onClick={() => router.push(backHref)}
            style={{
              flexShrink: 0,
              whiteSpace: "nowrap",
              padding: "6px 12px",
              borderRadius: "6px",
              backgroundColor: "var(--t-surface-3)",
              border: "none",
              color: "var(--t-link)",
              cursor: "pointer",
              fontSize: "13px",
            }}
          >
            {courseId ? "← 課程簡介與關卡" : "← 返回自主探索"}
          </button>
          <div>
            <h1 style={{ fontSize: "16px", fontWeight: "bold", margin: 0 }}>
              學生實踐工作台・{currentMod?.title || "訓練模組"}
            </h1>
            <p style={{ fontSize: "12px", color: "var(--t-text-muted)", margin: 0 }}>
              {courseName ? `課程：${courseName}・` : ""}依序完成每一關，送出後會得到逐項評分與回饋
            </p>
          </div>
        </div>
        <div className="ws-header-actions" style={{ display: "flex", alignItems: "center", gap: "10px", flexShrink: 0 }}>
          {/* 介面風格切換（#64 預覽）：選了就換，網址同步 ?theme=，重新整理後保留 */}
          <label style={{ display: "flex", alignItems: "center", gap: "6px", fontSize: "12px", color: "var(--t-text-muted)" }}>
            <span className="ws-theme-label">風格</span>
            <select
              aria-label="介面風格"
              value={theme ?? ""}
              onChange={(e) => changeTheme(e.target.value)}
              style={{
                padding: "6px 8px",
                borderRadius: "6px",
                border: "1px solid var(--t-border-strong)",
                backgroundColor: "var(--t-field)",
                color: "var(--t-text)",
                fontSize: "12px",
                cursor: "pointer",
              }}
            >
              {THEME_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </label>
          <button
            onClick={handleSignOut}
            style={{
              padding: "6px 14px",
              borderRadius: "6px",
              border: "1px solid var(--t-border-strong)",
              backgroundColor: "transparent",
              color: "var(--t-text-2)",
              fontSize: "12px",
              cursor: "pointer",
            }}
          >
            登出
          </button>
        </div>
      </header>

      {(loading || modules.length === 0) && (
        <div style={{ maxWidth: "640px", margin: "48px auto", padding: "24px", borderRadius: "14px", backgroundColor: "var(--t-surface)", border: "1px solid var(--t-border)", textAlign: "center" }}>
          <p style={{ margin: 0, fontSize: "14px", color: loading ? "var(--t-text-muted)" : "var(--t-warning)", lineHeight: 1.6 }}>
            {loading ? "載入訓練模組中…" : errorMsg || "目前沒有可以練習的訓練模組。"}
          </p>
          {!loading && (
            <button
              onClick={() => router.push(backHref)}
              style={{ marginTop: "16px", padding: "8px 16px", borderRadius: "8px", border: "none", backgroundColor: "var(--t-primary)", color: "var(--t-on-primary)", fontSize: "13px", fontWeight: "bold", cursor: "pointer" }}
            >
              {courseId ? "回到課程簡介" : "回到自主探索"}
            </button>
          )}
        </div>
      )}

      {/* 主體：GPT 式對話（關卡說明 → 提示、作答、評分依時間排下來，輸入框固定在視窗底部中間）；
          記憶與作答紀錄收在右側抽屜，按右上角的圖示才滑出 */}
      {!loading && modules.length > 0 && (
      <main className="ws-grid" style={{ padding: "0 24px" }}>
        <section className="ws-chat" style={{ minWidth: 0, width: "100%", maxWidth: "820px", margin: "0 auto", display: "flex", flexDirection: "column", gap: "22px", paddingTop: "20px" }}>
          {/* 題目切換 */}
          <StepNav
            position={stepPos}
            total={flatSteps.length}
            prev={prevItem}
            next={nextItem}
            nextLockedReason={nextLockedReason}
            justPassed={stepPassed}
            onGo={(item) => goToStep(item.moduleId, item.step)}
            onOverview={() => router.push(backHref)}
            overviewLabel={courseId ? "回到課程簡介" : "回到自主探索"}
          />

          {/* 關卡說明：對話的第一則，告訴學生這一關要做什麼 */}
          <BotTurn name={botName} note="關卡說明">
            <div style={{ padding: "14px 16px", borderRadius: "14px", backgroundColor: "var(--t-surface)", border: "1px solid var(--t-border)" }}>
              <div style={{ fontSize: "16px", fontWeight: "bold", lineHeight: 1.5 }}>
                第 {selectedStep?.step_order} 關：{selectedStep?.step_title}
              </div>
              {selectedStep?.ai_tools?.target_competency && (
                <div style={{ fontSize: "13px", color: "var(--t-text-muted)", marginTop: "4px" }}>練習能力：{selectedStep.ai_tools.target_competency}</div>
              )}
              {caseData ? (
                <div style={{ marginTop: "12px", padding: "12px 14px", borderRadius: "10px", backgroundColor: "var(--t-surface-2)", border: "1px solid var(--t-border)" }}>
                  <div style={{ fontSize: "12px", fontWeight: "bold", color: "var(--t-link)" }}>教學案例：{caseData.title}</div>
                  <p style={{ fontSize: "14px", lineHeight: 1.8, color: "var(--t-text)", margin: "6px 0 0", whiteSpace: "pre-wrap" }}>{caseData.case_background}</p>
                </div>
              ) : (
                <p style={{ fontSize: "13px", color: "var(--t-warning)", margin: "10px 0 0" }}>讀不到教學案例，請聯絡老師確認案例已建立。</p>
              )}
              <p style={{ fontSize: "14px", lineHeight: 1.7, color: "var(--t-text-2)", margin: "12px 0 0" }}>
                {chat
                  ? "和下面的機器人討論這個案例，想清楚後按「送出評分」，評分機器人會看整段對話給你回饋。"
                  : "請依案例寫下你的作答，送出後會逐項評分並給你回饋；可以依回饋修改後再送出。卡住時可以按「我需要提示」。"}
              </p>
            </div>
          </BotTurn>

          {chat ? (
            <ChatRoom
              key={chat.step_id}
              room={chat}
              sessionId={sessionId}
              disabledReason={lockReason ?? quotaBlockReason}
              footnote={lockReason ? <span>{lockReason}</span> : <QuotaBadge state={quotaState} />}
              onEvaluationStart={() => setRun({ ...EMPTY_RUN })}
              onEvaluationEvent={handleEvent}
              onFinished={() => {
                setQuotaKey((k) => k + 1);
                setProgressKey((k) => k + 1);
              }}
            />
          ) : (
            <>
              {timeline.map((it, i) =>
                it.kind === "answer" ? (
                  <StudentTurn key={i} label="你的作答">{it.text}</StudentTurn>
                ) : it.kind === "hint" ? (
                  <BotTurn key={i} name={botName} note="提示" tone="indigo">
                    <HintCard hint={it.hint} />
                  </BotTurn>
                ) : (
                  <BotTurn key={i} name={botName} note="評分與回饋">
                    <RunResultCard run={it.run} />
                  </BotTurn>
                )
              )}

              {run && (
                <BotTurn name={botName} note="評分與回饋">
                  <RunResultCard run={run} streaming={isSubmitting} />
                  {!isSubmitting && stepPassed && nextItem && !nextLockedReason && (
                    <button
                      onClick={() => goToStep(nextItem.moduleId, nextItem.step)}
                      style={{ marginTop: "12px", padding: "8px 18px", borderRadius: "9999px", border: "none", backgroundColor: "var(--t-success-strong)", color: "var(--t-on-primary)", fontSize: "13px", fontWeight: "bold", cursor: "pointer" }}
                    >
                      前往下一題：{nextItem.step.step_title} →
                    </button>
                  )}
                </BotTurn>
              )}

              {hintMessage && <p style={{ margin: "0 0 0 40px", fontSize: "13px", color: "var(--t-text-muted)" }}>{hintMessage}</p>}
              {/* 捲動時留出底部輸入框的高度，最後一則才不會被蓋住 */}
              <div ref={threadEndRef} style={{ scrollMarginBottom: "var(--composer-h, 220px)" }} />

              <Composer
                value={studentAnswer}
                onChange={(v) => {
                  setStudentAnswer(v);
                  if (piiWarning) setPiiWarning("");
                }}
                onSend={handleSubmit}
                sendLabel={isSubmitting ? "評分中…" : "送出作答"}
                sendDisabled={submitDisabled || !studentAnswer.trim()}
                enterToSend={false}
                minHeight={96}
                maxLength={MAX_ANSWER_CHARS}
                placeholder={lockReason ?? quotaBlockReason ?? "依案例寫下你的作答（Enter 換行，Ctrl+Enter 送出）"}
                warning={piiWarning}
                tools={
                  <>
                    <ToolChip
                      tone="indigo"
                      onClick={handleRevealHint}
                      disabled={hintDisabled}
                      title={hintTitle}
                    >
                      {isLoadingHint ? "取得提示中…" : `我需要提示${hintStatus?.total_levels != null ? `（${hintStatus.hints_used} / ${hintStatus.total_levels}）` : ""}`}
                    </ToolChip>
                    {lastAnswer && lastAnswer !== studentAnswer && (
                      <ToolChip onClick={() => setStudentAnswer(lastAnswer)} title="把上一次送出的內容放回輸入框，接著修改">
                        帶入上一次作答
                      </ToolChip>
                    )}
                  </>
                }
                footnote={
                  <>
                    {lockReason ? <span>{lockReason}</span> : <QuotaBadge state={quotaState} />}
                    <span>・{studentAnswer.length} / {MAX_ANSWER_CHARS} 字・請勿輸入真實姓名、電話等個資</span>
                  </>
                }
              />
            </>
          )}
        </section>

        {/* 開抽屜的圖示：固定在右上角 */}
        <button
          type="button"
          onClick={() => setSideOpen(true)}
          aria-label="打開作答紀錄與 AI 記憶"
          aria-expanded={sideOpen}
          title="作答紀錄與 AI 記憶"
          className="ws-side-toggle"
          style={{
            position: "fixed",
            top: "88px",
            right: "20px",
            zIndex: 30,
            width: "44px",
            height: "44px",
            borderRadius: "12px",
            border: "1px solid var(--t-border-strong)",
            backgroundColor: "var(--t-surface)",
            color: "var(--t-text-2)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            cursor: "pointer",
          }}
        >
          {/* 側欄圖示（線條） */}
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <rect x="3" y="4" width="18" height="16" rx="2" />
            <path d="M15 4v16" />
            <path d="M18 9h0.01M18 12h0.01" />
          </svg>
          {history.length > 0 && (
            <span style={{ position: "absolute", top: "-6px", right: "-6px", minWidth: "18px", height: "18px", padding: "0 5px", boxSizing: "border-box", borderRadius: "9999px", fontSize: "11px", fontWeight: "bold", lineHeight: "18px", textAlign: "center", color: "var(--t-on-primary)", backgroundColor: "var(--t-primary)" }}>
              {history.length}
            </span>
          )}
        </button>

        {/* 抽屜外的半透明底：點一下收起 */}
        <div
          aria-hidden="true"
          onClick={() => setSideOpen(false)}
          style={{ position: "fixed", inset: 0, zIndex: 39, backgroundColor: "var(--t-overlay)", opacity: sideOpen ? 1 : 0, pointerEvents: sideOpen ? "auto" : "none", transition: "opacity 0.2s" }}
        />

        {/* 右側抽屜：AI 助教記得你、作答紀錄、跨步驟檢核（一直掛著，收起時移到畫面外，記憶與紀錄照常更新） */}
        <aside
          className="ws-side"
          aria-label="作答紀錄與 AI 記憶"
          aria-hidden={!sideOpen}
          inert={!sideOpen}
          style={{
            position: "fixed",
            top: 0,
            right: 0,
            bottom: 0,
            zIndex: 40,
            width: "min(380px, 92vw)",
            boxSizing: "border-box",
            overflowY: "auto",
            padding: "16px",
            display: "flex",
            flexDirection: "column",
            gap: "16px",
            backgroundColor: "var(--t-bg)",
            borderLeft: "1px solid var(--t-border)",
            transform: sideOpen ? "translateX(0)" : "translateX(100%)",
            transition: "transform 0.25s ease",
          }}
        >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <h2 style={{ fontSize: "15px", fontWeight: "bold", margin: 0 }}>作答紀錄與 AI 記憶</h2>
              <button
                type="button"
                onClick={() => setSideOpen(false)}
                aria-label="收起"
                style={{ padding: "4px 10px", borderRadius: "8px", border: "1px solid var(--t-border-strong)", background: "none", color: "var(--t-text-2)", fontSize: "13px", cursor: "pointer" }}
              >
                收起 →
              </button>
            </div>
            {/* 機器人記憶：送出後（progressKey 變動）重新讀 */}
            {selectedStep && <BotMemoryPanel stepId={selectedStep.id} refreshKey={progressKey} panelStyle={panel} />}

            {/* 作答版本紀錄 */}
            {history.length > 0 && (
              <div style={panel}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: "8px", marginBottom: "10px" }}>
                  <h3 style={{ fontSize: "14px", fontWeight: "bold", margin: 0 }}>作答紀錄</h3>
                  {history.length >= 2 && (
                    <button
                      onClick={() => setShowCompare(true)}
                      style={{ flexShrink: 0, padding: "5px 12px", borderRadius: "8px", border: "1px solid var(--t-link)", background: "none", color: "var(--t-link)", fontSize: "12px", fontWeight: "bold", cursor: "pointer" }}
                    >
                      ⇄ 比較版本
                    </button>
                  )}
                </div>
                <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
                  {[...history].reverse().map((sub) => {
                    const status = STATUS_LABEL[sub.status as AttemptStatus];
                    const score = attemptScore(sub);
                    return (
                      <details key={sub.id} style={{ padding: "8px 10px", borderRadius: "8px", backgroundColor: "var(--t-surface-2)", fontSize: "13px" }}>
                        <summary style={{ cursor: "pointer", display: "flex", alignItems: "center", gap: "8px", flexWrap: "wrap" }}>
                          <b>第 {sub.attempt_number} 次</b>
                          {status && (
                            <span style={{ fontSize: "11px", padding: "1px 8px", borderRadius: "9999px", color: status.color, backgroundColor: status.bg }}>{status.text}</span>
                          )}
                          <span style={{ color: "var(--t-text-2)" }}>{score == null ? "未評分" : `${Math.round(score)} 分`}</span>
                          <span style={{ fontSize: "11px", color: "var(--t-text-faint)" }}>{new Date(sub.created_at).toLocaleString()}</span>
                        </summary>
                        {sub.user_input_content && (
                          <div style={{ marginTop: "8px", display: "flex", gap: "8px", alignItems: "flex-start" }}>
                            <p style={{ flex: 1, margin: 0, whiteSpace: "pre-wrap", color: "var(--t-text-2)", lineHeight: 1.6 }}>{sub.user_input_content}</p>
                            <button
                              onClick={() => setStudentAnswer(sub.user_input_content ?? "")}
                              style={{ flexShrink: 0, padding: "4px 10px", borderRadius: "6px", border: "1px solid var(--t-border-strong)", background: "none", color: "var(--t-text-2)", fontSize: "11px", cursor: "pointer" }}
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
                    {/* 跨步驟一致性檢核區塊 */}
            <div
              style={{
                backgroundColor: "var(--t-surface)",
                borderRadius: "14px",
                padding: "18px",
                border: "1px solid var(--t-border)",
              }}
            >
              <h3 style={{ fontSize: "14px", fontWeight: "bold", margin: "0 0 6px 0", color: "var(--t-indigo)" }}>
                跨步驟一致性檢核
              </h3>
              <p style={{ fontSize: "12px", color: "var(--t-text-muted)", margin: "0 0 12px 0", lineHeight: "1.5" }}>
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
                  backgroundColor: "var(--t-indigo-strong)",
                  color: "var(--t-on-primary)",
                  fontSize: "13px",
                  fontWeight: "bold",
                  cursor: checkingModule ? "not-allowed" : "pointer",
                  opacity: checkingModule ? 0.6 : 1,
                }}
              >
                {checkingModule ? "跨步驟檢核中…" : "執行前後邏輯檢核"}
              </button>

              {typeof coherenceData === "string" && (
                <p style={{ fontSize: "12px", color: "var(--t-text-2)", marginTop: "10px" }}>{coherenceData}</p>
              )}

              {coherenceData && typeof coherenceData === "object" && (
                <div
                  style={{
                    marginTop: "12px",
                    padding: "12px",
                    borderRadius: "8px",
                    fontSize: "12px",
                    lineHeight: 1.5,
                    backgroundColor: coherenceData.overall_coherent ? "var(--t-success-soft)" : "var(--t-danger-soft)",
                    border: `1px solid ${coherenceData.overall_coherent ? "var(--t-success-border)" : "var(--t-danger-border)"}`,
                  }}
                >
                  <div style={{ fontWeight: "bold", color: coherenceData.overall_coherent ? "var(--t-success)" : "var(--t-danger)" }}>
                    {coherenceData.summary}
                  </div>
                  {coherenceData.issues.map((iss, i) => (
                    <div key={i} style={{ marginTop: "8px", borderTop: "1px solid var(--t-border)", paddingTop: "6px" }}>
                      <strong>{iss.check_name}</strong>（建議修改關卡 {iss.go_to_step_order}）
                      <div style={{ color: "var(--t-text-2)" }}>{iss.problem}</div>
                      <div style={{ color: "var(--t-link)", marginTop: "2px" }}>建議：{iss.suggestion}</div>
                    </div>
                  ))}
                </div>
              )}
            </div>
        </aside>
      </main>
      )}

      <style>{`
        @media (prefers-reduced-motion: reduce) { .ws-side { transition: none !important; } }
        .ws-side-toggle:hover { border-color: var(--t-primary) !important; color: var(--t-primary) !important; }
        .chat-dots { display: inline-flex; gap: 3px; }
        .chat-dots i { width: 6px; height: 6px; border-radius: 9999px; background: var(--t-text-faint); animation: chatdot 1s infinite ease-in-out; }
        .chat-dots i:nth-child(2) { animation-delay: 0.15s; }
        .chat-dots i:nth-child(3) { animation-delay: 0.3s; }
        @keyframes chatdot { 0%, 80%, 100% { opacity: 0.3; } 40% { opacity: 1; } }
        /* 手機 */
        @media (max-width: 760px) {
          .ws-theme-label { display: none; }
          .ws-header { padding: 12px 16px !important; }
          .ws-header-actions { width: 100%; justify-content: flex-end; }
          .ws-grid { padding: 0 12px !important; }
          .ws-side-toggle { top: auto !important; bottom: calc(var(--composer-h, 200px) + 8px); right: 12px !important; }
        }
      `}</style>

      {showCompare && history.length >= 2 && <VersionCompare attempts={history} onClose={() => setShowCompare(false)} />}
    </div>
  );
}

// 送出按鈕旁的剩餘次數：有次數上限時（自主模式、課程的免費邀請碼）顯示今天剩幾次與使用期限，
// 沒有上限時（教師、沒有邀請碼的課程成員）顯示不限次數
function QuotaBadge({ state }: { state: QuotaState }) {
  const base: React.CSSProperties = { fontSize: "12px", lineHeight: 1.5 };
  if (!state) return <span style={{ ...base, color: "var(--t-text-faint)" }}>送出後，AI 會依評分規準逐項評分</span>;
  if ("blocked" in state) return <span style={{ ...base, color: "var(--t-danger)" }}>{state.blocked}</span>;

  const q = state.quota;
  if (q.mode === "staff") return <span style={{ ...base, color: "var(--t-text-faint)" }}>教師帳號：不限次數</span>;
  if (q.mode === "self_key") return <span style={{ ...base, color: "var(--t-text-faint)" }}>使用你自己的 Gemini 金鑰：不限次數</span>;
  if (q.quota === null) return <span style={{ ...base, color: "var(--t-text-faint)" }}>正式課程：不限練習次數</span>;

  const color = q.remaining === 0 ? "var(--t-danger)" : (q.remaining ?? 0) <= 2 ? "var(--t-warning)" : "var(--t-success)";
  return (
    <span style={{ ...base, color }}>
      今天還可以作答 <b>{q.remaining}</b> / {q.quota} 次
      <span style={{ color: "var(--t-text-faint)" }}>
        {q.expire_date ? `・可使用到 ${q.expire_date}` : "・不限期"}
        {q.mode === "permission" ? "・自主練習，AI 回饋僅供參考" : ""}
      </span>
    </span>
  );
}

// 題目切換列：上一題／第幾題／下一題；最後一題時改成回到課程簡介
function StepNav({
  position,
  total,
  prev,
  next,
  nextLockedReason,
  justPassed,
  onGo,
  onOverview,
  overviewLabel,
}: {
  position: number;
  total: number;
  prev: NavItem | null;
  next: NavItem | null;
  nextLockedReason?: string;
  justPassed: boolean;
  onGo: (item: NavItem) => void;
  onOverview: () => void;
  overviewLabel: string;
}) {
  const btn: React.CSSProperties = {
    padding: "10px 18px",
    borderRadius: "8px",
    fontSize: "13px",
    fontWeight: "bold",
    cursor: "pointer",
    whiteSpace: "nowrap",
  };
  const nextDisabled = Boolean(nextLockedReason);
  return (
    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: "12px", flexWrap: "wrap" }}>
      <button
        onClick={() => prev && onGo(prev)}
        disabled={!prev}
        style={{ ...btn, border: "1px solid var(--t-border-strong)", background: "none", color: "var(--t-text-2)", visibility: prev ? "visible" : "hidden" }}
      >
        ← 上一題
      </button>
      <span style={{ fontSize: "12px", color: "var(--t-text-muted)" }}>
        {position > 0 ? `第 ${position} / ${total} 題` : ""}
      </span>
      {next ? (
        <span style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: "4px" }}>
          <button
            onClick={() => onGo(next)}
            disabled={nextDisabled}
            title={nextLockedReason ?? next.step.step_title}
            style={{
              ...btn,
              border: "none",
              color: "var(--t-on-primary)",
              backgroundColor: nextDisabled ? "var(--t-text-faint)" : justPassed ? "var(--t-success-strong)" : "var(--t-primary)",
              cursor: nextDisabled ? "not-allowed" : "pointer",
            }}
          >
            下一題 →
          </button>
          {nextLockedReason && <span style={{ fontSize: "11px", color: "var(--t-text-muted)" }}>{nextLockedReason}</span>}
        </span>
      ) : (
        <button onClick={onOverview} style={{ ...btn, border: "none", color: "var(--t-on-primary)", backgroundColor: justPassed ? "var(--t-success-strong)" : "var(--t-primary)" }}>
          {overviewLabel}
        </button>
      )}
    </div>
  );
}
