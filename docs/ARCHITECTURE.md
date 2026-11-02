# 系統架構

> 描述**現在程式碼實際的樣子**（2026-11-02 更新，基準 main `e465eb3`）。目標規格見 [api.md](./api.md)；兩者不一致時以程式碼為準，差異記在 [KNOWN_GAPS.md](./KNOWN_GAPS.md)。
> AI 評分、分層提示、錯誤分類、機器人記憶的細節見 [LLM_SYSTEM.md](./LLM_SYSTEM.md)，這裡不重複。

---

## 1. 全貌

```
瀏覽器
  │
  ▼
Next.js 16（frontend/）──────────── Vercel
  │                    │
  │ publishable key    │ fetch + Authorization: Bearer <token>（JSON / SSE）
  │ 受 RLS 限制         │
  ▼                    ▼
Supabase ◄──────── FastAPI（backend/）── Render
PostgreSQL + Auth   │  Service Role / Secret Key（不受 RLS 限制）
                    ▼
               Google Gemini（GEMINI_MODEL，預設 gemini-3.5-flash）
```

**兩條資料路徑並存**：

| 路徑 | 用途 |
|------|------|
| 前端 → Supabase 直連（受 RLS 限制） | 登入、讀寫自己的 `profiles`、註冊流程（`complete_registration` RPC）、讀 `learning_modules` / `tool_cases` / 自己的 `step_attempts`、教師新增 `ai_tools` |
| 前端 → FastAPI → Supabase | 提交作答、AI 評分、提示、記憶、額度、教師複核、課程、指派、機器人申請、刪除機器人 |

RLS 政策在 migration 005（指派制：學生只看得到被指派的模組與機器人）與 006（`profiles`）。
後端用 service_role，**不受 RLS 限制**，所以權限一律在 API 裡用 token 檢查（見第 4 節）。

---

## 2. 目錄

```text
Summer_project/
├── backend/
│   ├── main.py                 進入點：掛 router + CORS（允許 FRONTEND_URL）
│   ├── check_setup.py          .env、Supabase、資料表、Gemini 模型檢查
│   ├── api/
│   │   ├── practice.py         練習、評分、提示、記憶、額度、一致性檢核（SSE）
│   │   ├── review.py           教師複核
│   │   ├── teacher.py          機器人 list / delete
│   │   ├── courses.py          課程、邀請碼加入、成員審核
│   │   ├── course_modules.py   模組指派給課程
│   │   ├── modules.py          模組與步驟管理（建立、排序、停用、發布）
│   │   ├── tool_requests.py    學生申請機器人、教師核准（期限、每日次數）
│   │   └── privacy.py          個資偵測
│   ├── core/
│   │   ├── config.py           環境變數（get_settings）
│   │   ├── auth.py             驗證 Supabase token：get_current_user / require_staff
│   │   ├── access.py           指派檢查與每日額度（check_step_access / consume_quota）
│   │   ├── step_unlock.py      步驟解鎖：前面的必修步驟通過才解鎖（check_step_unlocked）
│   │   ├── practice_session.py 練習 Session：第一次送出時建立並檢查擁有者（ensure_session）
│   │   ├── privacy.py          PII 正規表達式
│   │   └── ai/                 ← 見 LLM_SYSTEM.md（含 memory.py 機器人記憶）
│   ├── database/client.py      get_supabase()：每個執行緒各一個 client
│   └── tests/                  單元測試（數量以 py -m pytest -q 為準）
├── frontend/src/
│   ├── middleware.ts           登入守門 + 角色守門
│   ├── utils/api.ts            apiFetch / streamSSE：自動帶 token，後端網址讀 NEXT_PUBLIC_API_URL
│   ├── utils/supabase/         browser / server client
│   └── app/                    頁面（見第 5 節）
├── supabase/
│   ├── schema.sql              17 張表 + 觸發器（已同步 migrations）
│   ├── seed.sql                示範資料（只限本機）
│   ├── migrations/             001–010，可重複執行
│   └── practice_repo.py        ⚠️ 已停用，沒有任何地方 import
└── docs/
```

---

## 3. 一次提交作答的完整路徑

`POST /api/practice/sessions/{id}/submit`：

```
[0] 驗證 token → 作答者一律取自 token（body 的 user_id 忽略）
    session_id 必須是 UUID ─── 否則 400 INVALID_SESSION_ID
[1] 個資偵測 ─── 命中 → 400 PII_DETECTED，結束（不呼叫任何 LLM、不扣額度）
[2] 讀 module_steps JOIN ai_tools
      pass_score ← module_steps（不是 ai_tools）
      normalize_rubric()：max_score 是配分，量表由 scale_type 決定
[3] 指派檢查（core/access.py）─── 沒被指派 403 NOT_ASSIGNED / 核准過期 403 PERMISSION_EXPIRED
      解鎖檢查（core/step_unlock.py）─── 步驟停用 403 STEP_DISABLED / 前面必修沒通過 403 STEP_LOCKED
      練習 Session（core/practice_session.py）─── 第一次出現就建立；別人的 403 SESSION_FORBIDDEN
      自主模式扣 1 次每日額度 ─── 用完 429 QUOTA_EXCEEDED
      （以上依序；任一不過就不寫入作答、不扣額度。教師端跳過指派、解鎖與額度）
[4] 查此步驟的歷次嘗試 → attempt_number、上次分數、整個步驟累計的已用提示數
[5] 讀機器人記憶（core/ai/memory.py）：本步驟歷程 + 前面步驟成果；失敗當作沒有記憶
[6] INSERT step_attempts（status = submitted，連結 session_id）
[7] SSE：score_start（含 quota）→ tutor_decision（決策寫入 step_attempts.tutor_action）
[8] TutorAgent 四選一（規則見 LLM_SYSTEM.md 6.1）
      EVALUATE / ENCOURAGE → 逐構面評分（不看記憶）→ 整體回饋（參考記憶）→ score_complete
      GIVE_HINT            → 提示（參考記憶；拿不出來就改評分）
      ESCALATE             → 建議找老師（會進教師複核佇列）
      只給提示 / 轉介       → UPDATE step_attempts.status → revision_required
[9] 有 score_complete 時：
      錯誤分類偵測 → errors_detected
      INSERT ai_evaluations（含 confidence）
      UPDATE step_attempts.status → passed / revision_required
```

SSE 事件欄位見 [LLM_SYSTEM.md 第 5 節](./LLM_SYSTEM.md)。

---

## 4. 後端 API

全部端點（`/`、`/api/health` 除外）都要 `Authorization: Bearer <Supabase access token>`，
身分由 `core/auth.py` 驗證；body / query 裡的 `user_id`、`teacher_id` 一律忽略。
沒有 token 回 401 `UNAUTHORIZED`，角色不符回 403 `PERMISSION_DENIED`。

| 端點 | 權限 | 備註 |
|------|------|------|
| `POST /api/practice/sessions` | 登入者 | 只回傳新的 session_id；前端沒有呼叫，自己產生 UUID。Session 在第一次送出時才寫進 `practice_sessions` |
| `POST /api/practice/sessions/{id}/submit` | 已被指派、步驟已解鎖的學生 | 核心流程，SSE；扣每日額度 |
| `GET /api/practice/sessions/{id}/hints` | 已被指派的學生 | query：`step_id`、`reveal` |
| `GET /api/practice/sessions/{id}/history` | 登入者（只看自己） | |
| `GET /api/practice/steps/{id}/quota` | 已被指派的學生 | 只查不扣 |
| `GET /api/practice/steps/{id}/memory` | 已被指派的學生 | 機器人記憶摘要，不呼叫 LLM |
| `GET /api/practice/modules/{id}/progress` | 登入者（只看自己） | 每個啟用中步驟的通過與解鎖狀態；學生看未發布模組回 404 |
| `POST /api/practice/modules/{id}/coherence-check` | 登入者（只看自己） | SSE，至少 2 步驟有作答；跳過停用的步驟 |
| `POST /api/privacy/detect` | 不需登入 | 只做規則比對，不存原文 |
| `GET /api/review/pending` | 教師端 | 無教師判定，且有 AI 初評或被轉介（escalate）；`review_reasons` 非空者排前面 |
| `GET /api/review/submissions/{id}` | 教師端 | |
| `POST /api/review/submissions/{id}/judge` | 教師端 | `decision`：accept_ai / modify / override / request_retry |
| `GET /api/teacher/tools` | 教師端 | 回 `{data: [...]}` |
| `DELETE /api/teacher/tools/{id}` | 只限 owner | 仍被步驟使用時回 409 `TOOL_IN_USE` |
| `/api/courses/*` | 建立、審核限教師端；加入、查自己的課程限登入者 | 邀請碼加入 |
| `/api/courses/{id}/modules/*` | 指派、取消限教師端 | 模組 ↔ 課程 |
| `/api/modules/*` | 讀取限教師端；新增、修改、刪除、排序、發布只限 owner | 有作答的步驟不能刪、不能換機器人，只能停用；需要 migration 010 |
| `/api/tool-requests/*` | 學生申請；核准、退回限教師端 | 核准時設定期限與每日次數 |
| `GET /api/health` | 不需登入 | |

「教師端」= `profiles.role` 為 owner 或 assistant。
`main.py` 仍以註解預留：`auth`、`tools`（機器人建立／編輯目前由前端直接寫 Supabase）。

錯誤處理慣例：業務錯誤用 `HTTPException`，`detail` 為 `{code, message}`；SSE 串流中途的例外無法改狀態碼，改送 `event: error`。

---

## 5. 前端頁面

| 路徑 | 說明 |
|------|------|
| `/` | 登入與註冊（Email 驗證信），不受 middleware 守門 |
| `/login` | 登入，依 `profiles.role` 導向 |
| `/onboarding` | 新帳號補資料（`complete_registration` RPC） |
| `/forgot-password`、`/reset-password` | 忘記密碼、重設密碼 |
| `/portal` | 系統選擇頁（本平台 / XR 系統） |
| `/student/courses` | 我的課程、輸入邀請碼加入 |
| `/student/explore` | 自主探索：申請機器人、查看申請狀態 |
| `/student/modules` | 學生工作台：依課程列出被指派的模組、作答（SSE 逐構面顯示）、分層提示、作答紀錄、今天剩幾次；沒解鎖的步驟顯示 🔒 並停用送出，停用的步驟不顯示 |
| `/admin/tools` | 機器人清單 + 待複核清單 |
| `/admin/tools/new` | 建立機器人（含分層提示與錯誤分類欄位），直接寫 Supabase |
| `/admin/review/[id]` | 複核詳情：AI 初評、信心值、錯誤分類，送出教師判定 |
| `/admin/courses` | 課程管理：成員審核、指派／取消指派模組 |
| `/admin/modules` | 模組管理：建立模組、新增步驟、↑↓ 排序、停用／啟用、發布（只限教授操作） |
| `/admin/tool-requests` | 審核學生的機器人申請 |
| `/xr` | 轉址到 XR 系統（`XR_FRONTEND_URL`），只對這個路徑加 CSP |

`middleware.ts`（守 `/admin`、`/student`、`/onboarding`）：未登入 → `/`；還沒補資料 → `/onboarding`；
進 `/admin` 但角色不是 owner / assistant → `/student/courses`。

呼叫後端一律用 `utils/api.ts`：`apiFetch()` 一般請求、`streamSSE()` 讀 SSE。
兩者都會自動帶登入 token，後端網址讀 `NEXT_PUBLIC_API_URL`。

**為什麼 SSE 不能用 `EventSource`**：它只支援 GET，而送出作答是 POST，
所以 `streamSSE()` 用 `fetch` + `res.body.getReader()` 自己切 `event:` / `data:` 區塊。

---

## 6. 資料庫

17 張表，完整定義在 `supabase/schema.sql`（欄位都有註解）。這裡只列容易踩錯的地方。

| 分組 | 資料表 |
|------|--------|
| 帳號與課程 | `profiles`、`courses`、`course_members`、`tool_permissions`、`course_modules` |
| 機器人與案例 | `ai_tools`、`tool_cases` |
| 模組 | `learning_modules`、`module_steps` |
| 學習歷程 | `practice_sessions`、`step_attempts`、`ai_evaluations`、`teacher_reviews`、`prompt_logs` |
| 表單與倫理 | `forms`、`form_responses`、`research_consents` |

**容易踩錯的欄位：**

| 位置 | 注意 |
|------|------|
| `profiles.full_name` | 不是 `display_name` |
| `ai_tools.title` | 不是 `name` |
| `tool_cases.case_background` | 不是 `content` |
| `module_steps.pass_score` | 通過門檻在這裡，不在 `ai_tools` |
| `module_steps.is_active` | false = 停用：學生看不到、不能作答，作答保留。有作答的步驟不能刪（`step_attempts.step_id` 是 ON DELETE CASCADE） |
| `module_steps.is_required` | 必修：沒通過會鎖住後面的步驟（`core/step_unlock.py`） |
| `ai_tools.rubric_criteria[].max_score` | 是**配分（權重）**，不是量表上限 |
| `ai_tools.teaching_strategy.memory` | 機器人記憶的開關與筆數，見 LLM_SYSTEM.md 6.3 |
| `ai_evaluations.total_score` | 加權後的百分制得分 0–100 |
| `ai_evaluations` 無 `teacher_review_id` | 刻意的：是否已複核看有無對應 `teacher_reviews` |
| `step_attempts` 無 `course_id` | 違反共同規範，見 KNOWN_GAPS |
| `step_attempts.tutor_action` | TutorAgent 決策；只有 `evaluate` / `encourage` 會有對應的 `ai_evaluations` |
| `step_attempts.session_id` | 屬於哪一次練習；還沒跑 009 時不寫 |
| `practice_sessions.id` | 由前端產生（`crypto.randomUUID()`），所以沒有 DEFAULT |
| `tool_permissions.used_today` / `used_date` | 自主模式每日額度；扣次數一律走 `consume_tool_quota()`（migration 008），避免競態 |

**觸發器**：`auth.users` 新增一筆時，自動建立 `profiles`（role 預設 student）並產生匿名研究編號 `SPED-xxxxxxxx`。

**RLS**：所有表都已開啟；政策見 migration 005（指派制）、006（`profiles`）。新表要記得補政策與 GRANT（參考 002）。

**Migrations**（`supabase/migrations/`，都可重複執行，新環境要依序跑完）：

| 編號 | 內容 |
|------|------|
| 001 | LLM 邏輯系統欄位（confidence、提示來源等） |
| 002 | 資料表 GRANT（Supabase 新規則不再自動開放） |
| 003 | `step_attempts.tutor_action` |
| 004 | 註冊流程 |
| 005 | 指派制 RLS 政策 + `course_modules` |
| 006 | `profiles` RLS |
| 007 | 課程成員狀態加入 rejected |
| 008 | 自主模式每日額度（`consume_tool_quota()`） |
| 009 | 練習 Session：`practice_sessions`、`step_attempts.session_id` |
| 010 | 模組管理：`module_steps.is_active`、學生看不到停用步驟（重建 005 的政策）、`reorder_module_steps()` |

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
| 個資偵測放最前面 | 命中直接中止，不燒 API、不把個資送出平台、不扣額度 |
| 身分一律取自 token | 後端用 service_role 繞過 RLS，所以權限必須在 API 層檢查，不能相信前端傳的 id |
| 記憶不進逐構面評分 | 避免「上次低分、這次也偏低」的錨定效應；記憶只用在回饋與提示 |
| Secret key 只在後端 | 後端以最高權限統一操作；前端只拿公開金鑰 |
| `step_attempts` 保留每次作答 | `attempt_number` 遞增，供版本比對、研究分析與機器人記憶 |
| 停用取代刪除 | 刪步驟會連學生作答一起刪掉；有作答的步驟與模組只能停用／取消發布，研究資料不會消失 |
| 教師判定決定是否通過 | 解鎖時，已發布且有給分的教師複核優先於 AI 結果（AI 永不覆蓋教師判定） |

---

## 8. 技術版本

| 層 | 技術 |
|----|------|
| 前端 | Next.js 16.3、React 19.2、Tailwind 4、`@supabase/ssr` 0.12 |
| 後端 | FastAPI、pydantic-settings、LangChain + `langchain-google-genai` |
| AI | Gemini 3.5 Flash（由 `GEMINI_MODEL` 設定） |
| 資料庫 | Supabase PostgreSQL（pgvector 已啟用但未使用） |
| 測試 | pytest + `unittest.mock` |
