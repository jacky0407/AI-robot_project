// 後端 FastAPI 的共用呼叫工具
// 後端網址由 NEXT_PUBLIC_API_URL 設定（見 frontend/.env.example），不要在頁面裡寫死

export const API_URL = (process.env.NEXT_PUBLIC_API_URL ?? 'http://127.0.0.1:8000').replace(/\/$/, '')

export class ApiError extends Error {
  status: number
  code?: string
  detail: unknown

  constructor(status: number, detail: unknown) {
    super(describeDetail(detail) || `HTTP ${status}`)
    this.status = status
    this.detail = detail
    if (detail && typeof detail === 'object' && 'code' in detail) {
      this.code = String((detail as { code: unknown }).code)
    }
  }
}

// FastAPI 的錯誤放在 { detail }，detail 可能是字串或 { code, message }
function describeDetail(detail: unknown): string {
  if (typeof detail === 'string') return detail
  if (detail && typeof detail === 'object' && 'message' in detail) {
    return String((detail as { message: unknown }).message)
  }
  return ''
}

async function toApiError(res: Response): Promise<ApiError> {
  let body: unknown = null
  try {
    body = await res.json()
  } catch {
    // 非 JSON 錯誤（例如後端沒開）就只回狀態碼
  }
  const detail = body && typeof body === 'object' && 'detail' in body ? (body as { detail: unknown }).detail : body
  return new ApiError(res.status, detail)
}

export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
  })
  if (!res.ok) throw await toApiError(res)
  return (await res.json()) as T
}

/**
 * 呼叫回傳 SSE 的 POST 端點，逐一把事件交給 onEvent。
 *
 * 不能用 EventSource（只支援 GET），改用 fetch + ReadableStream 邊收邊解析，
 * 才能做到 AI 逐構面即時顯示。
 */
export async function streamSSE(
  path: string,
  init: RequestInit,
  onEvent: (event: string, data: Record<string, unknown>) => void,
): Promise<void> {
  const res = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(init.headers ?? {}) },
  })
  if (!res.ok) throw await toApiError(res)
  if (!res.body) throw new ApiError(res.status, '瀏覽器不支援串流回應')

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  const flush = (block: string) => {
    const event = block.match(/^event: (.+)$/m)?.[1]
    const data = block.match(/^data: (.+)$/m)?.[1]
    if (!event || !data) return
    try {
      onEvent(event, JSON.parse(data))
    } catch {
      // 單一事件解析失敗不影響後續事件
    }
  }

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, '\n')
    const blocks = buffer.split('\n\n')
    buffer = blocks.pop() ?? ''
    blocks.forEach(flush)
  }
  if (buffer.trim()) flush(buffer)
}
