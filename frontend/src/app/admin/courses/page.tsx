"use client";

// 教師端：課程管理（建立課程、產生與管理邀請碼、審核加入申請、移出成員、封存）
// 後端：/api/courses、/api/courses/{id}、/api/courses/{id}/invite-codes、/api/courses/{id}/members（backend/api/courses.py）
// 邀請碼：一門課可以有多組，各自設定可用日期與方案（免費每日次數／自備金鑰），建立課程時不會自動產生

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { createClient } from "@/utils/supabase/client";
import { ApiError, apiFetch } from "@/utils/api";
import AdminNav from "../AdminNav";

type InvitePlan = "free" | "self_key";
type InviteCode = {
  id: string;
  code: string;
  plan: InvitePlan;
  daily_quota: number | null;
  access_until: string;
  is_active: boolean;
  expired: boolean;
  member_count: number;
};

type Course = {
  id: string;
  title: string;
  description: string | null;
  require_approval: boolean;
  is_archived: boolean;
  created_at: string;
  active_count: number;
  pending_count: number;
  invite_codes: InviteCode[];
};

type MemberStatus = "pending" | "active" | "rejected" | "removed";
type Member = {
  user_id: string;
  full_name: string | null;
  email: string | null;
  status: MemberStatus;
  joined_at: string | null;
  invite_code: string | null;
};

type CodeForm = { courseId: string; plan: InvitePlan; access_until: string; daily_quota: string };

const planLabel = (c: Pick<InviteCode, "plan" | "daily_quota">) =>
  c.plan === "free" ? `免費・每天 ${c.daily_quota} 次` : "自備金鑰・不限次數";

// 本機（台灣）的今天，YYYY-MM-DD；可用日期不能早於今天
const todayLocal = () => new Date().toLocaleDateString("sv-SE");
type MemberAction = "approve" | "reject" | "remove";

type AssignedModule = { module_id: string; title: string | null; is_published: boolean };
type ModuleOption = { id: string; title: string; is_published: boolean };

const MEMBER_STATUS_LABEL: Record<MemberStatus, string> = {
  pending: "待審核",
  active: "已加入",
  rejected: "已拒絕",
  removed: "已移出",
};

const panel: React.CSSProperties = { backgroundColor: "#1e293b", borderRadius: "12px", padding: "20px", border: "1px solid rgba(255,255,255,0.1)" };
const input: React.CSSProperties = { width: "100%", boxSizing: "border-box", padding: "8px 10px", borderRadius: "6px", border: "1px solid rgba(255,255,255,0.2)", backgroundColor: "rgba(255,255,255,0.05)", color: "#fff", fontSize: "13px", outline: "none" };
const smallBtn: React.CSSProperties = { padding: "4px 10px", fontSize: "12px", borderRadius: "6px", border: "1px solid rgba(255,255,255,0.2)", backgroundColor: "transparent", color: "#cbd5e1", cursor: "pointer" };

const errorText = (err: unknown, fallback: string) => (err instanceof ApiError ? err.message : fallback);

export default function AdminCoursesPage() {
  const router = useRouter();
  const [supabase] = useState(() => createClient());

  const [courses, setCourses] = useState<Course[] | null>(null);
  const [showArchived, setShowArchived] = useState(false);
  const [errorMsg, setErrorMsg] = useState("");
  const [busyCourse, setBusyCourse] = useState<string | null>(null);
  const [copied, setCopied] = useState<string | null>(null);

  // 建立課程
  const [form, setForm] = useState({ title: "", description: "", require_approval: false });
  const [creating, setCreating] = useState(false);
  const [createMsg, setCreateMsg] = useState("");

  // 邀請碼
  const [codeForm, setCodeForm] = useState<CodeForm | null>(null);
  const [busyCode, setBusyCode] = useState<string | null>(null);
  const [codeMsg, setCodeMsg] = useState<{ courseId: string; text: string } | null>(null);

  // 成員
  const [openCourse, setOpenCourse] = useState<string | null>(null);
  const [members, setMembers] = useState<Member[] | null>(null);
  const [memberError, setMemberError] = useState("");
  const [busyMember, setBusyMember] = useState<string | null>(null);

  // 指派模組
  const [assigned, setAssigned] = useState<AssignedModule[] | null>(null);
  const [allModules, setAllModules] = useState<ModuleOption[]>([]);
  const [moduleToAssign, setModuleToAssign] = useState("");
  const [moduleMsg, setModuleMsg] = useState("");
  const [busyModule, setBusyModule] = useState(false);

  const loadAssigned = useCallback(async (courseId: string) => {
    setAssigned(null);
    try {
      const res = await apiFetch<{ data: AssignedModule[] }>(`/api/courses/${courseId}/modules`);
      setAssigned(res.data);
    } catch (err) {
      setAssigned([]);
      setModuleMsg(`無法讀取已指派模組：${errorText(err, "請稍後再試")}`);
    }
  }, []);

  // 所有模組（含未發布）：教師可直接讀 learning_modules（migrations/005 的 RLS 允許教師看全部）
  const loadAllModules = useCallback(async () => {
    const { data, error } = await supabase.from("learning_modules").select("id, title, is_published").order("created_at");
    if (error) {
      setModuleMsg(`無法讀取模組清單：${error.message}`);
      return;
    }
    setAllModules(data ?? []);
  }, [supabase]);

  // 用 .then 寫：setState 都在非同步回呼裡，避免在 effect 裡同步 setState
  const loadCourses = useCallback(
    () =>
      apiFetch<{ data: Course[] }>(`/api/courses?include_archived=${showArchived}`).then(
        (res) => {
          setCourses(res.data);
          setErrorMsg("");
        },
        (err) => {
          setCourses([]);
          setErrorMsg(errorText(err, "無法連線到後端（請確認 FastAPI 已啟動，且 NEXT_PUBLIC_API_URL 設定正確）"));
        }
      ),
    [showArchived]
  );

  const loadMembers = useCallback(async (courseId: string) => {
    setMembers(null);
    setMemberError("");
    try {
      const res = await apiFetch<{ data: Member[] }>(`/api/courses/${courseId}/members`);
      setMembers(res.data);
    } catch (err) {
      setMembers([]);
      setMemberError(errorText(err, "無法讀取成員名單"));
    }
  }, []);

  useEffect(() => {
    loadCourses();
  }, [loadCourses]);

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!form.title.trim()) return;
    setCreating(true);
    setCreateMsg("");
    try {
      const res = await apiFetch<{ data: Course; message: string }>("/api/courses", {
        method: "POST",
        body: JSON.stringify({
          title: form.title.trim(),
          description: form.description.trim() || null,
          require_approval: form.require_approval,
        }),
      });
      setCreateMsg(res.message);
      setForm({ title: "", description: "", require_approval: false });
      await loadCourses();
      // 新課程沒有邀請碼，直接打開它的「產生邀請碼」表單
      openCodeForm(res.data.id);
    } catch (err) {
      setCreateMsg(`建立失敗：${errorText(err, "請稍後再試")}`);
    } finally {
      setCreating(false);
    }
  };

  const updateCourse = async (course: Course, changes: Partial<Pick<Course, "require_approval" | "is_archived">>) => {
    if (changes.is_archived && !confirm(`確定要封存「${course.title}」嗎？封存後學生無法再用邀請碼加入。`)) return;
    setBusyCourse(course.id);
    try {
      await apiFetch(`/api/courses/${course.id}`, { method: "PATCH", body: JSON.stringify(changes) });
      await loadCourses();
    } catch (err) {
      alert(`更新失敗：${errorText(err, "請稍後再試")}`);
    } finally {
      setBusyCourse(null);
    }
  };

  const openCodeForm = (courseId: string) => {
    setCodeMsg(null);
    setCodeForm({ courseId, plan: "free", access_until: "", daily_quota: "10" });
  };

  const handleCreateCode = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!codeForm) return;
    setBusyCode(codeForm.courseId);
    try {
      const res = await apiFetch<{ data: InviteCode; message: string }>(`/api/courses/${codeForm.courseId}/invite-codes`, {
        method: "POST",
        body: JSON.stringify({
          plan: codeForm.plan,
          access_until: codeForm.access_until,
          daily_quota: codeForm.plan === "free" ? Number(codeForm.daily_quota) : null,
        }),
      });
      setCodeMsg({ courseId: codeForm.courseId, text: `${res.message}：${res.data.code}` });
      setCodeForm(null);
      await loadCourses();
    } catch (err) {
      setCodeMsg({ courseId: codeForm.courseId, text: `產生失敗：${errorText(err, "請稍後再試")}` });
    } finally {
      setBusyCode(null);
    }
  };

  // 停用／啟用、改可用日期或每日次數；已用這組碼加入的學生跟著變
  const updateCode = async (
    courseId: string,
    code: InviteCode,
    changes: Partial<Pick<InviteCode, "is_active" | "access_until" | "daily_quota">>
  ) => {
    setBusyCode(code.id);
    setCodeMsg(null);
    try {
      await apiFetch(`/api/courses/${courseId}/invite-codes/${code.id}`, { method: "PATCH", body: JSON.stringify(changes) });
      await loadCourses();
    } catch (err) {
      setCodeMsg({ courseId, text: `更新失敗：${errorText(err, "請稍後再試")}` });
    } finally {
      setBusyCode(null);
    }
  };

  // 刪除：從介面移除，並把用這組碼加入的學生移出課程（作答紀錄保留）；只想停止新的人加入請用「停用」
  const deleteCode = async (courseId: string, code: InviteCode) => {
    const who = code.member_count > 0 ? `用這組碼加入的 ${code.member_count} 位學生會被移出課程（作答紀錄保留）。` : "目前沒有學生用這組碼加入。";
    if (!window.confirm(`確定要刪除邀請碼「${code.code}」嗎？\n\n${who}\n刪除後無法復原。只想停止新的人加入，請改用「停用」。`)) return;
    setBusyCode(code.id);
    setCodeMsg(null);
    try {
      const res = await apiFetch<{ message: string }>(`/api/courses/${courseId}/invite-codes/${code.id}`, { method: "DELETE" });
      setCodeMsg({ courseId, text: res.message });
      await loadCourses();
    } catch (err) {
      setCodeMsg({ courseId, text: `刪除失敗：${errorText(err, "請稍後再試")}` });
    } finally {
      setBusyCode(null);
    }
  };

  const changeAccessUntil = (courseId: string, code: InviteCode) => {
    const value = prompt(`「${code.code}」的新可用日期（YYYY-MM-DD）\n用這組碼加入的 ${code.member_count} 位學生會一起改。`, code.access_until);
    if (value && value.trim() !== code.access_until) updateCode(courseId, code, { access_until: value.trim() });
  };

  const changeDailyQuota = (courseId: string, code: InviteCode) => {
    const value = prompt(`「${code.code}」的每日作答次數（1–1000）\n用這組碼加入的 ${code.member_count} 位學生會一起改。`, String(code.daily_quota ?? 10));
    if (!value) return;
    const quota = Number(value);
    if (!Number.isInteger(quota) || quota < 1 || quota > 1000) {
      alert("每日次數要是 1 到 1000 的整數");
      return;
    }
    if (quota !== code.daily_quota) updateCode(courseId, code, { daily_quota: quota });
  };

  const copyCode = async (code: string) => {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(code);
      setTimeout(() => setCopied(null), 1500);
    } catch {
      // 不支援剪貼簿（非 HTTPS）時，老師可以直接手動選取邀請碼
    }
  };

  const toggleMembers = (courseId: string) => {
    if (openCourse === courseId) {
      setOpenCourse(null);
      return;
    }
    setOpenCourse(courseId);
    setModuleMsg("");
    setModuleToAssign("");
    loadMembers(courseId);
    loadAssigned(courseId);
    loadAllModules();
  };

  const handleMemberAction = async (courseId: string, member: Member, action: MemberAction) => {
    if (action === "remove" && !confirm(`確定要將「${member.full_name || member.email}」移出課程嗎？`)) return;
    setBusyMember(member.user_id);
    try {
      await apiFetch(`/api/courses/${courseId}/members/${member.user_id}`, {
        method: "PATCH",
        body: JSON.stringify({ action }),
      });
      await Promise.all([loadMembers(courseId), loadCourses()]);
    } catch (err) {
      alert(`操作失敗：${errorText(err, "請稍後再試")}`);
    } finally {
      setBusyMember(null);
    }
  };

  const handleAssign = async (courseId: string) => {
    if (!moduleToAssign) return;
    setBusyModule(true);
    setModuleMsg("");
    try {
      const res = await apiFetch<{ message: string }>(`/api/courses/${courseId}/modules`, {
        method: "POST",
        body: JSON.stringify({ module_id: moduleToAssign }),
      });
      setModuleMsg(res.message);
      setModuleToAssign("");
      await loadAssigned(courseId);
    } catch (err) {
      setModuleMsg(`指派失敗：${errorText(err, "請稍後再試")}`);
    } finally {
      setBusyModule(false);
    }
  };

  const handleUnassign = async (courseId: string, m: AssignedModule) => {
    if (!confirm(`確定要取消指派「${m.title}」嗎？學生將看不到這個模組，但已作答的紀錄會保留。`)) return;
    setBusyModule(true);
    setModuleMsg("");
    try {
      await apiFetch(`/api/courses/${courseId}/modules/${m.module_id}`, { method: "DELETE" });
      await loadAssigned(courseId);
    } catch (err) {
      setModuleMsg(`取消指派失敗：${errorText(err, "請稍後再試")}`);
    } finally {
      setBusyModule(false);
    }
  };

  const handleSignOut = async () => {
    await supabase.auth.signOut();
    router.push("/");
    router.refresh();
  };

  const memberGroups: { status: MemberStatus; title: string }[] = [
    { status: "pending", title: "待審核申請" },
    { status: "active", title: "已加入成員" },
    { status: "rejected", title: "已拒絕" },
    { status: "removed", title: "已移出" },
  ];

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

      <div style={{ maxWidth: "1200px", margin: "24px auto", padding: "0 16px", display: "grid", gridTemplateColumns: "2fr 1fr", gap: "20px", alignItems: "start" }}>
        {/* 左：課程清單 */}
        <div style={panel}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "16px" }}>
            <h3 style={{ fontSize: "15px", fontWeight: "bold", margin: 0 }}>所有課程</h3>
            <label style={{ fontSize: "12px", color: "#94a3b8", display: "flex", alignItems: "center", gap: "6px", cursor: "pointer" }}>
              <input type="checkbox" checked={showArchived} onChange={(e) => setShowArchived(e.target.checked)} />
              顯示已封存
            </label>
          </div>

          {courses === null && <p style={{ fontSize: "13px", color: "#94a3b8" }}>載入中…</p>}
          {courses?.length === 0 && !errorMsg && <p style={{ fontSize: "13px", color: "#94a3b8" }}>尚未建立任何課程，請從右側建立。</p>}

          <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
            {courses?.map((c) => (
              <div key={c.id} style={{ borderRadius: "10px", border: "1px solid rgba(255,255,255,0.08)", backgroundColor: c.is_archived ? "rgba(255,255,255,0.02)" : "rgba(255,255,255,0.04)" }}>
                <div style={{ padding: "14px", display: "flex", justifyContent: "space-between", gap: "12px" }}>
                  <div style={{ minWidth: 0 }}>
                    <div style={{ display: "flex", alignItems: "center", gap: "8px", flexWrap: "wrap" }}>
                      <span style={{ fontWeight: "bold", fontSize: "15px" }}>{c.title}</span>
                      {c.is_archived && <span style={{ fontSize: "11px", padding: "1px 8px", borderRadius: "9999px", backgroundColor: "rgba(255,255,255,0.08)", color: "#94a3b8" }}>已封存</span>}
                      {c.pending_count > 0 && (
                        <span style={{ fontSize: "11px", padding: "1px 8px", borderRadius: "9999px", backgroundColor: "rgba(245,158,11,0.15)", color: "#fbbf24" }}>
                          {c.pending_count} 筆待審核
                        </span>
                      )}
                    </div>
                    {c.description && <p style={{ margin: "6px 0 0", fontSize: "12px", color: "#94a3b8" }}>{c.description}</p>}
                    <div style={{ marginTop: "8px", fontSize: "12px", color: "#cbd5e1" }}>已加入 {c.active_count} 人</div>
                  </div>
                  <div style={{ display: "flex", flexDirection: "column", gap: "6px", alignItems: "flex-end", flexShrink: 0 }}>
                    <label style={{ fontSize: "12px", display: "flex", alignItems: "center", gap: "6px", cursor: "pointer" }}>
                      <input
                        type="checkbox"
                        checked={c.require_approval}
                        disabled={busyCourse === c.id}
                        onChange={(e) => updateCourse(c, { require_approval: e.target.checked })}
                      />
                      加入需老師核准
                    </label>
                    <div style={{ display: "flex", gap: "6px" }}>
                      <button onClick={() => toggleMembers(c.id)} style={{ ...smallBtn, backgroundColor: openCourse === c.id ? "#16a34a" : "transparent", color: openCourse === c.id ? "#fff" : "#cbd5e1", border: openCourse === c.id ? "1px solid #16a34a" : smallBtn.border }}>
                        {openCourse === c.id ? "收合" : "模組與成員"}
                      </button>
                      <button
                        disabled={busyCourse === c.id}
                        onClick={() => updateCourse(c, { is_archived: !c.is_archived })}
                        style={{ ...smallBtn, color: c.is_archived ? "#4ade80" : "#f87171" }}
                      >
                        {c.is_archived ? "取消封存" : "封存"}
                      </button>
                    </div>
                  </div>
                </div>

                {/* 邀請碼：一門課可以有多組，各自的可用日期與方案 */}
                <div style={{ padding: "0 14px 14px", display: "flex", flexDirection: "column", gap: "6px" }}>
                  {c.invite_codes.length === 0 && (
                    <p style={{ margin: 0, fontSize: "12px", color: "#fbbf24" }}>還沒有邀請碼，學生無法加入這門課。</p>
                  )}
                  {c.invite_codes.map((code) => {
                    const dimmed = !code.is_active || code.expired;
                    return (
                      <div key={code.id} style={{ display: "flex", alignItems: "center", gap: "8px", flexWrap: "wrap", fontSize: "12px", padding: "6px 8px", borderRadius: "6px", backgroundColor: "rgba(255,255,255,0.03)", opacity: dimmed ? 0.6 : 1 }}>
                        <code style={{ fontSize: "14px", fontWeight: "bold", letterSpacing: "2px", padding: "2px 8px", borderRadius: "4px", backgroundColor: "rgba(56,189,248,0.15)", color: "#7dd3fc", textDecoration: code.is_active ? "none" : "line-through" }}>
                          {code.code}
                        </code>
                        {code.is_active && (
                          <button onClick={() => copyCode(code.code)} style={{ ...smallBtn, padding: "2px 8px", fontSize: "11px" }}>
                            {copied === code.code ? "已複製" : "複製"}
                          </button>
                        )}
                        <span style={{ color: "#cbd5e1" }}>{planLabel(code)}</span>
                        <span style={{ color: code.expired ? "#f87171" : "#94a3b8" }}>
                          {code.expired ? `已過可用日期（${code.access_until}）` : `可用到 ${code.access_until}`}
                        </span>
                        <span style={{ color: "#94a3b8" }}>{code.member_count} 人</span>
                        {!code.is_active && <span style={{ color: "#f87171" }}>已停用</span>}
                        <span style={{ marginLeft: "auto", display: "flex", gap: "6px" }}>
                          <button disabled={busyCode === code.id} onClick={() => changeAccessUntil(c.id, code)} style={{ ...smallBtn, padding: "2px 8px", fontSize: "11px" }}>
                            改日期
                          </button>
                          {code.plan === "free" && (
                            <button disabled={busyCode === code.id} onClick={() => changeDailyQuota(c.id, code)} style={{ ...smallBtn, padding: "2px 8px", fontSize: "11px" }}>
                              改次數
                            </button>
                          )}
                          <button
                            disabled={busyCode === code.id}
                            onClick={() => updateCode(c.id, code, { is_active: !code.is_active })}
                            title={code.is_active ? "停止用這組碼加入；已加入的學生不受影響" : "重新開放用這組碼加入"}
                            style={{ ...smallBtn, padding: "2px 8px", fontSize: "11px", color: code.is_active ? "#fbbf24" : "#4ade80" }}
                          >
                            {code.is_active ? "停用" : "啟用"}
                          </button>
                          <button
                            disabled={busyCode === code.id}
                            onClick={() => deleteCode(c.id, code)}
                            title="刪除這組碼，並移出用它加入的學生"
                            style={{ ...smallBtn, padding: "2px 8px", fontSize: "11px", color: "#f87171" }}
                          >
                            刪除
                          </button>
                        </span>
                      </div>
                    );
                  })}

                  {codeForm?.courseId === c.id ? (
                    <form onSubmit={handleCreateCode} style={{ display: "flex", alignItems: "center", gap: "8px", flexWrap: "wrap", fontSize: "12px", padding: "8px", borderRadius: "6px", border: "1px dashed rgba(56,189,248,0.4)" }}>
                      <select
                        value={codeForm.plan}
                        onChange={(e) => setCodeForm({ ...codeForm, plan: e.target.value as InvitePlan })}
                        style={{ ...input, width: "auto", backgroundColor: "#0f172a" }}
                      >
                        <option value="free">免費（平台金鑰）</option>
                        <option value="self_key">自備金鑰（學生自己的 Gemini）</option>
                      </select>
                      {codeForm.plan === "free" && (
                        <label style={{ display: "flex", alignItems: "center", gap: "4px" }}>
                          每天
                          <input
                            type="number"
                            required
                            min={1}
                            max={1000}
                            value={codeForm.daily_quota}
                            onChange={(e) => setCodeForm({ ...codeForm, daily_quota: e.target.value })}
                            style={{ ...input, width: "70px" }}
                          />
                          次
                        </label>
                      )}
                      <label style={{ display: "flex", alignItems: "center", gap: "4px" }}>
                        可用到
                        <input
                          type="date"
                          required
                          min={todayLocal()}
                          value={codeForm.access_until}
                          onChange={(e) => setCodeForm({ ...codeForm, access_until: e.target.value })}
                          style={{ ...input, width: "auto" }}
                        />
                      </label>
                      <button type="submit" disabled={busyCode === c.id} style={{ ...smallBtn, backgroundColor: "#0284c7", color: "#fff", border: "none" }}>
                        產生
                      </button>
                      <button type="button" onClick={() => setCodeForm(null)} style={smallBtn}>
                        取消
                      </button>
                      {codeForm.plan === "self_key" && (
                        <span style={{ width: "100%", color: "#fbbf24" }}>自備金鑰功能尚未開放，用這組碼加入的學生要等金鑰設定上線後才能作答。</span>
                      )}
                    </form>
                  ) : (
                    !c.is_archived && (
                      <button onClick={() => openCodeForm(c.id)} style={{ ...smallBtn, alignSelf: "flex-start", color: "#38bdf8", borderColor: "rgba(56,189,248,0.4)" }}>
                        ＋ 產生邀請碼
                      </button>
                    )
                  )}
                  {codeMsg?.courseId === c.id && (
                    <p style={{ margin: 0, fontSize: "12px", color: /失敗/.test(codeMsg.text) ? "#fca5a5" : "#86efac" }}>{codeMsg.text}</p>
                  )}
                </div>

                {openCourse === c.id && (
                  <div style={{ borderTop: "1px solid rgba(255,255,255,0.08)", padding: "14px", backgroundColor: "rgba(0,0,0,0.15)", borderRadius: "0 0 10px 10px" }}>
                    <div style={{ marginBottom: "16px", paddingBottom: "14px", borderBottom: "1px solid rgba(255,255,255,0.08)" }}>
                      <h4 style={{ fontSize: "13px", fontWeight: "bold", margin: "0 0 8px 0", color: "#38bdf8" }}>
                        指派的訓練模組{assigned ? `（${assigned.length}）` : ""}
                      </h4>
                      {assigned === null && <p style={{ fontSize: "13px", color: "#94a3b8", margin: 0 }}>載入中…</p>}
                      {assigned?.length === 0 && (
                        <p style={{ fontSize: "13px", color: "#94a3b8", margin: "0 0 8px 0" }}>還沒有指派模組，學生進入課程後會看不到任何練習。</p>
                      )}
                      <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
                        {assigned?.map((m) => (
                          <div key={m.module_id} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: "8px", fontSize: "13px", padding: "8px 10px", borderRadius: "6px", backgroundColor: "rgba(255,255,255,0.04)" }}>
                            <span>
                              {m.title || "（未命名模組）"}
                              {!m.is_published && (
                                <span style={{ fontSize: "11px", padding: "1px 8px", borderRadius: "9999px", backgroundColor: "rgba(245,158,11,0.15)", color: "#fbbf24", marginLeft: "8px" }}>
                                  未發布，學生暫時看不到
                                </span>
                              )}
                            </span>
                            <button disabled={busyModule} onClick={() => handleUnassign(c.id, m)} style={{ ...smallBtn, color: "#f87171" }}>
                              取消指派
                            </button>
                          </div>
                        ))}
                      </div>
                      {assigned && (() => {
                        const assignedIds = new Set(assigned.map((a) => a.module_id));
                        const options = allModules.filter((m) => !assignedIds.has(m.id));
                        return (
                          <div style={{ display: "flex", gap: "8px", marginTop: "10px" }}>
                            <select
                              value={moduleToAssign}
                              onChange={(e) => setModuleToAssign(e.target.value)}
                              style={{ ...input, flex: 1, width: "auto", backgroundColor: "#0f172a" }}
                            >
                              <option value="">{options.length ? "選擇要指派的模組…" : "沒有其他可指派的模組"}</option>
                              {options.map((m) => (
                                <option key={m.id} value={m.id}>
                                  {m.title}{m.is_published ? "" : "（未發布）"}
                                </option>
                              ))}
                            </select>
                            <button
                              disabled={busyModule || !moduleToAssign}
                              onClick={() => handleAssign(c.id)}
                              style={{ ...smallBtn, backgroundColor: "#16a34a", color: "#fff", border: "none", opacity: busyModule || !moduleToAssign ? 0.5 : 1 }}
                            >
                              指派
                            </button>
                          </div>
                        );
                      })()}
                      {moduleMsg && (
                        <p style={{ fontSize: "12px", margin: "8px 0 0", color: /失敗|無法/.test(moduleMsg) ? "#fca5a5" : "#86efac" }}>{moduleMsg}</p>
                      )}
                    </div>

                    {members === null && <p style={{ fontSize: "13px", color: "#94a3b8", margin: 0 }}>載入成員中…</p>}
                    {memberError && <p style={{ fontSize: "13px", color: "#fca5a5", margin: 0 }}>{memberError}</p>}
                    {members?.length === 0 && !memberError && <p style={{ fontSize: "13px", color: "#94a3b8", margin: 0 }}>目前還沒有學生申請或加入。</p>}

                    {memberGroups.map(({ status, title }) => {
                      const list = members?.filter((m) => m.status === status) ?? [];
                      if (list.length === 0) return null;
                      return (
                        <div key={status} style={{ marginBottom: "12px" }}>
                          <h4 style={{ fontSize: "13px", fontWeight: "bold", margin: "0 0 8px 0", color: status === "pending" ? "#fbbf24" : "#cbd5e1" }}>
                            {title}（{list.length}）
                          </h4>
                          <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
                            {list.map((m) => (
                              <div key={m.user_id} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: "8px", fontSize: "13px", padding: "8px 10px", borderRadius: "6px", backgroundColor: "rgba(255,255,255,0.04)" }}>
                                <span>
                                  {m.full_name || "（未填姓名）"}
                                  <span style={{ fontSize: "12px", color: "#94a3b8", marginLeft: "8px" }}>{m.email}</span>
                                  <span style={{ fontSize: "11px", color: "#64748b", marginLeft: "8px" }}>{MEMBER_STATUS_LABEL[m.status]}</span>
                                  {m.invite_code && (
                                    <span style={{ fontSize: "11px", color: "#64748b", marginLeft: "8px" }}>邀請碼 {m.invite_code}</span>
                                  )}
                                </span>
                                <span style={{ display: "flex", gap: "6px", flexShrink: 0 }}>
                                  {m.status !== "active" && (
                                    <button disabled={busyMember === m.user_id} onClick={() => handleMemberAction(c.id, m, "approve")} style={{ ...smallBtn, backgroundColor: "#16a34a", color: "#fff", border: "none" }}>
                                      {m.status === "pending" ? "核准" : "重新加入"}
                                    </button>
                                  )}
                                  {m.status === "pending" && (
                                    <button disabled={busyMember === m.user_id} onClick={() => handleMemberAction(c.id, m, "reject")} style={{ ...smallBtn, color: "#f87171" }}>
                                      拒絕
                                    </button>
                                  )}
                                  {m.status === "active" && (
                                    <button disabled={busyMember === m.user_id} onClick={() => handleMemberAction(c.id, m, "remove")} style={{ ...smallBtn, color: "#f87171" }}>
                                      移出
                                    </button>
                                  )}
                                </span>
                              </div>
                            ))}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>

        {/* 右：建立課程 */}
        <form onSubmit={handleCreate} style={{ ...panel, display: "flex", flexDirection: "column", gap: "10px" }}>
          <h3 style={{ fontSize: "14px", fontWeight: "bold", margin: 0 }}>建立新課程</h3>
          <input
            required
            maxLength={100}
            placeholder="課程名稱，例如：學前 IEP 撰寫能力訓練"
            value={form.title}
            onChange={(e) => setForm({ ...form, title: e.target.value })}
            style={input}
          />
          <textarea
            rows={3}
            placeholder="課程說明（選填）"
            value={form.description}
            onChange={(e) => setForm({ ...form, description: e.target.value })}
            style={{ ...input, resize: "vertical" }}
          />
          <label style={{ fontSize: "12px", display: "flex", alignItems: "center", gap: "6px", cursor: "pointer" }}>
            <input type="checkbox" checked={form.require_approval} onChange={(e) => setForm({ ...form, require_approval: e.target.checked })} />
            學生輸入邀請碼後需老師核准才能加入
          </label>
          <button
            type="submit"
            disabled={creating || !form.title.trim()}
            style={{ padding: "8px 0", backgroundColor: "#16a34a", color: "#fff", border: "none", borderRadius: "6px", fontSize: "13px", fontWeight: "bold", cursor: "pointer", opacity: creating || !form.title.trim() ? 0.5 : 1 }}
          >
            {creating ? "建立中…" : "+ 建立課程（之後再產生邀請碼）"}
          </button>
          {createMsg && <p style={{ margin: 0, fontSize: "12px", color: createMsg.startsWith("建立失敗") ? "#fca5a5" : "#86efac" }}>{createMsg}</p>}
        </form>
      </div>
    </div>
  );
}
