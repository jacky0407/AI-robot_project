import { NextResponse, type NextRequest } from 'next/server'
import { createServerClient } from '@supabase/ssr'

export async function middleware(request: NextRequest) {
  let response = NextResponse.next({
    request: {
      headers: request.headers,
    },
  })

  const supabase = createServerClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
    {
      cookies: {
        getAll() {
          return request.cookies.getAll()
        },
        setAll(cookiesToSet) {
          cookiesToSet.forEach(({ name, value }) => request.cookies.set(name, value))
          response = NextResponse.next({
            request,
          })
          cookiesToSet.forEach(({ name, value, options }) =>
            response.cookies.set(name, value, options)
          )
        },
      },
    }
  )

  // 取得當前登入者
  const {
    data: { user },
  } = await supabase.auth.getUser()
  const pathname = request.nextUrl.pathname

  // 登入頁（根路徑 /）與 OAuth 回呼一律放行
  const isPublicPath = pathname === '/' || pathname.startsWith('/auth')

  // 1. 若未登入且不是公開頁面，強制導回登入頁 /
  if (!user && !isPublicPath) {
    return NextResponse.redirect(new URL('/', request.url))
  }

  if (user) {
    const { data: profile } = await supabase
      .from('profiles')
      .select('role, registration_completed')
      .eq('id', user.id)
      .single()

    const isStaff = profile?.role === 'owner' || profile?.role === 'assistant'

    if (pathname.startsWith('/onboarding')) {
      // 2. 已完成註冊的人不需要再填一次，直接送去對應頁面
      if (profile?.registration_completed) {
        return NextResponse.redirect(
          new URL(isStaff ? '/admin/tools' : '/student/modules', request.url)
        )
      }
    } else {
      // 3. 還沒完成註冊，不能進入系統內頁
      if (!profile?.registration_completed) {
        return NextResponse.redirect(new URL('/onboarding', request.url))
      }

      // 4. /admin 後台只有 owner / assistant 可以進入
      if (pathname.startsWith('/admin') && !isStaff) {
        return NextResponse.redirect(new URL('/student/modules', request.url))
      }
    }
  }

  return response
}

export const config = {
  matcher: ['/admin/:path*', '/student/:path*', '/onboarding'],
}
