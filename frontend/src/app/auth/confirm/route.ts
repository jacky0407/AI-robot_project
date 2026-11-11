import { NextResponse } from 'next/server'
import type { EmailOtpType } from '@supabase/supabase-js'
import { createClient } from '@/utils/supabase/server'
import { DEFAULT_AFTER_LOGIN, safeNext } from '@/utils/redirect'

// 信件連結驗證（token_hash）：重設密碼信、驗證信點進來的地方
//
// 為什麼不用 /auth/callback 的 ?code=：
//   code 是 PKCE 流程，要搭配「寄信那個瀏覽器」存的 code_verifier 才換得到 session。
//   在別的瀏覽器／手機／信箱 App 內建瀏覽器開信，或同一個信箱寄了第二封（舊信的 verifier 被蓋掉），
//   都會換不到，使用者就被導回登入頁看到「登入驗證失敗」。
//   token_hash 由伺服器直接向 Supabase 驗證，在哪裡開信都可以。
//
// 需要在 Supabase 後台把信件範本的連結改成指向這裡（docs/SETUP.md「忘記密碼」一節）：
//   {{ .SiteURL }}/auth/confirm?token_hash={{ .TokenHash }}&type=recovery&next=/reset-password

const RESET_PASSWORD_PAGE = '/reset-password'

export async function GET(request: Request) {
  const { searchParams, origin } = new URL(request.url)
  const tokenHash = searchParams.get('token_hash')
  const type = searchParams.get('type') as EmailOtpType | null
  const isRecovery = type === 'recovery'
  // 重設密碼一律到設定新密碼頁；其他信件（如註冊驗證信）只接受站內路徑，避免被拿來當開放轉址，
  // 預設到系統選擇頁，還沒完成註冊的人 middleware 會再導去 /onboarding
  const next = isRecovery ? RESET_PASSWORD_PAGE : safeNext(searchParams.get('next')) ?? DEFAULT_AFTER_LOGIN

  if (tokenHash && type) {
    const supabase = await createClient()
    const { error } = await supabase.auth.verifyOtp({ type, token_hash: tokenHash })
    if (!error) {
      return NextResponse.redirect(`${origin}${next}`)
    }
    console.error('[auth/confirm] verifyOtp 失敗:', error.message)
  }

  // 連結過期、已被使用或格式不對：
  // 重設密碼頁沒有 session 時會自己顯示「連結已失效」並提供重寄；其他信件回登入頁說明
  return NextResponse.redirect(`${origin}${isRecovery ? RESET_PASSWORD_PAGE : '/?error=link_expired'}`)
}
