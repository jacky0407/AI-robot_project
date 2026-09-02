import { redirect } from 'next/navigation'

export default function HomePage() {
  // 當任何人打開根目錄 http://localhost:3000/ 時，自動跳轉到登入頁
  redirect('/login')
}