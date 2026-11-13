import { NextResponse, type NextRequest } from 'next/server'
import { createServerClient } from '@supabase/ssr'
import { DEFAULT_AFTER_LOGIN, safeNext, withNext } from '@/utils/redirect'

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

  const pathname = request.nextUrl.pathname

  // /xr 底下的靜態檔（.js、.css、圖片…）不需要逐一查登入，否則每個檔案都要多打兩次 Supabase；
  // 擋住 /xr 的頁面本身就夠了，靜態檔本來就是公開的程式碼
  if (pathname.startsWith('/xr/') && /\.[a-z0-9]+$/i.test(pathname)) {
    return response
  }

  // 取得當前登入者
  const {
    data: { user },
  } = await supabase.auth.getUser()

  // 登入後要回到的地方：目前這頁（含 query），交給登入頁或 onboarding 帶回來
  const here = safeNext(pathname + request.nextUrl.search)

  // 登入頁（根路徑 /）與 OAuth 回呼一律放行
  const isPublicPath = pathname === '/' || pathname.startsWith('/auth')

  // 1. 若未登入且不是公開頁面，導回登入頁，登入後回到原本的頁面
  if (!user && !isPublicPath) {
    return NextResponse.redirect(new URL(withNext('/', here), request.url))
  }

  if (user) {
    const { data: profile } = await supabase
      .from('profiles')
      .select('role, registration_completed')
      .eq('id', user.id)
      .single()

    const isStaff = profile?.role === 'owner' || profile?.role === 'assistant'

    if (pathname.startsWith('/onboarding')) {
      // 2. 已完成註冊的人不需要再填一次：回到原本要去的頁面，否則到系統選擇頁
      if (profile?.registration_completed) {
        const next = safeNext(request.nextUrl.searchParams.get('next'))
        return NextResponse.redirect(new URL(next ?? DEFAULT_AFTER_LOGIN, request.url))
      }
    } else {
      // 3. 還沒完成註冊，不能進入系統內頁（含 /portal、/xr），填完後回到原本的頁面
      if (!profile?.registration_completed) {
        return NextResponse.redirect(new URL(withNext('/onboarding', here), request.url))
      }

      // 4. /admin 後台只有 owner / assistant 可以進入
      if (pathname.startsWith('/admin') && !isStaff) {
        return NextResponse.redirect(new URL('/student/courses', request.url))
      }

      // 5. 教師不進學生練習頁填寫模組（開會紀錄 #49 第 2 項）：/student 一律導回後台的監控總覽
      if (pathname.startsWith('/student') && isStaff) {
        return NextResponse.redirect(new URL('/admin/overview', request.url))
      }
    }
  }

  return response
}

export const config = {
  // /portal、/xr：兩系統共用登入整合方案 #3，沒登入或沒完成註冊的人進不去
  matcher: ['/admin/:path*', '/student/:path*', '/onboarding', '/portal', '/xr', '/xr/:path*'],
}
