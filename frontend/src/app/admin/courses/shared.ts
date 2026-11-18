// 教師端課程管理共用的型別、樣式與小工具（課程列表頁與課程詳細頁共用）
// 後端：backend/api/courses.py

import { ApiError } from "@/utils/api";

export type InvitePlan = "free" | "self_key";
export type InviteCode = {
  id: string;
  code: string;
  plan: InvitePlan;
  daily_quota: number | null;
  access_until: string;
  is_active: boolean;
  expired: boolean;
  member_count: number;
};

export type Course = {
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

export type MemberStatus = "pending" | "active" | "rejected" | "removed";
export type Member = {
  user_id: string;
  full_name: string | null;
  email: string | null;
  status: MemberStatus;
  joined_at: string | null;
  invite_code: string | null;
};

export type AssignedModule = { module_id: string; title: string | null; is_published: boolean };
export type ModuleOption = { id: string; title: string; is_published: boolean };

export const planLabel = (c: Pick<InviteCode, "plan" | "daily_quota">) =>
  c.plan === "free" ? `免費・每天 ${c.daily_quota} 次` : "自備金鑰・不限次數";

// 本機（台灣）的今天，YYYY-MM-DD；可用日期不能早於今天
export const todayLocal = () => new Date().toLocaleDateString("sv-SE");

export const errorText = (err: unknown, fallback: string) => (err instanceof ApiError ? err.message : fallback);

export const panel: React.CSSProperties = {
  backgroundColor: "#1e293b",
  borderRadius: "12px",
  padding: "20px",
  border: "1px solid rgba(255,255,255,0.1)",
};
export const input: React.CSSProperties = {
  width: "100%",
  boxSizing: "border-box",
  padding: "8px 10px",
  borderRadius: "6px",
  border: "1px solid rgba(255,255,255,0.2)",
  backgroundColor: "rgba(255,255,255,0.05)",
  color: "#fff",
  fontSize: "13px",
  outline: "none",
};
export const smallBtn: React.CSSProperties = {
  padding: "4px 10px",
  fontSize: "12px",
  borderRadius: "6px",
  border: "1px solid rgba(255,255,255,0.2)",
  backgroundColor: "transparent",
  color: "#cbd5e1",
  cursor: "pointer",
};
export const pill = (color: string, bg: string): React.CSSProperties => ({
  fontSize: "11px",
  padding: "2px 8px",
  borderRadius: "9999px",
  color,
  backgroundColor: bg,
  whiteSpace: "nowrap",
});
