"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { createClient } from "@/utils/supabase/client";
import { ApiError, apiFetch } from "@/utils/api";

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
  review_reasons?: string[]; // 舊版後端沒有這個欄位
};

type Tool = { id: string; title: string; domain: string; status: string; version: number };

const REASON_LABEL: Record<string, { text: string; color: string; bg: string }> = {
  escalated: { text: "AI 建議找老師", color: "#9a3412", bg: "#ffedd5" },
  low_confidence: { text: "AI 沒把握", color: "#92400e", bg: "#fef3c7" },
  step_requires_review: { text: "強制審核點", color: "#1e40af", bg: "#dbeafe" },
};

function timeAgo(iso: string): string {
  const minutes = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (minutes < 1) return "剛剛";
  if (minutes < 60) return `${minutes} 分鐘前`;
  if (minutes < 60 * 24) return `${Math.round(minutes / 60)} 小時前`;
  return new Date(iso).toLocaleDateString();
}

export default function AdminToolsPage() {
  const router = useRouter();
  const [supabase] = useState(() => createClient());

  const [pending, setPending] = useState<PendingItem[] | null>(null);
  const [tools, setTools] = useState<Tool[] | null>(null);
  const [errorMsg, setErrorMsg] = useState("");
  const [deleting, setDeleting] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0); // 按「重新整理」時 +1，重新抓資料

  useEffect(() => {
    let active = true;
    Promise.all([
      apiFetch<{ data: PendingItem[] }>("/api/review/pending"),
      // 舊版後端直接回陣列，新版包成 { data }，兩種都接受
      apiFetch<Tool[] | { data: Tool[] }>("/api/teacher/tools"),
    ])
      .then(([pendingRes, toolsRes]) => {
        if (!active) return;
        setErrorMsg("");
        setPending(pendingRes.data);
        setTools(Array.isArray(toolsRes) ? toolsRes : toolsRes.data);
      })
      .catch((err) => {
        if (!active) return;
        setErrorMsg(`無法連線到後端：${err instanceof Error ? err.message : ""}（請確認 FastAPI 已啟動，且 NEXT_PUBLIC_API_URL 設定正確）`);
      });
    return () => {
      active = false;
    };
  }, [refreshKey]);

  const handleDelete = async (tool: Tool) => {
    if (!confirm(`確定要刪除「${tool.title}」？此動作無法復原。`)) return;
    setDeleting(tool.id);
    try {
      await apiFetch(`/api/teacher/tools/${tool.id}`, { method: "DELETE" });
      setTools((prev) => (prev ?? []).filter((t) => t.id !== tool.id));
    } catch (err) {
      alert(err instanceof ApiError && err.status === 409 ? err.message : `刪除失敗：${err instanceof Error ? err.message : ""}`);
    } finally {
      setDeleting(null);
    }
  };

  const handleSignOut = async () => {
    await supabase.auth.signOut();
    router.push("/");
    router.refresh();
  };

  return (
    <div style={{ minHeight: "100vh", backgroundColor: "#f8fafc", color: "#1e293b", fontFamily: "sans-serif" }}>
      <header style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "14px 24px", backgroundColor: "#fff", borderBottom: "1px solid #e2e8f0" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
          <span style={{ backgroundColor: "#16a34a", color: "#fff", padding: "4px 8px", borderRadius: "6px", fontSize: "12px", fontWeight: "bold" }}>
            管理與複核後台
          </span>
          <span style={{ fontWeight: "bold", fontSize: "16px" }}>培訓分析儀表板</span>
        </div>
        <button
          onClick={handleSignOut}
          style={{ padding: "6px 12px", fontSize: "12px", border: "1px solid #cbd5e1", borderRadius: "6px", cursor: "pointer", backgroundColor: "#fff", color: "#1e293b" }}
        >
          登出
        </button>
      </header>

      {errorMsg && (
        <div style={{ maxWidth: "1200px", margin: "16px auto 0", padding: "10px 14px", borderRadius: "8px", backgroundColor: "#fef2f2", border: "1px solid #fecaca", color: "#b91c1c", fontSize: "13px" }}>
          {errorMsg}
        </div>
      )}

      <div style={{ maxWidth: "1200px", margin: "24px auto", padding: "0 16px", display: "grid", gridTemplateColumns: "2fr 1fr", gap: "20px" }}>
        {/* 左：待複核清單（GET /api/review/pending） */}
        <div style={{ backgroundColor: "#fff", borderRadius: "12px", padding: "20px", border: "1px solid #e2e8f0" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "16px" }}>
            <h3 style={{ fontSize: "15px", fontWeight: "bold", margin: 0 }}>待複核學生初評清單 (需求書 ASM-002)</h3>
            <button onClick={() => setRefreshKey((k) => k + 1)} style={{ fontSize: "12px", color: "#2563eb", background: "none", border: "none", cursor: "pointer" }}>
              重新整理
            </button>
          </div>

          {pending === null && !errorMsg && <p style={{ fontSize: "13px", color: "#64748b" }}>載入中…</p>}
          {pending?.length === 0 && <p style={{ fontSize: "13px", color: "#64748b" }}>目前沒有待複核的作答。</p>}

          <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
            {pending?.map((item) => (
              <div key={item.submission_id} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: "12px", padding: "12px", backgroundColor: "#f8fafc", borderRadius: "8px", border: "1px solid #e2e8f0" }}>
                <div>
                  <span style={{ fontWeight: "bold", fontSize: "14px" }}>{item.student.full_name || "（未填姓名）"}</span>
                  <span style={{ fontSize: "12px", color: "#64748b", marginLeft: "10px" }}>
                    {item.step_title}・第 {item.attempt_number} 次・{timeAgo(item.submitted_at)}
                  </span>
                  <div style={{ display: "flex", gap: "6px", marginTop: "6px", flexWrap: "wrap" }}>
                    {(item.review_reasons ?? []).map((r) => {
                      const label = REASON_LABEL[r] ?? { text: r, color: "#475569", bg: "#f1f5f9" };
                      return (
                        <span key={r} style={{ fontSize: "11px", padding: "1px 8px", borderRadius: "9999px", color: label.color, backgroundColor: label.bg }}>
                          {label.text}
                        </span>
                      );
                    })}
                  </div>
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: "12px", flexShrink: 0 }}>
                  <span style={{ fontSize: "12px", backgroundColor: "#dbeafe", color: "#1e40af", padding: "2px 8px", borderRadius: "4px" }}>
                    {item.ai_total_score == null ? "未評分" : `AI 初評: ${item.ai_total_score}分`}
                  </span>
                  <Link
                    href={`/admin/review/${item.submission_id}`}
                    style={{ padding: "4px 10px", fontSize: "12px", backgroundColor: "#16a34a", color: "#fff", borderRadius: "6px", textDecoration: "none" }}
                  >
                    審核判定
                  </Link>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* 右：機器人管理與其他工具 */}
        <div style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
          <div style={{ backgroundColor: "#fff", borderRadius: "12px", padding: "20px", border: "1px solid #e2e8f0" }}>
            <h3 style={{ fontSize: "14px", fontWeight: "bold", margin: "0 0 12px 0" }}>AI 機器人</h3>
            <Link
              href="/admin/tools/new"
              style={{ display: "block", textAlign: "center", padding: "8px 0", backgroundColor: "#0f172a", color: "#fff", borderRadius: "6px", fontSize: "13px", fontWeight: "bold", textDecoration: "none" }}
            >
              + 建立 AI 機器人
            </Link>
            <div style={{ display: "flex", flexDirection: "column", gap: "8px", marginTop: "12px" }}>
              {tools?.map((tool) => (
                <div key={tool.id} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", fontSize: "13px", padding: "8px", borderRadius: "6px", backgroundColor: "#f8fafc" }}>
                  <span>
                    {tool.title}
                    <span style={{ fontSize: "11px", color: "#64748b", marginLeft: "6px" }}>{tool.status}</span>
                  </span>
                  <button
                    onClick={() => handleDelete(tool)}
                    disabled={deleting === tool.id}
                    style={{ fontSize: "11px", color: "#dc2626", background: "none", border: "none", cursor: "pointer" }}
                  >
                    {deleting === tool.id ? "刪除中…" : "刪除"}
                  </button>
                </div>
              ))}
              {tools?.length === 0 && <p style={{ fontSize: "12px", color: "#64748b" }}>尚未建立任何機器人。</p>}
            </div>
          </div>

          <div style={{ backgroundColor: "#fff", borderRadius: "12px", padding: "20px", border: "1px solid #e2e8f0" }}>
            <h3 style={{ fontSize: "14px", fontWeight: "bold", margin: "0 0 12px 0" }}>研究資料匯出 (DAT-001)</h3>
            <p style={{ fontSize: "12px", color: "#64748b", margin: "0 0 12px 0" }}>
              去識別化學習歷程 CSV 匯出。後端 <code>/api/export</code> 尚未實作。
            </p>
            <button
              disabled
              style={{ width: "100%", padding: "8px 0", border: "1px solid #cbd5e1", backgroundColor: "#f1f5f9", color: "#94a3b8", borderRadius: "6px", fontSize: "13px", cursor: "not-allowed" }}
            >
              匯出（尚未開放）
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
