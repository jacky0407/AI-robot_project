"use client";

// 教授：操作紀錄（#90）——誰在什麼時間做了什麼（改機器人、改評分規準、教師判定、匯出、核准或撤銷權限…）
// 後端：GET /api/admin/audit-logs（backend/api/audit_logs.py，只限教授）；紀錄只能新增，這裡沒有修改或刪除
// 時間以台灣時間顯示；篩選的日期也是台灣時間

import { useCallback, useEffect, useState } from "react";
import AdminHeader from "../courses/AdminHeader";
import { errorText, input, panel, pill, smallBtn } from "../courses/shared";
import { apiFetch } from "@/utils/api";

type AuditLog = {
  id: string;
  actor_id: string | null;
  actor_role: string | null;
  actor_name: string;
  actor_email: string | null;
  action: string;
  action_label: string;
  target_type: string | null;
  target_id: string | null;
  detail: Record<string, unknown>;
  created_at: string;
};

type ActionOption = { value: string; label: string };

const PAGE_SIZE = 50;

const ROLE_LABEL: Record<string, string> = { owner: "教授", assistant: "助理" };

// 篩選選單：先依對象分組（tool. 代表機器人的全部操作），再列出個別操作
const GROUPS: ActionOption[] = [
  { value: "tool.", label: "機器人（全部）" },
  { value: "module.", label: "模組（全部）" },
  { value: "step.", label: "關卡（全部）" },
  { value: "export.", label: "資料匯出（全部）" },
  { value: "permission.", label: "自主探索申請（全部）" },
];

const fmtTime = (iso: string) =>
  new Date(iso).toLocaleString("zh-TW", { timeZone: "Asia/Taipei", hour12: false });

// detail 轉成一行看得懂的摘要（欄位名稱沿用後端的 key，不顯示長內容）
function summarize(detail: Record<string, unknown>): string {
  return Object.entries(detail ?? {})
    .filter(([, v]) => v !== null && v !== undefined && !(Array.isArray(v) && v.length === 0))
    .map(([k, v]) => `${k}: ${typeof v === "object" ? JSON.stringify(v) : String(v)}`)
    .join("・");
}

export default function AuditLogPage() {
  const [logs, setLogs] = useState<AuditLog[] | null>(null);
  const [actions, setActions] = useState<ActionOption[]>([]);
  const [hasMore, setHasMore] = useState(false);
  const [offset, setOffset] = useState(0);
  const [filters, setFilters] = useState({ action: "", dateFrom: "", dateTo: "" });
  const [errorMsg, setErrorMsg] = useState("");

  const load = useCallback(
    (nextOffset: number) => {
      const params = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(nextOffset) });
      if (filters.action) params.set("action", filters.action);
      if (filters.dateFrom) params.set("date_from", filters.dateFrom);
      if (filters.dateTo) params.set("date_to", filters.dateTo);
      return apiFetch<{ data: AuditLog[]; has_more: boolean; actions: ActionOption[] }>(`/api/admin/audit-logs?${params}`).then(
        (res) => {
          setLogs(res.data);
          setHasMore(res.has_more);
          setActions(res.actions);
          setOffset(nextOffset);
          setErrorMsg("");
        },
        (err) => {
          setLogs([]);
          setErrorMsg(`無法讀取操作紀錄：${errorText(err, "請稍後再試")}`);
        }
      );
    },
    [filters]
  );

  useEffect(() => {
    load(0);
  }, [load]);

  return (
    <div style={{ minHeight: "100vh", backgroundColor: "#0f172a", color: "#f8fafc", fontFamily: "sans-serif", colorScheme: "dark" }}>
      <AdminHeader />
      <main style={{ maxWidth: "1100px", margin: "24px auto", padding: "0 20px", display: "flex", flexDirection: "column", gap: "16px" }}>
        <div>
          <h1 style={{ fontSize: "20px", fontWeight: "bold", margin: "0 0 4px" }}>操作紀錄</h1>
          <p style={{ fontSize: "12px", color: "#94a3b8", margin: 0 }}>
            教授與助理在後台的操作都會留下紀錄，只能新增、不能修改或刪除。只有教授看得到這一頁。
          </p>
        </div>

        <section style={{ ...panel, display: "flex", gap: "10px", flexWrap: "wrap", alignItems: "flex-end" }}>
          <label style={{ display: "flex", flexDirection: "column", gap: "4px", fontSize: "12px", color: "#94a3b8", flex: "1 1 220px" }}>
            操作
            <select value={filters.action} onChange={(e) => setFilters({ ...filters, action: e.target.value })} style={{ ...input, backgroundColor: "#0f172a" }}>
              <option value="">全部</option>
              <optgroup label="依對象">
                {GROUPS.map((g) => (
                  <option key={g.value} value={g.value}>{g.label}</option>
                ))}
              </optgroup>
              <optgroup label="個別操作">
                {actions.map((a) => (
                  <option key={a.value} value={a.value}>{a.label}</option>
                ))}
              </optgroup>
            </select>
          </label>
          <label style={{ display: "flex", flexDirection: "column", gap: "4px", fontSize: "12px", color: "#94a3b8" }}>
            從
            <input type="date" value={filters.dateFrom} onChange={(e) => setFilters({ ...filters, dateFrom: e.target.value })} style={input} />
          </label>
          <label style={{ display: "flex", flexDirection: "column", gap: "4px", fontSize: "12px", color: "#94a3b8" }}>
            到
            <input type="date" value={filters.dateTo} onChange={(e) => setFilters({ ...filters, dateTo: e.target.value })} style={input} />
          </label>
          {(filters.action || filters.dateFrom || filters.dateTo) && (
            <button onClick={() => setFilters({ action: "", dateFrom: "", dateTo: "" })} style={smallBtn}>
              清除篩選
            </button>
          )}
        </section>

        {errorMsg && <p style={{ fontSize: "13px", color: "#fca5a5", margin: 0 }}>{errorMsg}</p>}
        {logs === null && <p style={{ fontSize: "13px", color: "#94a3b8", margin: 0 }}>載入中…</p>}
        {logs?.length === 0 && !errorMsg && <p style={{ fontSize: "13px", color: "#94a3b8", margin: 0 }}>沒有符合的紀錄。</p>}

        {logs && logs.length > 0 && (
          <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
            {logs.map((l) => {
              const summary = summarize(l.detail);
              return (
                <div key={l.id} style={{ padding: "10px 12px", borderRadius: "8px", backgroundColor: "rgba(255,255,255,0.04)", border: "1px solid rgba(255,255,255,0.06)", fontSize: "13px" }}>
                  <div style={{ display: "flex", gap: "8px", alignItems: "center", flexWrap: "wrap" }}>
                    <span style={{ color: "#94a3b8", fontSize: "12px", minWidth: "150px" }}>{fmtTime(l.created_at)}</span>
                    <b>{l.actor_name}</b>
                    {l.actor_role && <span style={pill("#94a3b8", "rgba(255,255,255,0.08)")}>{ROLE_LABEL[l.actor_role] ?? l.actor_role}</span>}
                    <span style={pill("#4ade80", "rgba(34,197,94,0.15)")}>{l.action_label}</span>
                    {l.target_id && (
                      <span style={{ fontSize: "11px", color: "#64748b" }}>
                        {l.target_type}：{l.target_id}
                      </span>
                    )}
                  </div>
                  {summary && <div style={{ marginTop: "4px", fontSize: "12px", color: "#94a3b8", wordBreak: "break-all" }}>{summary}</div>}
                </div>
              );
            })}
          </div>
        )}

        {logs && (offset > 0 || hasMore) && (
          <div style={{ display: "flex", justifyContent: "center", gap: "10px", paddingBottom: "24px" }}>
            <button disabled={offset === 0} onClick={() => load(Math.max(0, offset - PAGE_SIZE))} style={{ ...smallBtn, opacity: offset === 0 ? 0.4 : 1 }}>
              ← 較新
            </button>
            <button disabled={!hasMore} onClick={() => load(offset + PAGE_SIZE)} style={{ ...smallBtn, opacity: hasMore ? 1 : 0.4 }}>
              較舊 →
            </button>
          </div>
        )}
      </main>
    </div>
  );
}
