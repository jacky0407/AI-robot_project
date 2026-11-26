"use client";

import { TITLE_FONT } from "../../theme";

// 作答區：前面關卡傳過來的資料（#84）
// 前面每一關通過（或最新）那次作答，依老師設定的欄位整理出的重點；標出第幾關、第幾次作答、AI 整理或老師確認。
// 後端：GET /api/practice/steps/{step_id}/forwarded

import { useEffect, useState } from "react";
import { apiFetch } from "@/utils/api";

type Forwarded = {
  step_id: string;
  step_order: number;
  step_title: string;
  attempt_number: number;
  source: "ai" | "teacher" | "mixed";
  fields: { key: string; label: string; value: string; source: "ai" | "teacher" }[];
};

const SOURCE_LABEL: Record<string, string> = { ai: "AI 整理", teacher: "老師確認", mixed: "部分老師確認" };

export default function ForwardedPanel({ stepId, refreshKey }: { stepId: string; refreshKey: number }) {
  const [items, setItems] = useState<Forwarded[]>([]);

  useEffect(() => {
    let active = true;
    apiFetch<{ data: Forwarded[] }>(`/api/practice/steps/${encodeURIComponent(stepId)}/forwarded`).then(
      (res) => active && setItems(res.data),
      () => active && setItems([]) // 讀不到就不顯示，不擋作答
    );
    return () => {
      active = false;
    };
  }, [stepId, refreshKey]);

  if (items.length === 0) return null;

  return (
    <div style={{ backgroundColor: "#fbf8f1", borderRadius: "6px", padding: "16px", border: "1px solid #e4dccb" }}>
      <h3 style={{ fontFamily: TITLE_FONT, fontSize: "14px", fontWeight: "bold", margin: "0 0 8px" }}>前面關卡傳來的資料</h3>
      <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
        {items.map((s) => (
          <div key={s.step_id}>
            <div style={{ fontSize: "12px", color: "#6b6255", marginBottom: "4px" }}>
              關卡 {s.step_order}「{s.step_title}」・第 {s.attempt_number} 次作答・{SOURCE_LABEL[s.source] ?? s.source}
            </div>
            <dl style={{ margin: 0, display: "grid", gridTemplateColumns: "auto 1fr", gap: "4px 10px", fontSize: "13px" }}>
              {s.fields.map((f) => (
                <div key={f.key} style={{ display: "contents" }}>
                  <dt style={{ color: "#3d372e", fontWeight: "bold" }}>{f.label}</dt>
                  <dd style={{ margin: 0, color: "#26221c", whiteSpace: "pre-wrap" }}>
                    {f.value}
                    {f.source === "teacher" && <span style={{ marginLeft: "6px", fontSize: "11px", color: "#1f6b47" }}>老師確認</span>}
                  </dd>
                </div>
              ))}
            </dl>
          </div>
        ))}
      </div>
    </div>
  );
}
