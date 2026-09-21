import { NextResponse } from 'next/server'
import { createClient } from '@/utils/supabase/server'

export async function GET(request: Request) {
  const { searchParams, origin } = new URL(request.url)
  const code = searchParams.get('code')
  const errorCode = searchParams.get('error_code')

  // Supabase 在驗證階段就失敗（連結過期或已被使用）
  if (errorCode === 'otp_expired') {
    return NextResponse.redirect(`${origin}/?error=link_expired`)
  }

  if (code) {
    const supabase = await createClient()

    // 把 Google / 驗證信給的 code 換成 session
    const { error } = await supabase.auth.exchangeCodeForSession(code)

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
          `${origin}${isStaff ? '/admin/tools' : '/student/modules'}`
        )
      }
    }
  }

  // 其他失敗回登入頁（登入頁在根路徑 /）
  return NextResponse.redirect(`${origin}/?error=auth`)
}