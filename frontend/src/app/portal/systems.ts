// /portal 列出的系統（兩系統共用登入整合方案 #1）。之後有新的系統（別組的專案）要加進來，只要在 SYSTEMS 加一筆。
//
// staffOnly：只有教師（owner／assistant）看得到
// enabled 回傳 false 時卡片會顯示「尚未開放」且不能點，用來處理還沒部署或沒設定網址的系統

export type PortalUser = { isStaff: boolean };

export type PortalSystem = {
  id: string;
  name: string;
  description: string;
  icon: string;
  accent: string; // 卡片主色
  href: string;
  staffOnly?: boolean;
  enabled?: () => boolean;
  disabledReason?: string;
  // 別組的系統（例如 /xr 轉址到 XR 前端）不是本站的 Next.js 頁面，要整頁載入，不能用 <Link> 換頁
  fullPageLoad?: boolean;
};

export const SYSTEMS: PortalSystem[] = [
  {
    id: "iep",
    name: "IEP 撰寫培訓",
    description: "學前 IEP 逐步撰寫練習：AI 依評分規準即時回饋、分層提示，老師最終判定",
    icon: "📝",
    accent: "#2563eb",
    href: "/student/courses",
  },
  {
    id: "xr",
    name: "XR 訪談訓練",
    description: "在模擬情境中練習家長與專業人員訪談（由 XR 小組開發）",
    icon: "🥽",
    accent: "#7c3aed",
    href: "/xr/",
    fullPageLoad: true,
    // /xr 由 next.config.ts 轉址到 XR_FRONTEND_URL；沒設定時轉址不存在，點進去會 404
    enabled: () => Boolean(process.env.XR_FRONTEND_URL?.trim()),
    disabledReason: "尚未開放",
  },
  {
    id: "admin",
    name: "管理後台",
    description: "課程與邀請碼、模組指派、機器人申請審核、學生作答複核",
    icon: "🧑‍🏫",
    accent: "#16a34a",
    href: "/admin/courses",
    staffOnly: true,
  },
];

export function systemsFor(user: PortalUser): PortalSystem[] {
  return SYSTEMS.filter((s) => !s.staffOnly || user.isStaff);
}
