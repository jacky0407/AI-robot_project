// /portal：登入後選擇要進入哪個系統（本平台、XR 小組的系統，之後可能還有其他系統）
// 系統清單在 ./systems.ts。這頁在伺服器端判斷登入狀態與身分，不需要 middleware 也能擋住未登入的人。

import Link from "next/link";
import { redirect } from "next/navigation";
import { createClient } from "@/utils/supabase/server";
import SignOutButton from "./SignOutButton";
import { systemsFor } from "./systems";

export const metadata = { title: "選擇系統｜學前特教 AI 培訓平台" };

export default async function PortalPage() {
  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) redirect("/");

  const { data: profile } = await supabase
    .from("profiles")
    .select("full_name, role, registration_completed")
    .eq("id", user.id)
    .single();
  if (!profile?.registration_completed) redirect("/onboarding");

  const portalUser = { isStaff: profile.role === "owner" || profile.role === "assistant" };
  const displayName = profile.full_name || user.email;

  return (
    <div style={{ minHeight: "100vh", backgroundColor: "#0f172a", color: "#f8fafc", fontFamily: "sans-serif" }}>
      <header
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          padding: "14px 28px",
          borderBottom: "1px solid rgba(255,255,255,0.1)",
          backgroundColor: "#1e293b",
        }}
      >
        <span style={{ fontSize: "14px", color: "#cbd5e1" }}>
          {displayName}
          <span style={{ marginLeft: "8px", fontSize: "11px", padding: "2px 8px", borderRadius: "9999px", backgroundColor: portalUser.isStaff ? "#16a34a" : "#2563eb", color: "#fff" }}>
            {portalUser.isStaff ? "教師／人員" : "學生"}
          </span>
        </span>
        <SignOutButton />
      </header>

      <main style={{ maxWidth: "960px", margin: "0 auto", padding: "56px 24px" }}>
        <div style={{ textAlign: "center", marginBottom: "40px" }}>
          <h1 style={{ fontSize: "28px", fontWeight: "bold", margin: "0 0 8px 0" }}>請選擇要進入的系統</h1>
          <p style={{ fontSize: "14px", color: "#94a3b8", margin: 0 }}>同一組帳號可以使用以下所有系統，不用重新登入</p>
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))", gap: "20px" }}>
          {systemsFor(portalUser).map((sys) => {
            const enabled = sys.enabled ? sys.enabled() : true;
            const card = (
              <div
                style={{
                  height: "100%",
                  boxSizing: "border-box",
                  padding: "24px",
                  borderRadius: "20px",
                  backgroundColor: "#1e293b",
                  border: `2px solid ${enabled ? sys.accent : "rgba(255,255,255,0.08)"}`,
                  opacity: enabled ? 1 : 0.5,
                  cursor: enabled ? "pointer" : "not-allowed",
                  display: "flex",
                  flexDirection: "column",
                  gap: "10px",
                }}
              >
                <div style={{ width: "48px", height: "48px", borderRadius: "14px", backgroundColor: "rgba(255,255,255,0.08)", display: "flex", alignItems: "center", justifyContent: "center", fontSize: "24px" }}>
                  {sys.icon}
                </div>
                <span style={{ fontSize: "18px", fontWeight: "bold", color: "#fff" }}>{sys.name}</span>
                <span style={{ fontSize: "13px", color: "#94a3b8", lineHeight: 1.6 }}>{sys.description}</span>
                <span style={{ marginTop: "auto", fontSize: "13px", fontWeight: "bold", color: enabled ? "#38bdf8" : "#64748b" }}>
                  {enabled ? "進入 →" : sys.disabledReason || "尚未開放"}
                </span>
              </div>
            );

            if (enabled && sys.fullPageLoad) {
              return (
                <a key={sys.id} href={sys.href} style={{ textDecoration: "none" }}>
                  {card}
                </a>
              );
            }
            return enabled ? (
              <Link key={sys.id} href={sys.href} style={{ textDecoration: "none" }}>
                {card}
              </Link>
            ) : (
              <div key={sys.id} aria-disabled="true">
                {card}
              </div>
            );
          })}
        </div>
      </main>
    </div>
  );
}
