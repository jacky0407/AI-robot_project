// 介面風格方案 A「溫暖專業」（docs/UI_STYLE.md，2026-11-26 定案）
// 各頁改版時從這裡取色碼與字族，不要再寫死顏色（#64）

export const A = {
  page: "#f4efe4", // 頁面背景（米色紙張）
  card: "#fbf8f1", // 卡片、區塊
  input: "#fffdf8", // 輸入框
  line: "#e4dccb", // 邊框、分隔線
  ink: "#26221c", // 主要文字（墨色）
  muted: "#6b6255", // 次要文字
  faint: "#cfc6b4", // 線條與停用
  primary: "#1f6b47", // 主色：按鈕、目前頁籤、連結（白字 6.5:1）
  primaryHover: "#185a3b",
  brand: "#2a9763", // 品牌綠：圖示、進度條；不放小字
  primarySoft: "#e6efe4",
  highlight: "#f4d35e", // 螢光筆：AI 引用的原文
  warn: "#8a5a00",
  warnSoft: "#f7ecd2",
  bad: "#a3361f",
  badSoft: "#f6e3dc",
} as const;

// 標題用明體、內文用黑體；沒有安裝思源字型時退回系統的明體／黑體
export const TITLE_FONT = '"Noto Serif TC", "Songti TC", "PMingLiU", serif';
export const BODY_FONT = '"Noto Sans TC", "PingFang TC", "Microsoft JhengHei", sans-serif';

// AI 引用學生原文：下半截劃螢光筆（app-12）
export const MARK_STYLE = {
  background: `linear-gradient(transparent 55%, ${A.highlight} 55%, ${A.highlight} 92%, transparent 92%)`,
  color: "inherit",
} as const;
