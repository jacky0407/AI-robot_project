"use client";

// 忘記密碼：寄出重設密碼信
// 信中的連結會回到 /auth/callback?next=/reset-password，換到 session 後再到 /reset-password 設新密碼

import { Suspense, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { createClient } from "@/utils/supabase/client";
import { authStyles as s } from "./styles";

export default function ForgotPasswordPage() {
  return (
    <Suspense fallback={null}>
      <ForgotPasswordForm />
    </Suspense>
  );
}

function ForgotPasswordForm() {
  const searchParams = useSearchParams();
  const [email, setEmail] = useState(searchParams.get("email") ?? "");
  const [sending, setSending] = useState(false);
  const [sent, setSent] = useState(false);
  const [error, setError] = useState("");

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSending(true);
    setError("");

    const { error } = await createClient().auth.resetPasswordForEmail(email.trim(), {
      redirectTo: `${window.location.origin}/auth/callback?next=/reset-password`,
    });

    setSending(false);
    if (error) {
      setError(
        error.status === 429
          ? "寄信太頻繁，請稍候一分鐘再試。"
          : `寄送失敗：${error.message}`
      );
      return;
    }
    // 不論信箱是否存在都顯示相同訊息，避免被拿來探測哪些信箱有註冊
    setSent(true);
  };

  return (
    <div style={s.page}>
      <div style={s.card}>
        <h1 style={s.title}>忘記密碼</h1>

        {sent ? (
          <>
            <p style={s.success}>
              如果 <b>{email}</b> 有註冊帳號，我們已寄出重設密碼信。請到信箱點擊信中的連結設定新密碼（也請檢查垃圾郵件匣）。
            </p>
            <p style={s.hint}>若當初是用 Google 註冊，沒有本站密碼，請回登入頁改用「使用 Google 帳號登入」。</p>
          </>
        ) : (
          <form onSubmit={handleSubmit} style={s.form}>
            <p style={s.hint}>輸入註冊時使用的電子信箱，我們會寄送重設密碼的連結給你。</p>
            <input
              type="email"
              required
              autoFocus
              placeholder="電子信箱 (Email)"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              style={s.input}
            />
            {error && <p style={s.error}>{error}</p>}
            <button type="submit" disabled={sending} style={{ ...s.button, opacity: sending ? 0.5 : 1 }}>
              {sending ? "寄送中…" : "寄送重設密碼信"}
            </button>
          </form>
        )}

        <Link href="/" style={s.link}>
          ← 返回登入
        </Link>
      </div>
    </div>
  );
}
