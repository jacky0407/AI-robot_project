// /portal 列出的系統。之後有新的系統（別組的專案）要加進來，只要在 SYSTEMS 加一筆。
//
// href 可以依身分不同（例如教師進後台、學生進課程頁）；
// enabled 回傳 false 時卡片會顯示「尚未開放」且不能點，用來處理還沒部署或沒設定網址的系統。

export type PortalUser = { isStaff: boolean };

export type PortalSystem = {
  id: string;
  name: string;
  description: string;
  icon: string;
  accent: string; // 卡片主色
  href: (user: PortalUser) => string;
  enabled?: () => boolean;
  // 別組的系統（例如 /xr 轉址到 XR 前端）不是本站的 Next.js 頁面，要整頁載入，不能用 <Link> 換頁
  fullPageLoad?: boolean;
  disabledReason?: string;
};

export const SYSTEMS: PortalSystem[] = [
  {
    id: "ai-training",
    name: "AI 專業能力培訓平台",
    description: "IEP 逐步撰寫練習、AI 依規準評分與分層提示、教師複核",
    icon: "🤖",
    accent: "#2563eb",
    href: (u) => (u.isStaff ? "/admin/courses" : "/student/courses"),
  },
  {
    id: "xr",
    name: "XR 情境模擬系統",
    description: "沉浸式情境演練（由 XR 小組開發）",
    icon: "🥽",
    accent: "#7c3aed",
    href: () => "/xr",
    fullPageLoad: true,
    // /xr 由 next.config.ts 轉址到 XR_FRONTEND_URL；沒設定時轉址不存在，點進去會 404
    enabled: () => Boolean(process.env.XR_FRONTEND_URL?.trim()),
    disabledReason: "尚未開放",
  },
];
