"use client";

// 教師端：模組與步驟管理（建立模組、把機器人排成步驟、調整順序、停用、發布）
// 後端：/api/modules（backend/api/modules.py）；機器人清單：GET /api/teacher/tools
// 新增、修改、刪除只限教授；助教看得到但送出會被後端擋下（403）

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { createClient } from "@/utils/supabase/client";
import { ApiError, apiFetch } from "@/utils/api";
import AdminNav from "../AdminNav";

type ModuleSummary = {
  id: string;
  title: string;
  description: string | null;
  domain: string;
  is_published: boolean;
  step_count: number;
  active_step_count: number;
};

type Step = {
  id: string;
  tool_id: string;
  step_order: number;
  step_title: string;
  is_required: boolean;
  pass_score: number | null;
  require_teacher_review: boolean;
  is_active: boolean;
  tool_title: string | null;
  tool_status: string | null;
  attempt_count: number;
  can_delete: boolean;
};

type ModuleDetail = Omit<ModuleSummary, "step_count" | "active_step_count"> & { steps: Step[] };

type Tool = { id: string; title: string; status: string };

type StepForm = {
  tool_id: string;
  step_title: string;
  is_required: boolean;
  pass_score: string;
  require_teacher_review: boolean;
};

const EMPTY_STEP: StepForm = { tool_id: "", step_title: "", is_required: true, pass_score: "70", require_teacher_review: false };

const panel: React.CSSProperties = { backgroundColor: "#1e293b", borderRadius: "12px", padding: "20px", border: "1px solid rgba(255,255,255,0.1)" };
const input: React.CSSProperties = { width: "100%", boxSizing: "border-box", padding: "8px 10px", borderRadius: "6px", border: "1px solid rgba(255,255,255,0.2)", backgroundColor: "rgba(255,255,255,0.05)", color: "#fff", fontSize: "13px", outline: "none" };
const smallBtn: React.CSSProperties = { padding: "4px 10px", fontSize: "12px", borderRadius: "6px", border: "1px solid rgba(255,255,255,0.2)", backgroundColor: "transparent", color: "#cbd5e1", cursor: "pointer" };
const primaryBtn: React.CSSProperties = { padding: "8px 14px", backgroundColor: "#16a34a", color: "#fff", border: "none", borderRadius: "6px", fontSize: "13px", fontWeight: "bold", cursor: "pointer" };
const pill = (color: string, bg: string): React.CSSProperties => ({ fontSize: "11px", padding: "1px 8px", borderRadius: "9999px", color, backgroundColor: bg, whiteSpace: "nowrap" });

const errorText = (err: unknown, fallback: string) => (err instanceof ApiError ? err.message : fallback);

const toolLabel = (t: Tool) => `${t.title}${t.status === "published" ? "" : t.status === "draft" ? "（草稿）" : `（${t.status}）`}`;

// 送出前檢查通過分數，後端也會擋（422）
function parsePassScore(value: string): number | null {
  const n = Number(value);
  return value.trim() !== "" && Number.isInteger(n) && n >= 0 && n <= 100 ? n : null;
}

export default function AdminModulesPage() {
  const router = useRouter();
  const [supabase] = useState(() => createClient());

  const [modules, setModules] = useState<ModuleSummary[] | null>(null);
  const [tools, setTools] = useState<Tool[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<ModuleDetail | null>(null);
  const [errorMsg, setErrorMsg] = useState("");
  const [notice, setNotice] = useState<{ text: string; ok: boolean } | null>(null);
  const [busy, setBusy] = useState(false);
  // 每次重新載入模組詳情就 +1，讓步驟列用新的資料重新初始化
  const [version, setVersion] = useState(0);

  const [createForm, setCreateForm] = useState({ title: "", description: "" });
  const [moduleForm, setModuleForm] = useState({ title: "", description: "" });
  const [newStep, setNewStep] = useState<StepForm>(EMPTY_STEP);

  const loadModules = useCallback(
    () =>
      apiFetch<{ data: ModuleSummary[] }>("/api/modules").then(
        (res) => {
          setModules(res.data);
          setErrorMsg("");
        },
        (err) => {
          setModules([]);
          setErrorMsg(errorText(err, "無法連線到後端（請確認 FastAPI 已啟動，且 NEXT_PUBLIC_API_URL 設定正確）"));
        }
      ),
    []
  );

  const showDetail = useCallback((d: ModuleDetail) => {
    setDetail(d);
    setModuleForm({ title: d.title, description: d.description ?? "" });
    setVersion((v) => v + 1);
  }, []);

  const loadDetail = useCallback(
    (id: string) =>
      apiFetch<{ data: ModuleDetail }>(`/api/modules/${id}`).then(
        (res) => showDetail(res.data),
        (err) => setNotice({ text: `無法讀取模組：${errorText(err, "請稍後再試")}`, ok: false })
      ),
    [showDetail]
  );

  useEffect(() => {
    loadModules();
    apiFetch<{ data: Tool[] }>("/api/teacher/tools").then(
      (res) => setTools(res.data.filter((t) => t.status !== "archived")),
      () => setTools([])
    );
  }, [loadModules]);

  useEffect(() => {
    if (selectedId) loadDetail(selectedId);
  }, [selectedId, loadDetail]);

  // 所有寫入共用：成功後更新詳情與左側清單，失敗顯示後端的訊息
  const run = async (action: () => Promise<{ data?: ModuleDetail; message?: string } | void>, okText: string) => {
    setBusy(true);
    setNotice(null);
    try {
      const res = await action();
      if (res && res.data) showDetail(res.data);
      setNotice({ text: (res && res.message) || okText, ok: true });
      await loadModules();
      return true;
    } catch (err) {
      setNotice({ text: errorText(err, "操作失敗，請稍後再試"), ok: false });
      if (selectedId) await loadDetail(selectedId);
      return false;
    } finally {
      setBusy(false);
    }
  };

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!createForm.title.trim()) return;
    setBusy(true);
    setNotice(null);
    try {
      const res = await apiFetch<{ data: ModuleDetail; message: string }>("/api/modules", {
        method: "POST",
        body: JSON.stringify({ title: createForm.title, description: createForm.description || null }),
      });
      setCreateForm({ title: "", description: "" });
      await loadModules();
      setSelectedId(res.data.id);
      setNotice({ text: res.message, ok: true });
    } catch (err) {
      setNotice({ text: `建立失敗：${errorText(err, "請稍後再試")}`, ok: false });
    } finally {
      setBusy(false);
    }
  };

  const patchModule = (changes: Record<string, unknown>, okText: string) =>
    detail &&
    run(
      () => apiFetch<{ data: ModuleDetail }>(`/api/modules/${detail.id}`, { method: "PATCH", body: JSON.stringify(changes) }),
      okText
    );

  const handleDeleteModule = async () => {
    if (!detail || !window.confirm(`確定要刪除模組「${detail.title}」嗎？指派給課程的紀錄也會一起取消。`)) return;
    const ok = await run(
      () => apiFetch<{ message: string }>(`/api/modules/${detail.id}`, { method: "DELETE" }),
      "已刪除模組"
    );
    if (ok) {
      setSelectedId(null);
      setDetail(null);
    }
  };

  const handleAddStep = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!detail) return;
    const pass = parsePassScore(newStep.pass_score);
    if (!newStep.tool_id || !newStep.step_title.trim()) {
      setNotice({ text: "請選擇機器人並填寫步驟名稱", ok: false });
      return;
    }
    if (pass === null) {
      setNotice({ text: "通過分數要是 0–100 的整數", ok: false });
      return;
    }
    const ok = await run(
      () =>
        apiFetch<{ data: ModuleDetail }>(`/api/modules/${detail.id}/steps`, {
          method: "POST",
          body: JSON.stringify({ ...newStep, pass_score: pass }),
        }),
      "已新增步驟"
    );
    if (ok) setNewStep(EMPTY_STEP);
  };

  const moveStep = (index: number, delta: number) => {
    if (!detail) return;
    const ids = detail.steps.map((s) => s.id);
    const target = index + delta;
    if (target < 0 || target >= ids.length) return;
    [ids[index], ids[target]] = [ids[target], ids[index]];
    run(
      () => apiFetch<{ data: ModuleDetail }>(`/api/modules/${detail.id}/steps/order`, { method: "PUT", body: JSON.stringify({ step_ids: ids }) }),
      "已調整順序"
    );
  };

  const saveStep = (step: Step, changes: Record<string, unknown>, okText = "已儲存步驟") =>
    detail &&
    run(
      () =>
        apiFetch<{ data: ModuleDetail }>(`/api/modules/${detail.id}/steps/${step.id}`, {
          method: "PATCH",
          body: JSON.stringify(changes),
        }),
      okText
    );

  const deleteStep = (step: Step) => {
    if (!detail || !window.confirm(`確定要刪除「${step.step_title}」嗎？`)) return;
    run(() => apiFetch<{ data: ModuleDetail }>(`/api/modules/${detail.id}/steps/${step.id}`, { method: "DELETE" }), "已刪除步驟");
  };

  const handleSignOut = async () => {
    await supabase.auth.signOut();
    router.push("/");
    router.refresh();
  };

  const moduleFormChanged = detail && (moduleForm.title !== detail.title || moduleForm.description !== (detail.description ?? ""));

  return (
    <div style={{ minHeight: "100vh", backgroundColor: "#0f172a", color: "#f8fafc", fontFamily: "sans-serif", colorScheme: "dark" }}>
      <header style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "14px 24px", backgroundColor: "#1e293b", borderBottom: "1px solid rgba(255,255,255,0.1)" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
          <span style={{ backgroundColor: "#16a34a", color: "#fff", padding: "4px 8px", borderRadius: "6px", fontSize: "12px", fontWeight: "bold" }}>
            管理與複核後台
          </span>
          <AdminNav />
        </div>
        <button onClick={handleSignOut} style={{ ...smallBtn, padding: "6px 12px" }}>
          登出
        </button>
      </header>

      {errorMsg && (
        <div style={{ maxWidth: "1200px", margin: "16px auto 0", padding: "10px 14px", borderRadius: "8px", backgroundColor: "rgba(239,68,68,0.15)", border: "1px solid rgba(239,68,68,0.4)", color: "#fca5a5", fontSize: "13px" }}>
          {errorMsg}
        </div>
      )}

      <div style={{ maxWidth: "1200px", margin: "24px auto", padding: "0 16px", display: "grid", gridTemplateColumns: "1fr 2.2fr", gap: "20px", alignItems: "start" }}>
        {/* 左：模組清單與建立 */}
        <div style={{ display: "flex", flexDirection: "column", gap: "20px" }}>
          <div style={panel}>
            <h3 style={{ fontSize: "15px", fontWeight: "bold", margin: "0 0 12px 0" }}>所有模組</h3>
            {modules === null && <p style={{ fontSize: "13px", color: "#94a3b8" }}>載入中…</p>}
            {modules?.length === 0 && !errorMsg && <p style={{ fontSize: "13px", color: "#94a3b8" }}>還沒有模組，請從下方建立。</p>}
            <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
              {modules?.map((m) => {
                const active = m.id === selectedId;
                return (
                  <button
                    key={m.id}
                    onClick={() => setSelectedId(m.id)}
                    style={{ textAlign: "left", padding: "10px 12px", borderRadius: "8px", cursor: "pointer", color: "#f8fafc", border: `1.5px solid ${active ? "#38bdf8" : "rgba(255,255,255,0.08)"}`, backgroundColor: active ? "rgba(56,189,248,0.12)" : "rgba(255,255,255,0.03)" }}
                  >
                    <div style={{ display: "flex", justifyContent: "space-between", gap: "8px", alignItems: "center" }}>
                      <span style={{ fontWeight: "bold", fontSize: "13px" }}>{m.title}</span>
                      {m.is_published ? <span style={pill("#4ade80", "rgba(34,197,94,0.15)")}>已發布</span> : <span style={pill("#fbbf24", "rgba(245,158,11,0.15)")}>未發布</span>}
                    </div>
                    <div style={{ fontSize: "11px", color: "#94a3b8", marginTop: "4px" }}>
                      {m.active_step_count} 個啟用中的步驟{m.step_count > m.active_step_count ? `（另有 ${m.step_count - m.active_step_count} 個停用）` : ""}
                    </div>
                  </button>
                );
              })}
            </div>
          </div>

          <form onSubmit={handleCreate} style={{ ...panel, display: "flex", flexDirection: "column", gap: "10px" }}>
            <h3 style={{ fontSize: "14px", fontWeight: "bold", margin: 0 }}>建立新模組</h3>
            <input required maxLength={100} placeholder="模組名稱，例如：學前 IEP 逐步撰寫模組" value={createForm.title} onChange={(e) => setCreateForm({ ...createForm, title: e.target.value })} style={input} />
            <textarea rows={2} maxLength={1000} placeholder="模組說明（選填）" value={createForm.description} onChange={(e) => setCreateForm({ ...createForm, description: e.target.value })} style={{ ...input, resize: "vertical" }} />
            <button type="submit" disabled={busy || !createForm.title.trim()} style={{ ...primaryBtn, opacity: busy || !createForm.title.trim() ? 0.5 : 1 }}>
              + 建立模組
            </button>
            <p style={{ margin: 0, fontSize: "11px", color: "#64748b" }}>新模組一律從「未發布」開始，排好步驟後再發布；發布後到「課程管理」指派給課程。</p>
          </form>
        </div>

        {/* 右：模組詳情與步驟 */}
        <div style={panel}>
          {!detail && <p style={{ fontSize: "13px", color: "#94a3b8", margin: 0 }}>從左側選擇一個模組，或建立新模組。</p>}

          {detail && (
            <div style={{ display: "flex", flexDirection: "column", gap: "18px" }}>
              {/* 模組資料與發布 */}
              <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
                <div style={{ display: "flex", gap: "8px" }}>
                  <input maxLength={100} value={moduleForm.title} onChange={(e) => setModuleForm({ ...moduleForm, title: e.target.value })} style={{ ...input, fontSize: "15px", fontWeight: "bold" }} />
                </div>
                <textarea rows={2} maxLength={1000} placeholder="模組說明（選填）" value={moduleForm.description} onChange={(e) => setModuleForm({ ...moduleForm, description: e.target.value })} style={{ ...input, resize: "vertical" }} />
                <div style={{ display: "flex", gap: "8px", flexWrap: "wrap", alignItems: "center" }}>
                  <button
                    disabled={busy || !moduleFormChanged || !moduleForm.title.trim()}
                    onClick={() => patchModule({ title: moduleForm.title, description: moduleForm.description }, "已儲存模組資料")}
                    style={{ ...smallBtn, opacity: busy || !moduleFormChanged || !moduleForm.title.trim() ? 0.4 : 1 }}
                  >
                    儲存名稱與說明
                  </button>
                  <button
                    disabled={busy}
                    onClick={() => patchModule({ is_published: !detail.is_published }, detail.is_published ? "已取消發布，學生暫時看不到這個模組" : "已發布，學生在被指派的課程裡看得到了")}
                    style={{ ...smallBtn, ...(detail.is_published ? { color: "#fbbf24" } : { backgroundColor: "#16a34a", color: "#fff", border: "none" }) }}
                  >
                    {detail.is_published ? "取消發布" : "發布模組"}
                  </button>
                  <button disabled={busy} onClick={handleDeleteModule} style={{ ...smallBtn, color: "#f87171", marginLeft: "auto" }}>
                    刪除模組
                  </button>
                </div>
                {notice && <p style={{ margin: 0, fontSize: "12px", color: notice.ok ? "#86efac" : "#fca5a5" }}>{notice.text}</p>}
              </div>

              {/* 步驟清單 */}
              <div>
                <h4 style={{ fontSize: "14px", fontWeight: "bold", margin: "0 0 4px 0", color: "#38bdf8" }}>步驟（{detail.steps.length}）</h4>
                <p style={{ fontSize: "12px", color: "#94a3b8", margin: "0 0 10px 0" }}>
                  學生要通過前面的「必修」步驟，下一步才會解鎖。已有學生作答的步驟不能刪除或更換機器人，請改用「停用」：學生會看不到，作答紀錄完整保留。
                </p>
                {detail.steps.length === 0 && <p style={{ fontSize: "13px", color: "#94a3b8" }}>還沒有步驟，請從下方新增。</p>}
                <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
                  {detail.steps.map((s, i) => (
                    <StepRow
                      key={`${s.id}-${version}`}
                      step={s}
                      index={i}
                      total={detail.steps.length}
                      tools={tools}
                      busy={busy}
                      onMove={moveStep}
                      onSave={saveStep}
                      onDelete={deleteStep}
                    />
                  ))}
                </div>
              </div>

              {/* 新增步驟 */}
              <form onSubmit={handleAddStep} style={{ display: "flex", flexDirection: "column", gap: "8px", padding: "14px", borderRadius: "10px", border: "1px dashed rgba(255,255,255,0.2)" }}>
                <h4 style={{ fontSize: "13px", fontWeight: "bold", margin: 0 }}>新增步驟（排在最後）</h4>
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "8px" }}>
                  <select value={newStep.tool_id} onChange={(e) => setNewStep({ ...newStep, tool_id: e.target.value })} style={{ ...input, backgroundColor: "#0f172a" }}>
                    <option value="">{tools.length ? "選擇這一步使用的機器人…" : "沒有可用的機器人，請先到「機器人與複核」建立"}</option>
                    {tools.map((t) => (
                      <option key={t.id} value={t.id}>
                        {toolLabel(t)}
                      </option>
                    ))}
                  </select>
                  <input maxLength={100} placeholder="步驟名稱，例如：步驟3 優勢與需求分析" value={newStep.step_title} onChange={(e) => setNewStep({ ...newStep, step_title: e.target.value })} style={input} />
                </div>
                <StepOptions form={newStep} onChange={(changes) => setNewStep({ ...newStep, ...changes })} />
                <button type="submit" disabled={busy} style={{ ...primaryBtn, alignSelf: "flex-start", opacity: busy ? 0.5 : 1 }}>
                  + 新增步驟
                </button>
              </form>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// 必修、通過分數、教師審核點（新增與編輯共用）
function StepOptions({ form, onChange }: { form: Pick<StepForm, "is_required" | "pass_score" | "require_teacher_review">; onChange: (changes: Partial<StepForm>) => void }) {
  return (
    <div style={{ display: "flex", gap: "16px", flexWrap: "wrap", alignItems: "center", fontSize: "12px" }}>
      <label style={{ display: "flex", alignItems: "center", gap: "6px", cursor: "pointer" }}>
        <input type="checkbox" checked={form.is_required} onChange={(e) => onChange({ is_required: e.target.checked })} />
        必修（沒通過會擋住後面的步驟）
      </label>
      <label style={{ display: "flex", alignItems: "center", gap: "6px" }}>
        通過分數
        <input type="number" min={0} max={100} value={form.pass_score} onChange={(e) => onChange({ pass_score: e.target.value })} onFocus={(e) => e.target.select()} style={{ ...input, width: "70px" }} />
      </label>
      <label style={{ display: "flex", alignItems: "center", gap: "6px", cursor: "pointer" }}>
        <input type="checkbox" checked={form.require_teacher_review} onChange={(e) => onChange({ require_teacher_review: e.target.checked })} />
        教師強制審核點（作答一律進複核清單）
      </label>
    </div>
  );
}

function StepRow({
  step,
  index,
  total,
  tools,
  busy,
  onMove,
  onSave,
  onDelete,
}: {
  step: Step;
  index: number;
  total: number;
  tools: Tool[];
  busy: boolean;
  onMove: (index: number, delta: number) => void;
  onSave: (step: Step, changes: Record<string, unknown>, okText?: string) => void;
  onDelete: (step: Step) => void;
}) {
  const [form, setForm] = useState<StepForm>({
    tool_id: step.tool_id,
    step_title: step.step_title,
    is_required: step.is_required,
    pass_score: String(step.pass_score ?? 70),
    require_teacher_review: step.require_teacher_review,
  });
  const [error, setError] = useState("");

  const changes: Record<string, unknown> = {};
  if (form.tool_id !== step.tool_id) changes.tool_id = form.tool_id;
  if (form.step_title !== step.step_title) changes.step_title = form.step_title;
  if (form.is_required !== step.is_required) changes.is_required = form.is_required;
  if (form.pass_score !== String(step.pass_score ?? 70)) changes.pass_score = form.pass_score;
  if (form.require_teacher_review !== step.require_teacher_review) changes.require_teacher_review = form.require_teacher_review;
  const dirty = Object.keys(changes).length > 0;

  const handleSave = () => {
    setError("");
    if (!form.step_title.trim()) return setError("步驟名稱不能空白");
    if (!("pass_score" in changes)) return onSave(step, changes);
    const pass = parsePassScore(form.pass_score);
    if (pass === null) return setError("通過分數要是 0–100 的整數");
    onSave(step, { ...changes, pass_score: pass });
  };

  // 目前用的機器人若已封存，選單裡沒有它，仍要顯示出來
  const toolOptions = tools.some((t) => t.id === step.tool_id)
    ? tools
    : [{ id: step.tool_id, title: step.tool_title ?? "（未知機器人）", status: step.tool_status ?? "archived" }, ...tools];
  const locked = step.attempt_count > 0;

  return (
    <div style={{ padding: "12px", borderRadius: "10px", border: "1px solid rgba(255,255,255,0.08)", backgroundColor: step.is_active ? "rgba(255,255,255,0.04)" : "rgba(255,255,255,0.01)", opacity: step.is_active ? 1 : 0.65 }}>
      <div style={{ display: "flex", alignItems: "center", gap: "8px", marginBottom: "8px", flexWrap: "wrap" }}>
        <span style={{ fontSize: "12px", fontWeight: "bold", color: "#38bdf8", minWidth: "52px" }}>步驟 {step.step_order}</span>
        <button disabled={busy || index === 0} onClick={() => onMove(index, -1)} title="往前移" style={{ ...smallBtn, padding: "2px 8px", opacity: busy || index === 0 ? 0.3 : 1 }}>↑</button>
        <button disabled={busy || index === total - 1} onClick={() => onMove(index, 1)} title="往後移" style={{ ...smallBtn, padding: "2px 8px", opacity: busy || index === total - 1 ? 0.3 : 1 }}>↓</button>
        {!step.is_active && <span style={pill("#94a3b8", "rgba(255,255,255,0.08)")}>已停用</span>}
        {step.tool_status && step.tool_status !== "published" && <span style={pill("#fbbf24", "rgba(245,158,11,0.15)")}>機器人未發布</span>}
        <span style={{ fontSize: "11px", color: "#94a3b8" }}>{step.attempt_count} 筆學生作答</span>
        <span style={{ marginLeft: "auto", display: "flex", gap: "6px" }}>
          <button disabled={busy} onClick={() => onSave(step, { is_active: !step.is_active }, step.is_active ? "已停用，學生看不到這一步" : "已重新啟用")} style={{ ...smallBtn, color: step.is_active ? "#fbbf24" : "#4ade80" }}>
            {step.is_active ? "停用" : "啟用"}
          </button>
          <button
            disabled={busy || !step.can_delete}
            onClick={() => onDelete(step)}
            title={step.can_delete ? "刪除這一步" : `已有 ${step.attempt_count} 筆學生作答，不能刪除；請改用停用`}
            style={{ ...smallBtn, color: "#f87171", opacity: busy || !step.can_delete ? 0.35 : 1 }}
          >
            刪除
          </button>
        </span>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "8px", marginBottom: "8px" }}>
        <input maxLength={100} value={form.step_title} onChange={(e) => setForm({ ...form, step_title: e.target.value })} style={input} />
        <select
          value={form.tool_id}
          disabled={locked}
          title={locked ? "已有學生作答，不能更換機器人；請停用這一步再新增一個" : undefined}
          onChange={(e) => setForm({ ...form, tool_id: e.target.value })}
          style={{ ...input, backgroundColor: "#0f172a", opacity: locked ? 0.6 : 1 }}
        >
          {toolOptions.map((t) => (
            <option key={t.id} value={t.id}>
              {toolLabel(t)}
            </option>
          ))}
        </select>
      </div>

      <div style={{ display: "flex", gap: "12px", alignItems: "center", flexWrap: "wrap" }}>
        <StepOptions form={form} onChange={(c) => setForm({ ...form, ...c })} />
        <button disabled={busy || !dirty} onClick={handleSave} style={{ ...smallBtn, marginLeft: "auto", ...(dirty ? { backgroundColor: "#2563eb", color: "#fff", border: "none" } : { opacity: 0.4 }) }}>
          儲存變更
        </button>
      </div>
      {error && <p style={{ margin: "6px 0 0", fontSize: "12px", color: "#fca5a5" }}>{error}</p>}
    </div>
  );
}
