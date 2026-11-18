"use client";

// 課程詳細頁：成員清單（核准／拒絕加入申請、移出成員）
// 後端：GET /api/courses/{id}/members、PATCH /api/courses/{id}/members/{user_id}

import { useCallback, useEffect, useState } from "react";
import { apiFetch } from "@/utils/api";
import { type Member, type MemberStatus, errorText, smallBtn } from "../shared";

type MemberAction = "approve" | "reject" | "remove";

const GROUPS: { status: MemberStatus; title: string }[] = [
  { status: "pending", title: "待審核申請" },
  { status: "active", title: "已加入" },
  { status: "rejected", title: "已拒絕" },
  { status: "removed", title: "已移出" },
];

export default function MembersTab({ courseId, onChanged }: { courseId: string; onChanged: () => void }) {
  const [members, setMembers] = useState<Member[] | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(
    () =>
      apiFetch<{ data: Member[] }>(`/api/courses/${encodeURIComponent(courseId)}/members`).then(
        (res) => {
          setMembers(res.data);
          setError("");
        },
        (err) => {
          setMembers([]);
          setError(errorText(err, "無法讀取成員名單"));
        }
      ),
    [courseId]
  );

  useEffect(() => {
    load();
  }, [load]);

  const act = async (m: Member, action: MemberAction) => {
    if (action === "remove" && !confirm(`確定要將「${m.full_name || m.email}」移出課程嗎？作答紀錄會保留。`)) return;
    setBusy(m.user_id);
    try {
      await apiFetch(`/api/courses/${encodeURIComponent(courseId)}/members/${m.user_id}`, {
        method: "PATCH",
        body: JSON.stringify({ action }),
      });
      await load();
      onChanged();
    } catch (err) {
      alert(`操作失敗：${errorText(err, "請稍後再試")}`);
    } finally {
      setBusy(null);
    }
  };

  return (
    <div>
      <h2 style={{ fontSize: "16px", fontWeight: "bold", margin: "0 0 4px" }}>成員清單</h2>
      <p style={{ fontSize: "12px", color: "#94a3b8", margin: "0 0 16px" }}>學生用邀請碼加入後會出現在這裡；課程設定為需核准時，要在「待審核申請」按核准。</p>

      {members === null && <p style={{ fontSize: "13px", color: "#94a3b8", margin: 0 }}>載入中…</p>}
      {error && <p style={{ fontSize: "13px", color: "#fca5a5", margin: 0 }}>{error}</p>}
      {members?.length === 0 && !error && <p style={{ fontSize: "13px", color: "#94a3b8", margin: 0 }}>目前還沒有學生申請或加入。把邀請碼給學生後，他們就會出現在這裡。</p>}

      {GROUPS.map(({ status, title }) => {
        const list = members?.filter((m) => m.status === status) ?? [];
        if (list.length === 0) return null;
        return (
          <div key={status} style={{ marginBottom: "18px" }}>
            <h3 style={{ fontSize: "13px", fontWeight: "bold", margin: "0 0 8px", color: status === "pending" ? "#fbbf24" : "#cbd5e1" }}>
              {title}（{list.length}）
            </h3>
            <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
              {list.map((m) => (
                <div key={m.user_id} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: "8px", fontSize: "13px", padding: "10px 12px", borderRadius: "8px", backgroundColor: "rgba(255,255,255,0.04)" }}>
                  <span style={{ minWidth: 0 }}>
                    <b>{m.full_name || "（未填姓名）"}</b>
                    <span style={{ fontSize: "12px", color: "#94a3b8", marginLeft: "8px" }}>{m.email}</span>
                    {m.invite_code && <span style={{ fontSize: "11px", color: "#64748b", marginLeft: "8px" }}>邀請碼 {m.invite_code}</span>}
                    {m.joined_at && <span style={{ fontSize: "11px", color: "#64748b", marginLeft: "8px" }}>{new Date(m.joined_at).toLocaleDateString()}</span>}
                  </span>
                  <span style={{ display: "flex", gap: "6px", flexShrink: 0 }}>
                    {m.status !== "active" && (
                      <button disabled={busy === m.user_id} onClick={() => act(m, "approve")} style={{ ...smallBtn, backgroundColor: "#16a34a", color: "#fff", border: "none" }}>
                        {m.status === "pending" ? "核准" : "重新加入"}
                      </button>
                    )}
                    {m.status === "pending" && (
                      <button disabled={busy === m.user_id} onClick={() => act(m, "reject")} style={{ ...smallBtn, color: "#f87171" }}>
                        拒絕
                      </button>
                    )}
                    {m.status === "active" && (
                      <button disabled={busy === m.user_id} onClick={() => act(m, "remove")} style={{ ...smallBtn, color: "#f87171" }}>
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
  );
}
