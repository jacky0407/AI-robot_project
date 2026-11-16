"use client";

// 課程詳細頁：指派模組清單（指派、取消指派）
// 後端：GET/POST /api/courses/{id}/modules、DELETE /api/courses/{id}/modules/{module_id}
// 所有模組（含未發布）直接讀 learning_modules（migrations/005 的 RLS 允許教師看全部）

import { useEffect, useState } from "react";
import Link from "next/link";
import { apiFetch } from "@/utils/api";
import { createClient } from "@/utils/supabase/client";
import { type AssignedModule, type ModuleOption, errorText, input, pill, smallBtn } from "../shared";

export default function ModulesTab({
  courseId,
  assigned,
  reload,
}: {
  courseId: string;
  assigned: AssignedModule[] | null;
  reload: () => Promise<void>;
}) {
  const [supabase] = useState(() => createClient());
  const [allModules, setAllModules] = useState<ModuleOption[]>([]);
  const [toAssign, setToAssign] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const base = `/api/courses/${encodeURIComponent(courseId)}/modules`;

  useEffect(() => {
    let active = true;
    supabase
      .from("learning_modules")
      .select("id, title, is_published")
      .order("created_at")
      .then(({ data, error }) => {
        if (!active) return;
        if (error) setMsg(`無法讀取模組清單：${error.message}`);
        else setAllModules(data ?? []);
      });
    return () => {
      active = false;
    };
  }, [supabase]);

  const assign = async () => {
    if (!toAssign) return;
    setBusy(true);
    setMsg("");
    try {
      const res = await apiFetch<{ message: string }>(base, { method: "POST", body: JSON.stringify({ module_id: toAssign }) });
      setMsg(res.message);
      setToAssign("");
      await reload();
    } catch (err) {
      setMsg(`指派失敗：${errorText(err, "請稍後再試")}`);
    } finally {
      setBusy(false);
    }
  };

  const unassign = async (m: AssignedModule) => {
    if (!confirm(`確定要取消指派「${m.title}」嗎？學生將看不到這個模組，但已作答的紀錄會保留。`)) return;
    setBusy(true);
    setMsg("");
    try {
      await apiFetch(`${base}/${m.module_id}`, { method: "DELETE" });
      await reload();
    } catch (err) {
      setMsg(`取消指派失敗：${errorText(err, "請稍後再試")}`);
    } finally {
      setBusy(false);
    }
  };

  const assignedIds = new Set((assigned ?? []).map((a) => a.module_id));
  const options = allModules.filter((m) => !assignedIds.has(m.id));

  return (
    <div>
      <h2 style={{ fontSize: "16px", fontWeight: "bold", margin: "0 0 4px" }}>指派模組清單</h2>
      <p style={{ fontSize: "12px", color: "#94a3b8", margin: "0 0 16px" }}>
        學生進入課程後會依這裡的順序看到模組與關卡。要新增或編輯模組內容請到
        <Link href="/admin/modules" style={{ color: "#4ade80", margin: "0 4px" }}>模組管理</Link>。
      </p>

      <div style={{ display: "flex", gap: "8px", marginBottom: "14px" }}>
        <select value={toAssign} onChange={(e) => setToAssign(e.target.value)} style={{ ...input, flex: 1, width: "auto", backgroundColor: "#0f172a" }}>
          <option value="">{options.length ? "選擇要指派的模組…" : "沒有其他可指派的模組"}</option>
          {options.map((m) => (
            <option key={m.id} value={m.id}>
              {m.title}
              {m.is_published ? "" : "（未發布）"}
            </option>
          ))}
        </select>
        <button
          disabled={busy || !toAssign}
          onClick={assign}
          style={{ ...smallBtn, padding: "7px 16px", backgroundColor: "#16a34a", color: "#fff", border: "none", fontWeight: "bold", opacity: busy || !toAssign ? 0.5 : 1 }}
        >
          指派
        </button>
      </div>

      {msg && <p style={{ fontSize: "12px", margin: "0 0 12px", color: /失敗|無法/.test(msg) ? "#fca5a5" : "#86efac" }}>{msg}</p>}

      {assigned === null && <p style={{ fontSize: "13px", color: "#94a3b8", margin: 0 }}>載入中…</p>}
      {assigned?.length === 0 && <p style={{ fontSize: "13px", color: "#fbbf24", margin: 0 }}>還沒有指派模組，學生進入課程後會看不到任何練習。</p>}

      <ol style={{ listStyle: "none", margin: 0, padding: 0, display: "flex", flexDirection: "column", gap: "8px" }}>
        {assigned?.map((m, i) => (
          <li key={m.module_id} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: "10px", fontSize: "13px", padding: "10px 12px", borderRadius: "8px", backgroundColor: "rgba(255,255,255,0.04)" }}>
            <span style={{ display: "flex", alignItems: "center", gap: "8px 10px", minWidth: 0, flexWrap: "wrap" }}>
              <span style={{ flexShrink: 0, width: "24px", height: "24px", borderRadius: "9999px", display: "flex", alignItems: "center", justifyContent: "center", fontSize: "12px", fontWeight: "bold", backgroundColor: "rgba(255,255,255,0.08)", color: "#cbd5e1" }}>
                {i + 1}
              </span>
              <b>{m.title || "（未命名模組）"}</b>
              {!m.is_published && <span style={pill("#fbbf24", "rgba(245,158,11,0.15)")}>未發布，學生暫時看不到</span>}
            </span>
            <button disabled={busy} onClick={() => unassign(m)} style={{ ...smallBtn, flexShrink: 0, color: "#f87171" }}>
              取消指派
            </button>
          </li>
        ))}
      </ol>
    </div>
  );
}
