// 登入後要去哪裡：?next= 的安全檢查與預設入口
//
// ?next= 讓使用者登入後回到原本要去的頁面（例如 XR 系統沒登入時導到 /?next=/xr/）。
// 只接受「站內路徑」，否則別人可以做一個「登入後跳到釣魚網站」的連結（開放轉址）。
// 登入頁、auth/callback、onboarding、middleware 都用這裡，規則只寫一次。

// 登入、完成註冊後的預設入口：系統選擇頁（docs：兩系統共用登入整合方案 #1、#2）
export const DEFAULT_AFTER_LOGIN = "/portal";

export function safeNext(raw: string | null | undefined): string | null {
  if (!raw) return null;
  // 必須是 / 開頭的站內路徑；擋掉 //evil.com（協定相對網址）
  if (!raw.startsWith("/") || raw.startsWith("//")) return null;
  // 擋掉反斜線（瀏覽器會把 /\evil.com 當成 //evil.com）與換行等控制字元
  if (/[\\\u0000-\u001f\u007f]/.test(raw)) return null;
  // 不要導回登入流程本身，避免繞圈
  if (raw === "/" || raw.startsWith("/auth/")) return null;
  return raw;
}

// 帶著 next 的網址，例如 withNext("/onboarding", "/xr/") → /onboarding?next=%2Fxr%2F
export function withNext(path: string, next: string | null): string {
  return next ? `${path}?next=${encodeURIComponent(next)}` : path;
}
