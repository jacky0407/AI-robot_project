"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { createClient } from "@/utils/supabase/client";
import { streamSSE } from "@/utils/api";

type Step = {
  id: string;
  module_id: string;
  step_order: number;
  step_title: string;
  pass_score: number | null;
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
  overall_coherent: boolean | null; // null：作答的步驟還不夠，沒有任何規則可檢核
  summary: string;
  strengths: string[];
  issues: CoherenceIssue[];
};

const STATUS_LABEL: Record<AttemptStatus, { text: string; color: string; bg: string }> = {
  draft: { text: "草稿", color: "#475569", bg: "#f1f5f9" },
  submitted: { text: "評分中", color: "#92400e", bg: "#fef3c7" },
  passed: { text: "已通過", color: "#166534", bg: "#dcfce7" },
  revision_required: { text: "需修改", color: "#9a3412", bg: "#ffedd5" },
};

export default function StudentModulesPage() {
  const router = useRouter();
  const [supabase] = useState(() => createClient());

  const [userId, setUserId] = useState("");
  const [modules, setModules] = useState<Module[]>([]);
  const [statusByStep, setStatusByStep] = useState<Record<string, AttemptStatus>>({});
  const [loading, setLoading] = useState(true);
  const [errorMsg, setErrorMsg] = useState("");

  const [checkingModule, setCheckingModule] = useState<string | null>(null);
  const [coherence, setCoherence] = useState<Record<string, CoherenceResult | string>>({});

  useEffect(() => {
    const load = async () => {
      const {
        data: { user },
      } = await supabase.auth.getUser();
      if (!user) {
        router.replace("/");
        return;
      }
      setUserId(user.id);

      const { data: moduleRows, error: moduleError } = await supabase
        .from("learning_modules")
        .select("id, title, description, module_steps(id, module_id, step_order, step_title, pass_score)")
        .eq("is_published", true)
        .order("created_at");

      if (moduleError) {
        setErrorMsg("讀取課程模組失敗：" + moduleError.message);
        setLoading(false);
        return;
      }

      setModules(
        (moduleRows ?? []).map((m) => ({
          id: m.id,
          title: m.title,
          description: m.description,
          steps: [...((m.module_steps as Step[]) ?? [])].sort((a, b) => a.step_order - b.step_order),
        }))
      );

      // 每個步驟取最新一次作答的狀態（attempt_number 由大到小，第一筆就是最新）
      const { data: attempts } = await supabase
        .from("step_attempts")
        .select("step_id, status, attempt_number")
        .eq("user_id", user.id)
        .order("attempt_number", { ascending: false });

      const latest: Record<string, AttemptStatus> = {};
      for (const a of attempts ?? []) {
        if (!(a.step_id in latest)) latest[a.step_id] = a.status as AttemptStatus;
      }
      setStatusByStep(latest);
      setLoading(false);
    };

    load();
  }, [supabase, router]);

  const handleSignOut = async () => {
    await supabase.auth.signOut();
    router.push("/");
    router.refresh();
  };

  const runCoherenceCheck = async (moduleId: string) => {
    setCheckingModule(moduleId);
    setCoherence((prev) => ({ ...prev, [moduleId]: "檢核中…" }));
    try {
      await streamSSE(
        `/api/practice/modules/${moduleId}/coherence-check?user_id=${encodeURIComponent(userId)}`,
        { method: "POST" },
        (event, data) => {
          if (event === "coherence_complete") {
            setCoherence((prev) => ({ ...prev, [moduleId]: data as unknown as CoherenceResult }));
          } else if (event === "error") {
            setCoherence((prev) => ({ ...prev, [moduleId]: `檢核失敗：${String(data.message ?? "")}` }));
          }
        }
      );
    } catch (err) {
      setCoherence((prev) => ({ ...prev, [moduleId]: err instanceof Error ? err.message : "檢核失敗" }));
    } finally {
      setCheckingModule(null);
    }
  };

  return (
    <div style={{ minHeight: "100vh", backgroundColor: "#f8fafc", color: "#1e293b", fontFamily: "sans-serif" }}>
      {/* 頂部導航 */}
      <header style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "12px 24px", backgroundColor: "#fff", borderBottom: "1px solid #e2e8f0" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
          <span style={{ backgroundColor: "#2563eb", color: "#fff", padding: "4px 8px", borderRadius: "6px", fontSize: "12px", fontWeight: "bold" }}>
            學生實踐端
          </span>
          <span style={{ fontWeight: "bold", fontSize: "15px" }}>我的培訓模組</span>
        </div>
        <button
          onClick={handleSignOut}
          style={{ padding: "6px 12px", fontSize: "12px", border: "1px solid #cbd5e1", borderRadius: "6px", cursor: "pointer", backgroundColor: "#fff", color: "#1e293b" }}
        >
          登出
        </button>
      </header>

      <main style={{ maxWidth: "960px", margin: "0 auto", padding: "24px 16px", display: "flex", flexDirection: "column", gap: "20px" }}>
        {loading && <p style={{ color: "#64748b", fontSize: "14px" }}>載入模組中…</p>}

        {errorMsg && (
          <div style={{ padding: "10px 14px", borderRadius: "8px", backgroundColor: "#fef2f2", border: "1px solid #fecaca", color: "#b91c1c", fontSize: "13px" }}>
            {errorMsg}
          </div>
        )}

        {!loading && !errorMsg && modules.length === 0 && (
          <p style={{ color: "#64748b", fontSize: "14px" }}>目前沒有已發布的培訓模組。</p>
        )}

        {modules.map((mod) => {
          const result = coherence[mod.id];
          return (
            <section key={mod.id} style={{ backgroundColor: "#fff", borderRadius: "12px", padding: "20px", border: "1px solid #e2e8f0" }}>
              <h2 style={{ fontSize: "17px", fontWeight: "bold", margin: "0 0 4px 0" }}>{mod.title}</h2>
              {mod.description && <p style={{ fontSize: "13px", color: "#64748b", margin: "0 0 16px 0" }}>{mod.description}</p>}

              <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
                {mod.steps.map((step) => {
                  const status = statusByStep[step.id];
                  const badge = status ? STATUS_LABEL[status] : null;
                  return (
                    <Link
                      key={step.id}
                      href={`/student/practice?step_id=${step.id}&module_id=${mod.id}`}
                      style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "12px 14px", borderRadius: "8px", border: "1px solid #e2e8f0", backgroundColor: "#f8fafc", textDecoration: "none", color: "#1e293b" }}
                    >
                      <span style={{ fontSize: "14px" }}>
                        <strong style={{ color: "#2563eb", marginRight: "8px" }}>{step.step_order}.</strong>
                        {step.step_title}
                      </span>
                      <span style={{ display: "flex", alignItems: "center", gap: "10px", fontSize: "12px", color: "#64748b" }}>
                        {step.pass_score != null && <span>門檻 {step.pass_score} 分</span>}
                        <span style={{ padding: "2px 8px", borderRadius: "9999px", color: badge?.color ?? "#64748b", backgroundColor: badge?.bg ?? "#f1f5f9" }}>
                          {badge?.text ?? "未作答"}
                        </span>
                      </span>
                    </Link>
                  );
                })}
                {mod.steps.length === 0 && <p style={{ fontSize: "13px", color: "#94a3b8" }}>此模組尚未設定步驟。</p>}
              </div>

              {/* 跨步驟一致性檢核（CoherenceAgent） */}
              <div style={{ marginTop: "16px", borderTop: "1px solid #e2e8f0", paddingTop: "14px" }}>
                <button
                  onClick={() => runCoherenceCheck(mod.id)}
                  disabled={checkingModule !== null || !userId}
                  style={{ padding: "8px 14px", borderRadius: "8px", border: "none", backgroundColor: "#0f172a", color: "#fff", fontSize: "13px", fontWeight: "bold", cursor: checkingModule ? "not-allowed" : "pointer", opacity: checkingModule ? 0.6 : 1 }}
                >
                  {checkingModule === mod.id ? "檢核中…" : "跨步驟一致性檢核"}
                </button>

                {typeof result === "string" && <p style={{ fontSize: "13px", color: "#475569", marginTop: "10px" }}>{result}</p>}

                {result && typeof result === "object" && (
                  <div style={{ marginTop: "12px", padding: "12px", borderRadius: "8px", fontSize: "13px", lineHeight: 1.6, backgroundColor: result.overall_coherent === null ? "#f8fafc" : result.overall_coherent ? "#f0fdf4" : "#fff7ed", border: "1px solid #e2e8f0" }}>
                    <p style={{ margin: 0, fontWeight: "bold" }}>{result.summary}</p>
                    {result.issues.map((iss) => (
                      <div key={iss.check_name} style={{ marginTop: "8px" }}>
                        <strong>{iss.check_name}</strong>（建議修改步驟 {iss.go_to_step_order}）
                        <div>{iss.problem}</div>
                        <div style={{ color: "#2563eb" }}>{iss.suggestion}</div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </section>
          );
        })}
      </main>
    </div>
  );
}
