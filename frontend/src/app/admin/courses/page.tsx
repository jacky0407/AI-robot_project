"use client";

// 教師端：課程管理（建立課程、邀請碼、審核加入申請、移出成員、封存）
// 後端：/api/courses、/api/courses/{id}、/api/courses/{id}/members（backend/api/courses.py）

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { createClient } from "@/utils/supabase/client";
import { ApiError, apiFetch } from "@/utils/api";

type Course = {
  id: string;
  title: string;
  description: string | null;
  invite_code: string;
  require_approval: boolean;
  is_archived: boolean;
  created_at: string;
  active_count: number;
  pending_count: number;
};

type MemberStatus = "pending" | "active" | "rejected" | "removed";
type Member = {
  user_id: string;
  full_name: string | null;
  email: string | null;
  status: MemberStatus;
  joined_at: string | null;
};
type MemberAction = "approve" | "reject" | "remove";

const MEMBER_STATUS_LABEL: Record<MemberStatus, string> = {
  pending: "待審核",
  active: "已加入",
  rejected: "已拒絕",
  removed: "已移出",
};

const panel: React.CSSProperties = { backgroundColor: "#fff", borderRadius: "12px", padding: "20px", border: "1px solid #e2e8f0" };
const input: React.CSSProperties = { width: "100%", boxSizing: "border-box", padding: "8px 10px", borderRadius: "6px", border: "1px solid #cbd5e1", fontSize: "13px" };
const smallBtn: React.CSSProperties = { padding: "4px 10px", fontSize: "12px", borderRadius: "6px", border: "1px solid #cbd5e1", backgroundColor: "#fff", color: "#1e293b", cursor: "pointer" };

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

  // 成員
  const [openCourse, setOpenCourse] = useState<string | null>(null);
  const [members, setMembers] = useState<Member[] | null>(null);
  const [memberError, setMemberError] = useState("");
  const [busyMember, setBusyMember] = useState<string | null>(null);

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
      setCreateMsg(`${res.message}，邀請碼：${res.data.invite_code}`);
      setForm({ title: "", description: "", require_approval: false });
      await loadCourses();
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
    loadMembers(courseId);
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
    <div style={{ minHeight: "100vh", backgroundColor: "#f8fafc", color: "#1e293b", fontFamily: "sans-serif" }}>
      <header style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "14px 24px", backgroundColor: "#fff", borderBottom: "1px solid #e2e8f0" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
          <span style={{ backgroundColor: "#16a34a", color: "#fff", padding: "4px 8px", borderRadius: "6px", fontSize: "12px", fontWeight: "bold" }}>
            管理與複核後台
          </span>
          <span style={{ fontWeight: "bold", fontSize: "16px" }}>課程管理</span>
          <Link href="/admin/tools" style={{ fontSize: "13px", color: "#2563eb", textDecoration: "none", marginLeft: "8px" }}>
            機器人與複核 →
          </Link>
        </div>
        <button onClick={handleSignOut} style={{ ...smallBtn, padding: "6px 12px" }}>
          登出
        </button>
      </header>

      {errorMsg && (
        <div style={{ maxWidth: "1200px", margin: "16px auto 0", padding: "10px 14px", borderRadius: "8px", backgroundColor: "#fef2f2", border: "1px solid #fecaca", color: "#b91c1c", fontSize: "13px" }}>
          {errorMsg}
        </div>
      )}

      <div style={{ maxWidth: "1200px", margin: "24px auto", padding: "0 16px", display: "grid", gridTemplateColumns: "2fr 1fr", gap: "20px", alignItems: "start" }}>
        {/* 左：課程清單 */}
        <div style={panel}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "16px" }}>
            <h3 style={{ fontSize: "15px", fontWeight: "bold", margin: 0 }}>所有課程</h3>
            <label style={{ fontSize: "12px", color: "#64748b", display: "flex", alignItems: "center", gap: "6px", cursor: "pointer" }}>
              <input type="checkbox" checked={showArchived} onChange={(e) => setShowArchived(e.target.checked)} />
              顯示已封存
            </label>
          </div>

          {courses === null && <p style={{ fontSize: "13px", color: "#64748b" }}>載入中…</p>}
          {courses?.length === 0 && !errorMsg && <p style={{ fontSize: "13px", color: "#64748b" }}>尚未建立任何課程，請從右側建立。</p>}

          <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
            {courses?.map((c) => (
              <div key={c.id} style={{ borderRadius: "10px", border: "1px solid #e2e8f0", backgroundColor: c.is_archived ? "#f1f5f9" : "#f8fafc" }}>
                <div style={{ padding: "14px", display: "flex", justifyContent: "space-between", gap: "12px" }}>
                  <div style={{ minWidth: 0 }}>
                    <div style={{ display: "flex", alignItems: "center", gap: "8px", flexWrap: "wrap" }}>
                      <span style={{ fontWeight: "bold", fontSize: "15px" }}>{c.title}</span>
                      {c.is_archived && <span style={{ fontSize: "11px", padding: "1px 8px", borderRadius: "9999px", backgroundColor: "#e2e8f0", color: "#475569" }}>已封存</span>}
                      {c.pending_count > 0 && (
                        <span style={{ fontSize: "11px", padding: "1px 8px", borderRadius: "9999px", backgroundColor: "#fef3c7", color: "#92400e" }}>
                          {c.pending_count} 筆待審核
                        </span>
                      )}
                    </div>
                    {c.description && <p style={{ margin: "6px 0 0", fontSize: "12px", color: "#64748b" }}>{c.description}</p>}
                    <div style={{ display: "flex", alignItems: "center", gap: "8px", marginTop: "10px", fontSize: "12px", color: "#475569", flexWrap: "wrap" }}>
                      邀請碼
                      <code style={{ fontSize: "14px", fontWeight: "bold", letterSpacing: "2px", padding: "2px 8px", borderRadius: "4px", backgroundColor: "#dbeafe", color: "#1e40af" }}>{c.invite_code}</code>
                      <button onClick={() => copyCode(c.invite_code)} style={{ ...smallBtn, padding: "2px 8px", fontSize: "11px" }}>
                        {copied === c.invite_code ? "已複製" : "複製"}
                      </button>
                      <span style={{ marginLeft: "8px" }}>已加入 {c.active_count} 人</span>
                    </div>
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
                      <button onClick={() => toggleMembers(c.id)} style={{ ...smallBtn, backgroundColor: openCourse === c.id ? "#0f172a" : "#fff", color: openCourse === c.id ? "#fff" : "#1e293b" }}>
                        {openCourse === c.id ? "收合成員" : "成員與審核"}
                      </button>
                      <button
                        disabled={busyCourse === c.id}
                        onClick={() => updateCourse(c, { is_archived: !c.is_archived })}
                        style={{ ...smallBtn, color: c.is_archived ? "#16a34a" : "#dc2626" }}
                      >
                        {c.is_archived ? "取消封存" : "封存"}
                      </button>
                    </div>
                  </div>
                </div>

                {openCourse === c.id && (
                  <div style={{ borderTop: "1px solid #e2e8f0", padding: "14px", backgroundColor: "#fff", borderRadius: "0 0 10px 10px" }}>
                    {members === null && <p style={{ fontSize: "13px", color: "#64748b", margin: 0 }}>載入成員中…</p>}
                    {memberError && <p style={{ fontSize: "13px", color: "#b91c1c", margin: 0 }}>{memberError}</p>}
                    {members?.length === 0 && !memberError && <p style={{ fontSize: "13px", color: "#64748b", margin: 0 }}>目前還沒有學生申請或加入。</p>}

                    {memberGroups.map(({ status, title }) => {
                      const list = members?.filter((m) => m.status === status) ?? [];
                      if (list.length === 0) return null;
                      return (
                        <div key={status} style={{ marginBottom: "12px" }}>
                          <h4 style={{ fontSize: "13px", fontWeight: "bold", margin: "0 0 8px 0", color: status === "pending" ? "#92400e" : "#334155" }}>
                            {title}（{list.length}）
                          </h4>
                          <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
                            {list.map((m) => (
                              <div key={m.user_id} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: "8px", fontSize: "13px", padding: "8px 10px", borderRadius: "6px", backgroundColor: "#f8fafc" }}>
                                <span>
                                  {m.full_name || "（未填姓名）"}
                                  <span style={{ fontSize: "12px", color: "#64748b", marginLeft: "8px" }}>{m.email}</span>
                                  <span style={{ fontSize: "11px", color: "#94a3b8", marginLeft: "8px" }}>{MEMBER_STATUS_LABEL[m.status]}</span>
                                </span>
                                <span style={{ display: "flex", gap: "6px", flexShrink: 0 }}>
                                  {m.status !== "active" && (
                                    <button disabled={busyMember === m.user_id} onClick={() => handleMemberAction(c.id, m, "approve")} style={{ ...smallBtn, backgroundColor: "#16a34a", color: "#fff", border: "none" }}>
                                      {m.status === "pending" ? "核准" : "重新加入"}
                                    </button>
                                  )}
                                  {m.status === "pending" && (
                                    <button disabled={busyMember === m.user_id} onClick={() => handleMemberAction(c.id, m, "reject")} style={{ ...smallBtn, color: "#dc2626" }}>
                                      拒絕
                                    </button>
                                  )}
                                  {m.status === "active" && (
                                    <button disabled={busyMember === m.user_id} onClick={() => handleMemberAction(c.id, m, "remove")} style={{ ...smallBtn, color: "#dc2626" }}>
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
            style={{ padding: "8px 0", backgroundColor: "#0f172a", color: "#fff", border: "none", borderRadius: "6px", fontSize: "13px", fontWeight: "bold", cursor: "pointer", opacity: creating || !form.title.trim() ? 0.5 : 1 }}
          >
            {creating ? "建立中…" : "+ 建立課程並產生邀請碼"}
          </button>
          {createMsg && <p style={{ margin: 0, fontSize: "12px", color: createMsg.startsWith("建立失敗") ? "#b91c1c" : "#15803d" }}>{createMsg}</p>}
        </form>
      </div>
    </div>
  );
}
