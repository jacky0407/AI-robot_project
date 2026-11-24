"use client";

// 課程詳細頁 → 模組管理：單一模組的編輯（原本「模組管理」頁的右半邊）
// 後端：/api/modules（backend/api/modules.py）
// 新增、修改、刪除只限教授；助教看得到但送出會被後端擋下（403）

import { useCallback, useEffect, useState } from "react";
import { ApiError, apiFetch } from "@/utils/api";
import ChatBotsPanel from "./ChatBotsPanel";

export type ModuleSummary = {
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
  pass_mode?: PassMode;
  step_type?: StepType;
  is_active: boolean;
  tool_title: string | null;
  tool_status: string | null;
  // 停在機器人的第幾版（修改機器人時選的，#89）；null = 跟著最新版
  tool_version?: number | null;
  attempt_count: number;
  can_delete: boolean;
};

type ModuleDetail = Omit<ModuleSummary, "step_count" | "active_step_count"> & { steps: Step[] };

export type Tool = { id: string; title: string; status: string };

// 關卡怎麼算通過（#65，migration 019）
type PassMode = "score" | "complete" | "teacher_review";
const PASS_MODE_OPTIONS: { value: PassMode; label: string }[] = [
  { value: "score", label: "分數達到通過分數" },
  { value: "complete", label: "送出作答即通過" },
  { value: "teacher_review", label: "老師複核後才通過" },
];

// 關卡類型（#68，migration 020）：作答關卡／多機器人聊天室
type StepType = "answer" | "chat";

type StepForm = {
  step_type: StepType;
  tool_id: string;
  step_title: string;
  is_required: boolean;
  pass_score: string;
  require_teacher_review: boolean;
  pass_mode: PassMode;
};

const EMPTY_STEP: StepForm = { step_type: "chat", tool_id: "", step_title: "", is_required: true, pass_score: "70", require_teacher_review: false, pass_mode: "score" };

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

// 單一模組的編輯區：名稱與說明、發布、刪除、關卡（步驟）的新增／排序／停用／刪除
export default function ModuleEditor({
  moduleId,
  tools,
  onChanged,
  onDeleted,
}: {
  moduleId: string;
  tools: Tool[];
  onChanged: () => void;
  onDeleted: () => void;
}) {
  const [detail, setDetail] = useState<ModuleDetail | null>(null);
  const [loadError, setLoadError] = useState("");
  const [notice, setNotice] = useState<{ text: string; ok: boolean } | null>(null);
  const [busy, setBusy] = useState(false);
  // 每次重新載入模組詳情就 +1，讓關卡列用新的資料重新初始化
  const [version, setVersion] = useState(0);
  const [moduleForm, setModuleForm] = useState({ title: "", description: "" });
  const [newStep, setNewStep] = useState<StepForm>(EMPTY_STEP);

  const showDetail = useCallback((d: ModuleDetail) => {
    setDetail(d);
    setModuleForm({ title: d.title, description: d.description ?? "" });
    setVersion((v) => v + 1);
  }, []);

  const loadDetail = useCallback(
    () =>
      apiFetch<{ data: ModuleDetail }>(`/api/modules/${moduleId}`).then(
        (res) => showDetail(res.data),
        (err) => setLoadError(`無法讀取模組：${errorText(err, "請稍後再試")}`)
      ),
    [moduleId, showDetail]
  );

  useEffect(() => {
    loadDetail();
  }, [loadDetail]);

  // 所有寫入共用：成功後更新詳情與外層清單，失敗顯示後端的訊息
  const run = async (action: () => Promise<{ data?: ModuleDetail; message?: string } | void>, okText: string) => {
    setBusy(true);
    setNotice(null);
    try {
      const res = await action();
      if (res && res.data) showDetail(res.data);
      setNotice({ text: (res && res.message) || okText, ok: true });
      onChanged();
      return true;
    } catch (err) {
      setNotice({ text: errorText(err, "操作失敗，請稍後再試"), ok: false });
      await loadDetail();
      return false;
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
    if (!detail || !window.confirm(`確定要刪除模組「${detail.title}」嗎？\n\n所有課程對這個模組的指派都會一起取消，刪除後無法復原。\n只想讓這門課不用它，請改按「移出課程」。`)) return;
    const ok = await run(() => apiFetch<{ message: string }>(`/api/modules/${detail.id}`, { method: "DELETE" }), "已刪除模組");
    if (ok) onDeleted();
  };

  const handleAddStep = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!detail) return;
    const pass = parsePassScore(newStep.pass_score);
    if (!newStep.tool_id || !newStep.step_title.trim()) {
      setNotice({ text: "請選擇機器人並填寫關卡名稱", ok: false });
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
      "已新增關卡"
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

  const saveStep = (step: Step, changes: Record<string, unknown>, okText = "已儲存關卡") =>
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
    run(() => apiFetch<{ data: ModuleDetail }>(`/api/modules/${detail.id}/steps/${step.id}`, { method: "DELETE" }), "已刪除關卡");
  };

  if (loadError) return <p style={{ margin: 0, fontSize: "13px", color: "#fca5a5" }}>{loadError}</p>;
  if (!detail) return <p style={{ margin: 0, fontSize: "13px", color: "#94a3b8" }}>載入中…</p>;

  const moduleFormChanged = moduleForm.title !== detail.title || moduleForm.description !== (detail.description ?? "");

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "18px" }}>
      {/* 模組資料與發布 */}
      <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
        <input maxLength={100} value={moduleForm.title} onChange={(e) => setModuleForm({ ...moduleForm, title: e.target.value })} style={{ ...input, fontSize: "15px", fontWeight: "bold" }} />
        <textarea rows={2} maxLength={1000} placeholder="模組說明（選填，學生在課程簡介會看到）" value={moduleForm.description} onChange={(e) => setModuleForm({ ...moduleForm, description: e.target.value })} style={{ ...input, resize: "vertical" }} />
        <div style={{ display: "flex", gap: "8px", flexWrap: "wrap", alignItems: "center" }}>
          <button
            disabled={busy || !moduleFormChanged || !moduleForm.title.trim()}
            onClick={() => patchModule({ title: moduleForm.title, description: moduleForm.description }, "已儲存模組名稱與說明")}
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

      {/* 關卡清單 */}
      <div>
        <h4 style={{ fontSize: "14px", fontWeight: "bold", margin: "0 0 4px 0" }}>關卡（{detail.steps.length}）</h4>
        <p style={{ fontSize: "12px", color: "#94a3b8", margin: "0 0 10px 0" }}>
          學生要通過前面的必修關卡，下一關才會解鎖。已有學生作答的關卡不能刪除或更換機器人，請改用「停用」：學生會看不到，作答紀錄完整保留。
        </p>
        {detail.steps.length === 0 && <p style={{ fontSize: "13px", color: "#94a3b8" }}>還沒有關卡，請從下方新增。</p>}
        <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
          {detail.steps.map((s, i) => (
            <StepRow key={`${s.id}-${version}`} moduleId={detail.id} step={s} index={i} total={detail.steps.length} tools={tools} busy={busy} onMove={moveStep} onSave={saveStep} onDelete={deleteStep} />
          ))}
        </div>
      </div>

      {/* 新增關卡 */}
      <form onSubmit={handleAddStep} style={{ display: "flex", flexDirection: "column", gap: "8px", padding: "14px", borderRadius: "10px", border: "1px dashed rgba(255,255,255,0.2)" }}>
        <h4 style={{ fontSize: "13px", fontWeight: "bold", margin: 0 }}>新增關卡（排在最後）</h4>
        <div style={{ display: "flex", gap: "16px", fontSize: "12px", flexWrap: "wrap" }}>
          <label style={{ display: "flex", alignItems: "center", gap: "6px", cursor: "pointer" }}>
            <input type="radio" checked={newStep.step_type === "chat"} onChange={() => setNewStep({ ...newStep, step_type: "chat" })} />
            聊天室（多個機器人對話，評分機器人評整段對話）
          </label>
          <label style={{ display: "flex", alignItems: "center", gap: "6px", cursor: "pointer" }}>
            <input type="radio" checked={newStep.step_type === "answer"} onChange={() => setNewStep({ ...newStep, step_type: "answer" })} />
            作答關卡（寫完一份作答再送出）
          </label>
        </div>
        <div className="me-two" style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "8px" }}>
          <select value={newStep.tool_id} onChange={(e) => setNewStep({ ...newStep, tool_id: e.target.value })} style={{ ...input, backgroundColor: "#0f172a" }}>
            <option value="">
              {tools.length ? (newStep.step_type === "chat" ? "選擇評分機器人…（對話機器人新增後再設定）" : "選擇這一關使用的機器人…") : "還沒有機器人，請先到「建立機器人」建立"}
            </option>
            {tools.map((t) => (
              <option key={t.id} value={t.id}>
                {toolLabel(t)}
              </option>
            ))}
          </select>
          <input maxLength={100} placeholder="關卡名稱，例如：優勢與需求分析" value={newStep.step_title} onChange={(e) => setNewStep({ ...newStep, step_title: e.target.value })} style={input} />
        </div>
        <StepOptions form={newStep} onChange={(changes) => setNewStep({ ...newStep, ...changes })} />
        <button type="submit" disabled={busy} style={{ ...primaryBtn, alignSelf: "flex-start", opacity: busy ? 0.5 : 1 }}>
          ＋ 新增關卡
        </button>
      </form>
    </div>
  );
}

// 必修、通過分數、教師審核點（新增與編輯共用）
function StepOptions({ form, onChange }: { form: Pick<StepForm, "is_required" | "pass_score" | "require_teacher_review" | "pass_mode">; onChange: (changes: Partial<StepForm>) => void }) {
  return (
    <div style={{ display: "flex", gap: "16px", flexWrap: "wrap", alignItems: "center", fontSize: "12px" }}>
      <label style={{ display: "flex", alignItems: "center", gap: "6px", cursor: "pointer" }}>
        <input type="checkbox" checked={form.is_required} onChange={(e) => onChange({ is_required: e.target.checked })} />
        必修（沒通過會擋住後面的關卡）
      </label>
      <label style={{ display: "flex", alignItems: "center", gap: "6px" }}>
        怎麼算通過
        <select value={form.pass_mode} onChange={(e) => onChange({ pass_mode: e.target.value as PassMode })} style={{ ...input, width: "auto" }}>
          {PASS_MODE_OPTIONS.map((o) => (
            <option key={o.value} value={o.value} style={{ color: "#000" }}>
              {o.label}
            </option>
          ))}
        </select>
      </label>
      <label style={{ display: "flex", alignItems: "center", gap: "6px" }} title="機器人沒有評分規準時，「分數達到通過分數」會當作「送出作答即通過」">
        通過分數
        <input type="number" min={0} max={100} value={form.pass_score} onChange={(e) => onChange({ pass_score: e.target.value })} onFocus={(e) => e.target.select()} style={{ ...input, width: "70px" }} />
      </label>
      <label style={{ display: "flex", alignItems: "center", gap: "6px", cursor: "pointer" }}>
        <input type="checkbox" checked={form.require_teacher_review} onChange={(e) => onChange({ require_teacher_review: e.target.checked })} />
        必須老師審核（作答一律進待複核）
      </label>
    </div>
  );
}

function StepRow({
  moduleId,
  step,
  index,
  total,
  tools,
  busy,
  onMove,
  onSave,
  onDelete,
}: {
  moduleId: string;
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
    step_type: step.step_type ?? "answer",
    tool_id: step.tool_id,
    step_title: step.step_title,
    is_required: step.is_required,
    pass_score: String(step.pass_score ?? 70),
    require_teacher_review: step.require_teacher_review,
    pass_mode: step.pass_mode ?? "score",
  });
  const [error, setError] = useState("");

  const changes: Record<string, unknown> = {};
  if (form.tool_id !== step.tool_id) changes.tool_id = form.tool_id;
  if (form.step_title !== step.step_title) changes.step_title = form.step_title;
  if (form.is_required !== step.is_required) changes.is_required = form.is_required;
  if (form.pass_score !== String(step.pass_score ?? 70)) changes.pass_score = form.pass_score;
  if (form.require_teacher_review !== step.require_teacher_review) changes.require_teacher_review = form.require_teacher_review;
  if (form.pass_mode !== (step.pass_mode ?? "score")) changes.pass_mode = form.pass_mode;
  if (form.step_type !== (step.step_type ?? "answer")) changes.step_type = form.step_type;
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
        <span style={{ fontSize: "12px", fontWeight: "bold", color: "#4ade80", minWidth: "52px" }}>第 {step.step_order} 關</span>
        <button disabled={busy || index === 0} onClick={() => onMove(index, -1)} title="往前移" style={{ ...smallBtn, padding: "2px 8px", opacity: busy || index === 0 ? 0.3 : 1 }}>↑</button>
        <button disabled={busy || index === total - 1} onClick={() => onMove(index, 1)} title="往後移" style={{ ...smallBtn, padding: "2px 8px", opacity: busy || index === total - 1 ? 0.3 : 1 }}>↓</button>
        {step.step_type === "chat" && <span style={pill("#7dd3fc", "rgba(14,165,233,0.15)")}>聊天室</span>}
        {!step.is_active && <span style={pill("#94a3b8", "rgba(255,255,255,0.08)")}>已停用</span>}
        {step.tool_status && step.tool_status !== "published" && <span style={pill("#fbbf24", "rgba(245,158,11,0.15)")}>機器人未發布</span>}
        {step.tool_version ? (
          <span title="修改機器人時選擇讓這個模組停在舊版；要改用新版，請到機器人編輯頁再存一次並勾選這個模組" style={pill("#c4b5fd", "rgba(139,92,246,0.15)")}>
            停在機器人第 {step.tool_version} 版
          </span>
        ) : null}
        <span style={{ fontSize: "11px", color: "#94a3b8" }}>{step.attempt_count} 筆學生作答</span>
        <span style={{ marginLeft: "auto", display: "flex", gap: "6px" }}>
          <button disabled={busy} onClick={() => onSave(step, { is_active: !step.is_active }, step.is_active ? "已停用，學生看不到這一關" : "已重新啟用")} style={{ ...smallBtn, color: step.is_active ? "#fbbf24" : "#4ade80" }}>
            {step.is_active ? "停用" : "啟用"}
          </button>
          <button
            disabled={busy || !step.can_delete}
            onClick={() => onDelete(step)}
            title={step.can_delete ? "刪除這一關" : `已有 ${step.attempt_count} 筆學生作答，不能刪除；請改用停用`}
            style={{ ...smallBtn, color: "#f87171", opacity: busy || !step.can_delete ? 0.35 : 1 }}
          >
            刪除
          </button>
        </span>
      </div>

      <div className="me-two" style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "8px", marginBottom: "8px" }}>
        <input maxLength={100} value={form.step_title} onChange={(e) => setForm({ ...form, step_title: e.target.value })} style={input} />
        <select
          value={form.tool_id}
          disabled={locked}
          title={locked ? "已有學生作答，不能更換機器人；請停用這一關再新增一個" : undefined}
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
      {!locked && (
        <label style={{ display: "flex", alignItems: "center", gap: "6px", marginTop: "8px", fontSize: "12px", color: "#94a3b8", cursor: "pointer" }}>
          <input type="checkbox" checked={form.step_type === "chat"} onChange={(e) => setForm({ ...form, step_type: e.target.checked ? "chat" : "answer" })} />
          改成聊天室（上面選的機器人會變成評分機器人）
        </label>
      )}
      {step.step_type === "chat" && (
        <ChatBotsPanel moduleId={moduleId} stepId={step.id} evaluatorToolId={step.tool_id} evaluatorTitle={step.tool_title} tools={tools} />
      )}
    </div>
  );
}
