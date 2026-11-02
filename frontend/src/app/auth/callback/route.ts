import { NextResponse } from 'next/server'
import { createClient } from '@/utils/supabase/server'

export async function GET(request: Request) {
  const { searchParams, origin } = new URL(request.url)
  const code = searchParams.get('code')
  const errorCode = searchParams.get('error_code')
  // 指定換完 session 後要去的頁面（目前只有重設密碼信會帶 next=/reset-password）
  // 只接受站內路徑，避免被拿來當開放轉址
  const next = searchParams.get('next')
  const safeNext = next && next.startsWith('/') && !next.startsWith('//') ? next : null

  // Supabase 在驗證階段就失敗（連結過期或已被使用）
  if (errorCode === 'otp_expired') {
    // 重設密碼頁自己會顯示「連結已失效」並提供重寄
    return NextResponse.redirect(`${origin}${safeNext ?? '/?error=link_expired'}`)
  }

  if (code) {
    const supabase = await createClient()

    // 把 Google / 驗證信給的 code 換成 session
    const { error } = await supabase.auth.exchangeCodeForSession(code)

    if (!error && safeNext) {
      return NextResponse.redirect(`${origin}${safeNext}`)
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
        if (!profile.registration_completed) {
          return NextResponse.redirect(`${origin}/onboarding`)
        }

        const isStaff = profile.role === 'owner' || profile.role === 'assistant'
        return NextResponse.redirect(
          `${origin}${isStaff ? '/admin/courses' : '/student/courses'}`
        )
      }
    }
  }

  // 其他失敗回登入頁（登入頁在根路徑 /）
  return NextResponse.redirect(`${origin}/?error=auth`)
}