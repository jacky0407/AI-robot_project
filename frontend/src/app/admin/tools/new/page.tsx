'use client'

import { useState } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import { createClient } from '@/utils/supabase/client'

interface RubricItem {
  dimension: string
  max_score: number
  description: string
}

export default function NewToolPage() {
  const router = useRouter()
  const supabase = createClient()
  const [submitting, setSubmitting] = useState(false)
  const [errorMsg, setErrorMsg] = useState('')

  const [title, setTitle] = useState('')
  const [domain, setDomain] = useState('學前IEP')
  const [targetCompetency, setTargetCompetency] = useState('')
  const [roleInstruction, setRoleInstruction] = useState('')
  const [systemPrompt, setSystemPrompt] = useState('')

  const [rubrics, setRubrics] = useState<RubricItem[]>([
    { dimension: '客觀事實辨識', max_score: 40, description: '能準確擷取案例中的具體行為與數據，不加入主觀猜測' },
    { dimension: '情境脈絡完整性', max_score: 30, description: '清楚描述日常活動或作息中的具體表現' },
    { dimension: '支持需求具體性', max_score: 30, description: '能說明需要何種視覺支持或口語提示' },
  ])

  const handleAddRubric = () => {
    setRubrics([...rubrics, { dimension: '', max_score: 10, description: '' }])
  }

  const handleRemoveRubric = (index: number) => {
    setRubrics(rubrics.filter((_, i) => i !== index))
  }

  const handleRubricChange = (index: number, field: keyof RubricItem, value: string | number) => {
    const updated = [...rubrics]
    updated[index] = { ...updated[index], [field]: value }
    setRubrics(updated)
  }

  const totalRubricScore = rubrics.reduce((sum, r) => sum + (Number(r.max_score) || 0), 0)

  const handleSaveTool = async (e: React.FormEvent) => {
    e.preventDefault()
    setErrorMsg('')
    setSubmitting(true)

    const { data: { user } } = await supabase.auth.getUser()
    if (!user) {
      setErrorMsg('請先登入')
      setSubmitting(false)
      return
    }

    const { error } = await supabase.from('ai_tools').insert({
      title,
      domain,
      target_competency: targetCompetency,
      role_instruction: roleInstruction,
      system_prompt: systemPrompt,
      rubric_criteria: rubrics,
      status: 'published',
      version: 1,
      created_by: user.id,
    })

    if (error) {
      setErrorMsg('儲存失敗：' + error.message)
      setSubmitting(false)
      return
    }

    router.push('/admin/tools')
  }

  return (
    <div className="min-h-screen bg-gray-50 p-8">
      <div className="mx-auto max-w-4xl rounded-xl bg-white p-8 shadow-sm">
        <div className="mb-6 flex items-center justify-between border-b border-gray-200 pb-4">
          <div>
            <h1 className="text-xl font-bold text-gray-900">建立全新 AI 助教機器人</h1>
            <p className="text-sm text-gray-500">自訂 AI 角色語氣、指引方針及客製化 Rubric 評分表</p>
          </div>
          <Link href="/admin/tools" className="text-sm text-gray-500 hover:text-gray-800">
            取消返回
          </Link>
        </div>

        {errorMsg && (
          <div className="mb-6 rounded-md bg-red-50 p-3 text-sm text-red-600">{errorMsg}</div>
        )}

        <form onSubmit={handleSaveTool} className="space-y-6">
          <div className="space-y-4">
            <h2 className="text-base font-semibold text-gray-800 border-l-4 border-blue-600 pl-2">
              1. 基本設定
            </h2>
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <div>
                <label className="block text-sm font-medium text-gray-700">機器人名稱</label>
                <input
                  type="text"
                  required
                  placeholder="例如：步驟3：優勢與需求分析教練"
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                  className="mt-1 w-full rounded-md border border-gray-300 p-2 text-sm focus:border-blue-500 focus:outline-none"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700">所屬領域</label>
                <select
                  value={domain}
                  onChange={(e) => setDomain(e.target.value)}
                  className="mt-1 w-full rounded-md border border-gray-300 p-2 text-sm focus:border-blue-500 focus:outline-none"
                >
                  <option value="學前IEP">學前IEP</option>
                  <option value="家庭IFSP">家庭IFSP</option>
                  <option value="正向行為支持">正向行為支持</option>
                </select>
              </div>
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700">目標培訓能力</label>
              <input
                type="text"
                required
                placeholder="例如：從客觀證據精準提煉幼兒優勢與特殊教育需求"
                value={targetCompetency}
                onChange={(e) => setTargetCompetency(e.target.value)}
                className="mt-1 w-full rounded-md border border-gray-300 p-2 text-sm focus:border-blue-500 focus:outline-none"
              />
            </div>
          </div>

          <div className="space-y-4">
            <h2 className="text-base font-semibold text-gray-800 border-l-4 border-blue-600 pl-2">
              2. 角色定位與系統指令（System Prompt）
            </h2>
            <div>
              <label className="block text-sm font-medium text-gray-700">AI 角色定位簡述</label>
              <input
                type="text"
                required
                placeholder="例如：你是一位嚴謹但具同理心的資深特教督導"
                value={roleInstruction}
                onChange={(e) => setRoleInstruction(e.target.value)}
                className="mt-1 w-full rounded-md border border-gray-300 p-2 text-sm focus:border-blue-500 focus:outline-none"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700">
                詳細引導與回饋指令（System Prompt）
              </label>
              <textarea
                rows={5}
                required
                placeholder="請詳細敘述 AI 該如何引導學生、回饋語氣（正向、具體、不浮誇）、禁止直接給出標準答案等規範..."
                value={systemPrompt}
                onChange={(e) => setSystemPrompt(e.target.value)}
                className="mt-1 w-full rounded-md border border-gray-300 p-2 text-sm focus:border-blue-500 focus:outline-none"
              />
            </div>
          </div>

          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <h2 className="text-base font-semibold text-gray-800 border-l-4 border-blue-600 pl-2">
                3. Rubric 評量規準（總配分：{totalRubricScore} 分）
              </h2>
              <button
                type="button"
                onClick={handleAddRubric}
                className="rounded-md bg-gray-100 px-3 py-1 text-xs font-medium text-gray-700 hover:bg-gray-200"
              >
                + 新增構面
              </button>
            </div>

            <div className="space-y-3">
              {rubrics.map((r, idx) => (
                <div key={idx} className="flex gap-2 rounded-lg border border-gray-200 p-3 bg-gray-50">
                  <div className="w-1/4">
                    <input
                      type="text"
                      placeholder="構面名稱"
                      required
                      value={r.dimension}
                      onChange={(e) => handleRubricChange(idx, 'dimension', e.target.value)}
                      className="w-full rounded border border-gray-300 p-1.5 text-xs focus:outline-none"
                    />
                  </div>
                  <div className="w-20">
                    <input
                      type="number"
                      placeholder="配分"
                      required
                      value={r.max_score}
                      onChange={(e) => handleRubricChange(idx, 'max_score', Number(e.target.value))}
                      className="w-full rounded border border-gray-300 p-1.5 text-xs focus:outline-none"
                    />
                  </div>
                  <div className="flex-1">
                    <input
                      type="text"
                      placeholder="評判規準標準說明"
                      required
                      value={r.description}
                      onChange={(e) => handleRubricChange(idx, 'description', e.target.value)}
                      className="w-full rounded border border-gray-300 p-1.5 text-xs focus:outline-none"
                    />
                  </div>
                  {rubrics.length > 1 && (
                    <button
                      type="button"
                      onClick={() => handleRemoveRubric(idx)}
                      className="text-xs text-red-500 hover:text-red-700 px-1"
                    >
                      刪除
                    </button>
                  )}
                </div>
              ))}
            </div>
          </div>

          <div className="pt-4">
            <button
              type="submit"
              disabled={submitting}
              className="w-full rounded-lg bg-blue-600 py-2.5 text-sm font-semibold text-white hover:bg-blue-700 disabled:opacity-50"
            >
              {submitting ? '儲存並發布中...' : '儲存並正式發布機器人'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}