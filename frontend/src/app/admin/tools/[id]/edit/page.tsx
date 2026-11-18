'use client'

// 編輯機器人：讀出現有內容帶入表單（../../ToolForm.tsx，與建立頁共用）
// 後端：GET /api/teacher/tools/{id}（含 can_edit、attempt_count）、PATCH /api/teacher/tools/{id}
// 只有建立者與教授可以修改（backend/api/teacher.py 的 can_edit_tool）

import { useEffect, useState } from 'react'
import Link from 'next/link'
import { useParams } from 'next/navigation'
import { apiFetch } from '@/utils/api'
import AdminHeader from '../../../courses/AdminHeader'
import ToolForm, { type ToolData } from '../../ToolForm'

export default function EditToolPage() {
  const params = useParams<{ id: string }>()
  const toolId = params.id
  const [tool, setTool] = useState<ToolData | null>(null)
  const [errorMsg, setErrorMsg] = useState('')

  useEffect(() => {
    let active = true
    apiFetch<{ data: ToolData }>(`/api/teacher/tools/${toolId}`).then(
      (res) => {
        if (active) setTool(res.data)
      },
      (err) => {
        if (active) setErrorMsg(`無法讀取機器人：${err instanceof Error ? err.message : ''}`)
      },
    )
    return () => {
      active = false
    }
  }, [toolId])

  // 讀到了而且可以改 → 顯示表單；其他情況顯示訊息
  if (tool && tool.can_edit !== false) return <ToolForm key={tool.id} initial={tool} />

  const message = errorMsg || (tool ? '只有建立者或教授可以修改這支機器人。' : '載入中…')
  return (
    <div style={{ minHeight: '100vh', backgroundColor: '#0f172a', color: '#f8fafc', fontFamily: 'sans-serif', colorScheme: 'dark' }}>
      <AdminHeader />
      <main style={{ maxWidth: '880px', margin: '24px auto', padding: '0 20px', display: 'flex', flexDirection: 'column', gap: '16px' }}>
        <Link href="/admin/tools" style={{ alignSelf: 'flex-start', fontSize: '13px', color: '#4ade80', textDecoration: 'none' }}>
          ← 返回機器人列表
        </Link>
        <p style={{ fontSize: '13px', color: errorMsg || tool ? '#fca5a5' : '#94a3b8' }}>{message}</p>
      </main>
    </div>
  )
}
