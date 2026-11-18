"use client";

// 教師端：單一課程管理。左邊是分頁列表，右邊是內容：
//   學習監控（原「監控總覽」）、待複核（原「機器人與複核」的複核清單）、成員清單、邀請碼清單、
//   模組管理（原「模組管理」＋指派模組）、資料匯出
// 目前分頁記在網址 ?tab=；建立課程後會帶 ?tab=invites&new=1 直接打開產生邀請碼
// 後端：GET /api/courses?include_archived=true（後端沒有單一課程的 GET，從列表找）、PATCH /api/courses/{id}

import { Suspense, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { apiFetch } from "@/utils/api";
import AdminHeader from "../AdminHeader";
import { type AssignedModule, type Course, errorText, panel, pill, smallBtn } from "../shared";
import MembersTab from "./MembersTab";
import InviteCodesTab from "./InviteCodesTab";
import ModulesTab from "./ModulesTab";
import MonitorTab from "./MonitorTab";
import ReviewTab from "./ReviewTab";
import ExportTab from "./ExportTab";
import MaterialsTab from "./MaterialsTab";

type Tab = "monitor" | "review" | "members" | "invites" | "modules" | "materials" | "export";
const TABS: Tab[] = ["monitor", "review", "members", "invites", "modules", "materials", "export"];
const isTab = (v: string | null): v is Tab => TABS.includes(v as Tab);

// useSearchParams() 必須包在 <Suspense> 裡，否則 next build 預先產生頁面時會失敗
export default function AdminCourseDetailPage() {
  return (
    <Suspense fallback={null}>
      <CourseDetail />
    </Suspense>
  );
}

function CourseDetail() {
  const router = useRouter();
  const params = useParams<{ courseId: string }>();
  const courseId = params.courseId;
  const searchParams = useSearchParams();
  const tabParam = searchParams.get("tab");
  const tab: Tab = isTab(tabParam) ? tabParam : "monitor";
  // 只在進頁時讀一次：剛建立課程時自動打開產生邀請碼表單
  const [openCodeForm] = useState(() => searchParams.get("new") === "1");

  const [course, setCourse] = useState<Course | null | undefined>(undefined); // undefined = 載入中，null = 找不到
  const [loadError, setLoadError] = useState("");
  const [assigned, setAssigned] = useState<AssignedModule[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [reviewCount, setReviewCount] = useState<number | null>(null);

  const loadCourse = useCallback(
    () =>
      apiFetch<{ data: Course[] }>("/api/courses?include_archived=true").then(
        (res) => {
          setCourse(res.data.find((c) => c.id === courseId) ?? null);
          setLoadError("");
        },
        (err) => {
          setCourse(null);
          setLoadError(errorText(err, "無法連線到後端（請確認 FastAPI 已啟動，且 NEXT_PUBLIC_API_URL 設定正確）"));
        }
      ),
    [courseId]
  );

  const loadAssigned = useCallback(
    () =>
      apiFetch<{ data: AssignedModule[] }>(`/api/courses/${encodeURIComponent(courseId)}/modules`).then(
        (res) => setAssigned(res.data),
        () => setAssigned([])
      ),
    [courseId]
  );

  useEffect(() => {
    loadCourse();
    loadAssigned();
  }, [loadCourse, loadAssigned]);

  // 待複核筆數（分頁上的標示）；打開「待複核」分頁時由分頁自己更新
  useEffect(() => {
    let active = true;
    apiFetch<{ data: unknown[] }>(`/api/review/pending?course_id=${encodeURIComponent(courseId)}`).then(
      (res) => active && setReviewCount(res.data.length),
      () => active && setReviewCount(null)
    );
    return () => {
      active = false;
    };
  }, [courseId]);

  const switchTab = (t: Tab) => router.replace(`?tab=${t}`, { scroll: false });

  const updateCourse = async (changes: Partial<Pick<Course, "require_approval" | "is_archived">>) => {
    if (!course) return;
    if (changes.is_archived && !confirm(`確定要封存「${course.title}」嗎？封存後學生無法再用邀請碼加入，也不能再作答。`)) return;
    setBusy(true);
    try {
      await apiFetch(`/api/courses/${encodeURIComponent(course.id)}`, { method: "PATCH", body: JSON.stringify(changes) });
      await loadCourse();
    } catch (err) {
      alert(`更新失敗：${errorText(err, "請稍後再試")}`);
    } finally {
      setBusy(false);
    }
  };

  // badge：需要老師處理的事（待審核加入、待複核）用琥珀色標示
  const tabItems: { id: Tab; label: string; count?: number; alert?: boolean; badge?: string }[] = [
    { id: "monitor", label: "學習監控" },
    { id: "review", label: "待複核", badge: reviewCount ? `${reviewCount}` : undefined },
    { id: "members", label: "成員清單", count: course?.active_count, badge: course?.pending_count ? `${course.pending_count} 待審` : undefined },
    { id: "invites", label: "邀請碼清單", count: course?.invite_codes.length, alert: course ? course.invite_codes.length === 0 && !course.is_archived : false },
    { id: "modules", label: "模組管理", count: assigned?.length, alert: assigned?.length === 0 },
    { id: "materials", label: "教材" },
    { id: "export", label: "資料匯出" },
  ];

  return (
    <div style={{ minHeight: "100vh", backgroundColor: "#0f172a", color: "#f8fafc", fontFamily: "sans-serif", colorScheme: "dark" }}>
      <AdminHeader />

      <main style={{ maxWidth: "1280px", margin: "24px auto", padding: "0 20px", display: "flex", flexDirection: "column", gap: "16px" }}>
        <Link href="/admin/courses" style={{ alignSelf: "flex-start", fontSize: "13px", color: "#4ade80", textDecoration: "none" }}>
          ← 所有課程
        </Link>

        {course === undefined && <p style={{ fontSize: "13px", color: "#94a3b8" }}>載入中…</p>}
        {course === null && (
          <section style={{ ...panel, textAlign: "center" }}>
            <p style={{ margin: 0, fontSize: "14px", color: "#fbbf24" }}>{loadError || "找不到這門課程，可能已被刪除。"}</p>
          </section>
        )}

        {course && (
          <>
            {/* 課程資訊與設定 */}
            <section style={{ ...panel, display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: "16px", flexWrap: "wrap" }}>
              <div style={{ minWidth: 0, flex: "1 1 360px" }}>
                <div style={{ display: "flex", alignItems: "center", gap: "8px", flexWrap: "wrap" }}>
                  <h1 style={{ fontSize: "20px", fontWeight: "bold", margin: 0 }}>{course.title}</h1>
                  {course.is_archived && <span style={pill("#94a3b8", "rgba(255,255,255,0.08)")}>已封存</span>}
                </div>
                <p style={{ margin: "8px 0 0", fontSize: "13px", lineHeight: 1.6, color: course.description ? "#cbd5e1" : "#64748b", whiteSpace: "pre-wrap" }}>
                  {course.description || "沒有課程簡介。"}
                </p>
              </div>
              <div className="cd-settings" style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: "8px", flexShrink: 0 }}>
                <label style={{ fontSize: "12px", display: "flex", alignItems: "center", gap: "6px", cursor: "pointer" }}>
                  <input type="checkbox" checked={course.require_approval} disabled={busy} onChange={(e) => updateCourse({ require_approval: e.target.checked })} />
                  加入需老師核准
                </label>
                <button disabled={busy} onClick={() => updateCourse({ is_archived: !course.is_archived })} style={{ ...smallBtn, color: course.is_archived ? "#4ade80" : "#f87171" }}>
                  {course.is_archived ? "取消封存" : "封存課程"}
                </button>
              </div>
            </section>

            {/* 左：分頁列表｜右：內容 */}
            <div className="cd-grid" style={{ display: "grid", gap: "16px", alignItems: "start" }}>
              <nav className="cd-tabs" role="tablist" aria-label="課程管理" style={{ ...panel, padding: "8px", display: "flex", flexDirection: "column", gap: "4px" }}>
                {tabItems.map((t) => {
                  const active = t.id === tab;
                  return (
                    <button
                      key={t.id}
                      role="tab"
                      aria-selected={active}
                      onClick={() => switchTab(t.id)}
                      className="cd-tab"
                      style={{
                        display: "flex",
                        justifyContent: "space-between",
                        alignItems: "center",
                        gap: "8px",
                        padding: "10px 12px",
                        borderRadius: "8px",
                        border: "none",
                        textAlign: "left",
                        font: "inherit",
                        fontSize: "14px",
                        fontWeight: active ? "bold" : "normal",
                        cursor: "pointer",
                        whiteSpace: "nowrap",
                        color: active ? "#fff" : "#cbd5e1",
                        backgroundColor: active ? "#16a34a" : "transparent",
                      }}
                    >
                      <span>{t.label}</span>
                      <span style={{ display: "flex", alignItems: "center", gap: "6px" }}>
                        {t.badge && <span style={pill(active ? "#fff" : "#fbbf24", active ? "rgba(255,255,255,0.2)" : "rgba(245,158,11,0.15)")}>{t.badge}</span>}
                        {t.count != null && <span style={{ fontSize: "12px", color: active ? "#dcfce7" : t.alert ? "#f87171" : "#94a3b8" }}>{t.count}</span>}
                      </span>
                    </button>
                  );
                })}
              </nav>

              <section role="tabpanel" style={{ ...panel, minWidth: 0 }}>
                {tab === "monitor" && <MonitorTab courseId={course.id} onGoToTab={switchTab} />}
                {tab === "review" && <ReviewTab courseId={course.id} onCount={setReviewCount} />}
                {tab === "members" && <MembersTab courseId={course.id} onChanged={loadCourse} />}
                {tab === "invites" && <InviteCodesTab course={course} openFormInitially={openCodeForm} onChanged={loadCourse} />}
                {tab === "modules" && <ModulesTab courseId={course.id} assigned={assigned} reload={loadAssigned} />}
                {tab === "materials" && <MaterialsTab courseId={course.id} />}
                {tab === "export" && <ExportTab courseId={course.id} />}
              </section>
            </div>
          </>
        )}
      </main>

      <style>{`
        .cd-grid { grid-template-columns: 220px minmax(0, 1fr); }
        .cd-tab:not([aria-selected="true"]):hover { background-color: rgba(255,255,255,0.06) !important; }
        .cd-tab:focus-visible { outline: 2px solid #4ade80; outline-offset: 2px; }
        /* 窄螢幕：分頁改成上方橫列 */
        @media (max-width: 760px) {
          .cd-grid { grid-template-columns: minmax(0, 1fr); }
          .cd-tabs { flex-direction: row !important; overflow-x: auto; }
          .cd-tab { flex: 1 0 auto; }
          .cd-settings { align-items: flex-start !important; }
        }
      `}</style>
    </div>
  );
}
