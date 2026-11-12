"use client";

// 學生：我的課程 + 輸入邀請碼加入課程
// 後端：GET /api/courses/mine、POST /api/courses/join（backend/api/courses.py）

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { createClient } from "@/utils/supabase/client";
import { ApiError, apiFetch } from "@/utils/api";
import StudentModeTabs from "../StudentModeTabs";

type MemberStatus = "pending" | "active" | "rejected" | "removed";

type MyCourse = {
  course_id: string;
  course_name: string | null;
  description: string | null;
  is_archived: boolean;
  status: MemberStatus;
  joined_at: string | null;
  // 加入時用的邀請碼（backend/api/courses.py）；沒有邀請碼的舊資料這幾個是 null，不限期、不限次數
  plan: "free" | "self_key" | null;
  daily_quota: number | null;
  access_until: string | null;
  access_expired: boolean;
};

const STATUS_LABEL: Record<MemberStatus, { text: string; color: string; bg: string }> = {
  active: { text: "已加入", color: "#4ade80", bg: "rgba(34,197,94,0.15)" },
  pending: { text: "等待老師核准", color: "#fbbf24", bg: "rgba(245,158,11,0.15)" },
  rejected: { text: "申請未通過", color: "#f87171", bg: "rgba(239,68,68,0.15)" },
  removed: { text: "已被移出", color: "#94a3b8", bg: "rgba(255,255,255,0.08)" },
};

// 課程卡片（長方形），與「＋ 加入課程」共用尺寸
const tile: React.CSSProperties = {
  display: "flex",
  flexDirection: "column",
  minHeight: "150px",
  padding: "18px",
  borderRadius: "14px",
  font: "inherit",
  color: "inherit",
};

// 說明文字最多兩行，超過以「…」截斷，卡片高度才會一致
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

export default function StudentCoursesPage() {
  const router = useRouter();
  const [supabase] = useState(() => createClient());

  const [courses, setCourses] = useState<MyCourse[] | null>(null);
  const [loadError, setLoadError] = useState("");

  const [inviteCode, setInviteCode] = useState("");
  const [joining, setJoining] = useState(false);
  const [joinError, setJoinError] = useState("");
  const [joinInfo, setJoinInfo] = useState("");
  const [showJoin, setShowJoin] = useState(false);

  const openJoin = () => {
    setInviteCode("");
    setJoinError("");
    setJoinInfo("");
    setShowJoin(true);
  };
  const closeJoin = () => {
    if (!joining) setShowJoin(false);
  };

  // 對話框開著時按 Esc 關閉
  useEffect(() => {
    if (!showJoin) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setShowJoin(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [showJoin]);

  // 用 .then 寫：setState 都在非同步回呼裡，避免在 effect 裡同步 setState
  const loadCourses = useCallback(
    () =>
      apiFetch<{ data: MyCourse[] }>("/api/courses/mine").then(
        (res) => {
          setCourses(res.data);
          setLoadError("");
        },
        (err) => {
          setCourses([]);
          setLoadError(
            err instanceof ApiError
              ? `無法讀取課程：${err.message}`
              : "無法連線到後端（請確認 FastAPI 已啟動，且 NEXT_PUBLIC_API_URL 設定正確）"
          );
        }
      ),
    []
  );

  useEffect(() => {
    loadCourses();
  }, [loadCourses]);

  const handleJoin = async (e: React.FormEvent) => {
    e.preventDefault();
    const code = inviteCode.trim().toUpperCase();
    if (!code) return;

    setJoining(true);
    setJoinError("");
    setJoinInfo("");
    try {
      const res = await apiFetch<{ data: { course_name: string; status: MemberStatus }; message: string }>(
        "/api/courses/join",
        { method: "POST", body: JSON.stringify({ invite_code: code }) }
      );
      setJoinInfo(`${res.data.course_name}：${res.message}`);
      setInviteCode("");
      setShowJoin(false);
      await loadCourses();
    } catch (err) {
      setJoinError(err instanceof ApiError ? err.message : "加入失敗，請確認網路連線後再試一次");
    } finally {
      setJoining(false);
    }
  };

  const handleSignOut = async () => {
    await supabase.auth.signOut();
    router.push("/");
    router.refresh();
  };

  const visible = (courses ?? []).filter((c) => c.status !== "removed");

  return (
    <div style={{ minHeight: "100vh", backgroundColor: "#0f172a", color: "#f8fafc", fontFamily: "sans-serif" }}>
      <header
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          padding: "14px 28px",
          borderBottom: "1px solid rgba(255,255,255,0.1)",
          backgroundColor: "#1e293b",
        }}
      >
        <StudentModeTabs />
        <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
          <Link href="/student/profile" style={{ fontSize: "12px", color: "#cbd5e1", textDecoration: "none" }}>
            個人資料
          </Link>
          <button
            onClick={handleSignOut}
            style={{ padding: "6px 12px", fontSize: "12px", borderRadius: "6px", border: "1px solid rgba(255,255,255,0.2)", background: "none", color: "#cbd5e1", cursor: "pointer" }}
          >
            登出
          </button>
        </div>
      </header>

      <main style={{ maxWidth: "1100px", margin: "28px auto", padding: "0 20px" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-end", gap: "12px", marginBottom: "16px" }}>
          <div>
            <h1 style={{ fontSize: "20px", fontWeight: "bold", margin: "0 0 4px 0" }}>我的課程</h1>
            <p style={{ fontSize: "12px", color: "#94a3b8", margin: 0 }}>點選課程開始練習。正式課程不限練習次數，成績由老師最終判定。</p>
          </div>
          <button onClick={loadCourses} style={{ flexShrink: 0, fontSize: "12px", color: "#38bdf8", background: "none", border: "none", cursor: "pointer" }}>
            重新整理
          </button>
        </div>

        {joinInfo && (
          <p style={{ margin: "0 0 16px", padding: "10px 14px", borderRadius: "8px", fontSize: "13px", color: "#86efac", backgroundColor: "rgba(34,197,94,0.12)" }}>{joinInfo}</p>
        )}
        {loadError && <p style={{ margin: "0 0 16px", fontSize: "13px", color: "#fca5a5" }}>{loadError}</p>}
        {courses === null && <p style={{ fontSize: "13px", color: "#94a3b8" }}>載入中…</p>}

        {/* 課程卡片：由左上往右排列，排滿換行；最後一張是「＋ 加入課程」 */}
        {courses !== null && (
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(240px, 1fr))", gap: "16px" }}>
            {visible.map((c) => {
              const label = STATUS_LABEL[c.status];
              const canEnter = c.status === "active" && !c.is_archived && !c.access_expired;
              const hint =
                c.is_archived ? "課程已封存"
                : c.status === "pending" ? "等老師核准後就能進入"
                : c.status === "rejected" ? "可按「＋」重新輸入邀請碼申請"
                : c.access_expired ? "可用日期已過，請向老師索取新的邀請碼"
                : "";
              return (
                <button
                  key={c.course_id}
                  type="button"
                  disabled={!canEnter}
                  onClick={() => router.push(`/student/modules?course=${encodeURIComponent(c.course_id)}`)}
                  title={canEnter ? "進入練習" : hint}
                  className="course-card"
                  style={{
                    ...tile,
                    textAlign: "left",
                    alignItems: "stretch",
                    justifyContent: "space-between",
                    gap: "12px",
                    backgroundColor: "#1e293b",
                    border: `1px solid ${canEnter ? "rgba(56,189,248,0.35)" : "rgba(255,255,255,0.1)"}`,
                    cursor: canEnter ? "pointer" : "default",
                    opacity: canEnter ? 1 : 0.65,
                  }}
                >
                  <div style={{ minWidth: 0 }}>
                    <div style={{ display: "flex", gap: "6px", flexWrap: "wrap", marginBottom: "8px" }}>
                      <span style={{ fontSize: "11px", padding: "2px 8px", borderRadius: "9999px", color: label.color, backgroundColor: label.bg }}>{label.text}</span>
                      {c.is_archived && (
                        <span style={{ fontSize: "11px", padding: "2px 8px", borderRadius: "9999px", color: "#94a3b8", backgroundColor: "rgba(255,255,255,0.08)" }}>已封存</span>
                      )}
                      {c.access_until && (
                        <span style={{ fontSize: "11px", padding: "2px 8px", borderRadius: "9999px", color: c.access_expired ? "#f87171" : "#94a3b8", backgroundColor: "rgba(255,255,255,0.08)" }}>
                          {c.access_expired ? "已過可用日期" : `可用到 ${c.access_until}`}
                        </span>
                      )}
                      {c.plan && (
                        <span style={{ fontSize: "11px", padding: "2px 8px", borderRadius: "9999px", color: "#94a3b8", backgroundColor: "rgba(255,255,255,0.08)" }}>
                          {c.plan === "free" ? `每天 ${c.daily_quota} 次` : "需自備金鑰"}
                        </span>
                      )}
                    </div>
                    <div style={{ fontWeight: "bold", fontSize: "16px", color: "#f8fafc", lineHeight: 1.4 }}>{c.course_name || "（未命名課程）"}</div>
                    {c.description && <p style={clamp2}>{c.description}</p>}
                  </div>
                  <span style={{ fontSize: "12px", fontWeight: "bold", color: canEnter ? "#38bdf8" : "#64748b" }}>{canEnter ? "進入練習 →" : hint}</span>
                </button>
              );
            })}

            <button
              type="button"
              onClick={openJoin}
              className="course-card"
              style={{ ...tile, alignItems: "center", justifyContent: "center", gap: "6px", backgroundColor: "transparent", border: "2px dashed rgba(148,163,184,0.45)", color: "#94a3b8", cursor: "pointer" }}
            >
              <span style={{ fontSize: "40px", lineHeight: 1, fontWeight: 300 }}>＋</span>
              <span style={{ fontSize: "14px", fontWeight: "bold" }}>加入課程</span>
              <span style={{ fontSize: "12px", color: "#64748b" }}>輸入老師提供的邀請碼</span>
            </button>
          </div>
        )}
      </main>

      {/* 加入課程對話框 */}
      {showJoin && (
        <div
          role="dialog"
          aria-modal="true"
          aria-labelledby="join-title"
          onClick={closeJoin}
          style={{ position: "fixed", inset: 0, zIndex: 50, display: "flex", alignItems: "center", justifyContent: "center", padding: "16px", backgroundColor: "rgba(2,6,23,0.7)" }}
        >
          <form
            onSubmit={handleJoin}
            onClick={(e) => e.stopPropagation()}
            style={{ width: "100%", maxWidth: "400px", padding: "24px", borderRadius: "16px", backgroundColor: "#1e293b", border: "1px solid rgba(255,255,255,0.12)" }}
          >
            <h2 id="join-title" style={{ fontSize: "17px", fontWeight: "bold", margin: "0 0 6px 0" }}>加入課程</h2>
            <p style={{ fontSize: "12px", color: "#94a3b8", margin: "0 0 16px 0" }}>輸入老師提供的邀請碼。有些課程需要老師核准後才能進入。</p>
            <input
              autoFocus
              value={inviteCode}
              onChange={(e) => setInviteCode(e.target.value.toUpperCase())}
              placeholder="例如 AB3K9XQZ"
              maxLength={32}
              autoComplete="off"
              style={{ width: "100%", boxSizing: "border-box", padding: "12px", borderRadius: "8px", border: "1px solid rgba(255,255,255,0.2)", backgroundColor: "rgba(255,255,255,0.05)", color: "#fff", fontSize: "16px", letterSpacing: "3px", textAlign: "center", outline: "none" }}
            />
            {joinError && <p style={{ margin: "10px 0 0", fontSize: "13px", color: "#fca5a5" }}>{joinError}</p>}
            <div style={{ display: "flex", justifyContent: "flex-end", gap: "8px", marginTop: "18px" }}>
              <button
                type="button"
                onClick={closeJoin}
                style={{ padding: "9px 16px", borderRadius: "8px", border: "1px solid rgba(255,255,255,0.2)", background: "none", color: "#cbd5e1", fontSize: "13px", cursor: "pointer" }}
              >
                取消
              </button>
              <button
                type="submit"
                disabled={joining || !inviteCode.trim()}
                style={{ padding: "9px 20px", borderRadius: "8px", border: "none", backgroundColor: "#2563eb", color: "#fff", fontWeight: "bold", fontSize: "13px", cursor: joining ? "not-allowed" : "pointer", opacity: joining || !inviteCode.trim() ? 0.5 : 1 }}
              >
                {joining ? "送出中…" : "加入"}
              </button>
            </div>
          </form>
        </div>
      )}

      {/* 卡片滑過與鍵盤焦點的效果（inline style 做不到 :hover） */}
      <style>{`
        .course-card { transition: transform 0.15s, border-color 0.15s, background-color 0.15s; }
        .course-card:not(:disabled):hover { transform: translateY(-2px); border-color: #38bdf8 !important; }
        .course-card:focus-visible { outline: 2px solid #38bdf8; outline-offset: 2px; }
      `}</style>
    </div>
  );
}
