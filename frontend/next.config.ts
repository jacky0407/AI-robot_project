import type { NextConfig } from "next";

// 和 XR 系統聯調用（見 docs/SETUP.md「和 XR 系統聯調」）。
// 兩個變數都放在 .env.local；沒設定就不加對應的轉址，沒跑 XR 的人照常開發。
const trimSlash = (url?: string) => url?.trim().replace(/\/+$/, "") || undefined;
const XR_FRONTEND_URL = trimSlash(process.env.XR_FRONTEND_URL);
const XR_BACKEND_URL = trimSlash(process.env.XR_BACKEND_URL);
const SUPABASE_URL = trimSlash(process.env.NEXT_PUBLIC_SUPABASE_URL);

// 只套用在 /xr 頁面：XR 的前端是別人的程式，限制它只能連回本站與 Supabase。
// TODO: 若 XR 使用 Unity WebGL，script-src 要再加 'wasm-unsafe-eval'（等對方回覆）
const XR_CSP = [
  "default-src 'self'",
  "script-src 'self'",
  "style-src 'self' 'unsafe-inline'",
  ["connect-src 'self'", SUPABASE_URL].filter(Boolean).join(" "),
  "img-src 'self' data: blob:",
  "font-src 'self' data:",
  "object-src 'none'",
  "base-uri 'self'",
  "frame-ancestors 'self'",
].join("; ");

const nextConfig: NextConfig = {
  async rewrites() {
    return [
      ...(XR_FRONTEND_URL
        ? [
            { source: "/xr", destination: `${XR_FRONTEND_URL}/` },
            { source: "/xr/:path*", destination: `${XR_FRONTEND_URL}/:path*` },
          ]
        : []),
      ...(XR_BACKEND_URL
        ? [{ source: "/xr-api/:path*", destination: `${XR_BACKEND_URL}/:path*` }]
        : []),
    ];
  },

  async headers() {
    const csp = [{ key: "Content-Security-Policy", value: XR_CSP }];
    return [
      { source: "/xr", headers: csp },
      { source: "/xr/:path*", headers: csp },
    ];
  },
};

export default nextConfig;
