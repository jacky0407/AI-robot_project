"use client";

// 學生端的兩種學習模式切換（需求書：正式課程模式／自主探索模式）
//   正式課程：用邀請碼加入課程，練習老師指派的模組，不限次數，由老師最終判定
//   自主探索：自己申請機器人，老師核准後在期限內使用，每天有次數上限，AI 回饋僅供參考

import Link from "next/link";
import { usePathname } from "next/navigation";

const MODES = [
  { href: "/student/courses", label: "正式課程模式", hint: "加入老師的課程" },
  { href: "/student/explore", label: "自主探索模式", hint: "自己申請機器人練習" },
];

export default function StudentModeTabs() {
  const pathname = usePathname();
  return (
    <nav style={{ display: "flex", gap: "4px", padding: "4px", borderRadius: "10px", backgroundColor: "rgba(255,255,255,0.06)" }}>
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
              backgroundColor: active ? "#2563eb" : "transparent",
              color: active ? "#fff" : "#94a3b8",
            }}
          >
            {m.label}
          </Link>
        );
      })}
    </nav>
  );
}
