"use client";

// 教師端：課程選擇（卡片由左上往右排列，最後一張是「＋ 建立課程」）；機器人申請在上方導覽列
// 點進課程後在 /admin/courses/[courseId] 管理成員、邀請碼、指派模組
// 後端：GET /api/courses、POST /api/courses（backend/api/courses.py）

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { apiFetch } from "@/utils/api";
import AdminHeader from "./AdminHeader";
import { type Course, errorText, input, pill } from "./shared";

// 課程卡片（長方形），與「＋ 建立課程」共用尺寸
const tile: React.CSSProperties = {
  display: "flex",
  flexDirection: "column",
  minHeight: "150px",
  padding: "18px",
  borderRadius: "14px",
  font: "inherit",
  color: "inherit",
};

// 說明文字最多兩行，卡片高度才會一致
const clamp2: React.CSSProperties = {
  margin: "6px 0 0",
  fontSize: "12px",
  color: "#94a3b8",
  lineHeight: 1.5,
  display: "-webkit-box",
  WebkitLineClamp: 2,
  WebkitBoxOrient: "vertical",
  overflow: "hidden",
};

export default function AdminCoursesPage() {
  const router = useRouter();

  const [courses, setCourses] = useState<Course[] | null>(null);
  const [showArchived, setShowArchived] = useState(false);
  const [errorMsg, setErrorMsg] = useState("");

  // 建立課程對話框
  const [showCreate, setShowCreate] = useState(false);
  const [form, setForm] = useState({ title: "", description: "", require_approval: false });
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState("");

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

  useEffect(() => {
    loadCourses();
  }, [loadCourses]);

  const openCreate = () => {
    setForm({ title: "", description: "", require_approval: false });
    setCreateError("");
    setShowCreate(true);
  };
  const closeCreate = () => {
    if (!creating) setShowCreate(false);
  };

  // 對話框開著時按 Esc 關閉
  useEffect(() => {
    if (!showCreate) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setShowCreate(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [showCreate]);

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!form.title.trim()) return;
    setCreating(true);
    setCreateError("");
    try {
      const res = await apiFetch<{ data: Course; message: string }>("/api/courses", {
        method: "POST",
        body: JSON.stringify({
          title: form.title.trim(),
          description: form.description.trim() || null,
          require_approval: form.require_approval,
        }),
      });
      // 新課程沒有邀請碼，直接進課程的邀請碼分頁並打開產生表單
      router.push(`/admin/courses/${encodeURIComponent(res.data.id)}?tab=invites&new=1`);
    } catch (err) {
      setCreateError(`建立失敗：${errorText(err, "請稍後再試")}`);
      setCreating(false);
    }
  };

  return (
    <div style={{ minHeight: "100vh", backgroundColor: "#0f172a", color: "#f8fafc", fontFamily: "sans-serif", colorScheme: "dark" }}>
      <AdminHeader />

      <main style={{ maxWidth: "1100px", margin: "24px auto", padding: "0 20px", display: "flex", flexDirection: "column", gap: "20px" }}>
        <div>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-end", gap: "12px", marginBottom: "16px" }}>
            <div>
              <h1 style={{ fontSize: "20px", fontWeight: "bold", margin: "0 0 4px 0" }}>我的課程</h1>
              <p style={{ fontSize: "12px", color: "#94a3b8", margin: 0 }}>點選課程管理成員、邀請碼與指派模組。</p>
            </div>
            <label style={{ flexShrink: 0, fontSize: "12px", color: "#94a3b8", display: "flex", alignItems: "center", gap: "6px", cursor: "pointer" }}>
              <input type="checkbox" checked={showArchived} onChange={(e) => setShowArchived(e.target.checked)} />
              顯示已封存
            </label>
          </div>

          {errorMsg && <p style={{ margin: "0 0 16px", fontSize: "13px", color: "#fca5a5" }}>{errorMsg}</p>}
          {courses === null && <p style={{ fontSize: "13px", color: "#94a3b8" }}>載入中…</p>}

          {/* 課程卡片：由左上往右排列，排滿換行；最後一張是「＋ 建立課程」 */}
          {courses !== null && (
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(240px, 1fr))", gap: "16px" }}>
              {courses.map((c) => {
                const usableCodes = c.invite_codes.filter((code) => code.is_active && !code.expired).length;
                return (
                  <button
                    key={c.id}
                    type="button"
                    onClick={() => router.push(`/admin/courses/${encodeURIComponent(c.id)}`)}
                    className="course-card"
                    style={{
                      ...tile,
                      textAlign: "left",
                      alignItems: "stretch",
                      justifyContent: "space-between",
                      gap: "12px",
                      backgroundColor: "#1e293b",
                      border: "1px solid rgba(255,255,255,0.12)",
                      cursor: "pointer",
                      opacity: c.is_archived ? 0.65 : 1,
                    }}
                  >
                    <div style={{ minWidth: 0 }}>
                      <div style={{ display: "flex", gap: "6px", flexWrap: "wrap", marginBottom: "8px" }}>
                        {c.is_archived && <span style={pill("#94a3b8", "rgba(255,255,255,0.08)")}>已封存</span>}
                        {c.pending_count > 0 && <span style={pill("#fbbf24", "rgba(245,158,11,0.15)")}>{c.pending_count} 筆待審核</span>}
                        {!c.is_archived && usableCodes === 0 && <span style={pill("#f87171", "rgba(239,68,68,0.15)")}>沒有可用的邀請碼</span>}
                      </div>
                      <div style={{ fontWeight: "bold", fontSize: "16px", lineHeight: 1.4 }}>{c.title}</div>
                      {c.description && <p style={clamp2}>{c.description}</p>}
                    </div>
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: "8px", fontSize: "12px" }}>
                      <span style={{ color: "#94a3b8" }}>
                        已加入 {c.active_count} 人・邀請碼 {c.invite_codes.length} 組
                      </span>
                      <span style={{ fontWeight: "bold", color: "#4ade80" }}>管理 →</span>
                    </div>
                  </button>
                );
              })}

              <button
                type="button"
                onClick={openCreate}
                className="course-card"
                style={{ ...tile, alignItems: "center", justifyContent: "center", gap: "6px", backgroundColor: "transparent", border: "2px dashed rgba(148,163,184,0.45)", color: "#94a3b8", cursor: "pointer" }}
              >
                <span style={{ fontSize: "40px", lineHeight: 1, fontWeight: 300 }}>＋</span>
                <span style={{ fontSize: "14px", fontWeight: "bold" }}>建立課程</span>
                <span style={{ fontSize: "12px", color: "#64748b" }}>建立後再產生邀請碼給學生</span>
              </button>
            </div>
          )}
        </div>
      </main>

      {/* 建立課程對話框 */}
      {showCreate && (
        <div
          role="dialog"
          aria-modal="true"
          aria-labelledby="create-title"
          onClick={closeCreate}
          style={{ position: "fixed", inset: 0, zIndex: 50, display: "flex", alignItems: "center", justifyContent: "center", padding: "16px", backgroundColor: "rgba(2,6,23,0.7)" }}
        >
          <form
            onSubmit={handleCreate}
            onClick={(e) => e.stopPropagation()}
            style={{ width: "100%", maxWidth: "440px", padding: "24px", borderRadius: "16px", backgroundColor: "#1e293b", border: "1px solid rgba(255,255,255,0.12)", display: "flex", flexDirection: "column", gap: "12px" }}
          >
            <div>
              <h2 id="create-title" style={{ fontSize: "17px", fontWeight: "bold", margin: "0 0 6px 0" }}>建立課程</h2>
              <p style={{ fontSize: "12px", color: "#94a3b8", margin: 0 }}>建立後會進入課程的邀請碼分頁，產生邀請碼給學生加入。</p>
            </div>
            <input
              autoFocus
              required
              maxLength={100}
              placeholder="課程名稱，例如：學前 IEP 撰寫能力訓練"
              value={form.title}
              onChange={(e) => setForm({ ...form, title: e.target.value })}
              style={input}
            />
            <textarea
              rows={4}
              placeholder="課程簡介（選填，學生進入課程時會看到）"
              value={form.description}
              onChange={(e) => setForm({ ...form, description: e.target.value })}
              style={{ ...input, resize: "vertical" }}
            />
            <label style={{ fontSize: "12px", display: "flex", alignItems: "center", gap: "6px", cursor: "pointer" }}>
              <input type="checkbox" checked={form.require_approval} onChange={(e) => setForm({ ...form, require_approval: e.target.checked })} />
              學生輸入邀請碼後需老師核准才能加入
            </label>
            {createError && <p style={{ margin: 0, fontSize: "13px", color: "#fca5a5" }}>{createError}</p>}
            <div style={{ display: "flex", justifyContent: "flex-end", gap: "8px", marginTop: "6px" }}>
              <button
                type="button"
                onClick={closeCreate}
                style={{ padding: "9px 16px", borderRadius: "8px", border: "1px solid rgba(255,255,255,0.2)", background: "none", color: "#cbd5e1", fontSize: "13px", cursor: "pointer" }}
              >
                取消
              </button>
              <button
                type="submit"
                disabled={creating || !form.title.trim()}
                style={{ padding: "9px 20px", borderRadius: "8px", border: "none", backgroundColor: "#16a34a", color: "#fff", fontWeight: "bold", fontSize: "13px", cursor: creating ? "not-allowed" : "pointer", opacity: creating || !form.title.trim() ? 0.5 : 1 }}
              >
                {creating ? "建立中…" : "建立"}
              </button>
            </div>
          </form>
        </div>
      )}

      {/* 卡片滑過與鍵盤焦點的效果（inline style 做不到 :hover） */}
      <style>{`
        .course-card { transition: transform 0.15s, border-color 0.15s; }
        .course-card:hover { transform: translateY(-2px); border-color: #4ade80 !important; }
        .course-card:focus-visible { outline: 2px solid #4ade80; outline-offset: 2px; }
      `}</style>
    </div>
  );
}
