# 資料字典（Data Dictionary）

> 需求說明書 DAT-003（P0）：交付每個欄位的名稱、意義、格式、缺失值、計算方式、版本及資料表關係。
> 對應程式：`backend/api/export.py`（匯出）、`supabase/schema.sql` 與 `supabase/migrations/`（資料表）。
> 2026-11-24 建立；**新增或修改匯出欄位時，同一個 PR 必須更新這份文件**（`tests/test_data_dictionary.py` 會檢查每個匯出欄位都有寫在這裡）。

---

## 1. 匯出檔案總覽

| 項目 | 研究用（去識別化） | 教學用（可識別） |
|---|---|---|
| API | `POST /api/export/research` | `POST /api/export/teaching` |
| 誰可以匯出 | 只限教授（owner） | 只限教授（owner） |
| 包含哪些學生 | 這門課的學生（active，以及已被移出者先前的作答），**且研究同意 `is_consented = true`、未撤回** | 這門課的學生（active，以及已被移出者先前的作答） |
| 識別欄位 | `research_id`（匿名研究編號） | `student_name`、`student_email` |
| 作答原文 `answer_text` | 選填；**符合個資規則的片段換成 `[已遮罩]`** | 選填；原文 |
| 用途 | 研究分析、發表 | 教學管理；**不可用於研究發表** |
| 稽核 | 每次匯出寫一筆 `export_logs` | 同左 |

**共同規格**

- 一列 = **一次作答**（`step_attempts` 一筆）。聊天室關卡是「送出評分」那一次，作答內容是當時整理的整段對話。
- 編碼 UTF-8，開頭有 BOM（Excel 直接開中文不亂碼）；分隔符號為逗號，含逗號或換行的欄位以雙引號包住（RFC 4180）。
- 排序：依學生、再依作答時間（舊 → 新）。
- 篩選條件（請求 body）：`course_id`（必填）、`module_id`、`date_from`、`date_to`（以作答時間的 UTC 日期比較，含首尾）、`include_content`。
- 只匯出「指派給這門課的模組」裡的作答；老師與助理自己的試作不匯出。
- 回應標頭 `X-Export-Row-Count` = 資料列數。

> ⚠️ 研究同意書流程（PRI-004）尚未上線：在此之前所有學生的 `is_consented` 都是 false，研究用匯出會是空的（只有欄位名稱）。

---

## 2. 欄位定義

缺失值一律輸出為**空白**（CSV 空字串）。時間一律為 **UTC**，ISO 8601 格式（例如 `2026-11-24T07:21:00.123456+00:00`），換算台灣時間要加 8 小時。

### 2.1 識別欄位

| 欄位 | 出現在 | 意義 | 格式 | 缺失值 | 來源 |
|---|---|---|---|---|---|
| `research_id` | 研究用 | 匿名研究編號；同一位學生在所有匯出裡都相同 | 文字，`SPED-` + 8 碼英數（例：`SPED-3f2a9c1e`） | 不會缺（沒有編號的學生不匯出） | `research_consents.anonymous_research_id`（註冊時由觸發器自動產生） |
| `student_name` | 教學用 | 學生姓名 | 文字 | 學生沒填時空白 | `profiles.full_name` |
| `student_email` | 教學用 | 學生 Email | 文字 | — | `profiles.email` |

### 2.2 作答與關卡

| 欄位 | 意義 | 格式 | 缺失值 | 計算方式／來源 |
|---|---|---|---|---|
| `module_title` | 模組名稱 | 文字 | — | `learning_modules.title` |
| `step_order` | 關卡在模組裡的順序 | 整數，從 1 開始 | — | `module_steps.step_order`（老師調整順序後會改變，請以 `step_title` 輔助辨識） |
| `step_title` | 關卡名稱 | 文字 | — | `module_steps.step_title` |
| `tool_title` | 這一關使用的機器人；聊天室關卡是「評分機器人」 | 文字 | — | `ai_tools.title`（目前的名稱，不是作答當時的名稱） |
| `attempt_number` | 這位學生在這一關的第幾次作答 | 整數，從 1 開始 | — | `step_attempts.attempt_number` |
| `submitted_at` | 送出作答的時間 | UTC，ISO 8601 | — | `step_attempts.created_at` |
| `status` | 這次作答的結果 | `submitted` 已送出等待中（例如等老師複核）／`passed` 通過／`revision_required` 需要修改 | — | `step_attempts.status`；依關卡「怎麼算通過」決定（`module_steps.pass_mode`，見第 4 節） |
| `tutor_action` | AI 對這次作答採取的行動 | `evaluate` 評分／`encourage` 評分並鼓勵／`give_hint` 先給提示不評分／`escalate` 建議找老師不評分 | 機器人沒有評分規準、或聊天室評分時空白 | `step_attempts.tutor_action`（migration 003） |
| `answer_chars` | 作答字數 | 整數 | 0 | `user_input_content` 的字元數（不含 `answer_text` 是否匯出） |
| `answer_text` | 作答原文（勾選「含作答內容」才有這欄） | 文字 | — | 研究用：符合個資規則（電話、身分證、Email、地址…，與送出作答前的檢查同一組，`core/privacy.PII_PATTERNS`）的片段換成 `[已遮罩]`；教學用：原文 |

### 2.3 AI 初評（AI 原始結果，永遠不會被教師判定覆蓋）

| 欄位 | 意義 | 格式 | 缺失值 | 計算方式／來源 |
|---|---|---|---|---|
| `ai_total_score` | AI 初評總分 | 整數 0–100 | 沒有評分時空白（`give_hint`、`escalate`、機器人沒有評分規準） | 加權百分制：Σ（構面得分 ÷ 該構面滿分 × 權重）÷ Σ權重 × 100，四捨五入。權重 = 評分規準的「配分」`rubric_criteria[].max_score`；所有構面都沒配分時等權重 |
| `ai_confidence` | AI 對這次評分的把握程度 | 小數 0–1 | 同上 | AI 自評；**< 0.7 會被標成需要教師複核**（`ai_evaluations.needs_teacher_review`） |
| `ai_dimensions` | 逐構面得分 | `構面名稱:得分/滿分`，多個構面以 `;` 分隔（例：`客觀事實辨識:3/4;推論與假設區分:2/4`） | 同上 | 滿分依量表：`4_point` = 4、`5_point` = 5、`100_point` = 100、`pass_fail` = 1（`ai_tools.scale_type`）；評分失敗的構面給最低分 |
| `detected_errors` | AI 偵測到的錯誤類型代碼 | 代碼以 `;` 分隔（例：`diagnosis_only;vague_goal`） | 機器人沒有定義錯誤分類、或沒偵測到時空白 | `ai_evaluations.detected_errors[].code`；代碼的定義在該機器人的 `ai_tools.error_taxonomy` |

### 2.4 分層提示

| 欄位 | 意義 | 格式 | 缺失值 | 計算方式／來源 |
|---|---|---|---|---|
| `hints_used` | 這次作答取得的提示則數 | 整數 | 0 | `prompt_logs` 筆數 |
| `hint_levels` | 取得的提示層級 | 層級以 `;` 分隔，由淺到深（例：`1;2`） | 沒有時空白 | `prompt_logs.hint_level`；是自動給的還是學生主動要的記在 `prompt_logs.hint_trigger`（目前未匯出） |

### 2.5 教師最終判定（與 AI 初評分開存放）

| 欄位 | 意義 | 格式 | 缺失值 | 計算方式／來源 |
|---|---|---|---|---|
| `teacher_decision` | 教師判定 | `accept_ai` 採用 AI 評分／`modify` 調整分數／`override` 推翻 AI 判定／`request_retry` 要求重做 | 還沒複核時空白 | `teacher_reviews.decision`；同一次作答有多筆判定時取資料庫回傳的最後一筆（通常是最新一筆，見第 5 節） |
| `teacher_final_score` | 教師最終分數 | 整數 0–100（與 `ai_total_score` 同一個尺度） | 老師沒給分或還沒複核時空白 | `teacher_reviews.final_score` |
| `teacher_published` | 教師判定是否已發布給學生 | `True`／`False` | 還沒複核時空白 | `teacher_reviews.is_published` |

**「是否通過」的判斷規則**（解鎖下一關、學習監控都用這套，`core/step_unlock.attempt_passed`）：有「已發布且有給分」的教師判定時，以教師分數 ≥ 關卡通過分數為準；否則看 `status = passed`。

---

## 3. 資料表關係

```mermaid
erDiagram
    profiles ||--o{ course_members : "加入"
    courses ||--o{ course_members : "成員"
    courses ||--o{ course_invite_codes : "邀請碼"
    courses ||--o{ course_modules : "指派"
    learning_modules ||--o{ course_modules : "被指派"
    learning_modules ||--o{ module_steps : "關卡"
    ai_tools ||--o{ module_steps : "評分／作答機器人"
    ai_tools ||--o{ ai_tool_versions : "舊版本"
    module_steps ||--o{ chat_room_bots : "聊天室對話機器人"
    profiles ||--o{ step_attempts : "作答"
    module_steps ||--o{ step_attempts : "作答"
    step_attempts ||--o| ai_evaluations : "AI 初評"
    step_attempts ||--o{ teacher_reviews : "教師判定"
    step_attempts ||--o{ prompt_logs : "提示"
    profiles ||--o{ chat_messages : "聊天"
    module_steps ||--o{ chat_messages : "聊天"
    profiles ||--o{ learner_profiles : "學習者側寫"
    profiles ||--|| research_consents : "研究同意與匿名編號"
    courses ||--o{ course_materials : "教材"
    course_materials ||--o{ material_chunks : "切段"
```

| 資料表 | 一列代表 | 研究上的用途 | 加入的 migration |
|---|---|---|---|
| `profiles` | 一個帳號 | 角色（student／owner／assistant） | schema |
| `research_consents` | 一位使用者的研究同意與匿名編號 | `research_id`、是否可匯出 | schema |
| `courses`、`course_members`、`course_invite_codes` | 課程、成員、邀請碼 | 課程模式、加入方式（免費額度或自備金鑰） | schema、013 |
| `learning_modules`、`module_steps` | 模組、關卡 | 關卡順序、通過分數、必修、通過方式、關卡類型 | schema、010、019、020 |
| `ai_tools`、`ai_tool_versions` | 機器人（目前版本）、修改前的舊版本 | 評分規準、量表、錯誤分類；`ai_evaluations.tool_version` 對應評分當時的版本 | schema、022 |
| `step_attempts` | 一次作答（聊天室是一次送出評分） | **匯出的主體** | schema、003、009 |
| `ai_evaluations` | 一次 AI 初評 | 分數、構面、信心值、錯誤、當時參考的記憶（`memory_snapshot`）、機器人版本 | schema、001、015、022 |
| `teacher_reviews` | 一次教師判定 | 最終判定，永不被 AI 覆蓋 | schema |
| `prompt_logs` | 一則提示 | 提示層級、觸發方式、來源 | schema、001 |
| `chat_room_bots`、`chat_messages` | 聊天室的機器人設定、一則聊天訊息 | 誰說的、哪個機器人回答、調度理由、助教引用與是否直接給答案 | 020、021 |
| `learner_profiles` | 學習者側寫的一個版本（只新增不覆蓋） | 錯誤習慣、構面掌握度隨時間的變化 | 015 |
| `course_materials`、`material_chunks` | 課程教材、教材切段 | 學習助教的參考資料 | 021 |
| `export_logs` | 一次匯出 | 稽核：誰、何時、什麼條件、幾筆 | 018 |
| `user_api_keys` | 學生自備的 Gemini 金鑰（加密） | 不匯出 | 017 |

---

## 4. 代碼值對照

| 欄位 | 值 | 意思 |
|---|---|---|
| `module_steps.pass_mode` | `score` | 分數達到通過分數才通過（預設） |
| | `complete` | 送出作答即通過；機器人沒有評分規準時 `score` 也當作 `complete` |
| | `teacher_review` | 老師複核後才通過；AI 結果只供參考 |
| `module_steps.step_type` | `answer` | 作答關卡：寫完一份作答再送出 |
| | `chat` | 聊天室：多機器人對話，評分機器人評整段對話 |
| `chat_messages.routing.by` | `mention` | 學生用 @ 指定機器人 |
| | `router` | 調度員依內容選的 |
| | `only_bot` | 聊天室只有一個對話機器人 |
| | `evaluate` | 評分機器人的評分結果 |
| `chat_messages.routing.answer_mode` | `guide` | 學習助教只引導、不給答案 |
| | `answer` | 同一個問題問到設定次數，助教直接給答案 |
| `prompt_logs.hint_trigger` | `score_below_threshold`／`student_request` | 分數不夠自動給／學生主動要求 |
| `prompt_logs.hint_source` | `teacher`／`generated` | 老師撰寫／AI 依評分結果產生 |

---

## 5. 目前還沒匯出的資料

以下資料有存在資料庫，但還沒進匯出檔（之後加入時更新本文件）：

- 聊天室逐則訊息、調度理由、學習助教的引用與 `answer_mode`（#83）
- 評分當時的機器人版本 `ai_evaluations.tool_version`、AI 當時參考的記憶 `memory_snapshot`
- 關卡的通過方式 `pass_mode`、關卡類型 `step_type`
- 提示的觸發方式與來源 `hint_trigger`、`hint_source`
- 學習者側寫 `learner_profiles`
- 同一次作答有多筆教師判定時，目前只匯出一筆；之後應明確依 `reviewed_at` 取最新並加入判定時間欄位
- 工具專屬表單與質性反思（#80，功能尚未完成）
- AI 用量與費用（#81，功能尚未完成）
