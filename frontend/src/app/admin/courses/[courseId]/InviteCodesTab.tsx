"use client";

// 課程詳細頁：邀請碼清單（產生、複製、改日期與次數、停用、刪除）
// 後端：POST/PATCH/DELETE /api/courses/{id}/invite-codes
// 一門課可以有多組邀請碼，各自設定可用日期與方案（免費每日次數／自備金鑰）

import { useState } from "react";
import { apiFetch } from "@/utils/api";
import { type Course, type InviteCode, type InvitePlan, errorText, input, planLabel, smallBtn, todayLocal } from "../shared";

type CodeForm = { plan: InvitePlan; access_until: string; daily_quota: string };
const NEW_FORM: CodeForm = { plan: "free", access_until: "", daily_quota: "10" };

export default function InviteCodesTab({
  course,
  openFormInitially,
  onChanged,
}: {
  course: Course;
  openFormInitially: boolean;
  onChanged: () => Promise<void>;
}) {
  const [form, setForm] = useState<CodeForm | null>(openFormInitially && !course.is_archived ? NEW_FORM : null);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState("");
  const [copied, setCopied] = useState<string | null>(null);
  const base = `/api/courses/${encodeURIComponent(course.id)}/invite-codes`;

  const create = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!form) return;
    setBusy("new");
    setMsg("");
    try {
      const res = await apiFetch<{ data: InviteCode; message: string }>(base, {
        method: "POST",
        body: JSON.stringify({
          plan: form.plan,
          access_until: form.access_until,
          daily_quota: form.plan === "free" ? Number(form.daily_quota) : null,
        }),
      });
      setMsg(`${res.message}：${res.data.code}`);
      setForm(null);
      await onChanged();
    } catch (err) {
      setMsg(`產生失敗：${errorText(err, "請稍後再試")}`);
    } finally {
      setBusy(null);
    }
  };

  // 停用／啟用、改可用日期或每日次數；已用這組碼加入的學生跟著變
  const update = async (code: InviteCode, changes: Partial<Pick<InviteCode, "is_active" | "access_until" | "daily_quota">>) => {
    setBusy(code.id);
    setMsg("");
    try {
      await apiFetch(`${base}/${code.id}`, { method: "PATCH", body: JSON.stringify(changes) });
      await onChanged();
    } catch (err) {
      setMsg(`更新失敗：${errorText(err, "請稍後再試")}`);
    } finally {
      setBusy(null);
    }
  };

  // 刪除：從介面移除，並把用這組碼加入的學生移出課程（作答紀錄保留）；只想停止新的人加入請用「停用」
  const remove = async (code: InviteCode) => {
    const who = code.member_count > 0 ? `用這組碼加入的 ${code.member_count} 位學生會被移出課程（作答紀錄保留）。` : "目前沒有學生用這組碼加入。";
    if (!window.confirm(`確定要刪除邀請碼「${code.code}」嗎？\n\n${who}\n刪除後無法復原。只想停止新的人加入，請改用「停用」。`)) return;
    setBusy(code.id);
    setMsg("");
    try {
      const res = await apiFetch<{ message: string }>(`${base}/${code.id}`, { method: "DELETE" });
      setMsg(res.message);
      await onChanged();
    } catch (err) {
      setMsg(`刪除失敗：${errorText(err, "請稍後再試")}`);
    } finally {
      setBusy(null);
    }
  };

  const changeAccessUntil = (code: InviteCode) => {
    const value = prompt(`「${code.code}」的新可用日期（YYYY-MM-DD）\n用這組碼加入的 ${code.member_count} 位學生會一起改。`, code.access_until);
    if (value && value.trim() !== code.access_until) update(code, { access_until: value.trim() });
  };

  const changeDailyQuota = (code: InviteCode) => {
    const value = prompt(`「${code.code}」的每日作答次數（1–1000）\n用這組碼加入的 ${code.member_count} 位學生會一起改。`, String(code.daily_quota ?? 10));
    if (!value) return;
    const quota = Number(value);
    if (!Number.isInteger(quota) || quota < 1 || quota > 1000) {
      alert("每日次數要是 1 到 1000 的整數");
      return;
    }
    if (quota !== code.daily_quota) update(code, { daily_quota: quota });
  };

  const copy = async (code: string) => {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(code);
      setTimeout(() => setCopied(null), 1500);
    } catch {
      // 不支援剪貼簿（非 HTTPS）時，老師可以直接手動選取邀請碼
    }
  };

  const tiny: React.CSSProperties = { ...smallBtn, padding: "3px 9px", fontSize: "11px" };

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: "12px", marginBottom: "16px" }}>
        <div>
          <h2 style={{ fontSize: "16px", fontWeight: "bold", margin: "0 0 4px" }}>邀請碼清單</h2>
          <p style={{ fontSize: "12px", color: "#94a3b8", margin: 0 }}>學生輸入邀請碼加入課程。每組碼可以設定不同的可用日期與每日次數。</p>
        </div>
        {!form && !course.is_archived && (
          <button onClick={() => { setMsg(""); setForm(NEW_FORM); }} style={{ ...smallBtn, flexShrink: 0, padding: "7px 14px", backgroundColor: "#16a34a", color: "#fff", border: "none", fontWeight: "bold" }}>
            ＋ 產生邀請碼
          </button>
        )}
      </div>

      {form && (
        <form onSubmit={create} style={{ display: "flex", alignItems: "center", gap: "10px", flexWrap: "wrap", fontSize: "12px", padding: "12px", marginBottom: "14px", borderRadius: "8px", border: "1px dashed rgba(74,222,128,0.45)" }}>
          <select value={form.plan} onChange={(e) => setForm({ ...form, plan: e.target.value as InvitePlan })} style={{ ...input, width: "auto", backgroundColor: "#0f172a" }}>
            <option value="free">免費（平台金鑰）</option>
            <option value="self_key">自備金鑰（學生自己的 Gemini）</option>
          </select>
          {form.plan === "free" && (
            <label style={{ display: "flex", alignItems: "center", gap: "4px" }}>
              每天
              <input type="number" required min={1} max={1000} value={form.daily_quota} onChange={(e) => setForm({ ...form, daily_quota: e.target.value })} style={{ ...input, width: "70px" }} />
              次
            </label>
          )}
          <label style={{ display: "flex", alignItems: "center", gap: "4px" }}>
            可用到
            <input type="date" required min={todayLocal()} value={form.access_until} onChange={(e) => setForm({ ...form, access_until: e.target.value })} style={{ ...input, width: "auto" }} />
          </label>
          <button type="submit" disabled={busy === "new"} style={{ ...smallBtn, backgroundColor: "#16a34a", color: "#fff", border: "none" }}>
            {busy === "new" ? "產生中…" : "產生"}
          </button>
          <button type="button" onClick={() => setForm(null)} style={smallBtn}>
            取消
          </button>
        </form>
      )}

      {msg && <p style={{ margin: "0 0 12px", fontSize: "12px", color: /失敗/.test(msg) ? "#fca5a5" : "#86efac" }}>{msg}</p>}

      {course.invite_codes.length === 0 ? (
        <p style={{ margin: 0, fontSize: "13px", color: "#fbbf24" }}>還沒有邀請碼，學生無法加入這門課。</p>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
          {course.invite_codes.map((code) => {
            const dimmed = !code.is_active || code.expired;
            return (
              <div key={code.id} style={{ display: "flex", alignItems: "center", gap: "10px", flexWrap: "wrap", fontSize: "12px", padding: "10px 12px", borderRadius: "8px", backgroundColor: "rgba(255,255,255,0.04)", opacity: dimmed ? 0.6 : 1 }}>
                <code style={{ fontSize: "15px", fontWeight: "bold", letterSpacing: "2px", padding: "2px 8px", borderRadius: "4px", backgroundColor: "rgba(255,255,255,0.08)", color: "#f8fafc", textDecoration: code.is_active ? "none" : "line-through" }}>
                  {code.code}
                </code>
                {code.is_active && (
                  <button onClick={() => copy(code.code)} style={tiny}>
                    {copied === code.code ? "已複製" : "複製"}
                  </button>
                )}
                <span style={{ color: "#cbd5e1" }}>{planLabel(code)}</span>
                <span style={{ color: code.expired ? "#f87171" : "#94a3b8" }}>
                  {code.expired ? `已過可用日期（${code.access_until}）` : `可用到 ${code.access_until}`}
                </span>
                <span style={{ color: "#94a3b8" }}>{code.member_count} 人使用</span>
                {!code.is_active && <span style={{ color: "#f87171" }}>已停用</span>}
                <span style={{ marginLeft: "auto", display: "flex", gap: "6px" }}>
                  <button disabled={busy === code.id} onClick={() => changeAccessUntil(code)} style={tiny}>
                    改日期
                  </button>
                  {code.plan === "free" && (
                    <button disabled={busy === code.id} onClick={() => changeDailyQuota(code)} style={tiny}>
                      改次數
                    </button>
                  )}
                  <button
                    disabled={busy === code.id}
                    onClick={() => update(code, { is_active: !code.is_active })}
                    title={code.is_active ? "停止用這組碼加入；已加入的學生不受影響" : "重新開放用這組碼加入"}
                    style={{ ...tiny, color: code.is_active ? "#fbbf24" : "#4ade80" }}
                  >
                    {code.is_active ? "停用" : "啟用"}
                  </button>
                  <button disabled={busy === code.id} onClick={() => remove(code)} title="刪除這組碼，並移出用它加入的學生" style={{ ...tiny, color: "#f87171" }}>
                    刪除
                  </button>
                </span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
