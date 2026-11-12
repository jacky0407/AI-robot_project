"use client";

// 資料匯出（DAT-001）：研究用（去識別化、只含同意研究使用的學生）／教學用（含姓名）
// 後端：POST /api/export/research、POST /api/export/teaching（backend/api/export.py），只限教授

import { useEffect, useState } from "react";
import { ApiError, apiDownload, apiFetch } from "@/utils/api";

type Course = { id: string; title: string };
type Kind = "research" | "teaching";

const field: React.CSSProperties = {
  boxSizing: "border-box",
  width: "100%",
  padding: "6px 8px",
  borderRadius: "6px",
  border: "1px solid rgba(255,255,255,0.2)",
  backgroundColor: "#0f172a",
  color: "#fff",
  fontSize: "12px",
};

export default function ExportPanel() {
  const [courses, setCourses] = useState<Course[]>([]);
  const [courseId, setCourseId] = useState("");
  const [kind, setKind] = useState<Kind>("research");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [includeContent, setIncludeContent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  useEffect(() => {
    apiFetch<{ data: Course[] }>("/api/courses").then(
      (res) => {
        setCourses(res.data);
        if (res.data.length) setCourseId(res.data[0].id);
      },
      () => setCourses([])
    );
  }, []);

  const handleExport = async () => {
    if (!courseId) return;
    if (kind === "teaching" && !window.confirm("教學用資料含學生姓名與 Email，只能用於教學管理，不可用於研究發表。確定要匯出嗎？")) return;
    setBusy(true);
    setMsg(null);
    try {
      const body = {
        course_id: courseId,
        include_content: includeContent,
        ...(dateFrom ? { date_from: dateFrom } : {}),
        ...(dateTo ? { date_to: dateTo } : {}),
      };
      const { blob, filename, headers } = await apiDownload(`/api/export/${kind}`, { method: "POST", body: JSON.stringify(body) });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename ?? `${kind}-export.csv`;
      a.click();
      URL.revokeObjectURL(url);
      const count = headers.get("X-Export-Row-Count");
      setMsg({
        ok: true,
        text:
          count === "0" && kind === "research"
            ? "已匯出，但沒有資料：目前沒有學生同意研究使用（研究同意書功能上線後才會有資料）。"
            : `已匯出 ${count ?? ""} 筆作答。`,
      });
    } catch (err) {
      setMsg({ ok: false, text: err instanceof ApiError ? `匯出失敗：${err.message}` : "匯出失敗：連不到後端" });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "8px", fontSize: "12px", color: "#cbd5e1" }}>
      <label>
        課程
        <select value={courseId} onChange={(e) => setCourseId(e.target.value)} style={{ ...field, marginTop: "4px" }}>
          {courses.map((c) => (
            <option key={c.id} value={c.id}>
              {c.title}
            </option>
          ))}
        </select>
      </label>

      <div style={{ display: "flex", flexDirection: "column", gap: "4px" }}>
        <label style={{ display: "flex", gap: "6px", alignItems: "flex-start" }}>
          <input type="radio" checked={kind === "research"} onChange={() => setKind("research")} />
          <span>
            研究用（去識別化）
            <br />
            <span style={{ color: "#64748b" }}>姓名、Email 換成匿名研究編號；只含同意研究使用的學生</span>
          </span>
        </label>
        <label style={{ display: "flex", gap: "6px", alignItems: "flex-start" }}>
          <input type="radio" checked={kind === "teaching"} onChange={() => setKind("teaching")} />
          <span>
            教學用（含姓名）
            <br />
            <span style={{ color: "#64748b" }}>供教學管理，不可用於研究發表</span>
          </span>
        </label>
      </div>

      <div style={{ display: "flex", gap: "6px" }}>
        <label style={{ flex: 1 }}>
          從
          <input type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} style={{ ...field, marginTop: "4px" }} />
        </label>
        <label style={{ flex: 1 }}>
          到
          <input type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)} style={{ ...field, marginTop: "4px" }} />
        </label>
      </div>

      <label style={{ display: "flex", gap: "6px", alignItems: "center" }}>
        <input type="checkbox" checked={includeContent} onChange={(e) => setIncludeContent(e.target.checked)} />
        包含作答原文{kind === "research" ? "（會遮罩疑似個資）" : ""}
      </label>

      <button
        onClick={handleExport}
        disabled={busy || !courseId}
        style={{ padding: "8px 0", border: "none", backgroundColor: busy ? "#334155" : "#16a34a", color: "#fff", borderRadius: "6px", fontSize: "13px", fontWeight: "bold", cursor: busy ? "default" : "pointer" }}
      >
        {busy ? "匯出中…" : "匯出 CSV"}
      </button>
      <p style={{ margin: 0, color: "#64748b" }}>只有教授可以匯出；每次匯出都會留下紀錄。</p>
      {msg && <p style={{ margin: 0, color: msg.ok ? "#86efac" : "#fca5a5" }}>{msg.text}</p>}
    </div>
  );
}
