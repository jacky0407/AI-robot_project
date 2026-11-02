"use client";

// 教師端：機器人申請審核（自主探索模式）
// 核准時設定到期日與每日次數；已核准的可以延長期限、改次數或撤銷；已過期的要設新到期日才能再核准
// 後端：GET /api/tool-requests?status=、PATCH /api/tool-requests/{id}（backend/api/tool_requests.py）

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { createClient } from "@/utils/supabase/client";
import { ApiError, apiFetch } from "@/utils/api";
import AdminNav from "../AdminNav";

type RequestStatus = "pending" | "approved" | "rejected" | "expired";
type ToolRequest = {
  id: string;
  user_id: string;
  full_name: string | null;
  email: string | null;
  tool_id: string | null;
  tool_title: string | null;
  status: RequestStatus;
  daily_quota: number | null;
  start_date: string | null;
  expire_date: string | null;
  created_at: string;
};

// 每一列核准時要送出的設定
type Draft = { expireDate: string; unlimited: boolean; dailyQuota: string };

const FILTERS: { value: RequestStatus | "all"; label: string }[] = [
  { value: "pending", label: "待審核" },
  { value: "approved", label: "已核准" },
  { value: "expired", label: "已過期" },
  { value: "rejected", label: "已拒絕" },
  { value: "all", label: "全部" },
];

const STATUS_LABEL: Record<RequestStatus, { text: string; color: string; bg: string }> = {
  pending: { text: "待審核", color: "#fbbf24", bg: "rgba(245,158,11,0.15)" },
  approved: { text: "已核准", color: "#4ade80", bg: "rgba(34,197,94,0.15)" },
  rejected: { text: "已拒絕", color: "#f87171", bg: "rgba(239,68,68,0.15)" },
  expired: { text: "已過期", color: "#94a3b8", bg: "rgba(255,255,255,0.08)" },
};

const DEFAULT_QUOTA = 10; // 與 schema.sql 的 tool_permissions.daily_quota 預設值一致
const DEFAULT_DAYS = 30;

const panel: React.CSSProperties = { backgroundColor: "#1e293b", borderRadius: "12px", padding: "20px", border: "1px solid rgba(255,255,255,0.1)" };
const input: React.CSSProperties = { boxSizing: "border-box", padding: "6px 8px", borderRadius: "6px", border: "1px solid rgba(255,255,255,0.2)", backgroundColor: "rgba(255,255,255,0.05)", color: "#fff", fontSize: "13px", outline: "none" };
const smallBtn: React.CSSProperties = { padding: "5px 12px", fontSize: "12px", borderRadius: "6px", border: "1px solid rgba(255,255,255,0.2)", backgroundColor: "transparent", color: "#cbd5e1", cursor: "pointer" };

// 台灣時間的日期（YYYY-MM-DD），與後端判斷到期的基準一致
function taipeiDate(offsetDays = 0): string {
  const d = new Date(Date.now() + 8 * 3600_000 + offsetDays * 86400_000);
  return d.toISOString().slice(0, 10);
}

function initialDraft(r: ToolRequest): Draft {
  const keepCurrent = r.status === "approved";
  return {
    expireDate: keepCurrent && r.expire_date ? r.expire_date : taipeiDate(DEFAULT_DAYS),
    unlimited: keepCurrent && !r.expire_date,
    dailyQuota: String(r.daily_quota ?? DEFAULT_QUOTA),
  };
}

export default function AdminToolRequestsPage() {
  const router = useRouter();
  const [supabase] = useState(() => createClient());

  const [filter, setFilter] = useState<RequestStatus | "all">("pending");
  const [requests, setRequests] = useState<ToolRequest[] | null>(null);
  const [errorMsg, setErrorMsg] = useState("");
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [rowMsg, setRowMsg] = useState<Record<string, { ok: boolean; text: string }>>({});
  // 成功訊息放在上方：核准後這筆會從「待審核」清單消失，放在列裡會看不到
  const [notice, setNotice] = useState("");

  // 用 .then 寫：setState 都在非同步回呼裡，避免在 effect 裡同步 setState
  const load = useCallback(
    () =>
      apiFetch<{ data: ToolRequest[] }>(`/api/tool-requests${filter === "all" ? "" : `?status=${filter}`}`).then(
        (res) => {
          setRequests(res.data);
          setDrafts(Object.fromEntries(res.data.map((r) => [r.id, initialDraft(r)])));
          setErrorMsg("");
        },
        (err) => {
          setRequests([]);
          setErrorMsg(
            err instanceof ApiError
              ? `無法讀取申請：${err.message}`
              : "無法連線到後端（請確認 FastAPI 已啟動，且 NEXT_PUBLIC_API_URL 設定正確）"
          );
        }
      ),
    [filter]
  );

  useEffect(() => {
    load();
  }, [load]);

  const updateDraft = (id: string, patch: Partial<Draft>) =>
    setDrafts((prev) => ({ ...prev, [id]: { ...prev[id], ...patch } }));

  const review = async (r: ToolRequest, action: "approve" | "reject") => {
    const draft = drafts[r.id];
    let body: Record<string, unknown> = { action };

    if (action === "approve") {
      const quota = Number(draft.dailyQuota);
      if (!Number.isInteger(quota) || quota < 1 || quota > 1000) {
        setRowMsg((p) => ({ ...p, [r.id]: { ok: false, text: "每日次數要是 1～1000 的整數" } }));
        return;
      }
      if (!draft.unlimited && (!draft.expireDate || draft.expireDate < taipeiDate())) {
        setRowMsg((p) => ({ ...p, [r.id]: { ok: false, text: "到期日不能早於今天" } }));
        return;
      }
      // 後端：expire_date 傳 null = 不限期；有傳日期 = 到那天為止
      body = { action, daily_quota: quota, expire_date: draft.unlimited ? null : draft.expireDate };
    } else if (r.status === "approved" && !confirm(`確定要撤銷「${r.full_name || r.email}」使用「${r.tool_title}」的權限嗎？`)) {
      return;
    }

    setBusy(r.id);
    setNotice("");
    setRowMsg((p) => ({ ...p, [r.id]: { ok: true, text: "" } }));
    try {
      const res = await apiFetch<{ message: string }>(`/api/tool-requests/${r.id}`, {
        method: "PATCH",
        body: JSON.stringify(body),
      });
      setNotice(`${r.full_name || r.email}・${r.tool_title}：${res.message}`);
      await load();
    } catch (err) {
      setRowMsg((p) => ({ ...p, [r.id]: { ok: false, text: err instanceof ApiError ? err.message : "操作失敗，請稍後再試" } }));
    } finally {
      setBusy(null);
    }
  };

  const handleSignOut = async () => {
    await supabase.auth.signOut();
    router.push("/");
    router.refresh();
  };

  return (
    <div style={{ minHeight: "100vh", backgroundColor: "#0f172a", color: "#f8fafc", fontFamily: "sans-serif", colorScheme: "dark" }}>
      <header style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: "12px", padding: "14px 24px", backgroundColor: "#1e293b", borderBottom: "1px solid rgba(255,255,255,0.1)", flexWrap: "wrap" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "12px", flexWrap: "wrap" }}>
          <span style={{ backgroundColor: "#16a34a", color: "#fff", padding: "4px 8px", borderRadius: "6px", fontSize: "12px", fontWeight: "bold" }}>管理與複核後台</span>
          <AdminNav />
        </div>
        <button onClick={handleSignOut} style={{ ...smallBtn, padding: "6px 12px" }}>
          登出
        </button>
      </header>

      <main style={{ maxWidth: "1100px", margin: "24px auto", padding: "0 16px" }}>
        <div style={panel}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: "12px", marginBottom: "16px", flexWrap: "wrap" }}>
            <div>
              <h1 style={{ fontSize: "16px", fontWeight: "bold", margin: "0 0 4px 0" }}>機器人使用申請（自主探索模式）</h1>
              <p style={{ fontSize: "12px", color: "#94a3b8", margin: 0 }}>核准時設定可使用到哪一天、每天可以作答幾次。正式課程的學生不需要申請。</p>
            </div>
            <div style={{ display: "flex", gap: "4px", padding: "4px", borderRadius: "10px", backgroundColor: "rgba(255,255,255,0.06)" }}>
              {FILTERS.map((f) => (
                <button
                  key={f.value}
                  onClick={() => {
                    setRequests(null);
                    setFilter(f.value);
                  }}
                  style={{ padding: "5px 12px", borderRadius: "8px", border: "none", fontSize: "12px", fontWeight: "bold", cursor: "pointer", backgroundColor: filter === f.value ? "#16a34a" : "transparent", color: filter === f.value ? "#fff" : "#94a3b8" }}
                >
                  {f.label}
                </button>
              ))}
            </div>
          </div>

          {errorMsg && <p style={{ fontSize: "13px", color: "#fca5a5" }}>{errorMsg}</p>}
          {notice && (
            <p style={{ fontSize: "13px", color: "#86efac", margin: "0 0 12px 0", padding: "8px 12px", borderRadius: "8px", backgroundColor: "rgba(34,197,94,0.12)" }}>{notice}</p>
          )}
          {requests === null && <p style={{ fontSize: "13px", color: "#94a3b8" }}>載入中…</p>}
          {requests?.length === 0 && !errorMsg && <p style={{ fontSize: "13px", color: "#94a3b8" }}>目前沒有這個狀態的申請。</p>}

          <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
            {requests?.map((r) => {
              const label = STATUS_LABEL[r.status];
              const draft = drafts[r.id] ?? initialDraft(r);
              const canReject = r.status === "pending" || r.status === "approved";
              const msg = rowMsg[r.id];
              return (
                <div key={r.id} style={{ padding: "14px", borderRadius: "10px", backgroundColor: "rgba(255,255,255,0.04)", border: "1px solid rgba(255,255,255,0.08)" }}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: "12px", flexWrap: "wrap" }}>
                    <div style={{ minWidth: 0 }}>
                      <div style={{ display: "flex", alignItems: "center", gap: "8px", flexWrap: "wrap" }}>
                        <span style={{ fontWeight: "bold", fontSize: "14px" }}>{r.full_name || "（未填姓名）"}</span>
                        <span style={{ fontSize: "12px", color: "#94a3b8" }}>{r.email}</span>
                        <span style={{ fontSize: "11px", padding: "2px 8px", borderRadius: "9999px", color: label.color, backgroundColor: label.bg }}>{label.text}</span>
                      </div>
                      <p style={{ margin: "6px 0 0", fontSize: "13px", color: "#cbd5e1" }}>
                        申請使用：<b>{r.tool_title || "（已刪除的機器人）"}</b>
                        <span style={{ fontSize: "12px", color: "#64748b", marginLeft: "8px" }}>申請於 {r.created_at.slice(0, 10)}</span>
                      </p>
                      {(r.status === "approved" || r.status === "expired") && (
                        <p style={{ margin: "4px 0 0", fontSize: "12px", color: "#94a3b8" }}>
                          目前設定：每天 {r.daily_quota ?? DEFAULT_QUOTA} 次・{r.expire_date ? `到 ${r.expire_date}` : "不限期"}
                        </p>
                      )}
                    </div>

                    <div style={{ display: "flex", alignItems: "center", gap: "8px", flexWrap: "wrap" }}>
                      {/* 後端允許從任何狀態核准：待審核、已核准（改設定）、已過期、已拒絕（重新核准） */}
                      <>
                          <label style={{ fontSize: "12px", color: "#cbd5e1", display: "flex", alignItems: "center", gap: "4px" }}>
                            每天
                            <input
                              type="number"
                              min={1}
                              max={1000}
                              value={draft.dailyQuota}
                              onChange={(e) => updateDraft(r.id, { dailyQuota: e.target.value })}
                              style={{ ...input, width: "64px" }}
                            />
                            次
                          </label>
                          <label style={{ fontSize: "12px", color: "#cbd5e1", display: "flex", alignItems: "center", gap: "4px" }}>
                            到
                            <input
                              type="date"
                              min={taipeiDate()}
                              disabled={draft.unlimited}
                              value={draft.expireDate}
                              onChange={(e) => updateDraft(r.id, { expireDate: e.target.value })}
                              style={{ ...input, opacity: draft.unlimited ? 0.4 : 1 }}
                            />
                          </label>
                          <label style={{ fontSize: "12px", color: "#cbd5e1", display: "flex", alignItems: "center", gap: "4px", cursor: "pointer" }}>
                            <input type="checkbox" checked={draft.unlimited} onChange={(e) => updateDraft(r.id, { unlimited: e.target.checked })} />
                            不限期
                          </label>
                          <button
                            disabled={busy === r.id}
                            onClick={() => review(r, "approve")}
                            style={{ ...smallBtn, backgroundColor: "#16a34a", color: "#fff", border: "none", opacity: busy === r.id ? 0.5 : 1 }}
                          >
                            {r.status === "approved" ? "更新設定" : r.status === "pending" ? "核准" : "重新核准"}
                          </button>
                      </>
                      {canReject && (
                        <button disabled={busy === r.id} onClick={() => review(r, "reject")} style={{ ...smallBtn, color: "#f87171" }}>
                          {r.status === "approved" ? "撤銷" : "拒絕"}
                        </button>
                      )}
                    </div>
                  </div>
                  {msg?.text && <p style={{ margin: "8px 0 0", fontSize: "12px", color: msg.ok ? "#86efac" : "#fca5a5" }}>{msg.text}</p>}
                </div>
              );
            })}
          </div>
        </div>
      </main>
    </div>
  );
}
