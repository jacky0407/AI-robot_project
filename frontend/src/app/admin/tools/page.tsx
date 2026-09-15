'use client'

import { useEffect, useState } from 'react'
import Link from 'next/link'
import { createClient } from '@/utils/supabase/client'

interface AiTool {
  id: string
  title: string
  domain: string
  target_competency: string
  status: string
  version: number
  created_at: string
  system_prompt?: string
}

export default function AdminToolsPage() {
  const [tools, setTools] = useState<AiTool[]>([])
  const [loading, setLoading] = useState(true)
  const supabase = createClient()

  // 取得所有 AI 機器人清單
  const loadTools = async () => {
    try {
      const { data, error } = await supabase
        .from('ai_tools')
        .select('*')
        .order('created_at', { ascending: false })

      if (!error && data) {
        setTools(data)
      }
    } catch (err) {
      console.error('無法取得機器人清單', err)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadTools()
  }, [])

  // 刪除指定的 AI 機器人
  const handleDelete = async (toolId: string, toolTitle: string) => {
    if (!confirm(`確定要刪除機器人「${toolTitle}」嗎？`)) return

    try {
      const res = await fetch(`http://127.0.0.1:8000/api/teacher/tools/${toolId}`, {
        method: 'DELETE',
      })

      if (res.ok) {
        alert('刪除成功！')
        // 從畫面清單中移除
        setTools(tools.filter((t) => t.id !== toolId))
      } else {
        const errData = await res.json()
        alert('刪除失敗：' + (errData.detail || '未知錯誤'))
      }
    } catch (err: any) {
      alert('發生錯誤：' + err.message)
    }
  }

  return (
    <div className="min-h-screen bg-gray-50 p-8">
      <div className="mx-auto max-w-5xl">
        <div className="mb-6 flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold text-gray-900">AI 工具建構器（教授管理後台）</h1>
            <p className="mt-1 text-sm text-gray-500">
              在此建立、調整與發布各步驟的 AI 能力教練與評量標準。
            </p>
          </div>
          <Link
            href="/admin/tools/new"
            className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 shadow-sm"
          >
            + 建立新 AI 機器人
          </Link>
        </div>

        {loading ? (
          <div className="p-8 text-center text-gray-500">讀取中...</div>
        ) : tools.length === 0 ? (
          <div className="rounded-xl border border-dashed border-gray-300 bg-white p-12 text-center">
            <p className="text-gray-500">目前尚無自訂 AI 機器人，請點擊上方按鈕新增。</p>
          </div>
        ) : (
          <div className="grid gap-4 md:grid-cols-2">
            {tools.map((tool) => (
              <div
                key={tool.id}
                className="flex flex-col justify-between rounded-xl border border-gray-200 bg-white p-5 shadow-sm hover:border-blue-300 transition"
              >
                <div>
                  <div className="flex items-center justify-between">
                    <span className="rounded bg-blue-50 px-2 py-0.5 text-xs font-semibold text-blue-600">
                      {tool.domain || '通用領域'}
                    </span>
                    <div className="flex items-center space-x-2">
                      <span
                        className={`rounded px-2 py-0.5 text-xs font-medium ${
                          tool.status === 'published'
                            ? 'bg-green-100 text-green-700'
                            : 'bg-yellow-100 text-yellow-700'
                        }`}
                      >
                        {tool.status === 'published' ? '已發布' : '草稿'} (v{tool.version || 1})
                      </span>
                    </div>
                  </div>
                  <h3 className="mt-3 text-lg font-bold text-gray-900">{tool.title}</h3>
                  <p className="mt-1 text-sm text-gray-600">
                    目標能力：{tool.target_competency || '未設定'}
                  </p>
                </div>

                <div className="mt-4 flex items-center justify-between border-t border-gray-100 pt-3 text-xs text-gray-400">
                  <span>建立時間：{new Date(tool.created_at).toLocaleDateString()}</span>
                  <button
                    onClick={() => handleDelete(tool.id, tool.title)}
                    className="rounded-md bg-red-50 px-3 py-1.5 text-xs font-semibold text-red-600 hover:bg-red-100 transition"
                  >
                    刪除機器人
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}