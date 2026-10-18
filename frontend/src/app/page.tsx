"use client";

import { Suspense, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { createClient } from "@/utils/supabase/client";

type RoleType = "student" | "teacher" | null;

// /auth/callback 失敗時會帶 ?error=xxx 導回首頁
const CALLBACK_ERRORS: Record<string, string> = {
  link_expired: "驗證連結已失效或已被使用。若你已點過驗證信，請直接登入；否則請重新註冊以取得新的驗證信。",
  profile: "無法讀取使用者資料，請稍後再試或聯絡管理員",
  auth: "登入驗證失敗，請再試一次",
};

// 登入成功後的導向頁面（依 profiles.role 決定）；路徑改這裡即可
const ROUTES = {
  staff: "/admin/tools", // owner / assistant
  student: "/student/modules", // 其他（學生）
  onboarding: "/onboarding", // 第一次登入，尚未完成註冊
};

// useSearchParams 需要包在 Suspense 裡，頁面其他部分才能預先渲染
export default function Home() {
  return (
    <Suspense fallback={null}>
      <HomeContent />
    </Suspense>
  );
}

function HomeContent() {
  const router = useRouter();
  const [selectedRole, setSelectedRole] = useState<RoleType>(null);
  const [isLoginMode, setIsLoginMode] = useState(true);

  // 表單狀態
  const [formData, setFormData] = useState({
    name: "",
    email: "",
    password: "",
  });

  // Google 登入相關狀態
  const supabase = createClient();
  const [googleLoading, setGoogleLoading] = useState(false);
  const [authError, setAuthError] = useState("");
  const [infoMsg, setInfoMsg] = useState("");
  const [submitting, setSubmitting] = useState(false);

  // 依資料庫 profiles.role 導向（身分以資料庫為準，不是以畫面上選的卡片為準）
  const redirectByRole = async (userId: string) => {
    const { data: profile, error } = await supabase
      .from("profiles")
      .select("role, registration_completed")
      .eq("id", userId)
      .single();

    if (error || !profile) {
      setAuthError("無法讀取使用者角色權限");
      return false;
    }

    const isStaff = profile.role === "owner" || profile.role === "assistant";

    // 選了「教師/人員」卻沒有教師權限：登出並提示
    if (selectedRole === "teacher" && !isStaff) {
      await supabase.auth.signOut();
      setAuthError("此帳號沒有教師/工作人員權限，請改用學生身分登入");
      return false;
    }

    // 還沒完成註冊（第一次登入）：先補完基本資料與同意條款
    if (!profile.registration_completed) {
      router.push(ROUTES.onboarding);
      return true;
    }

    router.push(isStaff ? ROUTES.staff : ROUTES.student);
    return true;
  };

  // 偵測是否已有登入狀態（不會自動跳轉，讓使用者自己選擇繼續或登出）
  const [existingUser, setExistingUser] = useState<{ id: string; email: string } | null>(null);

  useEffect(() => {
    supabase.auth.getUser().then(({ data: { user } }) => {
      if (user) setExistingUser({ id: user.id, email: user.email ?? "" });
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleContinue = async () => {
    if (existingUser) await redirectByRole(existingUser.id);
  };

  const handleSignOut = async () => {
    await supabase.auth.signOut();
    setExistingUser(null);
  };

  // 若從 /auth/callback 失敗導回（/?error=auth），顯示錯誤訊息
  // 直接由網址推導，不在 effect 裡 setState（避免多一次 render）
  const searchParams = useSearchParams();
  const callbackError = CALLBACK_ERRORS[searchParams.get("error") ?? ""] ?? "";
  const shownError = authError || callbackError;

  // Google 登入：會整頁跳轉到 Google，之後由 /auth/callback 接手
  const handleGoogleLogin = async () => {
    setGoogleLoading(true);
    setAuthError("");

    const { error } = await supabase.auth.signInWithOAuth({
      provider: "google",
      options: {
        redirectTo: `${window.location.origin}/auth/callback`,
        queryParams: { prompt: "select_account" }, // 每次都讓使用者選帳號
      },
    });

    if (error) {
      setAuthError("Google 登入失敗：" + error.message);
      setGoogleLoading(false);
    }
  };

  const handleSelectRole = (role: RoleType) => {
    setSelectedRole(role);
    setIsLoginMode(true);
  };

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const { name, value } = e.target;
    setFormData((prev) => ({ ...prev, [name]: value }));
  };

  // 信箱登入 / 註冊（原本是直接切換畫面的模擬版本，現在改為串接 Supabase）
  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSubmitting(true);
    setAuthError("");
    setInfoMsg("");

    // ---- 登入 ----
    if (isLoginMode) {
      const { data, error } = await supabase.auth.signInWithPassword({
        email: formData.email,
        password: formData.password,
      });

      if (error || !data.user) {
        const msg = error?.message ?? "請再試一次";
        setAuthError(
          "登入失敗：" +
            msg +
            (msg.includes("Invalid login credentials")
              ? "（信箱或密碼錯誤；若此信箱是用 Google 註冊的，請改按上方「使用 Google 帳號登入」）"
              : "")
        );
        setSubmitting(false);
        return;
      }

      const ok = await redirectByRole(data.user.id);
      if (!ok) setSubmitting(false);
      return;
    }

    // ---- 註冊 ----（不傳 role：新帳號一律是學生，教師由後台手動升級）
    const { data, error } = await supabase.auth.signUp({
      email: formData.email,
      password: formData.password,
      options: {
        data: { full_name: formData.name }, // 職業在 /onboarding 填寫
        emailRedirectTo: `${window.location.origin}/auth/callback`,
      },
    });

    if (error) {
      setAuthError(
        error.message.toLowerCase().includes("already registered")
          ? "此信箱已經註冊過。若當初是用 Google 註冊，請按上方「使用 Google 帳號」登入。"
          : "註冊失敗：" + error.message
      );
      setSubmitting(false);
      return;
    }

    // 開啟信箱驗證時，重複註冊不會回傳錯誤，而是 identities 為空陣列
    if (data.user && data.user.identities && data.user.identities.length === 0) {
      setAuthError("此信箱已經註冊過。若當初是用 Google 註冊，請按上方「使用 Google 帳號」登入。");
      setSubmitting(false);
      return;
    }

    if (data.session && data.user) {
      // 專案有關閉「Confirm email」時，註冊後直接就是登入狀態
      const ok = await redirectByRole(data.user.id);
      if (!ok) setSubmitting(false);
      return;
    }

    // 需要信箱驗證的情況
    setInfoMsg("註冊成功！請到信箱點擊驗證連結，完成後再回來登入。");
    setIsLoginMode(true);
    setSubmitting(false);
  };

  return (
    <div style={{ minHeight: "100vh", display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", backgroundColor: "#0f172a", color: "#f8fafc", fontFamily: "sans-serif", padding: "24px" }}>
      {/* 平台標題 */}
      <div style={{ textAlign: "center", marginBottom: "32px" }}>
        <span style={{ padding: "4px 12px", fontSize: "12px", borderRadius: "9999px", backgroundColor: "rgba(255,255,255,0.08)", border: "1px solid rgba(255,255,255,0.15)", color: "#a5b4fc" }}>
          學前特教與早療・AI 培訓平台
        </span>
        <h1 style={{ fontSize: "32px", fontWeight: "bold", margin: "16px 0 8px 0", color: "#ffffff" }}>
          專業能力情境化訓練系統
        </h1>
        <p style={{ fontSize: "14px", color: "#94a3b8", margin: 0 }}>
          {selectedRole === null ? "請選擇您的身分通道開始登入" : `目前入口：${selectedRole === "student" ? "學生登入" : "教師/工作人員登入"}`}
        </p>
      </div>

      {/* 已登入提示：可繼續使用或登出（測試其他帳號時請先登出） */}
      {existingUser && (
        <div style={{ width: "100%", maxWidth: "380px", marginBottom: "16px", padding: "12px 14px", borderRadius: "8px", backgroundColor: "rgba(56, 189, 248, 0.12)", border: "1px solid rgba(56, 189, 248, 0.4)", color: "#bae6fd", fontSize: "13px" }}>
          <div style={{ marginBottom: "8px" }}>目前已登入：{existingUser.email}</div>
          <div style={{ display: "flex", gap: "8px" }}>
            <button
              type="button"
              onClick={handleContinue}
              style={{ padding: "6px 12px", borderRadius: "6px", border: "none", backgroundColor: "#2563eb", color: "#fff", fontSize: "12px", fontWeight: "bold", cursor: "pointer" }}
            >
              繼續使用
            </button>
            <button
              type="button"
              onClick={handleSignOut}
              style={{ padding: "6px 12px", borderRadius: "6px", border: "1px solid rgba(255,255,255,0.25)", backgroundColor: "transparent", color: "#e2e8f0", fontSize: "12px", cursor: "pointer" }}
            >
              登出
            </button>
          </div>
        </div>
      )}

      {/* Google 登入錯誤訊息 */}
      {shownError && (
        <div style={{ width: "100%", maxWidth: "380px", marginBottom: "16px", padding: "10px 14px", borderRadius: "8px", backgroundColor: "rgba(239, 68, 68, 0.15)", border: "1px solid rgba(239, 68, 68, 0.4)", color: "#fca5a5", fontSize: "13px" }}>
          {shownError}
        </div>
      )}

      {/* 一般提示訊息（例如註冊後請驗證信箱） */}
      {infoMsg && (
        <div style={{ width: "100%", maxWidth: "380px", marginBottom: "16px", padding: "10px 14px", borderRadius: "8px", backgroundColor: "rgba(34, 197, 94, 0.15)", border: "1px solid rgba(34, 197, 94, 0.4)", color: "#86efac", fontSize: "13px" }}>
          {infoMsg}
        </div>
      )}

      {/* 狀態 A：雙卡片選擇 */}
      {selectedRole === null && (
        <div style={{ display: "flex", gap: "24px", flexWrap: "wrap", justifyContent: "center" }}>
          {/* 學生卡片 */}
          <div
            onClick={() => handleSelectRole("student")}
            style={{ width: "220px", height: "260px", backgroundColor: "#1e3a8a", border: "2px solid #3b82f6", borderRadius: "24px", display: "flex", flexDirection: "column", justifyContent: "center", alignItems: "center", cursor: "pointer", transition: "transform 0.2s" }}
          >
            <div style={{ width: "50px", height: "50px", borderRadius: "16px", backgroundColor: "rgba(255,255,255,0.15)", display: "flex", alignItems: "center", justifyContent: "center", marginBottom: "16px" }}>
              🎓
            </div>
            <span style={{ fontSize: "24px", fontWeight: "bold", color: "#fff" }}>學生</span>
            <span style={{ fontSize: "12px", color: "#93c5fd", marginTop: "8px" }}>IEP 逐步撰寫練習</span>
          </div>

          {/* 教師卡片 */}
          <div
            onClick={() => handleSelectRole("teacher")}
            style={{ width: "220px", height: "260px", backgroundColor: "#14532d", border: "2px solid #22c55e", borderRadius: "24px", display: "flex", flexDirection: "column", justifyContent: "center", alignItems: "center", cursor: "pointer", transition: "transform 0.2s" }}
          >
            <div style={{ width: "50px", height: "50px", borderRadius: "16px", backgroundColor: "rgba(255,255,255,0.15)", display: "flex", alignItems: "center", justifyContent: "center", marginBottom: "16px" }}>
              🧑‍🏫
            </div>
            <span style={{ fontSize: "24px", fontWeight: "bold", color: "#fff" }}>教師/人員</span>
            <span style={{ fontSize: "12px", color: "#86efac", marginTop: "8px" }}>後台複核・分析</span>
          </div>
        </div>
      )}

      {/* 狀態 B：信箱登入/註冊表單 */}
      {selectedRole !== null && (
        <div style={{ width: "100%", maxWidth: "380px", backgroundColor: "#1e293b", borderRadius: "24px", border: "1px solid rgba(255,255,255,0.15)", padding: "28px" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "20px" }}>
            <span style={{ fontSize: "12px", padding: "3px 10px", borderRadius: "9999px", backgroundColor: selectedRole === "student" ? "#2563eb" : "#16a34a", color: "#fff" }}>
              ● {selectedRole === "student" ? "學生登入" : "教師登入"}
            </span>
            <button
              onClick={() => setSelectedRole(null)}
              style={{ fontSize: "12px", color: "#94a3b8", background: "none", border: "none", cursor: "pointer" }}
            >
              切換身分 ↺
            </button>
          </div>

          {/* 登入 / 註冊 切換頁籤 */}
          <div style={{ display: "flex", gap: "4px", padding: "4px", borderRadius: "10px", backgroundColor: "rgba(255,255,255,0.06)", marginBottom: "16px" }}>
            {[
              { label: "登入", value: true },
              { label: "註冊", value: false },
            ].map((tab) => (
              <button
                key={tab.label}
                type="button"
                onClick={() => {
                  setIsLoginMode(tab.value);
                  setAuthError("");
                  setInfoMsg("");
                }}
                style={{
                  flex: 1,
                  padding: "8px 0",
                  borderRadius: "8px",
                  border: "none",
                  fontSize: "13px",
                  fontWeight: "bold",
                  cursor: "pointer",
                  backgroundColor: isLoginMode === tab.value ? (selectedRole === "student" ? "#2563eb" : "#16a34a") : "transparent",
                  color: isLoginMode === tab.value ? "#fff" : "#94a3b8",
                }}
              >
                {tab.label}
              </button>
            ))}
          </div>

          <h2 style={{ fontSize: "18px", fontWeight: "bold", margin: "0 0 16px 0" }}>
            {isLoginMode ? "登入您的帳號" : "註冊新帳號"}
          </h2>

          {!isLoginMode && selectedRole === "teacher" && (
            <p style={{ fontSize: "12px", color: "#fbbf24", margin: "0 0 16px 0", lineHeight: 1.5 }}>
              教師／工作人員帳號註冊後，需由管理員開通權限才能進入後台。
            </p>
          )}

          {/* Google 登入按鈕 */}
          <button
            type="button"
            onClick={handleGoogleLogin}
            disabled={googleLoading}
            style={{ width: "100%", display: "flex", alignItems: "center", justifyContent: "center", gap: "8px", padding: "10px", borderRadius: "8px", border: "1px solid rgba(255,255,255,0.2)", backgroundColor: "#fff", color: "#1f2937", fontWeight: "bold", fontSize: "14px", cursor: googleLoading ? "not-allowed" : "pointer", opacity: googleLoading ? 0.5 : 1 }}
          >
            <svg width="18" height="18" viewBox="0 0 48 48" aria-hidden="true">
              <path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z" />
              <path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z" />
              <path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z" />
              <path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z" />
            </svg>
            {googleLoading ? "跳轉中..." : isLoginMode ? "使用 Google 帳號登入" : "使用 Google 帳號註冊"}
          </button>

          <div style={{ display: "flex", alignItems: "center", gap: "12px", margin: "16px 0", fontSize: "12px", color: "#64748b" }}>
            <div style={{ flex: 1, height: "1px", backgroundColor: "rgba(255,255,255,0.15)" }} />
            或使用電子郵件
            <div style={{ flex: 1, height: "1px", backgroundColor: "rgba(255,255,255,0.15)" }} />
          </div>

          <form onSubmit={handleSubmit} style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
            {!isLoginMode && (
              <input
                type="text"
                name="name"
                required
                placeholder="姓名 / 稱呼"
                value={formData.name}
                onChange={handleChange}
                style={{ padding: "10px 12px", borderRadius: "8px", border: "1px solid rgba(255,255,255,0.2)", backgroundColor: "rgba(255,255,255,0.05)", color: "#fff", fontSize: "14px", outline: "none" }}
              />
            )}

            <input
              type="email"
              name="email"
              required
              placeholder="電子信箱 (Email)"
              value={formData.email}
              onChange={handleChange}
              style={{ padding: "10px 12px", borderRadius: "8px", border: "1px solid rgba(255,255,255,0.2)", backgroundColor: "rgba(255,255,255,0.05)", color: "#fff", fontSize: "14px", outline: "none" }}
            />

            <input
              type="password"
              name="password"
              required
              placeholder="密碼"
              value={formData.password}
              onChange={handleChange}
              style={{ padding: "10px 12px", borderRadius: "8px", border: "1px solid rgba(255,255,255,0.2)", backgroundColor: "rgba(255,255,255,0.05)", color: "#fff", fontSize: "14px", outline: "none" }}
            />

            <button
              type="submit"
              disabled={submitting}
              style={{ marginTop: "8px", opacity: submitting ? 0.5 : 1, padding: "10px", borderRadius: "8px", border: "none", backgroundColor: selectedRole === "student" ? "#2563eb" : "#16a34a", color: "#fff", fontWeight: "bold", fontSize: "14px", cursor: "pointer" }}
            >
              {submitting ? "處理中..." : isLoginMode ? "登入並進入系統" : "建立帳號"}
            </button>
          </form>

          <div style={{ marginTop: "16px", textAlign: "center", fontSize: "12px", color: "#94a3b8" }}>
            <button
              onClick={() => setIsLoginMode(!isLoginMode)}
              style={{ background: "none", border: "none", color: "#38bdf8", cursor: "pointer", textDecoration: "underline" }}
            >
              {isLoginMode ? "還沒有帳號？前往註冊 →" : "← 已有帳號？返回登入"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}