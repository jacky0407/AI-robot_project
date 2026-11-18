"use client";

// 聊天室關卡的機器人設定（#68）
// 評分機器人 = 關卡本身選的機器人；這裡設定「可以對話的機器人」：名稱（學生用 @名稱 指名）與出場時機（給調度員看）
// 後端：GET / PUT /api/modules/{module_id}/steps/{step_id}/chat-bots

import { useEffect, useState } from "react";
import { ApiError, apiFetch } from "@/utils/api";
import type { Tool } from "./ModuleEditor";

type BotRow = { tool_id: string; display_name: string; when_to_use: string; use_materials: boolean };

const input: React.CSSProperties = { width: "100%", boxSizing: "border-box", padding: "7px 9px", borderRadius: "6px", border: "1px solid rgba(255,255,255,0.2)", backgroundColor: "rgba(255,255,255,0.05)", color: "#fff", fontSize: "12px", outline: "none" };
const smallBtn: React.CSSProperties = { padding: "4px 10px", fontSize: "12px", borderRadius: "6px", border: "1px solid rgba(255,255,255,0.2)", backgroundColor: "transparent", color: "#cbd5e1", cursor: "pointer" };

const MAX_BOTS = 8;

export default function ChatBotsPanel({ moduleId, stepId, evaluatorToolId, evaluatorTitle, tools }: {
  moduleId: string;
  stepId: string;
  evaluatorToolId: string;
  evaluatorTitle: string | null;
  tools: Tool[];
}) {
  const [rows, setRows] = useState<BotRow[] | null>(null);
  const [saved, setSaved] = useState<string>("");
  const [notice, setNotice] = useState<{ text: string; ok: boolean } | null>(null);
  const [busy, setBusy] = useState(false);

  const url = `/api/modules/${moduleId}/steps/${stepId}/chat-bots`;

  useEffect(() => {
    let active = true;
    apiFetch<{ data: BotRow[] }>(url).then(
      (res) => {
        if (!active) return;
        const loaded = res.data.map((r) => ({ tool_id: r.tool_id, display_name: r.display_name, when_to_use: r.when_to_use ?? "", use_materials: Boolean(r.use_materials) }));
        setRows(loaded);
        setSaved(JSON.stringify(loaded));
      },
      (err) => active && setNotice({ text: err instanceof ApiError ? err.message : "讀不到聊天室設定", ok: false })
    );
    return () => {
      active = false;
    };
  }, [url]);

  if (!rows) return notice ? <p style={{ margin: 0, fontSize: "12px", color: "#fca5a5" }}>{notice.text}</p> : <p style={{ margin: 0, fontSize: "12px", color: "#94a3b8" }}>載入聊天室設定…</p>;

  const choices = tools.filter((t) => t.id !== evaluatorToolId && t.status !== "archived");
  const update = (i: number, patch: Partial<BotRow>) => setRows(rows.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  const dirty = JSON.stringify(rows) !== saved;

  const save = async () => {
    if (rows.some((r) => !r.tool_id || !r.display_name.trim())) {
      setNotice({ text: "每個機器人都要選機器人並填名稱", ok: false });
      return;
    }
    setBusy(true);
    setNotice(null);
    try {
      const res = await apiFetch<{ data: BotRow[] }>(url, { method: "PUT", body: JSON.stringify({ bots: rows }) });
      const loaded = res.data.map((r) => ({ tool_id: r.tool_id, display_name: r.display_name, when_to_use: r.when_to_use ?? "", use_materials: Boolean(r.use_materials) }));
      setRows(loaded);
      setSaved(JSON.stringify(loaded));
      setNotice({ text: "已儲存聊天室的機器人", ok: true });
    } catch (err) {
      setNotice({ text: err instanceof ApiError ? err.message : "儲存失敗，請稍後再試", ok: false });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={{ marginTop: "10px", padding: "12px", borderRadius: "8px", backgroundColor: "rgba(255,255,255,0.03)", border: "1px solid rgba(255,255,255,0.08)", display: "flex", flexDirection: "column", gap: "8px" }}>
      <p style={{ margin: 0, fontSize: "12px", color: "#cbd5e1" }}>
        <strong>聊天室的機器人</strong>：學生每傳一則訊息，只由一個機器人回答。學生可以打「@名稱」指定；沒指定時，系統依「出場時機」挑一個。
        評分機器人是上面選的「{evaluatorTitle ?? "這一關的機器人"}」，學生 @ 它或按「送出評分」時評整段對話。
      </p>
      {rows.length === 0 && <p style={{ margin: 0, fontSize: "12px", color: "#fbbf24" }}>還沒有可以對話的機器人，學生只能送出評分。</p>}
      {rows.map((r, i) => (
        <div key={i} className="cb-row" style={{ display: "grid", gridTemplateColumns: "1.2fr 1fr 2fr auto", gap: "6px", alignItems: "center" }}>
          <select value={r.tool_id} onChange={(e) => update(i, { tool_id: e.target.value })} style={{ ...input, backgroundColor: "#0f172a" }}>
            <option value="">選擇機器人…</option>
            {choices.map((t) => (
              <option key={t.id} value={t.id}>
                {t.title}
                {t.status !== "published" ? "（未發布，學生看不到）" : ""}
              </option>
            ))}
          </select>
          <input maxLength={30} placeholder="聊天室裡的名稱" value={r.display_name} onChange={(e) => update(i, { display_name: e.target.value })} style={input} />
          <input maxLength={300} placeholder="出場時機：學生說什麼時該它回答" value={r.when_to_use} onChange={(e) => update(i, { when_to_use: e.target.value })} style={input} />
          <button type="button" onClick={() => setRows(rows.filter((_, j) => j !== i))} style={{ ...smallBtn, color: "#f87171" }}>
            移除
          </button>
          <label style={{ gridColumn: "1 / -1", display: "flex", alignItems: "center", gap: "6px", fontSize: "12px", color: "#94a3b8", cursor: "pointer" }}>
            <input type="checkbox" checked={r.use_materials} onChange={(e) => update(i, { use_materials: e.target.checked })} />
            學習助教：回答前先查這門課的「教材」與學生自己的作答紀錄，並標出引用段落
          </label>
        </div>
      ))}
      <div style={{ display: "flex", gap: "8px", alignItems: "center", flexWrap: "wrap" }}>
        <button
          type="button"
          disabled={rows.length >= MAX_BOTS}
          onClick={() => setRows([...rows, { tool_id: "", display_name: "", when_to_use: "", use_materials: false }])}
          style={{ ...smallBtn, opacity: rows.length >= MAX_BOTS ? 0.4 : 1 }}
        >
          ＋ 加入機器人
        </button>
        <button type="button" disabled={busy || !dirty} onClick={save} style={{ ...smallBtn, ...(dirty ? { backgroundColor: "#2563eb", color: "#fff", border: "none" } : { opacity: 0.4 }) }}>
          儲存聊天室設定
        </button>
        {notice && <span style={{ fontSize: "12px", color: notice.ok ? "#86efac" : "#fca5a5" }}>{notice.text}</span>}
      </div>
      <style>{`@media (max-width: 640px) { .cb-row { grid-template-columns: minmax(0, 1fr) !important; } }`}</style>
    </div>
  );
}
