"use client";

// 複核頁：這一關要傳給後面關卡的欄位（#84）
// 左邊是 AI 整理的原始內容（不會被修改），右邊是老師確認的版本；老師有填的以老師為準，AI 之後也不會覆蓋。
// 後端：PUT /api/review/submissions/{id}/forward

import { useState } from "react";
import { ApiError, apiFetch } from "@/utils/api";

export type ForwardDetail = {
  fields: { key: string; label: string; description: string; ai_value: string | null; teacher_value: string | null }[];
  extracted_at: string | null;
  edited_at: string | null;
};

const card: React.CSSProperties = { backgroundColor: "#1e293b", borderRadius: "12px", padding: "20px", border: "1px solid rgba(255,255,255,0.1)" };
const area: React.CSSProperties = { width: "100%", boxSizing: "border-box", padding: "8px", borderRadius: "6px", border: "1px solid rgba(255,255,255,0.2)", backgroundColor: "#0f172a", color: "#fff", fontSize: "13px", resize: "vertical" };

export default function ForwardReview({ submissionId, initial }: { submissionId: string; initial: ForwardDetail }) {
  const [detail, setDetail] = useState(initial);
  const [values, setValues] = useState<Record<string, string>>(() =>
    Object.fromEntries(initial.fields.map((f) => [f.key, f.teacher_value ?? ""]))
  );
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<{ ok: boolean; text: string } | null>(null);

  if (detail.fields.length === 0) return null;

  const save = async () => {
    setBusy(true);
    setNotice(null);
    try {
      const res = await apiFetch<{ data: ForwardDetail }>(`/api/review/submissions/${submissionId}/forward`, {
        method: "PUT",
        body: JSON.stringify({ fields: Object.fromEntries(Object.entries(values).map(([k, v]) => [k, v.trim() || null])) }),
      });
      setDetail(res.data);
      setNotice({ ok: true, text: "已儲存，後面的關卡會以老師確認的內容為準" });
    } catch (err) {
      setNotice({ ok: false, text: err instanceof ApiError ? err.message : "儲存失敗" });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={card}>
      <h3 style={{ fontSize: "15px", fontWeight: "bold", margin: "0 0 4px" }}>要傳給後面關卡的資料</h3>
      <p style={{ fontSize: "12px", color: "#94a3b8", margin: "0 0 12px" }}>
        {detail.extracted_at ? "學生通過時 AI 依欄位整理。" : "這次作答還沒有 AI 整理（還沒通過，或整理失敗），可以直接由老師填寫。"}
        老師填寫的內容優先，留空就沿用 AI 整理的。
      </p>
      <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
        {detail.fields.map((f) => (
          <div key={f.key}>
            <div style={{ fontSize: "13px", fontWeight: "bold" }}>
              {f.label}
              {f.description && <span style={{ fontWeight: "normal", fontSize: "12px", color: "#94a3b8" }}>（{f.description}）</span>}
            </div>
            <div style={{ fontSize: "12px", color: "#cbd5e1", margin: "4px 0", whiteSpace: "pre-wrap" }}>
              AI 整理：{f.ai_value || <span style={{ color: "#64748b" }}>（沒有）</span>}
            </div>
            <textarea rows={2} maxLength={600} placeholder="老師確認的版本（留空 = 沿用 AI 整理）" value={values[f.key] ?? ""} onChange={(e) => setValues({ ...values, [f.key]: e.target.value })} style={area} />
          </div>
        ))}
      </div>
      <div style={{ display: "flex", gap: "10px", alignItems: "center", marginTop: "12px" }}>
        <button type="button" disabled={busy} onClick={save} style={{ padding: "8px 18px", border: "none", borderRadius: "6px", backgroundColor: "#2563eb", color: "#fff", fontSize: "13px", cursor: "pointer", opacity: busy ? 0.5 : 1 }}>
          儲存老師確認的版本
        </button>
        {notice && <span style={{ fontSize: "12px", color: notice.ok ? "#86efac" : "#fca5a5" }}>{notice.text}</span>}
      </div>
    </div>
  );
}
