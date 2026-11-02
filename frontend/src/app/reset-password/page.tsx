"use client";

// 重設密碼：從重設密碼信的連結進來（經 /auth/callback 換成 session），在這裡設定新密碼

import { useEffect, useState } from "react";
import Link from "next/link";
import { createClient } from "@/utils/supabase/client";
import { authStyles as s } from "../forgot-password/styles";

const MIN_PASSWORD_LENGTH = 6; // Supabase Auth 預設的最短密碼長度

export default function ResetPasswordPage() {
  const [supabase] = useState(() => createClient());
  const [hasSession, setHasSession] = useState<boolean | null>(null);
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [saving, setSaving] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    supabase.auth.getUser().then(({ data: { user } }) => setHasSession(Boolean(user)));
  }, [supabase]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    if (password.length < MIN_PASSWORD_LENGTH) {
      setError(`密碼至少需要 ${MIN_PASSWORD_LENGTH} 個字元`);
      return;
    }
    if (password !== confirmPassword) {
      setError("兩次輸入的密碼不一致");
      return;
    }

    setSaving(true);
    const { error } = await supabase.auth.updateUser({ password });
    setSaving(false);
    if (error) {
      setError(`更新失敗：${error.message}`);
      return;
    }
    // 改完密碼後登出，請使用者用新密碼重新登入
    await supabase.auth.signOut();
    setDone(true);
  };

  return (
    <div style={s.page}>
      <div style={s.card}>
        <h1 style={s.title}>設定新密碼</h1>

        {hasSession === null && <p style={s.hint}>確認連結中…</p>}

        {hasSession === false && !done && (
          <p style={s.error}>重設連結已失效或已被使用，請回到「忘記密碼」重新寄送一次。</p>
        )}

        {done && <p style={s.success}>密碼已更新，請用新密碼重新登入。</p>}

        {hasSession && !done && (
          <form onSubmit={handleSubmit} style={s.form}>
            <input
              type="password"
              required
              autoFocus
              autoComplete="new-password"
              placeholder="新密碼"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              style={s.input}
            />
            <input
              type="password"
              required
              autoComplete="new-password"
              placeholder="再輸入一次新密碼"
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              style={s.input}
            />
            {error && <p style={s.error}>{error}</p>}
            <button type="submit" disabled={saving} style={{ ...s.button, opacity: saving ? 0.5 : 1 }}>
              {saving ? "更新中…" : "更新密碼"}
            </button>
          </form>
        )}

        {hasSession === false && !done ? (
          <Link href="/forgot-password" style={s.link}>
            重新寄送重設密碼信 →
          </Link>
        ) : (
          <Link href="/" style={s.link}>
            ← 返回登入
          </Link>
        )}
      </div>
    </div>
  );
}
