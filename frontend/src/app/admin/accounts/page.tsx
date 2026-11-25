"use client";

// 帳號管理（#87）：停用、恢復、去識別化、永久刪除（30 天回收期）、復原、清除到期帳號
// 後端：/api/admin/accounts（backend/api/accounts.py，規則在 core/accounts.py）
//   - 停用、恢復：教授與助理（助理只能對學生）
//   - 去識別化、永久刪除、復原、清除到期帳號：只限教授
//   - 永久刪除與清除到期帳號要再輸入一次驗證器 App 的 6 位數（後端要求 5 分鐘內重新驗證過）
// 每個按鈕顯示與否看後端回傳的 actions；送出時後端會再檢查一次

import { useCallback, useEffect, useState } from "react";
import AdminHeader from "../courses/AdminHeader";
import { errorText, input, panel, pill, smallBtn } from "../courses/shared";
import { apiFetch } from "@/utils/api";
import { createClient } from "@/utils/supabase/client";

type Action = "disable" | "enable" | "deidentify" | "delete" | "restore";
type Status = "active" | "disabled" | "deidentified" | "pending_deletion";

type Account = {
  id: string;
  email: string | null;
  full_name: string | null;
  role: string;
  account_status: Status;
  status_label: string;
  status_reason: string | null;
  status_changed_at: string | null;
  deletion_scheduled_for: string | null;
  created_at: string;
  actions: Action[];
};

const ROLE_LABEL: Record<string, string> = { owner: "教授", assistant: "助理", student: "學生", explorer: "自主探索" };
const STATUS_COLOR: Record<Status, [string, string]> = {
  active: ["#4ade80", "rgba(34,197,94,0.15)"],
  disabled: ["#fbbf24", "rgba(245,158,11,0.15)"],
  deidentified: ["#93c5fd", "rgba(59,130,246,0.15)"],
  pending_deletion: ["#f87171", "rgba(239,68,68,0.15)"],
};
const FILTERS: { value: "" | Status; label: string }[] = [
  { value: "", label: "全部" },
  { value: "active", label: "使用中" },
  { value: "disabled", label: "已停用" },
  { value: "deidentified", label: "已去識別化" },
  { value: "pending_deletion", label: "待刪除" },
];

const fmtDate = (iso: string | null) => (iso ? new Date(iso).toLocaleDateString("zh-TW", { timeZone: "Asia/Taipei" }) : "");

// 對話框：哪一種操作、對哪個帳號（purge 沒有對象）
type Dialog = { kind: Action | "purge"; account?: Account } | null;

const DIALOG_TEXT: Record<Action | "purge", { title: string; desc: string; button: string; danger?: boolean }> = {
  disable: { title: "停用帳號", desc: "停用後不能登入，資料全部保留，之後可以隨時恢復。", button: "停用" },
  enable: { title: "恢復帳號", desc: "恢復後可以重新登入。", button: "恢復" },
  deidentify: {
    title: "去識別化",
    desc: "姓名與 Email 會換成匿名研究編號，學習歷程保留給研究，帳號不能再登入。⚠️ 無法復原：之後就看不出這是誰的資料。",
    button: "去識別化",
    danger: true,
  },
  delete: {
    title: "永久刪除",
    desc: "帳號會先停用並進入 30 天回收期，期間可以復原；30 天後按「清除到期帳號」才會真正刪除，連同他自己的作答、評分、聊天紀錄全部刪除，無法復原。若要保留研究資料，請改用「去識別化」。",
    button: "排定刪除",
    danger: true,
  },
  restore: { title: "復原帳號", desc: "取消刪除，回到申請刪除前的狀態。", button: "復原" },
  purge: {
    title: "清除到期帳號",
    desc: "回收期已過的帳號會被真正刪除，連同他們自己的學習資料，無法復原。",
    button: "永久刪除",
    danger: true,
  },
};

export default function AccountsPage() {
  const [supabase] = useState(() => createClient());
  const [accounts, setAccounts] = useState<Account[] | null>(null);
  const [dueForPurge, setDueForPurge] = useState(0);
  const [filter, setFilter] = useState<"" | Status>("");
  const [keyword, setKeyword] = useState("");
  const [msg, setMsg] = useState("");
  const [dialog, setDialog] = useState<Dialog>(null);
  const [form, setForm] = useState({ reason: "", confirm: "", code: "" });
  const [busy, setBusy] = useState(false);
  const [dialogError, setDialogError] = useState("");

  const load = useCallback(() => {
    const params = new URLSearchParams();
    if (filter) params.set("status", filter);
    if (keyword.trim()) params.set("q", keyword.trim());
    return apiFetch<{ data: Account[]; due_for_purge: number }>(`/api/admin/accounts?${params}`).then(
      (res) => {
        setAccounts(res.data);
        setDueForPurge(res.due_for_purge);
      },
      (err) => {
        setAccounts([]);
        setMsg(`無法讀取帳號：${errorText(err, "請稍後再試")}`);
      }
    );
  }, [filter, keyword]);

  useEffect(() => {
    load();
  }, [load]);

  const open = (kind: Action | "purge", account?: Account) => {
    setDialog({ kind, account });
    setForm({ reason: "", confirm: "", code: "" });
    setDialogError("");
  };

  // 永久刪除與清除到期帳號：先重新輸入驗證碼，讓這次登入的驗證時間是最新的（後端要求 5 分鐘內）
  const reverify = async (code: string) => {
    const { data, error } = await supabase.auth.mfa.listFactors();
    const factor = data?.totp[0];
    if (error || !factor) throw new Error("找不到兩步驟驗證設定，請重新登入");
    const verified = await supabase.auth.mfa.challengeAndVerify({ factorId: factor.id, code });
    if (verified.error) throw new Error("驗證碼不正確或已過期，請輸入 App 上目前顯示的 6 位數字");
  };

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!dialog) return;
    const { kind, account } = dialog;
    setBusy(true);
    setDialogError("");
    try {
      if (kind === "delete" || kind === "purge") await reverify(form.code);
      const path = kind === "purge" ? "/api/admin/accounts/purge-due" : `/api/admin/accounts/${account!.id}/${kind}`;
      const body =
        kind === "disable" || kind === "enable"
          ? { reason: form.reason }
          : kind === "deidentify"
            ? { reason: form.reason, confirm: form.confirm }
            : kind === "delete"
              ? { reason: form.reason, confirm_email: form.confirm }
              : undefined;
      const res = await apiFetch<{ message: string }>(path, { method: "POST", body: body ? JSON.stringify(body) : undefined });
      setMsg(`${account ? `${account.full_name || account.email}：` : ""}${res.message}`);
      setDialog(null);
      await load();
    } catch (err) {
      setDialogError(err instanceof Error ? err.message : "操作失敗，請稍後再試");
    } finally {
      setBusy(false);
    }
  };

  const needReason = dialog && dialog.kind !== "restore" && dialog.kind !== "purge";
  const needCode = dialog && (dialog.kind === "delete" || dialog.kind === "purge");
  const confirmHint =
    dialog?.kind === "deidentify" ? "請輸入「去識別化」確認" : dialog?.kind === "delete" ? `請輸入這個帳號的 Email（${dialog.account?.email}）確認` : "";
  const canSubmit =
    !busy &&
    (!needReason || form.reason.trim().length >= 2 || dialog?.kind === "enable") &&
    (!confirmHint || form.confirm.trim().length > 0) &&
    (!needCode || /^\d{6}$/.test(form.code));

  return (
    <div style={{ minHeight: "100vh", backgroundColor: "#0f172a", color: "#f8fafc", fontFamily: "sans-serif", colorScheme: "dark" }}>
      <AdminHeader />
      <main style={{ maxWidth: "1100px", margin: "24px auto", padding: "0 20px", display: "flex", flexDirection: "column", gap: "16px" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: "12px", flexWrap: "wrap" }}>
          <div>
            <h1 style={{ fontSize: "20px", fontWeight: "bold", margin: "0 0 4px" }}>帳號管理</h1>
            <p style={{ fontSize: "12px", color: "#94a3b8", margin: 0, lineHeight: 1.6 }}>
              停用：不能登入、資料保留、可恢復。去識別化（教授）：姓名與 Email 換成匿名編號，學習歷程留給研究，不能復原。
              <br />
              永久刪除（教授）：先停用 30 天、期間可復原，之後清除才真正刪除。所有操作都會記在操作紀錄。
            </p>
          </div>
          {dueForPurge > 0 && (
            <button onClick={() => open("purge")} style={{ ...smallBtn, color: "#f87171", borderColor: "rgba(239,68,68,0.5)" }}>
              清除到期帳號（{dueForPurge}）
            </button>
          )}
        </div>

        <section style={{ ...panel, display: "flex", gap: "10px", flexWrap: "wrap", alignItems: "center" }}>
          <div style={{ display: "flex", gap: "4px", flexWrap: "wrap" }}>
            {FILTERS.map((f) => (
              <button
                key={f.value}
                onClick={() => setFilter(f.value)}
                style={{ ...smallBtn, ...(filter === f.value ? { backgroundColor: "#16a34a", color: "#fff", border: "none" } : {}) }}
              >
                {f.label}
              </button>
            ))}
          </div>
          <input
            placeholder="搜尋姓名或 Email"
            value={keyword}
            onChange={(e) => setKeyword(e.target.value)}
            style={{ ...input, flex: "1 1 200px", width: "auto" }}
          />
        </section>

        {msg && <p style={{ fontSize: "13px", margin: 0, color: /失敗|無法/.test(msg) ? "#fca5a5" : "#86efac" }}>{msg}</p>}
        {accounts === null && <p style={{ fontSize: "13px", color: "#94a3b8", margin: 0 }}>載入中…</p>}
        {accounts?.length === 0 && <p style={{ fontSize: "13px", color: "#94a3b8", margin: 0 }}>沒有符合的帳號。</p>}

        <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
          {accounts?.map((a) => {
            const [color, bg] = STATUS_COLOR[a.account_status];
            return (
              <div key={a.id} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: "10px", flexWrap: "wrap", padding: "10px 12px", borderRadius: "8px", backgroundColor: "rgba(255,255,255,0.04)", border: "1px solid rgba(255,255,255,0.06)" }}>
                <div style={{ minWidth: 0, fontSize: "13px" }}>
                  <div style={{ display: "flex", gap: "8px", alignItems: "center", flexWrap: "wrap" }}>
                    <b>{a.full_name || "（未填姓名）"}</b>
                    <span style={{ color: "#94a3b8", fontSize: "12px" }}>{a.email}</span>
                    <span style={pill("#94a3b8", "rgba(255,255,255,0.08)")}>{ROLE_LABEL[a.role] ?? a.role}</span>
                    <span style={pill(color, bg)}>{a.status_label}</span>
                  </div>
                  {a.account_status !== "active" && (
                    <div style={{ marginTop: "4px", fontSize: "12px", color: "#94a3b8" }}>
                      {a.status_reason && `原因：${a.status_reason}`}
                      {a.status_changed_at && `・${fmtDate(a.status_changed_at)}`}
                      {a.deletion_scheduled_for && `・${fmtDate(a.deletion_scheduled_for)} 之後可以清除`}
                    </div>
                  )}
                </div>
                <div style={{ display: "flex", gap: "6px", flexShrink: 0 }}>
                  {a.actions.map((act) => (
                    <button
                      key={act}
                      onClick={() => open(act, a)}
                      style={{ ...smallBtn, padding: "3px 10px", fontSize: "11px", color: DIALOG_TEXT[act].danger ? "#f87171" : act === "disable" ? "#fbbf24" : "#4ade80" }}
                    >
                      {DIALOG_TEXT[act].title.replace("帳號", "")}
                    </button>
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      </main>

      {dialog && (
        <div role="dialog" aria-modal="true" style={{ position: "fixed", inset: 0, backgroundColor: "rgba(0,0,0,0.6)", display: "flex", alignItems: "center", justifyContent: "center", padding: "16px", zIndex: 50 }}>
          <form onSubmit={submit} style={{ ...panel, width: "100%", maxWidth: "460px", display: "flex", flexDirection: "column", gap: "10px", backgroundColor: "#1e293b" }}>
            <h2 style={{ fontSize: "16px", margin: 0, color: DIALOG_TEXT[dialog.kind].danger ? "#fca5a5" : "#f8fafc" }}>
              {DIALOG_TEXT[dialog.kind].title}
              {dialog.account && `：${dialog.account.full_name || dialog.account.email}`}
            </h2>
            <p style={{ margin: 0, fontSize: "13px", color: "#cbd5e1", lineHeight: 1.6 }}>{DIALOG_TEXT[dialog.kind].desc}</p>
            {needReason && (
              <textarea
                rows={2}
                maxLength={500}
                placeholder={dialog.kind === "enable" ? "原因（選填）" : "原因（必填），例如：學生撤回研究同意"}
                value={form.reason}
                onChange={(e) => setForm({ ...form, reason: e.target.value })}
                style={{ ...input, resize: "vertical" }}
              />
            )}
            {confirmHint && (
              <label style={{ fontSize: "12px", color: "#94a3b8", display: "flex", flexDirection: "column", gap: "4px" }}>
                {confirmHint}
                <input value={form.confirm} onChange={(e) => setForm({ ...form, confirm: e.target.value })} style={input} />
              </label>
            )}
            {needCode && (
              <label style={{ fontSize: "12px", color: "#94a3b8", display: "flex", flexDirection: "column", gap: "4px" }}>
                這個操作無法復原，請再輸入一次驗證器 App 的 6 位數
                <input
                  inputMode="numeric"
                  autoComplete="one-time-code"
                  maxLength={6}
                  value={form.code}
                  onChange={(e) => setForm({ ...form, code: e.target.value.replace(/\D/g, "") })}
                  style={{ ...input, letterSpacing: "4px" }}
                />
              </label>
            )}
            {dialogError && <p style={{ margin: 0, fontSize: "12px", color: "#fca5a5" }}>{dialogError}</p>}
            <div style={{ display: "flex", justifyContent: "flex-end", gap: "8px" }}>
              <button type="button" onClick={() => setDialog(null)} style={smallBtn}>
                取消
              </button>
              <button
                type="submit"
                disabled={!canSubmit}
                style={{ ...smallBtn, border: "none", color: "#fff", backgroundColor: DIALOG_TEXT[dialog.kind].danger ? "#dc2626" : "#16a34a", opacity: canSubmit ? 1 : 0.5 }}
              >
                {busy ? "處理中…" : DIALOG_TEXT[dialog.kind].button}
              </button>
            </div>
          </form>
        </div>
      )}
    </div>
  );
}
