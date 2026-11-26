"use client";

// 關卡設定：通過時要傳給後面關卡的欄位（#84，需求說明書 BLD-006）
// 學生這一關通過時，AI 依這些欄位從作答整理出重點，後面的關卡（與機器人）讀這些欄位，不用再複製整段作答。
// 存在 module_steps.pass_forward_keys：[{ label, description }]

import { useState } from "react";

export type ForwardField = { label: string; description: string };

const input: React.CSSProperties = { width: "100%", boxSizing: "border-box", padding: "6px 8px", borderRadius: "6px", border: "1px solid rgba(255,255,255,0.2)", backgroundColor: "rgba(255,255,255,0.05)", color: "#fff", fontSize: "12px", outline: "none" };
const smallBtn: React.CSSProperties = { padding: "4px 10px", fontSize: "12px", borderRadius: "6px", border: "1px solid rgba(255,255,255,0.2)", backgroundColor: "transparent", color: "#cbd5e1", cursor: "pointer" };

const MAX_FIELDS = 10;

export default function ForwardFieldsEditor({ initial, busy, onSave }: { initial: ForwardField[]; busy: boolean; onSave: (fields: ForwardField[]) => void }) {
  const [rows, setRows] = useState<ForwardField[]>(initial);
  const [open, setOpen] = useState(initial.length > 0);
  const dirty = JSON.stringify(rows) !== JSON.stringify(initial);
  const update = (i: number, patch: Partial<ForwardField>) => setRows(rows.map((r, j) => (j === i ? { ...r, ...patch } : r)));

  if (!open) {
    return (
      <button type="button" onClick={() => setOpen(true)} style={{ ...smallBtn, marginTop: "8px", fontSize: "11px", color: "#94a3b8" }}>
        ＋ 設定要傳給後面關卡的欄位
      </button>
    );
  }

  return (
    <div style={{ marginTop: "10px", padding: "10px", borderRadius: "8px", backgroundColor: "rgba(255,255,255,0.03)", border: "1px solid rgba(255,255,255,0.08)", display: "flex", flexDirection: "column", gap: "6px" }}>
      <p style={{ margin: 0, fontSize: "12px", color: "#cbd5e1" }}>
        <strong>要傳給後面關卡的欄位</strong>：學生這一關通過時，AI 依這些欄位整理作答重點；後面的關卡會看到這些欄位與來源（第幾次作答、AI 整理或老師確認），老師可在複核頁修正。
      </p>
      {rows.map((r, i) => (
        <div key={i} className="ff-row" style={{ display: "grid", gridTemplateColumns: "1fr 2fr auto", gap: "6px" }}>
          <input maxLength={30} placeholder="欄位名稱，例如：優勢" value={r.label} onChange={(e) => update(i, { label: e.target.value })} style={input} />
          <input maxLength={200} placeholder="說明（給 AI 看），例如：孩子能做到、喜歡的事" value={r.description} onChange={(e) => update(i, { description: e.target.value })} style={input} />
          <button type="button" onClick={() => setRows(rows.filter((_, j) => j !== i))} style={{ ...smallBtn, color: "#f87171" }}>
            移除
          </button>
        </div>
      ))}
      <div style={{ display: "flex", gap: "8px", alignItems: "center" }}>
        <button type="button" disabled={rows.length >= MAX_FIELDS} onClick={() => setRows([...rows, { label: "", description: "" }])} style={{ ...smallBtn, opacity: rows.length >= MAX_FIELDS ? 0.4 : 1 }}>
          ＋ 加入欄位
        </button>
        <button
          type="button"
          disabled={busy || !dirty || rows.some((r) => !r.label.trim())}
          onClick={() => onSave(rows.map((r) => ({ label: r.label.trim(), description: r.description.trim() })))}
          style={{ ...smallBtn, ...(dirty ? { backgroundColor: "#2563eb", color: "#fff", border: "none" } : { opacity: 0.4 }) }}
        >
          儲存欄位
        </button>
      </div>
      <style>{`@media (max-width: 640px) { .ff-row { grid-template-columns: minmax(0, 1fr) !important; } }`}</style>
    </div>
  );
}
