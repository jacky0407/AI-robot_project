"use client";

// 教師端：建立機器人（機器人卡片由左上往右排列，最後一張是「＋ 建立機器人」，連到 /admin/tools/new）
// 原本的「機器人與複核」頁：待複核清單移到每門課的「待複核」分頁，資料匯出移到課程的「資料匯出」分頁
// 後端：GET /api/teacher/tools、DELETE /api/teacher/tools/{id}（backend/api/teacher.py）
// 「編輯」只顯示在可以修改的機器人上（建立者或教授，後端回傳 can_edit），連到 /admin/tools/{id}/edit

import { useEffect, useState } from "react";
import Link from "next/link";
import { ApiError, apiFetch } from "@/utils/api";
import AdminHeader from "../courses/AdminHeader";
import { pill, smallBtn } from "../courses/shared";

type Tool = {
  id: string;
  title: string;
  domain: string;
  target_competency?: string | null;
  status: string;
  version: number;
  can_edit?: boolean;
};

const STATUS_LABEL: Record<string, { text: string; color: string; bg: string }> = {
  published: { text: "已發布", color: "#4ade80", bg: "rgba(34,197,94,0.15)" },
  draft: { text: "草稿", color: "#94a3b8", bg: "rgba(255,255,255,0.08)" },
  paused: { text: "暫停使用", color: "#fbbf24", bg: "rgba(245,158,11,0.15)" },
  archived: { text: "已封存", color: "#94a3b8", bg: "rgba(255,255,255,0.08)" },
};

// 卡片（長方形），與「＋ 建立機器人」共用尺寸
const tile: React.CSSProperties = {
  display: "flex",
  flexDirection: "column",
  minHeight: "150px",
  padding: "18px",
  borderRadius: "14px",
  boxSizing: "border-box",
};

export default function AdminToolsPage() {
  const [tools, setTools] = useState<Tool[] | null>(null);
  const [errorMsg, setErrorMsg] = useState("");
  const [deleting, setDeleting] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    // 舊版後端直接回陣列，新版包成 { data }，兩種都接受
    apiFetch<Tool[] | { data: Tool[] }>("/api/teacher/tools").then(
      (res) => {
        if (!active) return;
        setTools(Array.isArray(res) ? res : res.data);
        setErrorMsg("");
      },
      (err) => {
        if (!active) return;
        setTools([]);
        setErrorMsg(`無法讀取機器人：${err instanceof Error ? err.message : ""}（請確認 FastAPI 已啟動，且 NEXT_PUBLIC_API_URL 設定正確）`);
      }
    );
    return () => {
      active = false;
    };
  }, []);

  const handleDelete = async (tool: Tool) => {
    if (!confirm(`確定要刪除「${tool.title}」嗎？此動作無法復原。`)) return;
    setDeleting(tool.id);
    try {
      await apiFetch(`/api/teacher/tools/${tool.id}`, { method: "DELETE" });
      setTools((prev) => (prev ?? []).filter((t) => t.id !== tool.id));
    } catch (err) {
      // 409：已經被模組的關卡使用，不能刪
      alert(err instanceof ApiError && err.status === 409 ? err.message : `刪除失敗：${err instanceof Error ? err.message : ""}`);
    } finally {
      setDeleting(null);
    }
  };

  return (
    <div style={{ minHeight: "100vh", backgroundColor: "#0f172a", color: "#f8fafc", fontFamily: "sans-serif", colorScheme: "dark" }}>
      <AdminHeader />

      <main style={{ maxWidth: "1100px", margin: "24px auto", padding: "0 20px" }}>
        <div style={{ marginBottom: "16px" }}>
          <h1 style={{ fontSize: "20px", fontWeight: "bold", margin: "0 0 4px 0" }}>機器人</h1>
          <p style={{ fontSize: "12px", color: "#94a3b8", margin: 0 }}>
            機器人是學生在關卡裡練習時的 AI 助教，各自有評分規準與提示方式。建立後到課程的「模組管理」把它排進關卡。
          </p>
        </div>

        {errorMsg && <p style={{ margin: "0 0 16px", fontSize: "13px", color: "#fca5a5" }}>{errorMsg}</p>}
        {tools === null && <p style={{ fontSize: "13px", color: "#94a3b8" }}>載入中…</p>}

        {tools !== null && (
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(240px, 1fr))", gap: "16px" }}>
            {tools.map((t) => {
              const label = STATUS_LABEL[t.status] ?? { text: t.status, color: "#94a3b8", bg: "rgba(255,255,255,0.08)" };
              return (
                <div key={t.id} style={{ ...tile, justifyContent: "space-between", gap: "12px", backgroundColor: "#1e293b", border: "1px solid rgba(255,255,255,0.12)", opacity: t.status === "archived" ? 0.65 : 1 }}>
                  <div style={{ minWidth: 0 }}>
                    <div style={{ display: "flex", gap: "6px", flexWrap: "wrap", marginBottom: "8px" }}>
                      <span style={pill(label.color, label.bg)}>{label.text}</span>
                      {t.domain && <span style={pill("#94a3b8", "rgba(255,255,255,0.08)")}>{t.domain}</span>}
                    </div>
                    <div style={{ fontWeight: "bold", fontSize: "16px", lineHeight: 1.4 }}>{t.title}</div>
                    {t.target_competency && <p style={{ margin: "6px 0 0", fontSize: "12px", color: "#94a3b8", lineHeight: 1.5 }}>練習能力：{t.target_competency}</p>}
                  </div>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: "8px", fontSize: "12px" }}>
                    <span style={{ color: "#64748b" }}>第 {t.version} 版</span>
                    <div style={{ display: "flex", gap: "6px" }}>
                      {t.can_edit && (
                        <Link
                          href={`/admin/tools/${t.id}/edit`}
                          style={{ ...smallBtn, padding: "3px 10px", fontSize: "11px", color: "#4ade80", textDecoration: "none" }}
                        >
                          編輯
                        </Link>
                      )}
                      <button
                        onClick={() => handleDelete(t)}
                        disabled={deleting === t.id}
                        title="已排進關卡的機器人不能刪除"
                        style={{ ...smallBtn, padding: "3px 10px", fontSize: "11px", color: "#f87171" }}
                      >
                        {deleting === t.id ? "刪除中…" : "刪除"}
                      </button>
                    </div>
                  </div>
                </div>
              );
            })}

            <Link
              href="/admin/tools/new"
              className="tool-new"
              style={{ ...tile, alignItems: "center", justifyContent: "center", gap: "6px", border: "2px dashed rgba(148,163,184,0.45)", color: "#94a3b8", textDecoration: "none" }}
            >
              <span style={{ fontSize: "40px", lineHeight: 1, fontWeight: 300 }}>＋</span>
              <span style={{ fontSize: "14px", fontWeight: "bold" }}>建立機器人</span>
              <span style={{ fontSize: "12px", color: "#64748b" }}>設定角色、評分規準與提示</span>
            </Link>
          </div>
        )}
      </main>

      <style>{`
        .tool-new { transition: transform 0.15s, border-color 0.15s; }
        .tool-new:hover { transform: translateY(-2px); border-color: #4ade80 !important; color: #cbd5e1 !important; }
        .tool-new:focus-visible { outline: 2px solid #4ade80; outline-offset: 2px; }
      `}</style>
    </div>
  );
}
