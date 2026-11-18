"use client";

// 課程詳細頁：待複核（原本「機器人與複核」頁左側的清單，改成只列這門課）
// 後端：GET /api/review/pending?course_id=（backend/api/review.py）；點進去到 /admin/review/[id]?course= 判定

import { useEffect, useState } from "react";
import Link from "next/link";
import { apiFetch } from "@/utils/api";
import { errorText, pill, smallBtn } from "../shared";

type PendingItem = {
  submission_id: string;
  student: { user_id: string; full_name: string };
  step_title: string;
  tool_name: string;
  attempt_number: number;
  submitted_at: string;
  ai_total_score: number | null;
  ai_confidence?: number | null;
  needs_attention: boolean;
  review_reasons?: string[];
};

const REASON_LABEL: Record<string, { text: string; color: string; bg: string }> = {
  escalated: { text: "AI 建議找老師", color: "#fdba74", bg: "rgba(249,115,22,0.15)" },
  low_confidence: { text: "AI 沒把握", color: "#fbbf24", bg: "rgba(245,158,11,0.15)" },
  step_requires_review: { text: "必須老師審核的關卡", color: "#7dd3fc", bg: "rgba(56,189,248,0.15)" },
};

function timeAgo(iso: string): string {
  const minutes = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (minutes < 1) return "剛剛";
  if (minutes < 60) return `${minutes} 分鐘前`;
  if (minutes < 60 * 24) return `${Math.round(minutes / 60)} 小時前`;
  return new Date(iso).toLocaleDateString();
}

export default function ReviewTab({ courseId, onCount }: { courseId: string; onCount: (n: number) => void }) {
  const [items, setItems] = useState<PendingItem[] | null>(null);
  const [error, setError] = useState("");
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let active = true;
    apiFetch<{ data: PendingItem[] }>(`/api/review/pending?course_id=${encodeURIComponent(courseId)}`).then(
      (res) => {
        if (!active) return;
        setItems(res.data);
        setError("");
        onCount(res.data.length);
      },
      (err) => {
        if (!active) return;
        setItems([]);
        setError(errorText(err, "無法連線到後端（請確認 FastAPI 已啟動）"));
      }
    );
    return () => {
      active = false;
    };
  }, [courseId, tick, onCount]);

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: "12px", marginBottom: "16px" }}>
        <div>
          <h2 style={{ fontSize: "16px", fontWeight: "bold", margin: "0 0 4px" }}>待複核</h2>
          <p style={{ fontSize: "12px", color: "#94a3b8", margin: 0 }}>
            AI 已初評、還沒有老師判定的作答。標示原因的排在前面，請優先處理；成績以老師的判定為準。
          </p>
        </div>
        <button onClick={() => setTick((t) => t + 1)} style={{ ...smallBtn, flexShrink: 0 }}>
          重新整理
        </button>
      </div>

      {error && <p style={{ fontSize: "13px", color: "#fca5a5", margin: 0 }}>{error}</p>}
      {items === null && <p style={{ fontSize: "13px", color: "#94a3b8", margin: 0 }}>載入中…</p>}
      {items?.length === 0 && !error && <p style={{ fontSize: "13px", color: "#94a3b8", margin: 0 }}>目前沒有需要複核的作答。</p>}

      <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
        {items?.map((item) => (
          <div key={item.submission_id} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: "12px", padding: "12px", borderRadius: "8px", backgroundColor: "rgba(255,255,255,0.04)" }}>
            <div style={{ minWidth: 0 }}>
              <b style={{ fontSize: "14px" }}>{item.student.full_name || "（未填姓名）"}</b>
              <span style={{ fontSize: "12px", color: "#94a3b8", marginLeft: "10px" }}>
                {item.step_title}・第 {item.attempt_number} 次・{timeAgo(item.submitted_at)}
              </span>
              {(item.review_reasons ?? []).length > 0 && (
                <div style={{ display: "flex", gap: "6px", marginTop: "6px", flexWrap: "wrap" }}>
                  {(item.review_reasons ?? []).map((r) => {
                    const label = REASON_LABEL[r] ?? { text: r, color: "#cbd5e1", bg: "rgba(255,255,255,0.08)" };
                    return (
                      <span key={r} style={pill(label.color, label.bg)}>
                        {label.text}
                      </span>
                    );
                  })}
                </div>
              )}
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: "12px", flexShrink: 0 }}>
              <span style={{ fontSize: "12px", color: "#cbd5e1" }}>{item.ai_total_score == null ? "AI 未評分" : `AI 初評 ${item.ai_total_score} 分`}</span>
              <Link
                href={`/admin/review/${item.submission_id}?course=${encodeURIComponent(courseId)}`}
                style={{ padding: "6px 12px", fontSize: "12px", fontWeight: "bold", backgroundColor: "#16a34a", color: "#fff", borderRadius: "6px", textDecoration: "none" }}
              >
                複核
              </Link>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
