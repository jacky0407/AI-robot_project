"use client";

// 教師後台的頁面切換（深色主題的頁面使用）

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { createClient } from "@/utils/supabase/client";
import { FirstUsePrivacyDialog } from "@/components/PrivacyNotice";

const PAGES: { href: string; label: string; also?: string[] }[] = [
  // 學習監控、待複核、成員、邀請碼、模組管理、資料匯出都在課程裡；複核詳情頁（/admin/review/[id]）也算在課程管理
  { href: "/admin/courses", label: "課程管理", also: ["/admin/review"] },
  { href: "/admin/tools", label: "建立機器人" },
  { href: "/admin/tool-requests", label: "自主探索申請" },
  { href: "/admin/accounts", label: "帳號管理" },
];

// 只有教授看得到（#90 操作紀錄；後端也只給 owner）
const OWNER_PAGES: typeof PAGES = [{ href: "/admin/audit", label: "操作紀錄" }];

export default function AdminNav() {
  const pathname = usePathname();
  const [isOwner, setIsOwner] = useState(false);

  useEffect(() => {
    let active = true;
    const supabase = createClient();
    // profiles 的 RLS 只讓本人讀自己那一列
    supabase.auth.getUser().then(async ({ data }) => {
      if (!data.user) return;
      const { data: profile } = await supabase.from("profiles").select("role").eq("id", data.user.id).single();
      if (active) setIsOwner(profile?.role === "owner");
    });
    return () => {
      active = false;
    };
  }, []);

  return (
    <nav style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: "4px", padding: "4px", borderRadius: "10px", backgroundColor: "rgba(255,255,255,0.06)" }}>
      {/* 個資提醒（PRI-001）：第一次進後台時說明一次 */}
      <FirstUsePrivacyDialog scope="staff" />
      {/* 回到 /portal 選擇其他系統（例如 XR 訪談訓練） */}
      <Link
        href="/portal"
        title="回到系統選擇頁"
        style={{ padding: "6px 10px", borderRadius: "8px", fontSize: "13px", textDecoration: "none", whiteSpace: "nowrap", color: "#94a3b8", borderRight: "1px solid rgba(255,255,255,0.1)", marginRight: "4px" }}
      >
        ⌂ 選擇系統
      </Link>
      {[...PAGES, ...(isOwner ? OWNER_PAGES : [])].map((p) => {
        const active = [p.href, ...(p.also ?? [])].some((prefix) => pathname.startsWith(prefix));
        return (
          <Link
            key={p.href}
            href={p.href}
            style={{
              padding: "6px 14px",
              borderRadius: "8px",
              fontSize: "13px",
              fontWeight: "bold",
              textDecoration: "none",
              whiteSpace: "nowrap",
              backgroundColor: active ? "#16a34a" : "transparent",
              color: active ? "#fff" : "#94a3b8",
            }}
          >
            {p.label}
          </Link>
        );
      })}
    </nav>
  );
}
