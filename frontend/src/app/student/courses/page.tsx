"use client";

// 學生：我的課程 + 輸入邀請碼加入課程
// 後端：GET /api/courses/mine、POST /api/courses/join（backend/api/courses.py）

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { createClient } from "@/utils/supabase/client";
import { ApiError, apiFetch } from "@/utils/api";

type MemberStatus = "pending" | "active" | "rejected" | "removed";

type MyCourse = {
  course_id: string;
  course_name: string | null;
  description: string | null;
  is_archived: boolean;
  status: MemberStatus;
  joined_at: string | null;
};

const STATUS_LABEL: Record<MemberStatus, { text: string; color: string; bg: string }> = {
  active: { text: "已加入", color: "#4ade80", bg: "rgba(34,197,94,0.15)" },
  pending: { text: "等待老師核准", color: "#fbbf24", bg: "rgba(245,158,11,0.15)" },
  rejected: { text: "申請未通過", color: "#f87171", bg: "rgba(239,68,68,0.15)" },
  removed: { text: "已被移出", color: "#94a3b8", bg: "rgba(255,255,255,0.08)" },
};

const card: React.CSSProperties = {
  backgroundColor: "#1e293b",
  borderRadius: "16px",
  border: "1px solid rgba(255,255,255,0.1)",
  padding: "20px",
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
        <div>
          <h1 style={{ fontSize: "16px", fontWeight: "bold", margin: 0 }}>我的課程</h1>
          <p style={{ fontSize: "12px", color: "#94a3b8", margin: 0 }}>選擇課程開始練習，或輸入老師提供的邀請碼加入新課程</p>
        </div>
        <button
          onClick={handleSignOut}
          style={{ padding: "6px 12px", fontSize: "12px", borderRadius: "6px", border: "1px solid rgba(255,255,255,0.2)", background: "none", color: "#cbd5e1", cursor: "pointer" }}
        >
          登出
        </button>
      </header>

      <main style={{ maxWidth: "880px", margin: "28px auto", padding: "0 16px", display: "flex", flexDirection: "column", gap: "20px" }}>
        {/* 輸入邀請碼 */}
        <section style={card}>
          <h2 style={{ fontSize: "15px", fontWeight: "bold", margin: "0 0 12px 0" }}>加入課程</h2>
          <form onSubmit={handleJoin} style={{ display: "flex", gap: "10px", flexWrap: "wrap" }}>
            <input
              value={inviteCode}
              onChange={(e) => setInviteCode(e.target.value.toUpperCase())}
              placeholder="輸入課程邀請碼，例如 AB3K9XQZ"
              maxLength={32}
              autoComplete="off"
              style={{ flex: 1, minWidth: "200px", padding: "10px 12px", borderRadius: "8px", border: "1px solid rgba(255,255,255,0.2)", backgroundColor: "rgba(255,255,255,0.05)", color: "#fff", fontSize: "14px", letterSpacing: "2px", outline: "none" }}
            />
            <button
              type="submit"
              disabled={joining || !inviteCode.trim()}
              style={{ padding: "10px 20px", borderRadius: "8px", border: "none", backgroundColor: "#2563eb", color: "#fff", fontWeight: "bold", fontSize: "14px", cursor: joining ? "not-allowed" : "pointer", opacity: joining || !inviteCode.trim() ? 0.5 : 1 }}
            >
              {joining ? "送出中…" : "加入"}
            </button>
          </form>
          {joinError && <p style={{ margin: "10px 0 0", fontSize: "13px", color: "#fca5a5" }}>{joinError}</p>}
          {joinInfo && <p style={{ margin: "10px 0 0", fontSize: "13px", color: "#86efac" }}>{joinInfo}</p>}
        </section>

        {/* 課程清單 */}
        <section style={card}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "12px" }}>
            <h2 style={{ fontSize: "15px", fontWeight: "bold", margin: 0 }}>已加入／申請中的課程</h2>
            <button onClick={loadCourses} style={{ fontSize: "12px", color: "#38bdf8", background: "none", border: "none", cursor: "pointer" }}>
              重新整理
            </button>
          </div>

          {loadError && <p style={{ fontSize: "13px", color: "#fca5a5" }}>{loadError}</p>}
          {courses === null && <p style={{ fontSize: "13px", color: "#94a3b8" }}>載入中…</p>}
          {courses !== null && !loadError && visible.length === 0 && (
            <p style={{ fontSize: "13px", color: "#94a3b8" }}>還沒有加入任何課程，請在上方輸入老師提供的邀請碼。</p>
          )}

          <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
            {visible.map((c) => {
              const label = STATUS_LABEL[c.status];
              const canEnter = c.status === "active" && !c.is_archived;
              return (
                <div
                  key={c.course_id}
                  style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: "12px", padding: "14px", borderRadius: "10px", backgroundColor: "rgba(255,255,255,0.04)", border: "1px solid rgba(255,255,255,0.08)" }}
                >
                  <div style={{ minWidth: 0 }}>
                    <div style={{ display: "flex", alignItems: "center", gap: "8px", flexWrap: "wrap" }}>
                      <span style={{ fontWeight: "bold", fontSize: "15px" }}>{c.course_name || "（未命名課程）"}</span>
                      <span style={{ fontSize: "11px", padding: "2px 8px", borderRadius: "9999px", color: label.color, backgroundColor: label.bg }}>{label.text}</span>
                      {c.is_archived && (
                        <span style={{ fontSize: "11px", padding: "2px 8px", borderRadius: "9999px", color: "#94a3b8", backgroundColor: "rgba(255,255,255,0.08)" }}>已封存</span>
                      )}
                    </div>
                    {c.description && <p style={{ margin: "6px 0 0", fontSize: "12px", color: "#94a3b8" }}>{c.description}</p>}
                    {c.status === "rejected" && (
                      <p style={{ margin: "6px 0 0", fontSize: "12px", color: "#94a3b8" }}>如需重新申請，請再輸入一次邀請碼。</p>
                    )}
                  </div>
                  <button
                    disabled={!canEnter}
                    onClick={() => router.push(`/student/modules?course=${encodeURIComponent(c.course_id)}`)}
                    style={{ flexShrink: 0, padding: "8px 16px", borderRadius: "8px", border: "none", backgroundColor: canEnter ? "#2563eb" : "rgba(255,255,255,0.08)", color: canEnter ? "#fff" : "#64748b", fontSize: "13px", fontWeight: "bold", cursor: canEnter ? "pointer" : "not-allowed" }}
                  >
                    進入練習 →
                  </button>
                </div>
              );
            })}
          </div>
        </section>
      </main>
    </div>
  );
}
