'use client'

// 建立機器人：表單在 ../ToolForm.tsx（與編輯頁共用）

import { Suspense } from 'react'
import { useSearchParams } from 'next/navigation'
import ToolForm from '../ToolForm'

// useSearchParams() 必須包在 <Suspense> 裡，否則 next build 預先產生頁面時會失敗
export default function NewToolPage() {
  return (
    <Suspense fallback={null}>
      <NewTool />
    </Suspense>
  )
}

function NewTool() {
  // 從課程頁帶過來的 courseId：建立完回到課程管理
  const courseId = useSearchParams().get('courseId')
  return <ToolForm courseId={courseId} />
}
