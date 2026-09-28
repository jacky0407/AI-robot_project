"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { createClient } from "@/utils/supabase/client";

// 職業選項（value 會存進 profiles.profession，可自行增減）
const PROFESSIONS = [
  { value: "preschool_teacher", label: "學前教師" },
  { value: "special_ed_teacher", label: "特教教師" },
  { value: "early_intervention", label: "早療專業人員" },
  { value: "student", label: "師資培育／相關科系學生" },
  { value: "other", label: "其他" },
];

const inputStyle: React.CSSProperties = {
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

export default function OnboardingPage() {
  const router = useRouter();
  const supabase = createClient();

  const [userId, setUserId] = useState("");
  const [email, setEmail] = useState("");
  const [fullName, setFullName] = useState("");
  const [profession, setProfession] = useState("preschool_teacher");
  const [agreeTerms, setAgreeTerms] = useState(false);
  const [agreePrivacy, setAgreePrivacy] = useState(false);
  const [ready, setReady] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [errorMsg, setErrorMsg] = useState("");

  // 載入目前登入者，並預填姓名（Google 會提供名字）
  useEffect(() => {
    const load = async () => {
      const {
        data: { user },
      } = await supabase.auth.getUser();

      if (!user) {
        router.replace("/");
        return;
      }

      setUserId(user.id);
      setEmail(user.email ?? "");

      const { data: profile } = await supabase
        .from("profiles")
        .select("full_name")
        .eq("id", user.id)
        .single();

      const meta = user.user_metadata ?? {};
      setFullName(profile?.full_name || meta.full_name || meta.name || "");
      setReady(true);
    };

    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const canSubmit = fullName.trim().length > 0 && agreeTerms && agreePrivacy && !submitting;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!canSubmit) return;

    setSubmitting(true);
    setErrorMsg("");

    // 由資料庫函式完成註冊：更新姓名、職業、同意時間，並標記註冊完成
    const { error } = await supabase.rpc("complete_registration", {
      p_full_name: fullName.trim(),
      p_profession: profession,
    });

    if (error) {
      setErrorMsg("儲存失敗：" + error.message);
      setSubmitting(false);
      return;
    }

    // 依身分導向
    const { data: profile } = await supabase
      .from("profiles")
      .select("role")
      .eq("id", userId)
      .single();

    const isStaff = profile?.role === "owner" || profile?.role === "assistant";
    router.push(isStaff ? "/admin/tools" : "/student/modules");
    router.refresh();
  };

  const handleSignOut = async () => {
    await supabase.auth.signOut();
    router.push("/");
    router.refresh();
  };

  if (!ready) {
    return (
      <div style={{ minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center", backgroundColor: "#0f172a", color: "#94a3b8", fontFamily: "sans-serif" }}>
        載入中...
      </div>
    );
  }

  return (
    <div style={{ minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center", backgroundColor: "#0f172a", color: "#f8fafc", fontFamily: "sans-serif", padding: "24px" }}>
      <div style={{ width: "100%", maxWidth: "420px", backgroundColor: "#1e293b", borderRadius: "24px", border: "1px solid rgba(255,255,255,0.15)", padding: "28px" }}>
        <span style={{ padding: "4px 12px", fontSize: "12px", borderRadius: "9999px", backgroundColor: "rgba(255,255,255,0.08)", border: "1px solid rgba(255,255,255,0.15)", color: "#a5b4fc" }}>
          學前特教與早療・AI 培訓平台
        </span>

        <h1 style={{ fontSize: "22px", fontWeight: "bold", margin: "16px 0 6px 0" }}>完成註冊</h1>
        <p style={{ fontSize: "13px", color: "#94a3b8", margin: "0 0 20px 0", lineHeight: 1.6 }}>
          已使用 <span style={{ color: "#bae6fd" }}>{email}</span> 建立帳號，請補充基本資料並同意相關條款後開始使用。
        </p>

        {errorMsg && (
          <div style={{ marginBottom: "16px", padding: "10px 14px", borderRadius: "8px", backgroundColor: "rgba(239, 68, 68, 0.15)", border: "1px solid rgba(239, 68, 68, 0.4)", color: "#fca5a5", fontSize: "13px" }}>
            {errorMsg}
          </div>
        )}

        <form onSubmit={handleSubmit} style={{ display: "flex", flexDirection: "column", gap: "14px" }}>
          <label style={{ display: "flex", flexDirection: "column", gap: "6px", fontSize: "13px", color: "#cbd5e1" }}>
            姓名 / 稱呼
            <input
              type="text"
              required
              value={fullName}
              onChange={(e) => setFullName(e.target.value)}
              placeholder="請輸入您的姓名"
              style={inputStyle}
            />
          </label>

          <label style={{ display: "flex", flexDirection: "column", gap: "6px", fontSize: "13px", color: "#cbd5e1" }}>
            職業 / 身分
            <select
              value={profession}
              onChange={(e) => setProfession(e.target.value)}
              style={{ ...inputStyle, backgroundColor: "#0f172a" }}
            >
              {PROFESSIONS.map((p) => (
                <option key={p.value} value={p.value}>
                  {p.label}
                </option>
              ))}
            </select>
          </label>

          <label style={{ display: "flex", alignItems: "flex-start", gap: "8px", fontSize: "13px", color: "#cbd5e1", cursor: "pointer" }}>
            <input
              type="checkbox"
              checked={agreeTerms}
              onChange={(e) => setAgreeTerms(e.target.checked)}
              style={{ marginTop: "3px" }}
            />
            我已閱讀並同意《服務條款》
          </label>

          <label style={{ display: "flex", alignItems: "flex-start", gap: "8px", fontSize: "13px", color: "#cbd5e1", cursor: "pointer" }}>
            <input
              type="checkbox"
              checked={agreePrivacy}
              onChange={(e) => setAgreePrivacy(e.target.checked)}
              style={{ marginTop: "3px" }}
            />
            我已閱讀並同意《隱私權政策》
          </label>

          <button
            type="submit"
            disabled={!canSubmit}
            style={{ marginTop: "6px", padding: "10px", borderRadius: "8px", border: "none", backgroundColor: "#2563eb", color: "#fff", fontWeight: "bold", fontSize: "14px", cursor: canSubmit ? "pointer" : "not-allowed", opacity: canSubmit ? 1 : 0.5 }}
          >
            {submitting ? "處理中..." : "完成註冊並進入系統"}
          </button>
        </form>

        <div style={{ marginTop: "16px", textAlign: "center" }}>
          <button
            type="button"
            onClick={handleSignOut}
            style={{ background: "none", border: "none", color: "#94a3b8", fontSize: "12px", cursor: "pointer", textDecoration: "underline" }}
          >
            取消並登出
          </button>
        </div>
      </div>
    </div>
  );
}
