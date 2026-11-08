"use client";

// 教師後台的頁面切換（深色主題的頁面使用）

import Link from "next/link";
import { usePathname } from "next/navigation";

const PAGES: { href: string; label: string; also?: string[] }[] = [
  { href: "/admin/courses", label: "課程管理" },
  { href: "/admin/modules", label: "模組管理" },
  { href: "/admin/tool-requests", label: "機器人申請" },
  // 複核詳情頁（/admin/review/[id]）是從這頁點進去的，也算在這一頁
  { href: "/admin/tools", label: "機器人與複核", also: ["/admin/review"] },
];

export default function AdminNav() {
  const pathname = usePathname();
  return (
    <nav style={{ display: "flex", alignItems: "center", gap: "4px", padding: "4px", borderRadius: "10px", backgroundColor: "rgba(255,255,255,0.06)" }}>
      {/* 回到 /portal 選擇其他系統（例如 XR 訪談訓練） */}
      <Link
        href="/portal"
        title="回到系統選擇頁"
        style={{ padding: "6px 10px", borderRadius: "8px", fontSize: "13px", textDecoration: "none", color: "#94a3b8", borderRight: "1px solid rgba(255,255,255,0.1)", marginRight: "4px" }}
      >
        ⌂ 選擇系統
      </Link>
      {PAGES.map((p) => {
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
