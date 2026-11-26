"use client";

// 個資提醒（需求說明書 PRI-001、#91）：註冊、第一次使用、自由輸入文字、上傳檔案前都要顯示
// 提醒文字集中在這裡，各頁面用同一套說法。
// 顏色用 var(--t-*) 並附深色預設值：作答區套用介面風格時跟著換，其他頁面維持現在的深色。

import { useEffect, useState } from "react";
import { createClient } from "@/utils/supabase/client";

// 不可以輸入的資料（PRI-002 自動偵測的種類也在這裡面）
export const PRIVACY_ITEMS = [
  "真實姓名：個案、家長、同事請用化名，例如「小明」「A 生」「王老師」",
  "身分證字號、健保卡號、病歷號",
  "電話、電子郵件、住址",
  "出生年月日（可以寫「4 歲 2 個月」這類年齡）",
  "學校、幼兒園、機構的名稱，以及照片、聲音等能認出個人的內容",
];

// 一行版：放在文字輸入框旁邊
export const PRIVACY_HINT = "請用化名，不要輸入真實姓名、身分證字號、電話、地址、生日等可以認出個人的資料。";

const C = {
  text: "var(--t-text, #f8fafc)",
  text2: "var(--t-text-2, #cbd5e1)",
  muted: "var(--t-text-muted, #94a3b8)",
  warn: "var(--t-warning, #fbbf24)",
  warnSoft: "var(--t-warning-soft, rgba(245,158,11,0.12))",
  warnBorder: "var(--t-warning-border, rgba(245,158,11,0.4))",
  surface: "var(--t-surface, #1e293b)",
  border: "var(--t-border, rgba(255,255,255,0.12))",
  primary: "var(--t-primary, #2563eb)",
  onPrimary: "var(--t-on-primary, #ffffff)",
  overlay: "var(--t-overlay, rgba(2,6,23,0.75))",
};

// 完整清單：註冊頁、第一次使用的說明視窗
export function PrivacyNoticeBox({ audience = "student" }: { audience?: "student" | "staff" }) {
  return (
    <div style={{ padding: "12px 14px", borderRadius: "10px", backgroundColor: C.warnSoft, border: `1px solid ${C.warnBorder}`, fontSize: "13px", lineHeight: 1.7, color: C.text2 }}>
      <div style={{ fontWeight: "bold", color: C.warn, marginBottom: "4px" }}>請勿輸入或上傳可以認出個人的資料</div>
      <ul style={{ margin: 0, paddingLeft: "20px", listStyle: "disc" }}>
        {PRIVACY_ITEMS.map((t) => (
          <li key={t}>{t}</li>
        ))}
      </ul>
      <p style={{ margin: "6px 0 0" }}>
        {audience === "staff"
          ? "教材、機器人設定、給學生的回饋與上傳的檔案也一樣。學生作答與匯出資料只用在教學與研究，請勿外流。"
          : "作答、聊天、上傳檔案都一樣，描述案例時只寫和學習有關的事實。"}
      </p>
      <p style={{ margin: "6px 0 0", fontSize: "12px", color: C.muted }}>
        系統會自動偵測常見的身分證字號、電話、電子郵件等格式並擋下來，但偵測不保證完整，仍請你自己留意。
      </p>
    </div>
  );
}

// 一行提醒：放在作答框、聊天輸入框、回饋文字框旁邊
export function PrivacyHint({ style }: { style?: React.CSSProperties }) {
  return (
    <p style={{ margin: 0, fontSize: "12px", lineHeight: 1.6, color: C.warn, ...style }}>
      <b>個資提醒：</b>
      {PRIVACY_HINT}
    </p>
  );
}

// 上傳前的確認：沒勾選前不能選檔案
export function UploadPrivacyCheck({ checked, onChange }: { checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <label style={{ display: "flex", alignItems: "flex-start", gap: "8px", fontSize: "12px", lineHeight: 1.6, color: C.text2, cursor: "pointer" }}>
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} style={{ marginTop: "3px" }} />
      <span>
        我確認要上傳的檔案已移除真實個案的姓名、身分證字號、電話、地址、生日、機構名稱與照片等個資。
        <span style={{ color: C.muted }}>（檔案裡的文字會被學習助教引用給學生看）</span>
      </span>
    </label>
  );
}

const ackKey = (scope: string, userId: string) => `edubloom:privacy-ack:v1:${scope}:${userId}`;

// 第一次使用的說明視窗：每個帳號在這台裝置上看過一次就不再出現（記在 localStorage）
// 換裝置或清除瀏覽資料會再出現一次，寧可多提醒
export function FirstUsePrivacyDialog({ scope }: { scope: "student" | "staff" }) {
  const [userId, setUserId] = useState<string | null>(null);
  const [supabase] = useState(() => createClient());

  useEffect(() => {
    let active = true;
    supabase.auth.getUser().then(({ data }) => {
      const id = data.user?.id;
      if (!active || !id) return;
      let seen = false;
      try {
        seen = window.localStorage.getItem(ackKey(scope, id)) === "1";
      } catch {
        // 瀏覽器不允許 localStorage（隱私模式等）：照樣顯示
      }
      if (!seen) setUserId(id);
    });
    return () => {
      active = false;
    };
  }, [supabase, scope]);

  if (!userId) return null;

  const acknowledge = () => {
    try {
      window.localStorage.setItem(ackKey(scope, userId), "1");
    } catch {
      // 記不住就下次再提醒
    }
    setUserId(null);
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="privacy-first-use-title"
      style={{ position: "fixed", inset: 0, zIndex: 60, display: "flex", alignItems: "center", justifyContent: "center", padding: "16px", backgroundColor: C.overlay }}
    >
      <div style={{ width: "100%", maxWidth: "480px", maxHeight: "90vh", overflowY: "auto", padding: "22px", borderRadius: "16px", backgroundColor: C.surface, border: `1px solid ${C.border}`, color: C.text, fontFamily: "sans-serif" }}>
        <h2 id="privacy-first-use-title" style={{ fontSize: "17px", fontWeight: "bold", margin: "0 0 6px" }}>
          開始之前：保護個案與自己的隱私
        </h2>
        <p style={{ margin: "0 0 12px", fontSize: "13px", lineHeight: 1.7, color: C.text2 }}>
          {scope === "staff"
            ? "後台會看到學生的作答與對話，也會建立教材與機器人。請先確認以下規則。"
            : "練習時會描述幼兒、家長與教學現場的情況，這些內容會交給 AI 評分並保存下來。請先確認以下規則。"}
        </p>
        <PrivacyNoticeBox audience={scope} />
        <button
          type="button"
          autoFocus
          onClick={acknowledge}
          style={{ marginTop: "16px", width: "100%", padding: "11px", borderRadius: "10px", border: "none", backgroundColor: C.primary, color: C.onPrimary, fontSize: "14px", fontWeight: "bold", cursor: "pointer" }}
        >
          我了解，開始使用
        </button>
      </div>
    </div>
  );
}
