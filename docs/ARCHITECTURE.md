# 系統架構

> 描述**現在程式碼實際的樣子**。目標規格見 [api.md](./api.md)；兩者不一致時以程式碼為準，差異記在 [KNOWN_GAPS.md](./KNOWN_GAPS.md)。
> AI 評分、分層提示、錯誤分類的細節見 [LLM_SYSTEM.md](./LLM_SYSTEM.md)，這裡不重複。

---

## 1. 全貌

```
瀏覽器
  │
  ▼
Next.js 16（frontend/）──────────── Vercel
  │                    │
  │ anon/publishable   │ fetch（JSON / SSE）
  │ 直接讀寫            │
  ▼                    ▼
Supabase ◄──────── FastAPI（backend/）── Render
PostgreSQL + Auth   │  Service Role / Secret Key
                    ▼
               Google Gemini（GEMINI_MODEL，預設 gemini-3.5-flash）
```

**兩條資料路徑並存**，這是目前架構最需要注意的一點：

| 路徑 | 用途 |
|------|------|
| 前端 → Supabase 直連 | 登入、讀 `module_steps` / `ai_tools` / `tool_cases`、新增機器人 |
| 前端 → FastAPI → Supabase | 提交作答、AI 評分、提示、刪除機器人、教師複核 |

> ⚠️ RLS 尚未啟用，前端直連路徑等於對外全開。見 KNOWN_GAPS #9。

---

## 2. 目錄

```text
Summer_project/
├── backend/
│   ├── main.py                 進入點：掛 router + CORS
│   ├── check_setup.py          Supabase 設定檢查
│   ├── api/
│   │   ├── practice.py         練習、評分、提示、一致性檢核（SSE）
│   │   ├── review.py           教師複核
│   │   ├── teacher.py          機器人 list / delete
│   │   └── privacy.py          個資偵測
│   ├── core/
│   │   ├── config.py           環境變數（get_settings）
│   │   ├── privacy.py          PII 正規表達式
│   │   └── ai/                 ← 見 LLM_SYSTEM.md
│   ├── database/client.py      get_supabase()
│   └── tests/                  191 個單元測試
├── frontend/src/
│   ├── middleware.ts           登入守門 + 角色守門
│   ├── utils/supabase/         browser / server client
│   └── app/                    頁面（見第 5 節）
├── supabase/
│   ├── schema.sql              15 張表 + 觸發器
│   ├── seed.sql                示範資料
│   ├── migrations/             舊資料庫補欄位用
│   └── practice_repo.py        ⚠️ 已停用，沒有任何地方 import
└── docs/
```

---

## 3. 一次提交作答的完整路徑

`POST /api/practice/sessions/{id}/submit`：

```
[1] 個資偵測 ─── 命中 → 400 PII_DETECTED，結束（不呼叫任何 LLM）
[2] 讀 module_steps JOIN ai_tools
      pass_score ← module_steps（不是 ai_tools）
      normalize_rubric()：max_score 是配分，量表由 scale_type 決定
[3] 查上一次嘗試 → attempt_number、上次分數、已用提示數
[4] INSERT step_attempts（status = submitted）
[5] SSE：score_start → tutor_decision
[6] TutorAgent 四選一
      EVALUATE / ENCOURAGE → 逐構面評分 → score_complete
      GIVE_HINT            → 提示（拿不出來就改評分）
      ESCALATE             → 建議找老師
[7] 有 score_complete 時：
      錯誤分類偵測 → errors_detected
      INSERT ai_evaluations（含 confidence）
      UPDATE step_attempts.status → passed / revision_required
```

SSE 事件欄位見 [LLM_SYSTEM.md 第 5 節](./LLM_SYSTEM.md)。

---

## 4. 後端 API

| 端點 | 狀態 | 備註 |
|------|------|------|
| `POST /api/practice/sessions` | 🟡 | session_id 未落庫 |
| `POST /api/practice/sessions/{id}/submit` | ✅ | 核心流程，SSE |
| `GET /api/practice/sessions/{id}/hints` | ✅ | query：`user_id`、`step_id`、`reveal` |
| `GET /api/practice/sessions/{id}/history` | ✅ | |
| `POST /api/practice/modules/{id}/coherence-check` | ✅ | SSE，至少 2 步驟有作答 |
| `POST /api/privacy/detect` | ✅ | |
| `GET /api/review/pending` | ✅ | 有 AI 初評但無教師判定者 |
| `GET /api/review/submissions/{id}` | ✅ | |
| `POST /api/review/submissions/{id}/judge` | ✅ | `decision`：accept_ai / modify / override / request_retry |
| `GET /api/teacher/tools` | ✅ | ⚠️ 直接回陣列，未包 `{data}` |
| `DELETE /api/teacher/tools/{id}` | ✅ | |
| `GET /api/health` | ✅ | |

`main.py` 以註解預留、**檔案不存在**：`auth`、`courses`、`tools`、`export`。

> ⚠️ 所有端點都沒有身分驗證，`user_id` / `teacher_id` 由前端傳入。見 KNOWN_GAPS #8。

錯誤處理慣例：業務錯誤用 `HTTPException`；SSE 串流中途的例外無法改狀態碼，改送 `event: error`。

---

## 5. 前端頁面

| 路徑 | 狀態 | 說明 |
|------|------|------|
| `/login` | ✅ | Supabase Auth，依 `profiles.role` 導向 |
| `/student/modules` | ✅ | 列出全部 `module_steps`（未依課程篩選、無解鎖邏輯） |
| `/student/practice` | ⚠️ | 能提交，但 `res.text()` 一次讀完 SSE，結果以 JSON 原文顯示 |
| `/admin/tools` | ✅ | 機器人清單 + 刪除 |
| `/admin/tools/new` | ✅ | 建立機器人；**沒有**分層提示與錯誤分類欄位 |
| `/` | ⚠️ | 早期純前端原型，未串後端，不受 middleware 守門 |

`middleware.ts`：未登入 → `/login`；進 `/admin` 但角色不是 owner / assistant → `/student/modules`。

後端網址寫死 `http://127.0.0.1:8000`（`student/practice`、`admin/tools` 兩頁），應改成 `NEXT_PUBLIC_API_URL`。

**SSE 正確消費方式**（不能用 `EventSource`，它只支援 GET）：

```ts
const res = await fetch(url, { method: 'POST', body })
const reader = res.body!.getReader()
const decoder = new TextDecoder()
let buffer = ''
while (true) {
  const { done, value } = await reader.read()
  if (done) break
  buffer += decoder.decode(value, { stream: true })
  const blocks = buffer.split('\n\n')
  buffer = blocks.pop() ?? ''
  for (const block of blocks) {
    const event = block.match(/^event: (.+)$/m)?.[1]
    const data  = block.match(/^data: (.+)$/m)?.[1]
    if (event && data) handleEvent(event, JSON.parse(data))
  }
}
```

---

## 6. 資料庫

15 張表，完整定義在 `supabase/schema.sql`（欄位都有註解）。這裡只列容易踩錯的地方。

| 分組 | 資料表 |
|------|--------|
| 帳號 | `profiles`、`courses`、`course_members`、`tool_permissions` |
| 機器人與案例 | `ai_tools`、`tool_cases` |
| 模組 | `learning_modules`、`module_steps` |
| 學習歷程 | `step_attempts`、`ai_evaluations`、`teacher_reviews`、`prompt_logs` |
| 表單與倫理 | `forms`、`form_responses`、`research_consents` |

**容易踩錯的欄位：**

| 位置 | 注意 |
|------|------|
| `profiles.full_name` | 不是 `display_name` |
| `ai_tools.title` | 不是 `name` |
| `tool_cases.case_background` | 不是 `content` |
| `module_steps.pass_score` | 通過門檻在這裡，不在 `ai_tools` |
| `ai_tools.rubric_criteria[].max_score` | 是**配分（權重）**，不是量表上限 |
| `ai_evaluations.total_score` | 加權後的百分制得分 0–100 |
| `ai_evaluations` 無 `teacher_review_id` | 刻意的：是否已複核看有無對應 `teacher_reviews` |
| `step_attempts` 無 `course_id` | 違反共同規範，見 KNOWN_GAPS |

**觸發器**：`auth.users` 新增一筆時，自動建立 `profiles`（role 預設 student）並產生匿名研究編號 `SPED-xxxxxxxx`。

**RLS**：目前**完全沒有**。

**種子資料固定 UUID**：

| 前綴 | 指向 |
|------|------|
| `a0000001-…` / `a0000002-…` | 機器人：步驟1、步驟2 |
| `b0000001-…` | 學前 IEP 逐步撰寫模組 |
| `c1111111-…` | 課程（邀請碼 `IEP2026`） |
| `ca5e0001-…` | 案例A：小明 |

---

## 7. 關鍵設計決策

| 決策 | 理由 |
|------|------|
| SSE 而非 WebSocket | 評分是單向推送，SSE 更簡單，Render 免費方案也支援 |
| AI 初評與教師判定分表 | 研究資料需保留 AI 原始判斷，兩者永不互相覆蓋 |
| 配分與量表分離 | 配分是權重，評分用固定級距，兩者在 `normalize_rubric()` 交會 |
| 個資偵測放最前面 | 命中直接中止，不燒 API、不把個資送出平台 |
| Secret key 只在後端 | 後端以最高權限統一操作；前端只拿公開金鑰 |
| `step_attempts` 保留每次作答 | `attempt_number` 遞增，供版本比對與研究分析 |

---

## 8. 技術版本

| 層 | 技術 |
|----|------|
| 前端 | Next.js 16.3、React 19.2、Tailwind 4、`@supabase/ssr` 0.12 |
| 後端 | FastAPI、pydantic-settings、LangChain + `langchain-google-genai` |
| AI | Gemini 3.5 Flash（由 `GEMINI_MODEL` 設定） |
| 資料庫 | Supabase PostgreSQL（pgvector 已啟用但未使用） |
| 測試 | pytest + `unittest.mock` |
