# 資料庫說明（Supabase PostgreSQL）

> 對應檔案：`supabase/schema.sql`、`supabase/seed.sql`｜基準 commit `62f57f4` + 第一批修正
> `schema.sql` 未變動；`seed.sql` 已修正（見 [CHANGELOG.md](./CHANGELOG.md)）

---

## 1. 擴充套件

```sql
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS vector;     -- pgvector，目前尚無任何向量欄位
```

---

## 2. 關聯總覽

```
auth.users
   │ (trigger: handle_new_platform_user)
   ▼
profiles ─────┬──► course_members ──► courses
              ├──► tool_permissions
              ├──► research_consents
              ├──► ai_tools (created_by)
              ├──► step_attempts
              ├──► teacher_reviews (teacher_id)
              └──► form_responses

learning_modules ──► module_steps ──► ai_tools
                          │              └──► forms
                          ▼
                    step_attempts ──► tool_cases
                          ├──► ai_evaluations
                          ├──► teacher_reviews
                          ├──► prompt_logs
                          └──► form_responses
```

---

## 3. 逐表說明

### 帳號與權限

#### `profiles` — 使用者基本資料

綁定 `auth.users`（`id` 同步，`ON DELETE CASCADE`）。

| 欄位 | 型別 | 說明 |
|------|------|------|
| `id` | UUID PK | = `auth.users.id` |
| `email` | TEXT NOT NULL | |
| `full_name` | TEXT | ⚠️ 不是 `display_name` |
| `role` | TEXT | `owner` / `assistant` / `student` / `explorer`，預設 `student` |
| `is_active` | BOOLEAN | 預設 true |
| `created_at` | TIMESTAMPTZ | |

#### `courses` — 課程（正式課程模式）

| 欄位 | 說明 |
|------|------|
| `title`, `description` | |
| `invite_code` | TEXT **UNIQUE**，課程邀請碼 |
| `require_approval` | 加入是否需審核 |
| `is_archived` | |
| `created_by` | → `profiles.id` |

#### `course_members` — 課程成員

`UNIQUE(course_id, user_id)`。`status`：`active` / `pending` / `removed`。

#### `tool_permissions` — 自主探索者權限與每日額度

| 欄位 | 說明 |
|------|------|
| `tool_id` | UUID，**無外鍵約束**；NULL 表示全平台通用額度 |
| `daily_quota` | 預設 10 |
| `used_today` | 預設 0（**目前沒有任何程式碼會重設或遞增它**） |
| `start_date` / `expire_date` | |
| `status` | `pending` / `approved` / `rejected` / `expired` |

> 🔴 目前無任何程式碼使用此表。

---

### AI 工具與案例

#### `ai_tools` — AI 能力機器人

| 欄位 | 型別 | 說明 |
|------|------|------|
| `title` | TEXT NOT NULL | ⚠️ **不是 `name`** |
| `domain` | TEXT NOT NULL | 如「學前IEP」 |
| `target_competency` | TEXT NOT NULL | 目標能力 |
| `role_instruction` | TEXT NOT NULL | AI 角色與目標（簡述） |
| `system_prompt` | TEXT NOT NULL | 詳細系統指令 |
| `teaching_strategy` | JSONB | 引導時點、提示層級設定（**尚無程式碼讀取**） |
| `rubric_criteria` | JSONB | Rubric 陣列，見下方格式 |
| `error_taxonomy` | JSONB | 錯誤分類定義（**尚無程式碼讀取**） |
| `max_cost_limit` | FLOAT | 單次任務成本上限，預設 0.5（**未檢查**） |
| `version` | INT | 預設 1 |
| `status` | TEXT | `draft` / `published` / `paused` / `archived` |
| `created_by` | UUID | → `profiles.id` |

**`rubric_criteria` 的實際格式**（來自 `seed.sql` 與 `/admin/tools/new`）：

```json
[
  {"dimension": "客觀事實辨識", "max_score": 40, "description": "能準確擷取案例中的具體行為與數據"},
  {"dimension": "推論與假設區分", "max_score": 30, "description": "..."}
]
```

後端 `normalize_rubric()` 會把它轉成：

```python
{"pass_threshold_percent": 70,
 "dimensions": [{"name": "客觀事實辨識", "weight": 40, "levels": [4級量表]}]}
```

`max_score` 一律視為**配分（權重）**，評分固定用 4 級量表；
沒有 `levels` 時由 `build_default_levels()` 展開成描述各不相同的四級。
建議配分總和維持 100，才好對應 `module_steps.pass_score`。

#### `tool_cases` — 虛構教學案例

| 欄位 | 說明 |
|------|------|
| `title` | |
| `difficulty` | `basic` / `intermediate` / `advanced` |
| `case_background` | TEXT NOT NULL，⚠️ **不是 `content`** |
| `known_info` | JSONB，AI 掌握的背景資訊與揭露條件 |
| `is_synthetic` | 預設 true，標記為合成／虛構案例 |

> 此表**無 `tool_id`**，案例與機器人目前是多對多的隱含關係，未建關聯表。

---

### 模組編排

#### `learning_modules` — 培訓模組

`title`、`description`、`domain`、`is_published`。

#### `module_steps` — 模組步驟編排

| 欄位 | 說明 |
|------|------|
| `module_id` | → `learning_modules`（CASCADE） |
| `tool_id` | → `ai_tools`（**RESTRICT**，機器人被引用時不可刪） |
| `step_order` | INT，`UNIQUE(module_id, step_order)` |
| `step_title` | |
| `is_required` | 預設 true |
| `pass_score` | INT，預設 70 —— ⚠️ **通過門檻在這裡，不在 `ai_tools`** |
| `require_teacher_review` | 是否為教師強制審核點（**尚無程式碼讀取**） |
| `pass_forward_keys` | JSONB，需傳給下一步的欄位 key（**尚未實作傳遞邏輯**） |

---

### 學習歷程

#### `step_attempts` — 學生作答嘗試

| 欄位 | 說明 |
|------|------|
| `user_id` / `module_id` / `step_id` / `case_id` | 外鍵 |
| `attempt_number` | INT，預設 1，每次提交遞增 |
| `user_input_content` | TEXT NOT NULL，學生作答原文 |
| `structured_data` | JSONB，結構化欄位（**尚未使用**） |
| `status` | `draft` / `submitted` / `passed` / `revision_required` |

#### `ai_evaluations` — AI 初評結果

**永不被教師判定覆蓋。**

| 欄位 | 說明 |
|------|------|
| `attempt_id` | → `step_attempts`（CASCADE） |
| `total_score` | INT，**加權後的百分制得分（0–100）** |
| `dimension_scores` | JSONB NOT NULL |
| `evidence_text` | TEXT ⚠️ 目前存的是學生作答前 200 字，非 AI 引用佐證（AI 佐證在 `dimension_scores[].evidence`）|
| `detected_errors` | JSONB，預設 `[]` |
| `feedback_text` | TEXT NOT NULL |
| `suggested_next_step` | TEXT（**未寫入**） |
| `ai_cost` | FLOAT，預設 0.0（**未寫入**） |

> 📌 此表**沒有 `teacher_review_id` 欄位**，這是刻意的：
> 「是否已複核」由 `step_attempts` 底下有沒有對應的 `teacher_reviews` 判斷，
> AI 初評不會被任何教師動作修改。

#### `teacher_reviews` — 教師最終判定

| 欄位 | 說明 |
|------|------|
| `attempt_id` | → `step_attempts`（CASCADE） |
| `teacher_id` | UUID **NOT NULL** → `profiles.id` |
| `decision` | **NOT NULL**，`accept_ai` / `modify` / `override` / `request_retry` |
| `final_score` | INT |
| `final_feedback` | TEXT |
| `is_published` | 是否已發布給學生看見 |
| `reviewed_at` | |

> ✅ `api/review.py` 已對齊此定義。`decision` 在 API 層先驗證，不合法回 400。

#### `prompt_logs` — 分層提示使用紀錄

| 欄位 | 說明 |
|------|------|
| `attempt_id` | → `step_attempts` |
| `hint_level` | INT NOT NULL（1 重新思考 / 2 方向 / 3 結構 / 4 局部範例） |
| `hint_content` | TEXT NOT NULL |
| `student_reaction` | TEXT（**未寫入**） |

---

### 表單與研究倫理

#### `forms` — 工具專屬表單

`tool_id`、`title`、`trigger_timing`（`pre_practice` / `post_practice` / `after_feedback` / `post_revision`）、`schema_json`、`version`。

🔴 目前無任何程式碼使用。

#### `form_responses` — 表單填答

`form_id`、`user_id`、`attempt_id`、`responses` JSONB。🔴 未使用。

#### `research_consents` — 研究倫理同意與匿名編號

| 欄位 | 說明 |
|------|------|
| `anonymous_research_id` | TEXT **UNIQUE**，格式 `SPED-xxxxxxxx` |
| `consent_version` | 如 `v1.0` |
| `is_consented` / `consented_at` / `withdrawn_at` | |

由觸發器自動建立，但**同意書流程本身尚未實作**。

---

## 4. 觸發器

```sql
CREATE FUNCTION public.handle_new_platform_user() ... SECURITY DEFINER;
CREATE TRIGGER on_auth_user_created AFTER INSERT ON auth.users FOR EACH ROW ...
```

每當 `auth.users` 新增一筆，自動：

1. 建立對應 `profiles`，`full_name` 與 `role` 取自 `raw_user_meta_data`（`role` 預設 `student`）
2. 產生一組 `SPED-` 開頭的匿名研究編號寫進 `research_consents`（`is_consented = false`）

---

## 5. 種子資料（`seed.sql`）

| 內容 | 說明 |
|------|------|
| 2 個帳號 | `prof.wu@platform.edu`（owner）、`student.test@platform.edu`（student），密碼皆為 `Test1234!` |
| 1 門課 | 「115學年度 學前特教IEP實務工作坊」，邀請碼 `IEP2026` |
| 1 個案例 | 「案例A：4歲中度語言發展遲緩幼兒『小明』」，`is_synthetic = true` |
| 2 支機器人 | 步驟1 案例資料整理教練、步驟2 功能性現況撰寫教練 |
| 1 個模組 | 學前 IEP 逐步撰寫模組 |
| 2 個步驟 | 對應上面兩支機器人，`pass_score = 70` |

**固定 UUID 對照：**

| UUID 前綴 | 指向 |
|-----------|------|
| `a0000001-…` | 機器人：步驟1 案例資料整理教練 |
| `a0000002-…` | 機器人：步驟2 功能性現況撰寫教練 |
| `b0000001-…` | 模組：學前 IEP 逐步撰寫模組 |
| `c1111111-…` | 課程：115學年度 學前特教IEP實務工作坊 |
| `ca5e0001-…` | 案例A：小明 |

> ⚠️ **只在本地／示範環境執行**，檔案裡有公開的測試帳號密碼。
>
> ⚠️ 若之前跑過舊版 `seed.sql`，UUID 已全部更換，請先清掉 `module_steps` /
> `learning_modules` / `ai_tools` / `tool_cases` 再重跑。`schema.sql` 不需要重跑。
>
> ⚠️ 密碼用 `crypt('Test1234!', gen_salt('bf'))` 寫入（需 `pgcrypto`，檔頭已建立）。
> 若 GoTrue 版本仍不接受，請改到 Dashboard → Authentication → Users 手動設定。

---

## 6. RLS 現況

🔴 **`schema.sql` 中沒有任何 `ENABLE ROW LEVEL SECURITY` 或 `CREATE POLICY`。**

這與 `README.md` 的資安規範「Supabase RLS 開啟，後端以 Service Role Key 統一操作」不符。目前前端以 anon key 直接讀寫 `profiles`、`module_steps`、`ai_tools`、`tool_cases`，任何持有 anon key 的人（anon key 本來就公開在前端 bundle）都能任意讀寫這些表。

**最低限度應補上：**

| 表 | 建議策略 |
|----|---------|
| `profiles` | 只能讀自己的 row；`role` 不可由前端更新 |
| `ai_tools` | 學生只能讀 `status='published'`；寫入僅限 `owner` / `assistant` |
| `module_steps` / `learning_modules` | 已加入該課程的成員可讀 |
| `tool_cases` | 同上 |
| `step_attempts` / `ai_evaluations` / `prompt_logs` | 學生只能讀自己的；寫入一律走後端 service role |
| `teacher_reviews` | 僅 `owner` / `assistant` |
| `research_consents` | 只能讀寫自己的 |

---

## 7. 如何初始化資料庫

1. Supabase Dashboard → SQL Editor
2. 貼上並執行 `supabase/schema.sql`
3. （可選）執行 `supabase/seed.sql` 建立示範資料
4. 用 `prof.wu@platform.edu` / `Test1234!` 試登入；失敗的話到
   Authentication → Users 手動設定密碼

詳細步驟見 [DEVELOPMENT.md](./DEVELOPMENT.md)。
