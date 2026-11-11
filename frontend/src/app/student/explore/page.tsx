"use client";

// 學生：自主探索模式——申請使用機器人、查看申請狀態
// 後端：GET /api/tool-requests/tools、GET /api/tool-requests/mine、POST /api/tool-requests（backend/api/tool_requests.py）

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { createClient } from "@/utils/supabase/client";
import { ApiError, apiFetch } from "@/utils/api";
import StudentModeTabs from "../StudentModeTabs";

type Tool = { id: string; title: string; domain: string | null; target_competency: string | null };

type RequestStatus = "pending" | "approved" | "rejected" | "expired";
type ToolRequest = {
  id: string;
  tool_id: string | null; // null = 全部機器人
  tool_title: string | null;
  status: RequestStatus;
  daily_quota: number | null;
  start_date: string | null;
  expire_date: string | null;
  created_at: string;
};

const STATUS_LABEL: Record<RequestStatus, { text: string; color: string; bg: string }> = {
  pending: { text: "審核中", color: "#fbbf24", bg: "rgba(245,158,11,0.15)" },
  approved: { text: "已核准", color: "#4ade80", bg: "rgba(34,197,94,0.15)" },
  rejected: { text: "未通過", color: "#f87171", bg: "rgba(239,68,68,0.15)" },
  expired: { text: "已過期", color: "#94a3b8", bg: "rgba(255,255,255,0.08)" },
};

const card: React.CSSProperties = {
  backgroundColor: "#1e293b",
  borderRadius: "16px",
  border: "1px solid rgba(255,255,255,0.1)",
  padding: "20px",
};

const fmtDate = (d: string | null) => (d ? d.slice(0, 10) : "");

// 這支機器人目前的狀態：取最新的一筆；「全部機器人」的有效核准也算
function currentStatusFor(toolId: string, requests: ToolRequest[]): ToolRequest | null {
  const own = requests.filter((r) => r.tool_id === toolId);
  const activeAll = requests.find((r) => r.tool_id === null && r.status === "approved");
  const latest = own[0] ?? null; // /mine 已依 created_at 新到舊排序
  if (latest && (latest.status === "approved" || latest.status === "pending")) return latest;
  return activeAll ?? latest;
}

export default function StudentExplorePage() {
  const router = useRouter();
  const [supabase] = useState(() => createClient());

  const [tools, setTools] = useState<Tool[] | null>(null);
  const [requests, setRequests] = useState<ToolRequest[]>([]);
  const [loadError, setLoadError] = useState("");
  const [busyTool, setBusyTool] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ ok: boolean; text: string } | null>(null);

  // 用 .then 寫：setState 都在非同步回呼裡，避免在 effect 裡同步 setState
  const load = useCallback(
    () =>
      Promise.all([
        apiFetch<{ data: Tool[] }>("/api/tool-requests/tools"),
        apiFetch<{ data: ToolRequest[] }>("/api/tool-requests/mine"),
      ]).then(
        ([t, r]) => {
          setTools(t.data);
          setRequests(r.data);
          setLoadError("");
        },
        (err) => {
          setTools([]);
          setLoadError(
            err instanceof ApiError
              ? `無法讀取機器人清單：${err.message}`
              : "無法連線到後端（請確認 FastAPI 已啟動，且 NEXT_PUBLIC_API_URL 設定正確）"
          );
        }
      ),
    []
  );

  useEffect(() => {
    load();
  }, [load]);

  const handleApply = async (tool: Tool) => {
    setBusyTool(tool.id);
    setNotice(null);
    try {
      const res = await apiFetch<{ message: string }>("/api/tool-requests", {
        method: "POST",
        body: JSON.stringify({ tool_id: tool.id }),
      });
      setNotice({ ok: true, text: `${tool.title}：${res.message}` });
      await load();
    } catch (err) {
      setNotice({ ok: false, text: `${tool.title}：${err instanceof ApiError ? err.message : "申請失敗，請稍後再試"}` });
    } finally {
      setBusyTool(null);
    }
  };

  const handleSignOut = async () => {
    await supabase.auth.signOut();
    router.push("/");
    router.refresh();
  };

  const hasApproved = requests.some((r) => r.status === "approved");

  return (
    <div style={{ minHeight: "100vh", backgroundColor: "#0f172a", color: "#f8fafc", fontFamily: "sans-serif" }}>
      <header
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          gap: "16px",
          padding: "14px 28px",
          borderBottom: "1px solid rgba(255,255,255,0.1)",
          backgroundColor: "#1e293b",
          flexWrap: "wrap",
        }}
      >
        <StudentModeTabs />
        <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
          <Link href="/student/profile" style={{ fontSize: "12px", color: "#cbd5e1", textDecoration: "none" }}>
            👤 個人資料
          </Link>
          <button
            onClick={handleSignOut}
            style={{ padding: "6px 12px", fontSize: "12px", borderRadius: "6px", border: "1px solid rgba(255,255,255,0.2)", background: "none", color: "#cbd5e1", cursor: "pointer" }}
          >
            登出
          </button>
        </div>
      </header>

      <main style={{ maxWidth: "880px", margin: "28px auto", padding: "0 16px", display: "flex", flexDirection: "column", gap: "20px" }}>
        <section style={{ ...card, display: "flex", justifyContent: "space-between", alignItems: "center", gap: "16px", flexWrap: "wrap" }}>
          <div>
            <h1 style={{ fontSize: "16px", fontWeight: "bold", margin: "0 0 6px 0" }}>自主探索模式</h1>
            <p style={{ fontSize: "13px", color: "#94a3b8", margin: 0, lineHeight: 1.6 }}>
              申請想練習的機器人，老師核准後可以在期限內使用，每天有次數上限。
              <br />
              此模式的 AI 回饋僅供自我練習參考，不是正式評分。
            </p>
          </div>
          <button
            disabled={!hasApproved}
            onClick={() => router.push("/student/modules")}
            title={hasApproved ? "" : "有核准的機器人後才能開始練習"}
            style={{ flexShrink: 0, padding: "10px 18px", borderRadius: "8px", border: "none", backgroundColor: hasApproved ? "#2563eb" : "rgba(255,255,255,0.08)", color: hasApproved ? "#fff" : "#64748b", fontWeight: "bold", fontSize: "14px", cursor: hasApproved ? "pointer" : "not-allowed" }}
          >
            開始練習 →
          </button>
        </section>

        {notice && (
          <div
            style={{
              padding: "10px 14px",
              borderRadius: "8px",
              fontSize: "13px",
              backgroundColor: notice.ok ? "rgba(34,197,94,0.15)" : "rgba(239,68,68,0.15)",
              border: `1px solid ${notice.ok ? "rgba(34,197,94,0.4)" : "rgba(239,68,68,0.4)"}`,
              color: notice.ok ? "#86efac" : "#fca5a5",
            }}
          >
            {notice.text}
          </div>
        )}

        {/* 可申請的機器人 */}
        <section style={card}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "12px" }}>
            <h2 style={{ fontSize: "15px", fontWeight: "bold", margin: 0 }}>可申請的機器人</h2>
            <button onClick={load} style={{ fontSize: "12px", color: "#38bdf8", background: "none", border: "none", cursor: "pointer" }}>
              重新整理
            </button>
          </div>

          {loadError && <p style={{ fontSize: "13px", color: "#fca5a5" }}>{loadError}</p>}
          {tools === null && <p style={{ fontSize: "13px", color: "#94a3b8" }}>載入中…</p>}
          {tools?.length === 0 && !loadError && <p style={{ fontSize: "13px", color: "#94a3b8" }}>目前沒有開放申請的機器人。</p>}

          <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
            {tools?.map((tool) => {
              const current = currentStatusFor(tool.id, requests);
              const label = current ? STATUS_LABEL[current.status] : null;
              const canApply = !current || current.status === "rejected" || current.status === "expired";
              return (
                <div
                  key={tool.id}
                  style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: "12px", padding: "14px", borderRadius: "10px", backgroundColor: "rgba(255,255,255,0.04)", border: "1px solid rgba(255,255,255,0.08)" }}
                >
                  <div style={{ minWidth: 0 }}>
                    <div style={{ display: "flex", alignItems: "center", gap: "8px", flexWrap: "wrap" }}>
                      <span style={{ fontWeight: "bold", fontSize: "15px" }}>{tool.title}</span>
                      {tool.domain && <span style={{ fontSize: "11px", color: "#94a3b8" }}>{tool.domain}</span>}
                      {label && (
                        <span style={{ fontSize: "11px", padding: "2px 8px", borderRadius: "9999px", color: label.color, backgroundColor: label.bg }}>
                          {label.text}
                          {current?.tool_id === null ? "（全部機器人）" : ""}
                        </span>
                      )}
                    </div>
                    {tool.target_competency && <p style={{ margin: "6px 0 0", fontSize: "12px", color: "#94a3b8" }}>練習能力：{tool.target_competency}</p>}
                    {current?.status === "approved" && (
                      <p style={{ margin: "6px 0 0", fontSize: "12px", color: "#86efac" }}>
                        每天 {current.daily_quota ?? 10} 次・{current.expire_date ? `可使用到 ${fmtDate(current.expire_date)}` : "不限期"}
                      </p>
                    )}
                  </div>
                  {canApply ? (
                    <button
                      disabled={busyTool === tool.id}
                      onClick={() => handleApply(tool)}
                      style={{ flexShrink: 0, padding: "8px 16px", borderRadius: "8px", border: "none", backgroundColor: "#2563eb", color: "#fff", fontSize: "13px", fontWeight: "bold", cursor: "pointer", opacity: busyTool === tool.id ? 0.5 : 1 }}
                    >
                      {busyTool === tool.id ? "送出中…" : current ? "重新申請" : "申請使用"}
                    </button>
                  ) : null}
                </div>
              );
            })}
          </div>
        </section>

        {/* 申請紀錄 */}
        {requests.length > 0 && (
          <section style={card}>
            <h2 style={{ fontSize: "15px", fontWeight: "bold", margin: "0 0 12px 0" }}>我的申請紀錄</h2>
            <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
              {requests.map((r) => {
                const label = STATUS_LABEL[r.status];
                return (
                  <div key={r.id} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: "8px", fontSize: "13px", padding: "8px 10px", borderRadius: "6px", backgroundColor: "rgba(255,255,255,0.04)" }}>
                    <span>
                      {r.tool_title || "（已刪除的機器人）"}
                      <span style={{ fontSize: "12px", color: "#64748b", marginLeft: "8px" }}>申請於 {fmtDate(r.created_at)}</span>
                    </span>
                    <span style={{ fontSize: "11px", padding: "2px 8px", borderRadius: "9999px", color: label.color, backgroundColor: label.bg, flexShrink: 0 }}>
                      {label.text}
                    </span>
                  </div>
                );
              })}
            </div>
          </section>
        )}
      </main>
    </div>
  );
}
