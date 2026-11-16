"use client";

// 課程管理（列表、詳細頁）共用的頁首：後台標籤、頁面切換、登出

import { useState } from "react";
import { useRouter } from "next/navigation";
import { createClient } from "@/utils/supabase/client";
import AdminNav from "../AdminNav";
import { smallBtn } from "./shared";

export default function AdminHeader() {
  const router = useRouter();
  const [supabase] = useState(() => createClient());

  const handleSignOut = async () => {
    await supabase.auth.signOut();
    router.push("/");
    router.refresh();
  };

  return (
    <header style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: "12px", padding: "14px 24px", backgroundColor: "#1e293b", borderBottom: "1px solid rgba(255,255,255,0.1)" }}>
      <div style={{ display: "flex", alignItems: "center", gap: "12px", flexWrap: "wrap" }}>
        <span style={{ backgroundColor: "#16a34a", color: "#fff", padding: "4px 8px", borderRadius: "6px", fontSize: "12px", fontWeight: "bold" }}>
          管理與複核後台
        </span>
        <AdminNav />
      </div>
      <button onClick={handleSignOut} style={{ ...smallBtn, padding: "6px 12px", flexShrink: 0 }}>
        登出
      </button>
    </header>
  );
}
