import { NextResponse } from 'next/server'
import { createClient } from '@/utils/supabase/server'
import { DEFAULT_AFTER_LOGIN, safeNext, withNext } from '@/utils/redirect'

export async function GET(request: Request) {
  const { searchParams, origin } = new URL(request.url)
  const code = searchParams.get('code')
  const errorCode = searchParams.get('error_code')
  // 換完 session 後要去的頁面：重設密碼信帶 /reset-password；從 XR 等頁面被導去登入時帶原本的頁面
  // 只接受站內路徑（utils/redirect.ts），避免被拿來當開放轉址
  const next = safeNext(searchParams.get('next'))

  // Supabase 在驗證階段就失敗（連結過期或已被使用）
  if (errorCode === 'otp_expired') {
    // 重設密碼頁自己會顯示「連結已失效」並提供重寄
    return NextResponse.redirect(`${origin}${next === '/reset-password' ? next : '/?error=link_expired'}`)
  }

  if (code) {
    const supabase = await createClient()

    // 把 Google / 驗證信給的 code 換成 session
    const { error } = await supabase.auth.exchangeCodeForSession(code)

    // 重設密碼：不管有沒有完成註冊，都直接去設定新密碼。
    // 換不到 session（多半是在別的瀏覽器開信，或寄了第二封信讓舊連結的 PKCE verifier 失效）
    // 也去重設密碼頁：它會顯示「連結已失效」並提供重寄，比登入頁的「登入驗證失敗」清楚。
    // 信件範本改用 /auth/confirm（token_hash）後就不會走到這裡，這段是給舊範本的保險
    if (next === '/reset-password') {
      if (error) console.error('[auth/callback] 重設密碼連結換 session 失敗:', error.message)
      return NextResponse.redirect(`${origin}${next}`)
    }

    if (!error) {
      const {
        data: { user },
      } = await supabase.auth.getUser()

      if (user) {
        const { data: profile, error: profileError } = await supabase
          .from('profiles')
          .select('role, registration_completed')
          .eq('id', user.id)
          .single()

        // 讀不到 profiles：把真正原因印在終端機方便排查
        if (profileError || !profile) {
          console.error('[auth/callback] profiles 查詢失敗:', profileError)
          return NextResponse.redirect(`${origin}/?error=profile`)
        }

        // 第一次登入（尚未完成註冊）：先補完基本資料與同意條款
        // 填完後回到原本要去的頁面
        if (!profile.registration_completed) {
          return NextResponse.redirect(`${origin}${withNext('/onboarding', next)}`)
        }

        // 回到原本要去的頁面，否則到系統選擇頁（兩系統共用登入整合方案 #2）
        return NextResponse.redirect(`${origin}${next ?? DEFAULT_AFTER_LOGIN}`)
      }
    }
  }

  // 其他失敗回登入頁（登入頁在根路徑 /）
  return NextResponse.redirect(`${origin}/?error=auth`)
}