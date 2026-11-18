'use client'

// 機器人表單：建立（/admin/tools/new）與編輯（/admin/tools/[id]/edit）共用
// 後端：POST /api/teacher/tools、PATCH /api/teacher/tools/{id}（backend/api/teacher.py）
// 每次修改後端會把版本 + 1，舊版內容保留；已有作答時改評分規準，舊的評分不會重算

import { useState } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import { apiFetch } from '@/utils/api'
import AdminHeader from '../courses/AdminHeader'
import { input, panel, smallBtn } from '../courses/shared'

// 允許額外欄位（例如教授自訂的 levels），編輯時原樣保留
interface RubricItem {
  dimension: string
  max_score: number
  description: string
  [key: string]: unknown
}

interface HintItem {
  content: string
  trigger: 'score_below_threshold' | 'student_request'
  is_example: boolean
}

interface ErrorItem {
  code: string
  label: string
  description: string
  severity: 'low' | 'medium' | 'high'
  related_dimension: string
  [key: string]: unknown
}

// GET /api/teacher/tools/{id} 回傳的機器人（編輯時帶入表單）
export interface ToolData {
  id: string
  title: string
  domain: string
  target_competency: string
  role_instruction: string
  system_prompt: string
  teaching_strategy: Record<string, unknown> | null
  rubric_criteria: Record<string, unknown>[] | null
  scale_type: string | null
  error_taxonomy: Record<string, unknown>[] | null
  version: number
  can_edit?: boolean
  attempt_count?: number
}

const SCALE_OPTIONS = [
  { value: '4_point', label: '4 級量表（預設）' },
  { value: '5_point', label: '5 級量表' },
  { value: 'pass_fail', label: '達成 / 未達成' },
  { value: '100_point', label: '直接給 0–100 分' },
]

const DEFAULT_RUBRICS: RubricItem[] = [
  { dimension: '客觀事實辨識', max_score: 40, description: '能準確擷取案例中的具體行為與數據，不加入主觀猜測' },
  { dimension: '情境脈絡完整性', max_score: 30, description: '清楚描述日常活動或作息中的具體表現' },
  { dimension: '支持需求具體性', max_score: 30, description: '能說明需要何種視覺支持或口語提示' },
]

// teaching_strategy 裡由表單管理的欄位；其他欄位編輯時原樣保留
const FORM_STRATEGY_KEYS = ['hints', 'max_auto_hints', 'personalize', 'memory']

// 深色主題的表單元件樣式（與課程管理共用 input / panel / smallBtn）
const select: React.CSSProperties = { ...input, backgroundColor: '#0f172a' }
const label: React.CSSProperties = { display: 'block', fontSize: '13px', color: '#cbd5e1', marginBottom: '6px' }
const hint: React.CSSProperties = { margin: 0, fontSize: '12px', color: '#94a3b8', lineHeight: 1.6 }
const item: React.CSSProperties = { padding: '12px', borderRadius: '8px', border: '1px solid rgba(255,255,255,0.08)', backgroundColor: 'rgba(255,255,255,0.03)' }
const addBtn: React.CSSProperties = { ...smallBtn, flexShrink: 0, color: '#4ade80', borderColor: 'rgba(74,222,128,0.45)' }
const removeBtn: React.CSSProperties = { ...smallBtn, flexShrink: 0, padding: '3px 9px', fontSize: '11px', color: '#f87171' }
const check: React.CSSProperties = { display: 'flex', alignItems: 'center', gap: '6px', fontSize: '13px', color: '#cbd5e1', cursor: 'pointer' }
const warnBox: React.CSSProperties = { padding: '10px 14px', borderRadius: '8px', fontSize: '13px', lineHeight: 1.6, color: '#fde68a', backgroundColor: 'rgba(245,158,11,0.12)', border: '1px solid rgba(245,158,11,0.4)' }

// 每一區的標題列：標題、說明、右側按鈕
function SectionHead({ title, desc, action }: { title: string; desc?: string; action?: React.ReactNode }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: '12px', marginBottom: '14px' }}>
      <div>
        <h2 style={{ fontSize: '15px', fontWeight: 'bold', margin: '0 0 4px' }}>{title}</h2>
        {desc && <p style={hint}>{desc}</p>}
      </div>
      {action}
    </div>
  )
}

// 與 backend/core/ai/memory.py 的 DEFAULT_MAX_ATTEMPTS、MAX_ATTEMPTS_LIMIT 一致
const MEMORY_DEFAULT_ATTEMPTS = 3
const MEMORY_MAX_ATTEMPTS = 10

const asRecord = (v: unknown): Record<string, unknown> =>
  v && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : {}

// ── 把資料庫的內容轉成表單狀態 ──────────────────────────────────────────────

// 建立時給預設規準；編輯不評分的機器人（規準為空，#65）時保持空的，不要偷偷變成要評分
function toRubrics(raw: ToolData['rubric_criteria'], editing: boolean): RubricItem[] {
  if (!editing && !raw?.length) return DEFAULT_RUBRICS
  return (raw ?? []).map((r) => ({
    ...r,
    dimension: String(r.dimension ?? r.name ?? ''),
    max_score: Number(r.max_score ?? r.weight ?? 0),
    description: String(r.description ?? ''),
  }))
}

function toHints(strategy: Record<string, unknown>): HintItem[] {
  const hints = Array.isArray(strategy.hints) ? (strategy.hints as Record<string, unknown>[]) : []
  return [...hints]
    .sort((a, b) => Number(a.level ?? 0) - Number(b.level ?? 0))
    .map((h) => ({
      content: String(h.content ?? ''),
      trigger: h.trigger === 'student_request' ? 'student_request' : 'score_below_threshold',
      is_example: Boolean(h.is_example),
    }))
}

function toErrors(raw: ToolData['error_taxonomy']): ErrorItem[] {
  return (raw ?? []).map((e) => ({
    ...e,
    code: String(e.code ?? e.error_code ?? ''),
    label: String(e.label ?? e.name ?? ''),
    description: String(e.description ?? ''),
    severity: e.severity === 'low' || e.severity === 'high' ? e.severity : 'medium',
    related_dimension: String(e.related_dimension ?? ''),
  }))
}

const sameJson = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b)

export default function ToolForm({ initial, courseId }: { initial?: ToolData; courseId?: string | null }) {
  const router = useRouter()
  const editing = Boolean(initial)
  const attemptCount = initial?.attempt_count ?? 0

  const backUrl = courseId ? '/admin/courses' : '/admin/tools'

  const initialStrategy = asRecord(initial?.teaching_strategy)
  const initialMemory = asRecord(initialStrategy.memory)
  // 表單一開始的規準與級距（只算一次），用來判斷老師有沒有改
  const [initialRubrics] = useState<RubricItem[]>(() => toRubrics(initial?.rubric_criteria ?? null, Boolean(initial)))
  const initialScale = initial?.scale_type || '4_point'

  const [submitting, setSubmitting] = useState(false)
  const [errorMsg, setErrorMsg] = useState('')

  const [title, setTitle] = useState(initial?.title ?? '')
  const [domain, setDomain] = useState(initial?.domain ?? '學前IEP')
  const [targetCompetency, setTargetCompetency] = useState(initial?.target_competency ?? '')
  const [roleInstruction, setRoleInstruction] = useState(initial?.role_instruction ?? '')
  const [systemPrompt, setSystemPrompt] = useState(initial?.system_prompt ?? '')

  const [rubrics, setRubrics] = useState<RubricItem[]>(initialRubrics)

  const [scaleType, setScaleType] = useState(initialScale)

  const [hints, setHints] = useState<HintItem[]>(() => toHints(initialStrategy))
  const [maxAutoHints, setMaxAutoHints] = useState(
    typeof initialStrategy.max_auto_hints === 'number' ? initialStrategy.max_auto_hints : 2,
  )
  const [personalize, setPersonalize] = useState(initialStrategy.personalize !== false)

  const [errors, setErrors] = useState<ErrorItem[]>(() => toErrors(initial?.error_taxonomy ?? null))

  // 機器人記憶（backend/core/ai/memory.py 的 MemoryConfig，存在 teaching_strategy.memory）
  // 預設值與後端一致：開啟、記最近 3 次、讀前面步驟的成果
  const [memoryEnabled, setMemoryEnabled] = useState(initialMemory.enabled !== false)
  const [memoryMaxAttempts, setMemoryMaxAttempts] = useState(
    typeof initialMemory.max_attempts === 'number' ? initialMemory.max_attempts : MEMORY_DEFAULT_ATTEMPTS,
  )
  const [memoryIncludePrior, setMemoryIncludePrior] = useState(initialMemory.include_prior_steps !== false)

  // 已有作答的機器人改了評分規準或級距 → 舊的評分不會重算，要提醒老師
  const rubricChanged = editing && (!sameJson(rubrics, initialRubrics) || scaleType !== initialScale)
  const showRegradeWarning = rubricChanged && attemptCount > 0

  const updateHint = (index: number, patch: Partial<HintItem>) =>
    setHints(hints.map((h, i) => (i === index ? { ...h, ...patch } : h)))

  const updateError = (index: number, patch: Partial<ErrorItem>) =>
    setErrors(errors.map((er, i) => (i === index ? { ...er, ...patch } : er)))

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

  const buildBody = () => {
    // 表單以外的 teaching_strategy 欄位原樣保留
    const otherStrategy = Object.fromEntries(Object.entries(initialStrategy).filter(([k]) => !FORM_STRATEGY_KEYS.includes(k)))
    return {
      title,
      domain,
      target_competency: targetCompetency,
      role_instruction: roleInstruction,
      system_prompt: systemPrompt,
      rubric_criteria: rubrics,
      scale_type: scaleType,
      teaching_strategy: {
        ...otherStrategy,
        ...(hints.length
          ? {
              hints: hints.map((h, i) => ({
                level: i + 1,
                trigger: h.trigger,
                content: h.content,
                ...(h.is_example ? { is_example: true, warn_copy: true } : {}),
              })),
              max_auto_hints: maxAutoHints,
              personalize,
            }
          : {}),
        memory: {
          enabled: memoryEnabled,
          // 後端也會夾在 0～10，這裡先夾，避免存進看不懂的值
          max_attempts: Math.max(0, Math.min(MEMORY_MAX_ATTEMPTS, Math.round(memoryMaxAttempts) || 0)),
          include_prior_steps: memoryIncludePrior,
        },
      },
      error_taxonomy: errors,
    }
  }

  const handleSaveTool = async (e: React.FormEvent) => {
    e.preventDefault()
    setErrorMsg('')

    if (
      showRegradeWarning &&
      !confirm(
        `已有 ${attemptCount} 筆學生作答用過這支機器人。\n修改評分規準後，舊的評分不會重新計算，只有之後的作答會用新規準。\n\n確定要儲存嗎？`,
      )
    ) {
      return
    }

    setSubmitting(true)
    try {
      if (initial) {
        // expected_version：打開編輯頁後別人先存了新版本，後端回 409，不會蓋掉別人的修改
        await apiFetch(`/api/teacher/tools/${initial.id}`, {
          method: 'PATCH',
          body: JSON.stringify({ ...buildBody(), expected_version: initial.version }),
        })
      } else {
        await apiFetch('/api/teacher/tools', { method: 'POST', body: JSON.stringify({ ...buildBody(), status: 'published' }) })
      }
    } catch (err) {
      setErrorMsg(`儲存失敗：${err instanceof Error ? err.message : ''}`)
      setSubmitting(false)
      return
    }

    // 儲存完成後，返回對應的頁面
    router.push(backUrl)
  }

  return (
    <div style={{ minHeight: '100vh', backgroundColor: '#0f172a', color: '#f8fafc', fontFamily: 'sans-serif', colorScheme: 'dark' }}>
      <AdminHeader />

      <main style={{ maxWidth: '880px', margin: '24px auto', padding: '0 20px', display: 'flex', flexDirection: 'column', gap: '16px' }}>
        <Link href={backUrl} style={{ alignSelf: 'flex-start', fontSize: '13px', color: '#4ade80', textDecoration: 'none' }}>
          {courseId ? '← 返回課程管理' : '← 返回機器人列表'}
        </Link>

        <div>
          <h1 style={{ fontSize: '20px', fontWeight: 'bold', margin: '0 0 4px' }}>
            {editing ? `編輯機器人（目前第 ${initial?.version} 版）` : '建立機器人'}
          </h1>
          <p style={hint}>
            {editing
              ? '儲存後會成為新的一版，舊版內容會保留；之後的作答與評分都用新版。'
              : '設定 AI 助教的角色與語氣、評分規準、提示與記憶。建立後到課程的「模組管理」把它排進關卡。'}
          </p>
        </div>

        {editing && attemptCount > 0 && (
          <div style={warnBox}>
            已有 {attemptCount} 筆學生作答用過這支機器人。修改評分規準或評分級距後，<b>舊的評分不會重新計算</b>，只有之後的作答會用新規準。
          </div>
        )}

        {errorMsg && (
          <div style={{ padding: '10px 14px', borderRadius: '8px', fontSize: '13px', color: '#fca5a5', backgroundColor: 'rgba(239,68,68,0.15)', border: '1px solid rgba(239,68,68,0.4)' }}>
            {errorMsg}
          </div>
        )}

        <form onSubmit={handleSaveTool} style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
          {/* 基本設定 */}
          <section style={panel}>
            <SectionHead title="基本設定" />
            <div className="tf-two" style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '14px', marginBottom: '14px' }}>
              <label>
                <span style={label}>機器人名稱</span>
                <input type="text" required placeholder="例如：優勢與需求分析教練" value={title} onChange={(e) => setTitle(e.target.value)} style={input} />
              </label>
              <label>
                <span style={label}>所屬領域</span>
                <select value={domain} onChange={(e) => setDomain(e.target.value)} style={select}>
                  <option value="學前IEP">學前IEP</option>
                  <option value="家庭IFSP">家庭IFSP</option>
                  <option value="正向行為支持">正向行為支持</option>
                </select>
              </label>
            </div>
            <label>
              <span style={label}>練習能力</span>
              <input
                type="text"
                required
                placeholder="例如：從客觀證據提煉幼兒的優勢與特殊教育需求"
                value={targetCompetency}
                onChange={(e) => setTargetCompetency(e.target.value)}
                style={input}
              />
              <span style={{ ...hint, display: 'block', marginTop: '6px' }}>學生在課程簡介與作答區會看到這一行。</span>
            </label>
          </section>

          {/* 角色與指令 */}
          <section style={panel}>
            <SectionHead title="角色與指令" desc="AI 助教扮演的角色，以及引導、回饋學生的方式。" />
            <label style={{ display: 'block', marginBottom: '14px' }}>
              <span style={label}>角色</span>
              <input
                type="text"
                required
                placeholder="例如：你是一位嚴謹但有同理心的資深特教督導"
                value={roleInstruction}
                onChange={(e) => setRoleInstruction(e.target.value)}
                style={input}
              />
            </label>
            <label style={{ display: 'block' }}>
              <span style={label}>引導與回饋方式</span>
              <textarea
                rows={6}
                required
                placeholder="說明 AI 如何引導學生、回饋的語氣（正向、具體、不浮誇），以及不直接給出標準答案等規範…"
                value={systemPrompt}
                onChange={(e) => setSystemPrompt(e.target.value)}
                style={{ ...input, resize: 'vertical', lineHeight: 1.6 }}
              />
            </label>
          </section>

          {/* 評分規準 */}
          <section style={panel}>
            <SectionHead
              title={`評分規準（總配分 ${totalRubricScore} 分）`}
              desc="每個評分項目的配分是它的權重；AI 依說明逐項評分，總分一律換算成 0–100。"
              action={
                <button type="button" onClick={handleAddRubric} style={addBtn}>
                  ＋ 新增評分項目
                </button>
              }
            />
            <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
              {rubrics.map((r, idx) => (
                <div key={idx} className="tf-rubric" style={{ ...item, display: 'grid', gridTemplateColumns: '1fr 80px 2fr auto', gap: '8px', alignItems: 'center' }}>
                  <input type="text" placeholder="評分項目" required value={r.dimension} onChange={(e) => handleRubricChange(idx, 'dimension', e.target.value)} style={input} />
                  <input
                    type="number"
                    placeholder="配分"
                    title="配分"
                    required
                    value={r.max_score}
                    onChange={(e) => handleRubricChange(idx, 'max_score', Number(e.target.value))}
                    style={input}
                  />
                  <input type="text" placeholder="評分說明：怎樣算做得好" required value={r.description} onChange={(e) => handleRubricChange(idx, 'description', e.target.value)} style={input} />
                  {rubrics.length > 1 ? (
                    <button type="button" onClick={() => handleRemoveRubric(idx)} style={removeBtn}>
                      刪除
                    </button>
                  ) : (
                    <span />
                  )}
                </div>
              ))}
            </div>

            {showRegradeWarning && (
              <div style={{ ...warnBox, marginTop: '12px' }}>你改了評分規準：已經評過的 {attemptCount} 筆作答不會重算。</div>
            )}

            <label style={{ display: 'flex', alignItems: 'center', gap: '10px', marginTop: '14px', flexWrap: 'wrap' }}>
              <span style={{ fontSize: '13px', color: '#cbd5e1' }}>評分級距</span>
              <select value={scaleType} onChange={(e) => setScaleType(e.target.value)} style={{ ...select, width: 'auto', minWidth: '220px' }}>
                {SCALE_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            </label>
          </section>

          {/* 分層提示 */}
          <section style={panel}>
            <SectionHead
              title="分層提示（選填）"
              desc="由淺到深排列。沒有設定時，系統會依學生最弱的評分項目即時產生提示，並在後台標示為「AI 產生」。"
              action={
                <button type="button" onClick={() => setHints([...hints, { content: '', trigger: 'score_below_threshold', is_example: false }])} style={addBtn}>
                  ＋ 新增一層
                </button>
              }
            />
            <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
              {hints.map((h, idx) => (
                <div key={idx} style={{ ...item, display: 'flex', flexDirection: 'column', gap: '8px' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <b style={{ fontSize: '13px' }}>第 {idx + 1} 層</b>
                    <button type="button" onClick={() => setHints(hints.filter((_, i) => i !== idx))} style={removeBtn}>
                      刪除
                    </button>
                  </div>
                  <textarea rows={2} required placeholder="提示內容" value={h.content} onChange={(e) => updateHint(idx, { content: e.target.value })} style={{ ...input, resize: 'vertical' }} />
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: '14px', alignItems: 'center' }}>
                    <select value={h.trigger} onChange={(e) => updateHint(idx, { trigger: e.target.value as HintItem['trigger'] })} style={{ ...select, width: 'auto' }}>
                      <option value="score_below_threshold">分數未達門檻時自動給</option>
                      <option value="student_request">學生按「我需要提示」才給</option>
                    </select>
                    <label style={check}>
                      <input type="checkbox" checked={h.is_example} onChange={(e) => updateHint(idx, { is_example: e.target.checked })} />
                      示範型提示（原樣呈現，並提醒學生不要照抄）
                    </label>
                  </div>
                </div>
              ))}
            </div>
            {hints.length > 0 && (
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: '16px', alignItems: 'center', marginTop: '12px' }}>
                <label style={check}>
                  自動提示最多給
                  <input type="number" min={0} value={maxAutoHints} onChange={(e) => setMaxAutoHints(Number(e.target.value))} style={{ ...input, width: '64px' }} />
                  層
                </label>
                <label style={check}>
                  <input type="checkbox" checked={personalize} onChange={(e) => setPersonalize(e.target.checked)} />
                  讓 AI 依學生作答改寫提示用語
                </label>
              </div>
            )}
          </section>

          {/* 常見錯誤 */}
          <section style={panel}>
            <SectionHead
              title="常見錯誤（選填）"
              desc="AI 會對照這份清單標出學生犯的錯誤，並附上原文；沒有設定時不做錯誤偵測。"
              action={
                <button
                  type="button"
                  onClick={() => setErrors([...errors, { code: '', label: '', description: '', severity: 'medium', related_dimension: '' }])}
                  style={addBtn}
                >
                  ＋ 新增錯誤類型
                </button>
              }
            />
            <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
              {errors.map((er, idx) => (
                <div key={idx} className="tf-error" style={{ ...item, display: 'grid', gridTemplateColumns: 'repeat(4, minmax(0, 1fr))', gap: '8px', alignItems: 'center' }}>
                  <input required placeholder="代碼（英文，如 diagnosis_only）" value={er.code} onChange={(e) => updateError(idx, { code: e.target.value })} style={input} />
                  <input required placeholder="名稱（如 僅列診斷名稱）" value={er.label} onChange={(e) => updateError(idx, { label: e.target.value })} style={input} />
                  <select value={er.severity} onChange={(e) => updateError(idx, { severity: e.target.value as ErrorItem['severity'] })} style={select}>
                    <option value="low">輕微</option>
                    <option value="medium">中等</option>
                    <option value="high">嚴重</option>
                  </select>
                  <select value={er.related_dimension} onChange={(e) => updateError(idx, { related_dimension: e.target.value })} style={select}>
                    <option value="">（不指定評分項目）</option>
                    {rubrics.map((r) => (
                      <option key={r.dimension} value={r.dimension}>
                        {r.dimension}
                      </option>
                    ))}
                  </select>
                  <input
                    required
                    placeholder="判斷說明：什麼情況算這個錯誤"
                    value={er.description}
                    onChange={(e) => updateError(idx, { description: e.target.value })}
                    className="tf-span3"
                    style={{ ...input, gridColumn: 'span 3' }}
                  />
                  <button type="button" onClick={() => setErrors(errors.filter((_, i) => i !== idx))} style={{ ...removeBtn, justifySelf: 'end' }}>
                    刪除
                  </button>
                </div>
              ))}
            </div>
          </section>

          {/* 機器人記憶 */}
          <section style={panel}>
            <SectionHead title="機器人記憶" desc="讓機器人記得這位學生之前的作答與前面關卡的成果，用在整體回饋與提示。記憶不影響評分，也不會多用 AI 次數。" />
            <label style={{ ...check, fontWeight: 'bold', color: '#f8fafc' }}>
              <input type="checkbox" checked={memoryEnabled} onChange={(e) => setMemoryEnabled(e.target.checked)} />
              開啟記憶
            </label>
            <div style={{ ...item, marginTop: '10px', display: 'flex', flexDirection: 'column', gap: '10px', opacity: memoryEnabled ? 1 : 0.5 }}>
              <label style={{ ...check, flexWrap: 'wrap' }}>
                記住這位學生在這一關最近
                <input
                  type="number"
                  min={0}
                  max={MEMORY_MAX_ATTEMPTS}
                  disabled={!memoryEnabled}
                  value={memoryMaxAttempts}
                  onChange={(e) => setMemoryMaxAttempts(Number(e.target.value))}
                  style={{ ...input, width: '64px' }}
                />
                次作答（0～{MEMORY_MAX_ATTEMPTS}，0 表示不記這一關的歷程）
              </label>
              <label style={check}>
                <input type="checkbox" disabled={!memoryEnabled} checked={memoryIncludePrior} onChange={(e) => setMemoryIncludePrior(e.target.checked)} />
                參考學生在同一模組前面關卡的作答（通過的版本優先）
              </label>
              <p style={hint}>次數越多，AI 看到的歷程越完整，但每次送出的內容也越長。建議 3 次。</p>
            </div>
          </section>

          <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '10px', paddingBottom: '24px' }}>
            <Link href={backUrl} style={{ padding: '10px 18px', borderRadius: '8px', border: '1px solid rgba(255,255,255,0.2)', color: '#cbd5e1', fontSize: '13px', textDecoration: 'none' }}>
              取消
            </Link>
            <button
              type="submit"
              disabled={submitting}
              style={{ padding: '10px 24px', borderRadius: '8px', border: 'none', backgroundColor: '#16a34a', color: '#fff', fontSize: '13px', fontWeight: 'bold', cursor: submitting ? 'not-allowed' : 'pointer', opacity: submitting ? 0.6 : 1 }}
            >
              {submitting ? '儲存中…' : editing ? '儲存為新版本' : '建立並發布機器人'}
            </button>
          </div>
        </form>
      </main>

      <style>{`
        @media (max-width: 760px) {
          .tf-two, .tf-rubric, .tf-error { grid-template-columns: minmax(0, 1fr) !important; }
          .tf-span3 { grid-column: auto !important; }
        }
      `}</style>
    </div>
  )
}
