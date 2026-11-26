"use client";

// 學生端的兩種學習模式切換（需求書：正式課程模式／自主探索模式）
//   正式課程：用邀請碼加入課程，練習老師指派的模組，不限次數，由老師最終判定
//   自主探索：自己申請模組，老師核准後在期限內使用，每天有次數上限，AI 回饋僅供參考

import Link from "next/link";
import { usePathname } from "next/navigation";

const MODES = [
  { href: "/student/courses", label: "正式課程模式", hint: "加入老師的課程" },
  { href: "/student/explore", label: "自主探索模式", hint: "自己申請模組練習" },
];

export default function StudentModeTabs() {
  const pathname = usePathname();
  return (
    <nav style={{ display: "flex", alignItems: "center", gap: "4px", padding: "4px", borderRadius: "10px", backgroundColor: "var(--t-surface-2)" }}>
      {/* 回到 /portal 選擇其他系統（例如 XR 訪談訓練） */}
      <Link
        href="/portal"
        title="回到系統選擇頁"
        style={{ padding: "6px 10px", borderRadius: "8px", fontSize: "13px", textDecoration: "none", color: "var(--t-text-muted)", borderRight: "1px solid var(--t-border)", marginRight: "4px" }}
      >
        ⌂ 選擇系統
      </Link>
      {MODES.map((m) => {
        const active = pathname.startsWith(m.href);
        return (
          <Link
            key={m.href}
            href={m.href}
            title={m.hint}
            style={{
              padding: "6px 14px",
              borderRadius: "8px",
              fontSize: "13px",
              fontWeight: "bold",
              textDecoration: "none",
              backgroundColor: active ? "var(--t-primary)" : "transparent",
              color: active ? "var(--t-on-primary)" : "var(--t-text-muted)",
            }}
          >
            {m.label}
          </Link>
        );
      })}
    </nav>
  );
}
