'use client'

import { useEffect, useState, Suspense } from 'react'
import { useSearchParams } from 'next/navigation'
import Link from 'next/link'
import { createClient } from '@/utils/supabase/client'
import { ApiError, apiFetch, streamSSE } from '@/utils/api'

type StepData = {
  id: string
  step_title: string
  pass_score: number | null
  ai_tools: { title: string; target_competency: string } | null
}

type CaseData = { id: string; title: string; case_background: string }

type DimensionScore = {
  dimension: string
  score: number
  max_score: number
  reason?: string
  evidence?: string
}

type ScoreComplete = {
  total_score: number
  passed: boolean
  overall_feedback: string
  confidence?: number
  needs_teacher_review?: boolean
}

type Hint = {
  level: number
  content: string
  source?: 'teacher' | 'generated'
  is_example?: boolean
  warning?: string
}

type DetectedError = { code: string; label: string; severity?: string; evidence?: string; explanation?: string }

type HintStatus = { hints_used: number; total_levels: number | null; next_level_available: boolean }

type Submission = {
  id: string
  attempt_number: number
  status: string
  created_at: string
  ai_evaluations: { total_score: number | null }[] | { total_score: number | null } | null
}

// 一次提交的串流結果（逐事件累積）
type RunState = {
  decision?: string
  dimensions: DimensionScore[]
  totalDimensions?: number
  score?: ScoreComplete
  hint?: Hint
  encouragement?: string
  escalation?: string
  errors: DetectedError[]
  failure?: string
}

const EMPTY_RUN: RunState = { dimensions: [], errors: [] }

const STATUS_TEXT: Record<string, string> = {
  draft: '草稿',
  submitted: '評分中',
  passed: '已通過',
  revision_required: '需修改',
}

const DECISION_LABEL: Record<string, string> = {
  evaluate: 'AI 正在依 Rubric 評分',
  encourage: '接近通過門檻，AI 會評分並給你鼓勵',
  give_hint: 'AI 判斷先給你一則提示，修改後再交',
  escalate: 'AI 建議你先和老師討論',
}

function latestScore(s: Submission): number | null {
  const ev = Array.isArray(s.ai_evaluations) ? s.ai_evaluations[0] : s.ai_evaluations
  return ev?.total_score ?? null
}

function PracticeRoom() {
  const searchParams = useSearchParams()
  const stepId = searchParams.get('step_id')
  const moduleId = searchParams.get('module_id')

  const [supabase] = useState(() => createClient())
  const [userId, setUserId] = useState('')
  const [stepData, setStepData] = useState<StepData | null>(null)
  const [caseData, setCaseData] = useState<CaseData | null>(null)
  const [answer, setAnswer] = useState('')
  const [loading, setLoading] = useState(true)
  const [submitting, setSubmitting] = useState(false)
  const [run, setRun] = useState<RunState | null>(null)

  const [hintStatus, setHintStatus] = useState<HintStatus | null>(null)
  const [revealedHints, setRevealedHints] = useState<Hint[]>([])
  const [hintMessage, setHintMessage] = useState('')
  const [history, setHistory] = useState<Submission[]>([])

  // 後端目前不保存 session（KNOWN_GAPS #3），這裡只需要一個這次練習用的識別碼
  const [sessionId] = useState(() => crypto.randomUUID())

  // 交卷或取用提示後 +1，重新抓提示狀態與作答紀錄
  const [progressKey, setProgressKey] = useState(0)

  const query = (extra: Record<string, string> = {}) =>
    new URLSearchParams({ user_id: userId, step_id: stepId ?? '', ...extra }).toString()

  useEffect(() => {
    async function init() {
      if (!stepId) {
        setLoading(false)
        return
      }
      const {
        data: { user },
      } = await supabase.auth.getUser()
      if (user) setUserId(user.id)

      const { data: step } = await supabase
        .from('module_steps')
        .select('id, step_title, pass_score, ai_tools(title, target_competency)')
        .eq('id', stepId)
        .single()

      // ⚠️ tool_cases 與步驟之間沒有關聯欄位（schema 尚未定義），暫時取第一筆案例
      const { data: cases } = await supabase.from('tool_cases').select('id, title, case_background').limit(1)

      if (step) setStepData(step as unknown as StepData)
      if (cases && cases.length > 0) setCaseData(cases[0] as CaseData)
      setLoading(false)
    }

    init()
  }, [stepId, supabase])

  useEffect(() => {
    if (!userId || !stepId) return
    let active = true
    const params = new URLSearchParams({ user_id: userId, step_id: stepId }).toString()
    Promise.all([
      apiFetch<{ data: HintStatus }>(`/api/practice/sessions/${sessionId}/hints?${params}`),
      apiFetch<{ data: { submissions: Submission[] } }>(`/api/practice/sessions/${sessionId}/history?${params}`),
    ])
      .then(([hints, hist]) => {
        if (!active) return
        setHintStatus(hints.data)
        setHistory(hist.data.submissions)
      })
      .catch(() => {
        // 提示與歷程是輔助資訊，讀不到不影響作答
      })
    return () => {
      active = false
    }
  }, [userId, stepId, sessionId, progressKey])

  const handleEvent = (event: string, data: Record<string, unknown>) => {
    setRun((prev) => {
      const r = prev ?? EMPTY_RUN
      switch (event) {
        case 'tutor_decision':
          return { ...r, decision: String(data.action ?? '') }
        case 'analysis_start':
          return { ...r, totalDimensions: Number(data.total_dimensions ?? 0) }
        case 'dimension_score':
          return { ...r, dimensions: [...r.dimensions, data as unknown as DimensionScore] }
        case 'score_complete':
          return { ...r, score: data as unknown as ScoreComplete }
        case 'hint':
          return { ...r, hint: data as unknown as Hint }
        case 'encouragement':
          return { ...r, encouragement: String(data.message ?? '') }
        case 'escalation':
          return { ...r, escalation: String(data.message ?? '') }
        case 'errors_detected':
          return { ...r, errors: (data.errors as DetectedError[]) ?? [] }
        case 'error':
          return { ...r, failure: String(data.message ?? 'AI 評分失敗') }
        default:
          return r
      }
    })
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!stepId || !moduleId) return
    if (!userId) {
      alert('請先登入')
      return
    }

    setSubmitting(true)
    setRun({ ...EMPTY_RUN })

    try {
      await streamSSE(
        `/api/practice/sessions/${sessionId}/submit`,
        {
          method: 'POST',
          body: JSON.stringify({
            step_id: stepId,
            module_id: moduleId,
            case_id: caseData?.id ?? null,
            content: answer,
            user_id: userId,
          }),
        },
        handleEvent
      )
    } catch (err) {
      let message = err instanceof Error ? err.message : '發生錯誤'
      if (err instanceof ApiError && err.code === 'PII_DETECTED') {
        const types = (err.detail as { detected_types?: string[] })?.detected_types ?? []
        message = `${message}${types.length ? `（偵測到：${types.join('、')}）` : ''}`
      }
      setRun({ ...EMPTY_RUN, failure: message })
    } finally {
      setSubmitting(false)
      setProgressKey((k) => k + 1)
    }
  }

  const handleRevealHint = async () => {
    setHintMessage('')
    try {
      const res = await apiFetch<{ data: HintStatus & { hint: Hint | null; message?: string } }>(
        `/api/practice/sessions/${sessionId}/hints?${query({ reveal: 'true' })}`
      )
      if (res.data.hint) setRevealedHints((prev) => [...prev, res.data.hint as Hint])
      if (res.data.message) setHintMessage(res.data.message)
      setHintStatus(res.data)
    } catch (err) {
      setHintMessage(err instanceof Error ? err.message : '取得提示失敗')
    }
  }

  if (loading) return <div className="p-8 text-center text-gray-500">載入教學情境中...</div>

  if (!stepId || !moduleId || !stepData) {
    return (
      <div className="p-8 text-center text-gray-600">
        找不到這個練習步驟。
        <Link href="/student/modules" className="ml-2 text-blue-600 hover:underline">
          返回模組目錄
        </Link>
      </div>
    )
  }

  const shownHints = [...revealedHints, ...(run?.hint ? [run.hint] : [])]

  return (
    <div className="min-h-screen bg-gray-50 p-6">
      <div className="mx-auto max-w-4xl space-y-6">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-xl font-bold text-gray-900">{stepData.step_title}</h1>
            <p className="text-sm text-gray-500">
              專屬 AI 助教：{stepData.ai_tools?.title}
              {stepData.pass_score != null && `・通過門檻 ${stepData.pass_score} 分`}
            </p>
          </div>
          <Link href="/student/modules" className="text-sm text-blue-600 hover:underline">
            返回模組目錄
          </Link>
        </div>

        {caseData && (
          <div className="rounded-xl border border-amber-200 bg-amber-50 p-5">
            <h2 className="text-sm font-bold text-amber-900">教學案例：{caseData.title}</h2>
            <p className="mt-2 whitespace-pre-wrap text-sm leading-relaxed text-amber-800">{caseData.case_background}</p>
          </div>
        )}

        <div className="rounded-xl border border-gray-200 bg-white p-6 shadow-sm">
          <form onSubmit={handleSubmit} className="space-y-4">
            <label className="block text-sm font-medium text-gray-700">
              您的作答內容（請依案例情境完成本步驟專業撰寫）：
            </label>
            <textarea
              rows={8}
              required
              value={answer}
              onChange={(e) => setAnswer(e.target.value)}
              placeholder="請在此輸入您的完整作答..."
              className="w-full rounded-md border border-gray-300 p-3 text-sm focus:border-blue-500 focus:outline-none"
            />
            <div className="flex items-center justify-between">
              <span className="text-xs text-gray-500">字數：{answer.length}・請勿輸入真實姓名、電話、身分證字號等個資</span>
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

        {run && (
          <div className="space-y-4 rounded-xl border border-gray-200 bg-white p-6 shadow-sm">
            <h3 className="text-lg font-bold text-gray-900">AI 助教初評回饋</h3>

            {run.failure && <div className="rounded-lg bg-red-50 p-4 text-sm text-red-700">{run.failure}</div>}

            {run.decision && <p className="text-sm text-gray-600">{DECISION_LABEL[run.decision] ?? run.decision}</p>}

            {run.encouragement && <div className="rounded-lg bg-green-50 p-4 text-sm text-green-800">{run.encouragement}</div>}

            {run.escalation && <div className="rounded-lg bg-orange-50 p-4 text-sm text-orange-800">{run.escalation}</div>}

            {run.dimensions.length > 0 && (
              <div className="space-y-2">
                {run.dimensions.map((d) => (
                  <div key={d.dimension} className="rounded-lg border border-gray-100 bg-gray-50 p-3 text-sm">
                    <div className="flex justify-between font-semibold text-gray-800">
                      <span>{d.dimension}</span>
                      <span>
                        {d.score} / {d.max_score}
                      </span>
                    </div>
                    {d.reason && <p className="mt-1 text-gray-600">{d.reason}</p>}
                    {d.evidence && <p className="mt-1 text-xs italic text-gray-500">引用：{d.evidence}</p>}
                  </div>
                ))}
                {submitting && run.totalDimensions != null && (
                  <p className="text-xs text-gray-500">
                    評分中 {run.dimensions.length} / {run.totalDimensions}
                  </p>
                )}
              </div>
            )}

            {run.score && (
              <div className={`rounded-lg p-4 text-sm ${run.score.passed ? 'bg-green-50 text-green-900' : 'bg-blue-50 text-blue-900'}`}>
                <div className="flex items-center justify-between font-bold">
                  <span>{run.score.passed ? '已達通過門檻' : '尚未達通過門檻，請依回饋修改'}</span>
                  <span>{Math.round(run.score.total_score)} 分</span>
                </div>
                <p className="mt-2 whitespace-pre-wrap">{run.score.overall_feedback}</p>
                {run.score.needs_teacher_review && <p className="mt-2 text-xs text-gray-600">這次 AI 評分較沒把握，老師會再複核。</p>}
              </div>
            )}

            {run.errors.length > 0 && (
              <div className="rounded-lg border border-orange-200 bg-orange-50 p-4 text-sm text-orange-900">
                <div className="mb-1 font-bold">需要留意的常見錯誤</div>
                {run.errors.map((err) => (
                  <div key={err.code} className="mt-2">
                    <strong>{err.label}</strong>
                    {err.explanation && <span>：{err.explanation}</span>}
                    {err.evidence && <div className="text-xs italic">原文：{err.evidence}</div>}
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {/* 分層提示：先顯示還有沒有下一層，學生按下去才真的取用 */}
        <div className="space-y-3 rounded-xl border border-gray-200 bg-white p-6 shadow-sm">
          <div className="flex items-center justify-between">
            <h3 className="text-base font-bold text-gray-900">分層提示</h3>
            <button
              type="button"
              onClick={handleRevealHint}
              // 還沒交過作答時提示無處記錄，先交一次再要提示
              disabled={!hintStatus?.next_level_available || history.length === 0}
              className="rounded-md border border-blue-600 px-3 py-1 text-xs font-semibold text-blue-600 hover:bg-blue-50 disabled:border-gray-300 disabled:text-gray-400"
            >
              我需要提示
            </button>
          </div>
          {hintStatus && (
            <p className="text-xs text-gray-500">
              已使用 {hintStatus.hints_used} 則
              {hintStatus.total_levels != null && ` / 共 ${hintStatus.total_levels} 層`}
              {!hintStatus.next_level_available && '・目前沒有可用的下一層提示'}
              {history.length === 0 && '・先提交一次作答後才能索取提示'}
            </p>
          )}
          {hintMessage && <p className="text-xs text-gray-600">{hintMessage}</p>}
          {shownHints.map((h, i) => (
            <div key={`${h.level}-${i}`} className="rounded-lg bg-indigo-50 p-3 text-sm text-indigo-900">
              <div className="text-xs font-semibold">
                第 {h.level} 層{h.source === 'generated' && '（AI 依評分結果生成）'}
              </div>
              <p className="mt-1 whitespace-pre-wrap">{h.content}</p>
              {h.warning && <p className="mt-1 text-xs text-indigo-700">{h.warning}</p>}
            </div>
          ))}
        </div>

        {history.length > 0 && (
          <div className="rounded-xl border border-gray-200 bg-white p-6 shadow-sm">
            <h3 className="mb-3 text-base font-bold text-gray-900">作答紀錄</h3>
            <table className="w-full text-left text-sm">
              <thead className="text-xs text-gray-500">
                <tr>
                  <th className="py-1">次數</th>
                  <th className="py-1">狀態</th>
                  <th className="py-1">AI 分數</th>
                  <th className="py-1">時間</th>
                </tr>
              </thead>
              <tbody>
                {history.map((s) => (
                  <tr key={s.id} className="border-t border-gray-100">
                    <td className="py-1">第 {s.attempt_number} 次</td>
                    <td className="py-1">{STATUS_TEXT[s.status] ?? s.status}</td>
                    <td className="py-1">{latestScore(s) ?? '—'}</td>
                    <td className="py-1 text-xs text-gray-500">{new Date(s.created_at).toLocaleString()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
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
