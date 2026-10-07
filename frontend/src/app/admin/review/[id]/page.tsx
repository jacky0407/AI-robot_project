"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { createClient } from "@/utils/supabase/client";
import { apiFetch } from "@/utils/api";

type DimensionScore = { dimension: string; score: number; max_score: number; reason?: string; evidence?: string };
type DetectedError = { code: string; label?: string; explanation?: string; evidence?: string };
type TeacherReview = { id: string; decision: string; final_score: number | null; final_feedback: string | null; reviewed_at: string };
type PromptLog = { id: string; hint_level: number; hint_content: string; hint_trigger?: string | null; hint_source?: string | null };

type Submission = {
  submission_id: string;
  student: { full_name?: string; email?: string };
  step: { step_title: string; pass_score: number | null; require_teacher_review?: boolean; tool: { title?: string } };
  attempt_number: number;
  status: string;
  tutor_action?: string | null;
  content: string;
  ai_evaluation: {
    total_score: number | null;
    dimension_scores: DimensionScore[];
    feedback_text: string;
    evidence_text: string;
    confidence?: number | null;
    needs_teacher_review?: boolean;
    detected_errors?: DetectedError[];
  };
  teacher_reviews: TeacherReview[];
  prompt_logs: PromptLog[];
};

// 對應 teacher_reviews.decision 的 CHECK 條件
const DECISIONS = [
  { value: "accept_ai", label: "採用 AI 評分" },
  { value: "modify", label: "調整分數" },
  { value: "override", label: "推翻 AI 判定" },
  { value: "request_retry", label: "要求學生重做" },
];

const card: React.CSSProperties = { backgroundColor: "#fff", borderRadius: "12px", padding: "20px", border: "1px solid #e2e8f0" };

export default function ReviewDetailPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const [supabase] = useState(() => createClient());

  const [data, setData] = useState<Submission | null>(null);
  const [errorMsg, setErrorMsg] = useState("");

  const [decision, setDecision] = useState("accept_ai");
  const [finalScore, setFinalScore] = useState("");
  const [finalFeedback, setFinalFeedback] = useState("");
  const [isPublished, setIsPublished] = useState(true);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let active = true;
    apiFetch<{ data: Submission }>(`/api/review/submissions/${id}`)
      .then((res) => {
        if (!active) return;
        setData(res.data);
        // 預設帶入 AI 分數，老師可再調整
        if (res.data.ai_evaluation.total_score != null) setFinalScore(String(res.data.ai_evaluation.total_score));
      })
      .catch((err) => {
        if (active) setErrorMsg(err instanceof Error ? err.message : "讀取失敗");
      });
    return () => {
      active = false;
    };
  }, [id]);

  const handleJudge = async (e: React.FormEvent) => {
    e.preventDefault();
    setSaving(true);
    setErrorMsg("");

    const {
      data: { user },
    } = await supabase.auth.getUser();
    if (!user) {
      setErrorMsg("請先登入");
      setSaving(false);
      return;
    }

    try {
      // 教師判定另存 teacher_reviews，後端不會改動 AI 初評
      await apiFetch(`/api/review/submissions/${id}/judge`, {
        method: "POST",
        body: JSON.stringify({
          teacher_id: user.id, // TODO: 後端改由 JWT 取得身分後移除（KNOWN_GAPS #8）
          decision,
          final_score: finalScore === "" ? null : Number(finalScore),
          final_feedback: finalFeedback,
          is_published: isPublished,
        }),
      });
      router.push("/admin/tools");
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : "儲存失敗");
      setSaving(false);
    }
  };

  const ev = data?.ai_evaluation;

  return (
    <div style={{ minHeight: "100vh", backgroundColor: "#f8fafc", color: "#1e293b", fontFamily: "sans-serif", padding: "24px 16px" }}>
      <div style={{ maxWidth: "960px", margin: "0 auto", display: "flex", flexDirection: "column", gap: "16px" }}>
        <Link href="/admin/tools" style={{ fontSize: "13px", color: "#2563eb", textDecoration: "none" }}>
          ← 返回待複核清單
        </Link>

        {errorMsg && (
          <div style={{ padding: "10px 14px", borderRadius: "8px", backgroundColor: "#fef2f2", border: "1px solid #fecaca", color: "#b91c1c", fontSize: "13px" }}>{errorMsg}</div>
        )}

        {!data && !errorMsg && <p style={{ fontSize: "13px", color: "#64748b" }}>載入中…</p>}

        {data && ev && (
          <>
            <div style={card}>
              <h1 style={{ fontSize: "18px", fontWeight: "bold", margin: "0 0 6px 0" }}>
                {data.student.full_name || data.student.email}・{data.step.step_title}
              </h1>
              <p style={{ fontSize: "12px", color: "#64748b", margin: 0 }}>
                {data.step.tool?.title}・第 {data.attempt_number} 次作答・門檻 {data.step.pass_score ?? "—"} 分
                {data.step.require_teacher_review && "・教授設定的強制審核點"}
              </p>
              {data.tutor_action === "escalate" && (
                <p style={{ marginTop: "10px", fontSize: "13px", color: "#9a3412", backgroundColor: "#ffedd5", padding: "8px 10px", borderRadius: "6px" }}>
                  這位學生多次未達標，AI 沒有評分，而是建議學生找老師討論。
                </p>
              )}
              <h3 style={{ fontSize: "13px", fontWeight: "bold", margin: "16px 0 6px 0" }}>學生作答</h3>
              <p style={{ whiteSpace: "pre-wrap", fontSize: "14px", lineHeight: 1.7, backgroundColor: "#f8fafc", padding: "12px", borderRadius: "8px", margin: 0 }}>{data.content}</p>
            </div>

            <div style={card}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <h3 style={{ fontSize: "15px", fontWeight: "bold", margin: 0 }}>AI 初評（原始紀錄，不會被修改）</h3>
                <span style={{ fontSize: "13px" }}>
                  {ev.total_score == null ? "未評分" : `${ev.total_score} 分`}
                  {ev.confidence != null && (
                    <span style={{ marginLeft: "8px", fontSize: "12px", color: ev.needs_teacher_review ? "#b45309" : "#64748b" }}>
                      信心值 {ev.confidence.toFixed(2)}
                      {ev.needs_teacher_review && "（偏低）"}
                    </span>
                  )}
                </span>
              </div>
              {ev.dimension_scores.map((d) => (
                <div key={d.dimension} style={{ marginTop: "10px", fontSize: "13px", padding: "10px", backgroundColor: "#f8fafc", borderRadius: "6px" }}>
                  <strong>{d.dimension}</strong>：{d.score} / {d.max_score}
                  {d.reason && <div style={{ color: "#475569" }}>{d.reason}</div>}
                  {d.evidence && <div style={{ fontSize: "12px", color: "#64748b", fontStyle: "italic" }}>引用：{d.evidence}</div>}
                </div>
              ))}
              {ev.feedback_text && <p style={{ fontSize: "13px", marginTop: "12px", whiteSpace: "pre-wrap" }}>{ev.feedback_text}</p>}
              {(ev.detected_errors ?? []).length > 0 && (
                <div style={{ marginTop: "12px", fontSize: "13px" }}>
                  <strong>偵測到的常見錯誤</strong>
                  {(ev.detected_errors ?? []).map((err) => (
                    <div key={err.code} style={{ marginTop: "4px" }}>
                      {err.label ?? err.code}
                      {err.evidence && <span style={{ color: "#64748b" }}>（{err.evidence}）</span>}
                    </div>
                  ))}
                </div>
              )}
              {data.prompt_logs.length > 0 && (
                <div style={{ marginTop: "12px", fontSize: "12px", color: "#475569" }}>
                  <strong>此次作答使用的提示</strong>
                  {data.prompt_logs.map((log) => (
                    <div key={log.id}>
                      第 {log.hint_level} 層（{log.hint_trigger === "student_request" ? "學生主動要求" : "系統自動"}
                      {log.hint_source === "generated" ? "・AI 生成" : ""}）：{log.hint_content}
                    </div>
                  ))}
                </div>
              )}
            </div>

            {data.teacher_reviews.length > 0 && (
              <div style={card}>
                <h3 style={{ fontSize: "14px", fontWeight: "bold", margin: "0 0 8px 0" }}>先前的教師判定</h3>
                {data.teacher_reviews.map((r) => (
                  <div key={r.id} style={{ fontSize: "13px", marginTop: "4px" }}>
                    {DECISIONS.find((d) => d.value === r.decision)?.label ?? r.decision}・{r.final_score ?? "—"} 分・{r.final_feedback}
                  </div>
                ))}
              </div>
            )}

            <form onSubmit={handleJudge} style={{ ...card, display: "flex", flexDirection: "column", gap: "12px" }}>
              <h3 style={{ fontSize: "15px", fontWeight: "bold", margin: 0 }}>教師最終判定</h3>
              <label style={{ fontSize: "13px", display: "flex", flexDirection: "column", gap: "4px" }}>
                判定
                <select value={decision} onChange={(e) => setDecision(e.target.value)} style={{ padding: "8px", borderRadius: "6px", border: "1px solid #cbd5e1" }}>
                  {DECISIONS.map((d) => (
                    <option key={d.value} value={d.value}>
                      {d.label}
                    </option>
                  ))}
                </select>
              </label>
              <label style={{ fontSize: "13px", display: "flex", flexDirection: "column", gap: "4px" }}>
                最終分數（0–100，可留空）
                <input
                  type="number"
                  min={0}
                  max={100}
                  value={finalScore}
                  onChange={(e) => setFinalScore(e.target.value)}
                  style={{ padding: "8px", borderRadius: "6px", border: "1px solid #cbd5e1" }}
                />
              </label>
              <label style={{ fontSize: "13px", display: "flex", flexDirection: "column", gap: "4px" }}>
                給學生的回饋
                <textarea
                  rows={4}
                  value={finalFeedback}
                  onChange={(e) => setFinalFeedback(e.target.value)}
                  style={{ padding: "8px", borderRadius: "6px", border: "1px solid #cbd5e1" }}
                />
              </label>
              <label style={{ fontSize: "13px", display: "flex", alignItems: "center", gap: "6px" }}>
                <input type="checkbox" checked={isPublished} onChange={(e) => setIsPublished(e.target.checked)} />
                立即發布給學生
              </label>
              <button
                type="submit"
                disabled={saving}
                style={{ padding: "10px", borderRadius: "8px", border: "none", backgroundColor: "#16a34a", color: "#fff", fontWeight: "bold", cursor: "pointer", opacity: saving ? 0.6 : 1 }}
              >
                {saving ? "儲存中…" : "送出判定"}
              </button>
            </form>
          </>
        )}
      </div>
    </div>
  );
}
