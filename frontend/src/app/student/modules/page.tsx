'use client'

import { useEffect, useState } from 'react'
import Link from 'next/link'
import { createClient } from '@/utils/supabase/client'

interface ModuleStep {
  id: string
  step_order: number
  step_title: string
  pass_score: number
  module_id: string
  tool_id: string
}

export default function StudentModulesPage() {
  const [steps, setSteps] = useState<ModuleStep[]>([])
  const [loading, setLoading] = useState(true)
  const supabase = createClient()

  useEffect(() => {
    async function loadSteps() {
      const { data } = await supabase
        .from('module_steps')
        .select('*')
        .order('step_order', { ascending: true })

      if (data) setSteps(data)
      setLoading(false)
    }

    loadSteps()
  }, [])

  return (
    <div className="min-h-screen bg-gray-50 p-8">
      <div className="mx-auto max-w-4xl">
        <div className="mb-6 border-b border-gray-200 pb-4">
          <h1 className="text-2xl font-bold text-gray-900">學前 IEP 逐步撰寫培訓模組</h1>
          <p className="mt-1 text-sm text-gray-500">
            請依序完成各步驟練習，AI 助教將依據評量規準進行即時回饋與引導。
          </p>
        </div>

        {loading ? (
          <div className="text-center text-gray-500 py-10">讀取中...</div>
        ) : (
          <div className="space-y-4">
            {steps.map((step) => (
              <div
                key={step.id}
                className="flex items-center justify-between rounded-xl border border-gray-200 bg-white p-5 shadow-sm hover:border-blue-300 transition"
              >
                <div>
                  <span className="rounded bg-blue-100 px-2.5 py-0.5 text-xs font-semibold text-blue-800">
                    步驟 {step.step_order}
                  </span>
                  <h3 className="mt-2 text-lg font-bold text-gray-900">{step.step_title}</h3>
                  <p className="mt-1 text-xs text-gray-500">通過門檻：{step.pass_score} 分</p>
                </div>
                <Link
                  href={`/student/practice?step_id=${step.id}&module_id=${step.module_id}`}
                  className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700"
                >
                  進入練習室
                </Link>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}