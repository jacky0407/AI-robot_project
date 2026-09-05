'use client'

import { useEffect, useState, Suspense } from 'react'
import { useSearchParams } from 'next/navigation'
import Link from 'next/link'
import { createClient } from '@/utils/supabase/client'

function PracticeRoom() {
  const searchParams = useSearchParams()
  const stepId = searchParams.get('step_id')
  const moduleId = searchParams.get('module_id')

  const [stepData, setStepData] = useState<any>(null)
  const [caseData, setCaseData] = useState<any>(null)
  const [answer, setAnswer] = useState('')
  const [loading, setLoading] = useState(true)
  const [submitting, setSubmitting] = useState(false)
  const [evaluation, setEvaluation] = useState<any>(null)
  const supabase = createClient()

  useEffect(() => {
    async function init() {
      if (!stepId) return

      const { data: step } = await supabase
        .from('module_steps')
        .select('*, ai_tools(*)')
        .eq('id', stepId)
        .single()

      const { data: cases } = await supabase.from('tool_cases').select('*').limit(1)

      if (step) setStepData(step)
      if (cases && cases.length > 0) setCaseData(cases[0])
      setLoading(false)
    }

    init()
  }, [stepId])

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setSubmitting(true)
    setEvaluation(null)

    const { data: { user } } = await supabase.auth.getUser()
    if (!user) {
      alert('請先登入')
      setSubmitting(false)
      return
    }

    try {
      // 呼叫後端 FastAPI 提交作答與 AI 評分
      const res = await fetch('http://127.0.0.1:8000/api/practice/sessions/temp-session-id/submit', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          step_id: stepId,
          module_id: moduleId,
          content: answer,
          user_id: user.id,
        }),
      })

      const textResponse = await res.text()
      
      // 解析 SSE 串流格式中的 data 內容
      const lines = textResponse.split('\n')
      let evaluationData = null
      let errorMessage = ''

      for (const line of lines) {
        if (line.startsWith('data: ')) {
          try {
            const parsed = JSON.parse(line.substring(6))
            if (parsed.message) {
              errorMessage = parsed.message
            } else {
              evaluationData = parsed
            }
          } catch (e) {
            // 略過無法解析的行
          }
        }
      }

      if (errorMessage) {
        alert("評分失敗：" + errorMessage)
        return
      }

      if (evaluationData) {
        setEvaluation(evaluationData)
      } else {
        // 如果不是 SSE 格式，嘗試直接轉標準 JSON
        try {
          const result = JSON.parse(textResponse)
          setEvaluation(result)
        } catch {
          setEvaluation({ overall_feedback: textResponse })
        }
      }
    } catch (err: any) {
      alert(err.message || '發生錯誤')
    } finally {
      setSubmitting(false)
    }
  }

  if (loading) return <div className="p-8 text-center text-gray-500">載入教學情境中...</div>

  return (
    <div className="min-h-screen bg-gray-50 p-6">
      <div className="mx-auto max-w-4xl space-y-6">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-xl font-bold text-gray-900">{stepData?.step_title}</h1>
            <p className="text-sm text-gray-500">專屬 AI 助教：{stepData?.ai_tools?.title}</p>
          </div>
          <Link href="/student/modules" className="text-sm text-blue-600 hover:underline">
            返回模組目錄
          </Link>
        </div>

        {caseData && (
          <div className="rounded-xl border border-amber-200 bg-amber-50 p-5">
            <h2 className="text-sm font-bold text-amber-900">教學案例：{caseData.title}</h2>
            <p className="mt-2 text-sm leading-relaxed text-amber-800">{caseData.case_background}</p>
          </div>
        )}

        <div className="rounded-xl border border-gray-200 bg-white p-6 shadow-sm">
          <form onSubmit={handleSubmit} className="space-y-4">
            <label className="block text-sm font-medium text-gray-700">
              您的作答內容（請依案例情境完成本步驟專業撰寫）：
            </label>
            <textarea
              rows={6}
              required
              value={answer}
              onChange={(e) => setAnswer(e.target.value)}
              placeholder="請在此輸入您的完整作答..."
              className="w-full rounded-md border border-gray-300 p-3 text-sm focus:border-blue-500 focus:outline-none"
            />
            <div className="flex justify-end">
              <button
                type="submit"
                disabled={submitting}
                className="rounded-lg bg-blue-600 px-5 py-2 text-sm font-semibold text-white hover:bg-blue-700 disabled:opacity-50"
              >
                {submitting ? 'AI 正在進行個資過濾與評估中...' : '提交給 AI 助教初評'}
              </button>
            </div>
          </form>
        </div>

        {evaluation && (
          <div className="rounded-xl border border-green-200 bg-white p-6 shadow-sm space-y-4">
            <div className="flex items-center justify-between border-b border-gray-100 pb-3">
              <h3 className="text-lg font-bold text-gray-900">AI 助教初評回饋</h3>
              <span className="rounded-full bg-green-100 px-3 py-1 text-sm font-bold text-green-700">
                評定完成
              </span>
            </div>
            <div className="rounded-lg bg-blue-50 p-4 text-sm text-blue-900">
              <div className="font-bold mb-1">評量結果：</div>
              <pre className="whitespace-pre-wrap text-xs">{JSON.stringify(evaluation, null, 2)}</pre>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

export default function Page() {
  return (
    <Suspense fallback={<div className="p-8 text-center">載入中...</div>}>
      <PracticeRoom />
    </Suspense>
  )
}