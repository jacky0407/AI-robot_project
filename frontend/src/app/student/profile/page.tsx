"use client";

// 學生：個人資料
//   1. 基本資料（唯讀）：profiles 只能讀自己（migrations/006），學生不能直接改；
//      complete_registration() 會覆蓋 terms_accepted_at（研究同意紀錄），所以不拿來當「修改資料」用
//   2. 修改密碼：Supabase Auth 的 updateUser，現在就能用
//   3. 自備 Gemini API Key：依「邀請碼與自備金鑰設計提案」的 /api/me/api-key（PUT／GET／DELETE）
//      後端尚未實作時 GET 會回 404，畫面顯示「尚未開放」；後端完成後不用改前端就會啟用
//
// 金鑰安全（提案「自備金鑰的安全要求」的前端部分）：
//   - 只透過 PUT 的 body 送出，不放網址、不存 localStorage／sessionStorage／cookie、不 console.log
//   - 送出後立刻清空輸入框；畫面只顯示後端回傳的末 4 碼（key_hint），永不顯示完整金鑰

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { createClient } from "@/utils/supabase/client";
import { ApiError, apiFetch } from "@/utils/api";
import StudentModeTabs from "../StudentModeTabs";

type Profile = {
  full_name: string | null;
  email: string;
  role: string | null;
  profession: string | null;
  terms_accepted_at: string | null;
  created_at: string;
};

// GET /api/me/api-key 的回應（提案：永不回傳金鑰本身）
type KeyStatus = { has_key: boolean; key_hint: string | null; verified_at: string | null };
// null = 讀取中；"unavailable" = 後端還沒有這個 API
type KeyState = KeyStatus | "unavailable" | { error: string } | null;

const ROLE_LABEL: Record<string, string> = {
  owner: "教師（課程負責人）",
  assistant: "助教／工作人員",
  student: "學生（正式課程）",
  explorer: "自主探索使用者",
};

// 與 /onboarding 的選項一致
const PROFESSION_LABEL: Record<string, string> = {
  preschool_teacher: "學前教師",
  special_ed_teacher: "特教教師",
  early_intervention: "早療專業人員",
  student: "師資培育／相關科系學生",
  other: "其他",
};

const MIN_PASSWORD_LENGTH = 6; // Supabase Auth 預設的最短密碼長度

const section: React.CSSProperties = {
  backgroundColor: "#1e293b",
  borderRadius: "16px",
  border: "1px solid rgba(255,255,255,0.1)",
  padding: "22px",
};
const input: React.CSSProperties = {
  width: "100%",
  boxSizing: "border-box",
  padding: "10px 12px",
  borderRadius: "8px",
  border: "1px solid rgba(255,255,255,0.2)",
  backgroundColor: "rgba(255,255,255,0.05)",
  color: "#fff",
  fontSize: "14px",
  outline: "none",
};
const primaryBtn: React.CSSProperties = {
  padding: "9px 18px",
  borderRadius: "8px",
  border: "none",
  backgroundColor: "#2563eb",
  color: "#fff",
  fontWeight: "bold",
  fontSize: "13px",
  cursor: "pointer",
};
const ghostBtn: React.CSSProperties = {
  padding: "9px 16px",
  borderRadius: "8px",
  border: "1px solid rgba(255,255,255,0.2)",
  background: "none",
  color: "#cbd5e1",
  fontSize: "13px",
  cursor: "pointer",
};
const note = (ok: boolean): React.CSSProperties => ({ margin: "10px 0 0", fontSize: "13px", color: ok ? "#86efac" : "#fca5a5" });

const fmtDateTime = (iso: string | null) => (iso ? new Date(iso).toLocaleString("zh-TW", { hour12: false }) : "—");

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={{ display: "grid", gridTemplateColumns: "110px 1fr", gap: "12px", padding: "10px 0", borderBottom: "1px solid rgba(255,255,255,0.06)", fontSize: "14px" }}>
      <span style={{ color: "#94a3b8" }}>{label}</span>
      <span style={{ color: "#f8fafc", wordBreak: "break-all" }}>{children}</span>
    </div>
  );
}

export default function StudentProfilePage() {
  const router = useRouter();
  const [supabase] = useState(() => createClient());

  const [profile, setProfile] = useState<Profile | null>(null);
  const [provider, setProvider] = useState<string>("email");
  const [loadError, setLoadError] = useState("");

  // 修改密碼
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [savingPassword, setSavingPassword] = useState(false);
  const [passwordMsg, setPasswordMsg] = useState<{ ok: boolean; text: string } | null>(null);

  // 自備金鑰
  const [keyState, setKeyState] = useState<KeyState>(null);
  const [keyInput, setKeyInput] = useState("");
  const [showKeyForm, setShowKeyForm] = useState(false);
  const [savingKey, setSavingKey] = useState(false);
  const [keyMsg, setKeyMsg] = useState<{ ok: boolean; text: string } | null>(null);

  useEffect(() => {
    let active = true;
    supabase.auth.getUser().then(async ({ data: { user } }) => {
      if (!active || !user) return;
      // Google 帳號沒有本站密碼；identities 有 email 才能改密碼
      const providers = (user.identities ?? []).map((i) => i.provider);
      setProvider(providers.includes("email") ? "email" : providers[0] ?? "email");
      const { data, error } = await supabase
        .from("profiles")
        .select("full_name, email, role, profession, terms_accepted_at, created_at")
        .eq("id", user.id)
        .single();
      if (!active) return;
      if (error || !data) setLoadError("無法讀取個人資料，請重新整理或聯絡管理員。");
      else setProfile(data as Profile);
    });
    return () => {
      active = false;
    };
  }, [supabase]);

  const loadKey = useCallback(
    () =>
      apiFetch<{ data: KeyStatus }>("/api/me/api-key").then(
      (res) => setKeyState(res.data),
      (err) => {
        // 404 = 後端還沒有 /api/me/api-key（提案尚未實作）
        if (err instanceof ApiError && err.status === 404) setKeyState("unavailable");
        else setKeyState({ error: err instanceof ApiError ? err.message : "連不到後端，請確認 FastAPI 已啟動。" });
      }
    ),
    []
  );

  useEffect(() => {
    loadKey();
  }, [loadKey]);

  const handlePassword = async (e: React.FormEvent) => {
    e.preventDefault();
    setPasswordMsg(null);
    if (password.length < MIN_PASSWORD_LENGTH) {
      setPasswordMsg({ ok: false, text: `密碼至少需要 ${MIN_PASSWORD_LENGTH} 個字元` });
      return;
    }
    if (password !== confirmPassword) {
      setPasswordMsg({ ok: false, text: "兩次輸入的密碼不一致" });
      return;
    }
    setSavingPassword(true);
    const { error } = await supabase.auth.updateUser({ password });
    setSavingPassword(false);
    setPassword("");
    setConfirmPassword("");
    if (error) {
      // 專案若開啟「Secure password change」，太久以前登入的人需要重新登入才能改
      const needsReauth = /reauth|recent/i.test(error.message);
      setPasswordMsg({ ok: false, text: needsReauth ? "為了安全，請先登出再重新登入後修改密碼。" : `修改失敗：${error.message}` });
      return;
    }
    setPasswordMsg({ ok: true, text: "密碼已更新，下次登入請使用新密碼。" });
  };

  const handleSaveKey = async (e: React.FormEvent) => {
    e.preventDefault();
    const key = keyInput.trim();
    // 送出前就清空輸入框，金鑰只存在這次請求的 body 裡
    setKeyInput("");
    if (!key) return;
    setSavingKey(true);
    setKeyMsg(null);
    try {
      await apiFetch("/api/me/api-key", { method: "PUT", body: JSON.stringify({ api_key: key }) });
      setKeyMsg({ ok: true, text: "金鑰已驗證並儲存。用「自備金鑰」邀請碼加入的課程，作答會使用你自己的金鑰、不限次數。" });
      setShowKeyForm(false);
      await loadKey();
    } catch (err) {
      // 錯誤訊息只顯示後端說明，不把輸入的金鑰帶回畫面
      setKeyMsg({ ok: false, text: err instanceof ApiError ? `無法儲存：${err.message}` : "連不到後端，請稍後再試。" });
    } finally {
      setSavingKey(false);
    }
  };

  const handleDeleteKey = async () => {
    if (!confirm("確定要刪除你的金鑰嗎？刪除後，用「自備金鑰」邀請碼加入的課程將無法作答，直到重新填寫。")) return;
    setSavingKey(true);
    setKeyMsg(null);
    try {
      await apiFetch("/api/me/api-key", { method: "DELETE" });
      setKeyMsg({ ok: true, text: "已刪除金鑰。" });
      await loadKey();
    } catch (err) {
      setKeyMsg({ ok: false, text: err instanceof ApiError ? `刪除失敗：${err.message}` : "連不到後端，請稍後再試。" });
    } finally {
      setSavingKey(false);
    }
  };

  const handleSignOut = async () => {
    await supabase.auth.signOut();
    router.push("/");
    router.refresh();
  };

  const keyUnavailable = keyState === "unavailable";
  const keyStatus = keyState && typeof keyState === "object" && "has_key" in keyState ? keyState : null;
  const keyError = keyState && typeof keyState === "object" && "error" in keyState ? keyState.error : "";

  return (
    <div style={{ minHeight: "100vh", backgroundColor: "#0f172a", color: "#f8fafc", fontFamily: "sans-serif", colorScheme: "dark" }}>
      <header style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: "12px", padding: "14px 28px", borderBottom: "1px solid rgba(255,255,255,0.1)", backgroundColor: "#1e293b", flexWrap: "wrap" }}>
        <StudentModeTabs />
        <button onClick={handleSignOut} style={{ ...ghostBtn, padding: "6px 12px", fontSize: "12px" }}>
          登出
        </button>
      </header>

      <main style={{ maxWidth: "720px", margin: "28px auto", padding: "0 20px", display: "flex", flexDirection: "column", gap: "20px" }}>
        <div>
          <h1 style={{ fontSize: "20px", fontWeight: "bold", margin: "0 0 4px 0" }}>個人資料</h1>
          <p style={{ fontSize: "12px", color: "#94a3b8", margin: 0 }}>查看帳號資料、修改密碼與設定自己的 Gemini 金鑰。</p>
        </div>

        {/* 1. 基本資料（唯讀） */}
        <section style={section}>
          <h2 style={{ fontSize: "15px", fontWeight: "bold", margin: "0 0 8px 0" }}>基本資料</h2>
          {loadError && <p style={note(false)}>{loadError}</p>}
          {!profile && !loadError && <p style={{ fontSize: "13px", color: "#94a3b8" }}>載入中…</p>}
          {profile && (
            <>
              <Row label="姓名">{profile.full_name || "（未填）"}</Row>
              <Row label="電子信箱">{profile.email}</Row>
              <Row label="身分">{ROLE_LABEL[profile.role ?? ""] ?? profile.role ?? "—"}</Row>
              <Row label="職業">{PROFESSION_LABEL[profile.profession ?? ""] ?? profile.profession ?? "—"}</Row>
              <Row label="登入方式">{provider === "google" ? "Google 帳號" : "電子信箱與密碼"}</Row>
              <Row label="同意條款時間">{fmtDateTime(profile.terms_accepted_at)}</Row>
              <p style={{ fontSize: "12px", color: "#64748b", margin: "12px 0 0" }}>
                姓名、職業目前無法自行修改；如需更正請聯絡老師。身分由管理員設定。
              </p>
            </>
          )}
        </section>

        {/* 2. 修改密碼 */}
        <section style={section}>
          <h2 style={{ fontSize: "15px", fontWeight: "bold", margin: "0 0 8px 0" }}>修改密碼</h2>
          {provider === "google" ? (
            <p style={{ fontSize: "13px", color: "#94a3b8", margin: 0 }}>你使用 Google 帳號登入，沒有本站密碼，請到 Google 帳戶管理密碼。</p>
          ) : (
            <form onSubmit={handlePassword} style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
              <input type="password" autoComplete="new-password" placeholder="新密碼" value={password} onChange={(e) => setPassword(e.target.value)} style={input} />
              <input
                type="password"
                autoComplete="new-password"
                placeholder="再輸入一次新密碼"
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                style={input}
              />
              <div>
                <button type="submit" disabled={savingPassword || !password} style={{ ...primaryBtn, opacity: savingPassword || !password ? 0.5 : 1 }}>
                  {savingPassword ? "更新中…" : "更新密碼"}
                </button>
              </div>
              {passwordMsg && <p style={note(passwordMsg.ok)}>{passwordMsg.text}</p>}
            </form>
          )}
        </section>

        {/* 3. 自備 Gemini API Key */}
        <section style={{ ...section, opacity: keyUnavailable ? 0.7 : 1 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: "12px", marginBottom: "8px" }}>
            <h2 style={{ fontSize: "15px", fontWeight: "bold", margin: 0 }}>我的 Gemini API Key</h2>
            {keyUnavailable && (
              <span style={{ fontSize: "11px", padding: "2px 10px", borderRadius: "9999px", color: "#fbbf24", backgroundColor: "rgba(245,158,11,0.15)" }}>尚未開放</span>
            )}
            {keyStatus?.has_key && (
              <span style={{ fontSize: "11px", padding: "2px 10px", borderRadius: "9999px", color: "#4ade80", backgroundColor: "rgba(34,197,94,0.15)" }}>使用中</span>
            )}
          </div>

          <p style={{ fontSize: "13px", color: "#cbd5e1", margin: "0 0 12px", lineHeight: 1.7 }}>
            填入自己的金鑰後，作答評分會使用你的 Google 帳號額度，不受平台每日次數限制；部分課程要求一定要填。
          </p>
          <ul style={{ fontSize: "12px", color: "#94a3b8", margin: "0 0 16px", paddingLeft: "18px", lineHeight: 1.8 }}>
            <li>
              到{" "}
              <a href="https://aistudio.google.com/app/apikey" target="_blank" rel="noopener noreferrer" style={{ color: "#38bdf8" }}>
                Google AI Studio
              </a>{" "}
              建立金鑰。費用由你的 Google 帳號負擔，建議先在 Google Cloud 設定預算上限。
            </li>
            <li>未綁定帳單的免費金鑰，Google 可能會把送出的內容用於改進其產品；請勿在作答中寫入可辨識個人的資料。</li>
            <li>金鑰會加密保存，任何人（包含老師）都看不到完整內容，你可以隨時刪除。</li>
          </ul>

          {keyState === null && <p style={{ fontSize: "13px", color: "#94a3b8", margin: 0 }}>讀取中…</p>}
          {keyUnavailable && (
            <p style={{ fontSize: "13px", color: "#fbbf24", margin: 0 }}>此功能還在開發中，目前作答一律使用平台額度。開放後就可以在這裡填寫。</p>
          )}
          {keyError && <p style={note(false)}>{keyError}</p>}

          {keyStatus && (
            <>
              {keyStatus.has_key && !showKeyForm && (
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: "12px", flexWrap: "wrap", padding: "12px 14px", borderRadius: "10px", backgroundColor: "rgba(255,255,255,0.04)" }}>
                  <div style={{ fontSize: "13px" }}>
                    <code style={{ fontSize: "14px", letterSpacing: "1px", color: "#7dd3fc", fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace" }}>{keyStatus.key_hint ?? "AIza…"}</code>
                    <div style={{ fontSize: "12px", color: "#64748b", marginTop: "4px" }}>最後驗證：{fmtDateTime(keyStatus.verified_at)}</div>
                  </div>
                  <div style={{ display: "flex", gap: "8px" }}>
                    <button type="button" onClick={() => setShowKeyForm(true)} disabled={savingKey} style={ghostBtn}>
                      更換金鑰
                    </button>
                    <button type="button" onClick={handleDeleteKey} disabled={savingKey} style={{ ...ghostBtn, color: "#f87171" }}>
                      刪除
                    </button>
                  </div>
                </div>
              )}

              {(!keyStatus.has_key || showKeyForm) && (
                <form onSubmit={handleSaveKey} style={{ display: "flex", gap: "8px", flexWrap: "wrap" }}>
                  <input
                    type="password"
                    name="gemini-api-key"
                    autoComplete="off"
                    spellCheck={false}
                    placeholder="貼上你的 Gemini API Key（AIza 開頭）"
                    value={keyInput}
                    onChange={(e) => setKeyInput(e.target.value)}
                    style={{ ...input, flex: 1, minWidth: "220px" }}
                  />
                  <button type="submit" disabled={savingKey || !keyInput.trim()} style={{ ...primaryBtn, opacity: savingKey || !keyInput.trim() ? 0.5 : 1 }}>
                    {savingKey ? "驗證中…" : "驗證並儲存"}
                  </button>
                  {showKeyForm && (
                    <button type="button" onClick={() => setShowKeyForm(false)} style={ghostBtn}>
                      取消
                    </button>
                  )}
                </form>
              )}
            </>
          )}

          {/* 後端還沒好時也把欄位畫出來（停用），讓大家知道會長什麼樣子 */}
          {keyUnavailable && (
            <div style={{ display: "flex", gap: "8px", marginTop: "14px", flexWrap: "wrap" }}>
              <input type="password" disabled placeholder="貼上你的 Gemini API Key（AIza 開頭）" style={{ ...input, flex: 1, minWidth: "220px", cursor: "not-allowed" }} />
              <button type="button" disabled style={{ ...primaryBtn, opacity: 0.4, cursor: "not-allowed" }}>
                驗證並儲存
              </button>
            </div>
          )}

          {keyMsg && <p style={note(keyMsg.ok)}>{keyMsg.text}</p>}
        </section>

        <Link href="/student/courses" style={{ fontSize: "13px", color: "#38bdf8", textDecoration: "none" }}>
          ← 回到我的課程
        </Link>
      </main>
    </div>
  );
}
