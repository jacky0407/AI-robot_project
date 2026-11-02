"use client";

// 教師後台的頁面切換（深色主題的頁面使用）

import Link from "next/link";
import { usePathname } from "next/navigation";

const PAGES = [
  { href: "/admin/courses", label: "課程管理" },
  { href: "/admin/tool-requests", label: "機器人申請" },
  { href: "/admin/tools", label: "機器人與複核" },
];

export default function AdminNav() {
  const pathname = usePathname();
  return (
    <nav style={{ display: "flex", gap: "4px", padding: "4px", borderRadius: "10px", backgroundColor: "rgba(255,255,255,0.06)" }}>
      {PAGES.map((p) => {
        const active = pathname.startsWith(p.href);
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
