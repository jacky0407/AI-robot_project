# 已知落差與技術債

> 掃描時間：2026-09-17｜基準 commit `62f57f4`
> 標 ✅ 的項目已在「第一批修正」中處理完畢（見 [CHANGELOG.md](./CHANGELOG.md)）。
> 其餘項目仍待處理，標 🔴 者會在執行時直接失敗。

---

## 1. ✅ 已修正 — `api/review.py` 與 `schema.sql` 欄位完全不符

原問題：`backend/api/review.py` 是依 [api.md](./api.md) 的規格寫的，但 `supabase/schema.sql` 的 `teacher_reviews` 定義不同，**三個端點都會失敗**。

### `POST /api/review/submissions/{id}/judge`

| 程式碼寫入 | schema 實際欄位 | 結果 |
|-----------|----------------|------|
| `action` | `decision`（NOT NULL, CHECK） | 🔴 欄位不存在 |
| `teacher_comment` | `final_feedback` | 🔴 欄位不存在 |
| `dimension_overrides` | — | 🔴 欄位不存在 |
| `require_redo` | — | 🔴 欄位不存在 |
| （未提供） | `teacher_id` NOT NULL | 🔴 NOT NULL 違反 |
| （未提供） | `decision` NOT NULL | 🔴 NOT NULL 違反 |

接著又執行：

```python
db.table("ai_evaluations").update({"teacher_review_id": review_id}).eq("attempt_id", attempt_id)
```

`ai_evaluations` **沒有 `teacher_review_id` 欄位**。

### `GET /api/review/pending`

```python
db.table("ai_evaluations").select(
    "id, attempt_id, total_score, step_attempts(id, created_at, user_input_content, user_id, profiles(display_name))"
).is_("teacher_review_id", "null")
```

兩個問題：`teacher_review_id` 不存在；`profiles` 的欄位是 `full_name` 而非 `display_name`。

### ✅ 採用的修法：改 `review.py` 對齊現有 schema（不動資料庫）

- `JudgeRequest` 改為 `teacher_id` / `decision` / `final_score` / `final_feedback` / `is_published`
- `decision` 先驗證是否為 `accept_ai` / `modify` / `override` / `request_retry`，不合法回 400 而非讓資料庫報錯
- 「待複核」改以「`step_attempts` 有 `ai_evaluations` 但沒有 `teacher_reviews`」判斷，不再需要 `ai_evaluations.teacher_review_id`
- `profiles` 欄位改回 `full_name`
- 移除會覆寫 `ai_evaluations` 的那次 update——AI 初評永遠保持原狀
- `decision = request_retry` 時才把 `step_attempts.status` 改回 `revision_required`

> ⚠️ `teacher_id` 目前仍由前端傳入，等 #8 做完身分驗證後要改成從 JWT 取得。

---

## 2. ✅ 已修正 — `practice.py` 查提示次數時會 KeyError

修正前：

```python
prev_attempts = db.table("step_attempts").select("attempt_number")...   # 只選了 attempt_number
...
hints_res = db.table("prompt_logs").select("id", count="exact").eq(
    "attempt_id",
    prev_attempts.data[0]["id"] if prev_attempts.data else "none"       # ← 沒有 "id" 這個 key
).execute()
```

原問題有兩個：

- 第二次以後提交 → `KeyError: 'id'`
- 第一次提交 → 傳入字串 `"none"` 去比對 UUID 欄位，PostgreSQL 回 `invalid input syntax for type uuid`

### ✅ 採用的修法

`select("id, attempt_number, ai_evaluations(total_score, dimension_scores)")` 一次查回需要的欄位，
並在沒有前次嘗試時**跳過** `prompt_logs` 查詢（`hints_used = 0`）。
上次分數改由新的 `_extract_last_score_percent()` 取得，優先用 `total_score`，
舊資料才退回用 `dimension_scores` 換算。

回歸測試：`tests/test_practice_api.py::TestSecondAttempt`

---

## 3. ✅ 已修正 — `POST /api/practice/sessions` 讀不存在的欄位

| 程式碼 | schema 實際 |
|--------|------------|
| `tool_data.get("name")` | `ai_tools.title` |
| `tool_data.get("opening_message")` | 不存在 |
| `tool_data.get("task_description")` | 不存在 |
| `case_data.get("content")` | `tool_cases.case_background` |

原問題：因為都用 `.get(..., "")`，不會拋例外，但**回傳的欄位全是空字串**，前端拿不到機器人名稱與案例內容。

另外 `session_id` 只是 `str(uuid.uuid4())`，**沒有寫進任何資料表**，後續 `submit` 也不驗證它——前端目前直接傳 `temp-session-id` 也能通過。

### ✅ 採用的修法

欄位改讀真實的 `title` / `role_instruction` / `case_background`，同時保留 api.md 的回應鍵名，
並補上 `id` 與 `difficulty`。

> ⚠️ `session_id` 仍未落庫（已在程式碼註記）。要正式支援 Session 需新增 `practice_sessions` 表，
> 或把 `session_id` 從路徑移除、改以 `step_id` + `user_id` 定位。這部分尚未處理。

---

## 4. ✅ 已修正 — 通過門檻讀錯表

修正前：

```python
pass_threshold = tool.get("pass_score", 75)   # tool = step_res.data["ai_tools"]
```

原問題：`pass_score` 定義在 **`module_steps`**，不在 `ai_tools`。所以這行永遠取到預設值 `75`，
教授在 `module_steps` 設的 `70` 完全沒有作用。

### ✅ 採用的修法

```python
pass_threshold = step_res.data.get("pass_score") or 75
```

回歸測試：`tests/test_practice_api.py::TestPassThresholdAndWeighting::test_pass_threshold_comes_from_module_steps`

---

## 5. ✅ 已修正 — Rubric 等級自動生成的邏輯有問題

修正前：

```python
"levels": [
    {"score": lv, "description": r.get("description", "")}
    for lv in range(1, r.get("max_score", 4) + 1)
] if r.get("levels") is None else r.get("levels")
```

原問題：種子資料與前端建構器產生的 `rubric_criteria` 都**沒有 `levels`**，`max_score` 是配分（40、30…）。
所以 `max_score=40` 會生成 **40 個等級**，而且每一級的 `description` 完全相同。
送進 `EvaluatorAgent._score_dimension()` 後，prompt 會列出 40 行一模一樣的敘述，AI 無從判斷。

### ✅ 採用的修法：max_score 視為權重，固定 4 級量表評分後加權

新增 `core/ai/rubric_formatter.py` 的兩個函式：

- `build_default_levels(description)` — 沒有自訂 `levels` 時，展開成**描述各不相同**的 4 級量表
- `normalize_rubric(rubric_criteria, pass_threshold)` — 把資料庫格式轉成 Agent 需要的結構，`max_score → weight`

`evaluator_agent.py` 新增 `compute_weighted_percentage()`：
各構面先算得分率（`score / max_score`），再依 `weight` 加權平均。
`score_complete` 的 `total_score` 因此變成**百分制得分**，`max_total_score` 固定為 `100`。
權重一律由 Rubric 帶入，不採信 AI 自己回傳的值。

沒有 `weight` 的舊路徑（`GeminiProvider`）會退回原本的「總分 / 總滿分」算法，維持向下相容。

> 📌 教授在工具建構器填的配分總和建議維持 100，門檻（`module_steps.pass_score`）才好對應。

回歸測試：`tests/test_rubric_formatter.py::TestNormalizeRubric`、
`tests/test_evaluator_agent.py::TestComputeWeightedPercentage`

---

## 6. 🟠 API 回應格式不一致

[api.md](./api.md) 的通用規範是所有成功回應包成 `{"data": ...}`。

| 端點 | 目前回傳 |
|------|---------|
| `GET /api/teacher/tools` | 直接回陣列（未包 `data`） |
| `DELETE /api/teacher/tools/{id}` | `{"success": true, "message": "..."}` |
| 其他 | `{"data": ...}` ✅ |

---

## 7. 🟠 前端未正確消費 SSE

`/student/practice` 用 `await res.text()` 一次讀完整個串流，等同於同步等待，
「AI 逐構面即時評分」的體驗完全消失。解析後也只是 `JSON.stringify` 顯示原文。

修法與範例程式見 [FRONTEND.md](./FRONTEND.md#studentpractice--練習室)。

---

## 8. 🔴 後端 API 沒有任何身分驗證

所有端點都**沒有檢查呼叫者身分**：

- `POST /api/practice/.../submit` 的 `user_id` 由 request body 傳入 → 任何人可冒用他人身分作答
- `DELETE /api/teacher/tools/{id}` 無角色檢查 → 學生可刪除教授的機器人
- `POST /api/review/.../judge` 無角色檢查 → 學生可自己給自己判定

**修法**：加一個 FastAPI dependency，驗證 `Authorization: Bearer <supabase_jwt>`，
用 Supabase 的 `auth.get_user(token)` 取得 `user_id` 與 `role`，並以此取代前端傳入的 `user_id`。

---

## 9. 🔴 Supabase RLS 完全未啟用

`schema.sql` 中沒有任何 `ENABLE ROW LEVEL SECURITY` / `CREATE POLICY`。
前端 anon key 是公開資訊，目前任何人都能直接讀寫 `profiles`、`ai_tools`、`module_steps`、`tool_cases`。

建議的策略清單見 [DATABASE.md](./DATABASE.md#6-rls-現況)。

---

## 10. ✅ 已修正 — `seed.sql` 無法直接執行

原問題：

- `'case-0001-...'`、`'tool-0001-...'`、`'mod-0001-...'` **不是合法 UUID**（`s`、`t`、`o`、`l`、`m` 不是十六進位字元，且第一段需 8 字元）→ 執行時報 `invalid input syntax for type uuid`
- 兩個測試帳號的 `encrypted_password` 是空字串 → 無法登入

### ✅ 採用的修法

| 舊值 | 新值 | 指向 |
|------|------|------|
| `tool-0001-…` | `a0000001-…` | 步驟1 案例資料整理教練 |
| `tool-0002-…` | `a0000002-…` | 步驟2 功能性現況撰寫教練 |
| `mod-0001-…` | `b0000001-…` | 學前 IEP 逐步撰寫模組 |
| `case-0001-…` | `ca5e0001-…` | 案例A：小明 |

密碼改用 `crypt('Test1234!', gen_salt('bf'))`（檔頭加了 `CREATE EXTENSION IF NOT EXISTS pgcrypto`），
兩個測試帳號密碼皆為 `Test1234!`。檔頭也加了「正式環境勿執行」的警語。

> 若 GoTrue 版本對 `auth.users` 的必填欄位要求不同而仍無法登入，
> 請改到 Dashboard → Authentication → Users 手動設定密碼。

---

## 11. 🟡 寫死的設定

| 位置 | 內容 |
|------|------|
| `frontend/src/app/student/practice/page.tsx` | `http://127.0.0.1:8000` |
| `frontend/src/app/admin/tools/page.tsx` | `http://127.0.0.1:8000` |
| `frontend/src/app/student/practice/page.tsx` | session id `temp-session-id` |
| `backend/core/ai/agents/tutor_agent.py` | `_default_hint()` 的四層提示文字 |
| `backend/core/ai/agents/coherence_agent.py` | `DEFAULT_COHERENCE_CHECKS` 三條規則 |
| `backend/api/practice.py` | `/hints` 端點整段回傳值 |

前兩項應抽成 `NEXT_PUBLIC_API_URL`，後三項應改讀資料庫（`ai_tools.teaching_strategy`）。

---

## 12. 🟡 定義了但沒有人使用的欄位與檔案

| 項目 | 狀況 |
|------|------|
| `ai_tools.teaching_strategy` | 無程式碼讀取 |
| `ai_tools.error_taxonomy` | 無程式碼讀取；`ai_evaluations.detected_errors` 恆為 `[]` |
| `ai_tools.max_cost_limit` | 無程式碼檢查 |
| `ai_evaluations.ai_cost` | 恆為 0.0 |
| `ai_evaluations.suggested_next_step` | 從未寫入 |
| `module_steps.pass_forward_keys` | 步驟間資料傳遞未實作 |
| `module_steps.require_teacher_review` | 無程式碼讀取 |
| `step_attempts.structured_data` | 從未寫入 |
| `prompt_logs.student_reaction` | 從未寫入 |
| `tool_permissions` / `forms` / `form_responses` | 整張表無人使用 |
| `backend/core/ai/tools/evaluation_tools.py` | 四個 LangChain tool 未被任何 Agent 掛載 |
| `supabase/practice_repo.py` | 早期資料存取層，沒有任何檔案 import 它 |
| `requirements.txt` 的 `sse-starlette` | 實際使用 FastAPI `StreamingResponse` 手刻 SSE |
| pgvector extension | 已啟用，但無任何向量欄位或檢索邏輯 |

---

## 13. 🟡 其他小問題

| 項目 | 說明 |
|------|------|
| `ai_evaluations.evidence_text` | 存的是學生作答前 200 字，不是 AI 引用的佐證。AI 的佐證其實已在 `dimension_scores[].evidence` 裡 |
| `GeminiProvider.evaluate_stream()` | `score_complete` 的 `passed` 寫死 `False`，註解說「由呼叫方補上」但無人補 |
| `review.py` 的 `d.dict()` | Pydantic v2 已棄用，應改 `d.model_dump()` |
| `frontend/src/app/layout.tsx` | metadata 仍是 `create-next-app` 預設值（`title: "Create Next App"`） |
| `frontend/src/app/page.tsx` | 22KB 原型與正式路線並存，職責重疊，且不受 `middleware` 守門 |
| `/student/practice` 取案例 | `tool_cases.select('*').limit(1)` 固定取第一筆，與步驟無關 |
| `/student/modules` | 查全部 `module_steps`，未依課程篩選，也沒有解鎖邏輯 |
| `main.py` | router import 分散在檔案上方與中段，建議集中 |

---

## 14. 建議處理順序

```
✅ 第一批（已完成）  → #1 review 欄位、#2 KeyError、#3 session 欄位、#10 seed.sql
                      外加 #4 pass_score、#5 Rubric levels
   第二批（安全）    → #8 API 身分驗證、#9 RLS
   第三批（體驗）    → #7 前端 SSE
   第四批（可部署）  → #11 環境變數、#6 回應格式
   第五批（補需求）  → #12 未使用欄位對應的功能
```

第一批的完整異動見 [CHANGELOG.md](./CHANGELOG.md)。
