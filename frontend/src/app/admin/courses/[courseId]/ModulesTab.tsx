"use client";

// 課程詳細頁：模組管理（原本的「指派模組」＋「模組管理」頁）
// - 這門課指派的模組（依指派順序），每個可以展開編輯名稱、發布與關卡
// - 指派現有模組、建立新模組（建立後自動指派給這門課）、取消指派
// 後端：GET/POST /api/courses/{id}/modules、DELETE /api/courses/{id}/modules/{module_id}、/api/modules、GET /api/teacher/tools
// 模組可以同時指派給多門課，在這裡編輯關卡會影響所有用到它的課程

import { useEffect, useState } from "react";
import { apiFetch } from "@/utils/api";
import { type AssignedModule, errorText, input, pill, smallBtn } from "../shared";
import ModuleEditor, { type ModuleSummary, type Tool } from "./ModuleEditor";

export default function ModulesTab({
  courseId,
  assigned,
  reload,
}: {
  courseId: string;
  assigned: AssignedModule[] | null;
  reload: () => Promise<void>;
}) {
  const [allModules, setAllModules] = useState<ModuleSummary[]>([]);
  const [tools, setTools] = useState<Tool[]>([]);
  const [tick, setTick] = useState(0); // 模組有變動時 +1，重新讀模組清單（關卡數、發布狀態）
  const [openId, setOpenId] = useState<string | null>(null);
  const [toAssign, setToAssign] = useState("");
  const [showCreate, setShowCreate] = useState(false);
  const [createForm, setCreateForm] = useState({ title: "", description: "" });
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const base = `/api/courses/${encodeURIComponent(courseId)}/modules`;

  useEffect(() => {
    let active = true;
    apiFetch<{ data: ModuleSummary[] }>("/api/modules").then(
      (res) => active && setAllModules(res.data),
      (err) => active && setMsg(`無法讀取模組清單：${errorText(err, "請稍後再試")}`)
    );
    return () => {
      active = false;
    };
  }, [tick]);

  useEffect(() => {
    let active = true;
    apiFetch<{ data: Tool[] }>("/api/teacher/tools").then(
      (res) => active && setTools(res.data.filter((t) => t.status !== "archived")),
      () => active && setTools([])
    );
    return () => {
      active = false;
    };
  }, []);

  const refresh = async () => {
    setTick((t) => t + 1);
    await reload();
  };

  const assign = async (moduleId: string) => {
    const res = await apiFetch<{ message: string }>(base, { method: "POST", body: JSON.stringify({ module_id: moduleId }) });
    await refresh();
    return res.message;
  };

  const handleAssign = async () => {
    if (!toAssign) return;
    setBusy(true);
    setMsg("");
    try {
      setMsg(await assign(toAssign));
      setToAssign("");
    } catch (err) {
      setMsg(`加入失敗：${errorText(err, "請稍後再試")}`);
    } finally {
      setBusy(false);
    }
  };

  // 建立新模組，接著指派給這門課並展開編輯，讓老師直接新增關卡
  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!createForm.title.trim()) return;
    setBusy(true);
    setMsg("");
    try {
      const res = await apiFetch<{ data: { id: string } }>("/api/modules", {
        method: "POST",
        body: JSON.stringify({ title: createForm.title.trim(), description: createForm.description.trim() || null }),
      });
      await assign(res.data.id);
      setCreateForm({ title: "", description: "" });
      setShowCreate(false);
      setOpenId(res.data.id);
      setMsg("已建立模組並加入這門課。請在下方新增關卡，排好後按「發布模組」，學生才看得到。");
    } catch (err) {
      setMsg(`建立失敗：${errorText(err, "請稍後再試")}`);
    } finally {
      setBusy(false);
    }
  };

  const unassign = async (m: AssignedModule) => {
    if (!confirm(`確定要讓這門課不再使用「${m.title}」嗎？\n學生將看不到這個模組，已作答的紀錄會保留；模組本身不會被刪除。`)) return;
    setBusy(true);
    setMsg("");
    try {
      await apiFetch(`${base}/${m.module_id}`, { method: "DELETE" });
      if (openId === m.module_id) setOpenId(null);
      await refresh();
    } catch (err) {
      setMsg(`移出失敗：${errorText(err, "請稍後再試")}`);
    } finally {
      setBusy(false);
    }
  };

  const summaryOf = new Map(allModules.map((m) => [m.id, m]));
  const assignedIds = new Set((assigned ?? []).map((a) => a.module_id));
  const options = allModules.filter((m) => !assignedIds.has(m.id));

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: "12px", marginBottom: "16px" }}>
        <div>
          <h2 style={{ fontSize: "16px", fontWeight: "bold", margin: "0 0 4px" }}>模組管理</h2>
          <p style={{ fontSize: "12px", color: "#94a3b8", margin: 0 }}>
            模組是一組依序完成的關卡，每一關使用一個機器人。學生進入課程後會依這裡的順序看到模組與關卡。
          </p>
        </div>
        {!showCreate && (
          <button onClick={() => setShowCreate(true)} style={{ ...smallBtn, flexShrink: 0, padding: "7px 14px", backgroundColor: "#16a34a", color: "#fff", border: "none", fontWeight: "bold" }}>
            ＋ 建立新模組
          </button>
        )}
      </div>

      {showCreate && (
        <form onSubmit={handleCreate} style={{ display: "flex", flexDirection: "column", gap: "8px", padding: "14px", marginBottom: "14px", borderRadius: "8px", border: "1px dashed rgba(74,222,128,0.45)" }}>
          <input autoFocus required maxLength={100} placeholder="模組名稱，例如：學前 IEP 逐步撰寫" value={createForm.title} onChange={(e) => setCreateForm({ ...createForm, title: e.target.value })} style={input} />
          <textarea rows={2} maxLength={1000} placeholder="模組說明（選填，學生在課程簡介會看到）" value={createForm.description} onChange={(e) => setCreateForm({ ...createForm, description: e.target.value })} style={{ ...input, resize: "vertical" }} />
          <div style={{ display: "flex", gap: "8px", alignItems: "center", flexWrap: "wrap" }}>
            <button type="submit" disabled={busy || !createForm.title.trim()} style={{ ...smallBtn, backgroundColor: "#16a34a", color: "#fff", border: "none", opacity: busy || !createForm.title.trim() ? 0.5 : 1 }}>
              建立並加入這門課
            </button>
            <button type="button" onClick={() => setShowCreate(false)} style={smallBtn}>
              取消
            </button>
            <span style={{ fontSize: "11px", color: "#64748b" }}>新模組先是「未發布」，排好關卡再發布。</span>
          </div>
        </form>
      )}

      <div style={{ display: "flex", gap: "8px", marginBottom: "14px" }}>
        <select value={toAssign} onChange={(e) => setToAssign(e.target.value)} style={{ ...input, flex: 1, width: "auto", backgroundColor: "#0f172a" }}>
          <option value="">{options.length ? "加入已有的模組（其他課程建立的也可以用）…" : "沒有其他可加入的模組"}</option>
          {options.map((m) => (
            <option key={m.id} value={m.id}>
              {m.title}
              {m.is_published ? "" : "（未發布）"}・{m.active_step_count} 關
            </option>
          ))}
        </select>
        <button
          disabled={busy || !toAssign}
          onClick={handleAssign}
          style={{ ...smallBtn, padding: "7px 16px", backgroundColor: "#16a34a", color: "#fff", border: "none", fontWeight: "bold", opacity: busy || !toAssign ? 0.5 : 1 }}
        >
          加入
        </button>
      </div>

      {msg && <p style={{ fontSize: "12px", margin: "0 0 12px", color: /失敗|無法/.test(msg) ? "#fca5a5" : "#86efac" }}>{msg}</p>}

      {assigned === null && <p style={{ fontSize: "13px", color: "#94a3b8", margin: 0 }}>載入中…</p>}
      {assigned?.length === 0 && <p style={{ fontSize: "13px", color: "#fbbf24", margin: 0 }}>這門課還沒有模組，學生進入課程後會看不到任何練習。請建立新模組或加入已有的模組。</p>}

      <ol style={{ listStyle: "none", margin: 0, padding: 0, display: "flex", flexDirection: "column", gap: "10px" }}>
        {assigned?.map((m, i) => {
          const summary = summaryOf.get(m.module_id);
          const open = openId === m.module_id;
          const published = summary?.is_published ?? m.is_published;
          return (
            <li key={m.module_id} style={{ borderRadius: "10px", border: `1px solid ${open ? "rgba(74,222,128,0.45)" : "rgba(255,255,255,0.08)"}`, backgroundColor: "rgba(255,255,255,0.03)" }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: "10px", padding: "12px" }}>
                <span style={{ display: "flex", alignItems: "center", gap: "8px 10px", minWidth: 0, flexWrap: "wrap", fontSize: "13px" }}>
                  <span style={{ flexShrink: 0, width: "24px", height: "24px", borderRadius: "9999px", display: "flex", alignItems: "center", justifyContent: "center", fontSize: "12px", fontWeight: "bold", backgroundColor: "rgba(255,255,255,0.08)", color: "#cbd5e1" }}>
                    {i + 1}
                  </span>
                  <b>{summary?.title ?? m.title ?? "（未命名模組）"}</b>
                  {published ? <span style={pill("#4ade80", "rgba(34,197,94,0.15)")}>已發布</span> : <span style={pill("#fbbf24", "rgba(245,158,11,0.15)")}>未發布，學生暫時看不到</span>}
                  {summary && <span style={{ fontSize: "12px", color: "#94a3b8" }}>{summary.active_step_count} 關</span>}
                </span>
                <span style={{ display: "flex", gap: "6px", flexShrink: 0 }}>
                  <button onClick={() => setOpenId(open ? null : m.module_id)} style={{ ...smallBtn, ...(open ? { backgroundColor: "#16a34a", color: "#fff", border: "none" } : {}) }}>
                    {open ? "收合" : "編輯關卡"}
                  </button>
                  <button disabled={busy} onClick={() => unassign(m)} title="這門課不再使用這個模組（模組本身不會被刪除）" style={{ ...smallBtn, color: "#f87171" }}>
                    移出課程
                  </button>
                </span>
              </div>
              {open && (
                <div style={{ padding: "14px 12px", borderTop: "1px solid rgba(255,255,255,0.08)" }}>
                  <ModuleEditor
                    moduleId={m.module_id}
                    tools={tools}
                    onChanged={() => setTick((t) => t + 1)}
                    onDeleted={() => {
                      setOpenId(null);
                      refresh();
                    }}
                  />
                </div>
              )}
            </li>
          );
        })}
      </ol>

      <style>{`
        @media (max-width: 760px) { .me-two { grid-template-columns: minmax(0, 1fr) !important; } }
      `}</style>
    </div>
  );
}
