# API 規格書 (API Specification)

> AI 專業能力培訓平台｜Backend API｜v1.0
> 
> Base URL（本地開發）：`http://localhost:8000`
> Base URL（正式環境）：`https://your-api.render.com`

所有需要登入的 API 均透過 **HttpOnly Cookie** 進行身份驗證，前端不需在 Header 手動帶 Token。前端呼叫時需加上 `credentials: 'include'`。

---

## 通用規範

### 回應格式

所有 API 均回傳 JSON，結構如下：

**成功：**
```json
{
  "data": { ... },
  "message": "success"
}
```

**失敗：**
```json
{
  "error": {
    "code": "UNAUTHORIZED",
    "message": "請先登入"
  }
}
```

### HTTP 狀態碼

| 狀態碼 | 說明 |
|--------|------|
| `200` | 成功 |
| `201` | 建立成功 |
| `400` | 請求格式錯誤 |
| `401` | 未登入或 Token 失效 |
| `403` | 權限不足 |
| `404` | 資源不存在 |
| `429` | 請求過於頻繁（觸發速率限制） |
| `500` | 伺服器錯誤 |

### 角色權限縮寫

| 縮寫 | 說明 |
|------|------|
| `HOST` | 平台主持人（教授） |
| `ASSISTANT` | 助理（由主持人授權） |
| `STUDENT` | 學習者 |
| `PUBLIC` | 未登入的訪客 |

---

## 一、身份驗證 (Auth)

### `POST /api/auth/register`
**權限**：PUBLIC

註冊新帳號。完成後系統寄送驗證信，未驗證前無法使用 AI 工具。

**Request Body：**
```json
{
  "display_name": "王小明",
  "email": "student@example.com",
  "password": "SecurePassword123!",
  "identity_type": "student",
  "agree_terms": true,
  "agree_ai_usage": true
}
```

**Response 201：**
```json
{
  "data": {
    "user_id": "uuid",
    "email": "student@example.com",
    "email_verified": false
  },
  "message": "驗證信已寄出，請至信箱確認"
}
```

**400 Bad Request（欄位遺漏或未勾選條款）：**
```json
{
  "error": {
    "code": "INVALID_INPUT",
    "message": "請確認必填欄位皆已填寫，並完整勾選服務條款與個資保護承諾"
  }
}
```

**400 Bad Request（密碼強度不足）：**
```json
{
  "error": {
    "code": "WEAK_PASSWORD",
    "message": "密碼強度不足，長度需至少 8 碼且包含英數混合"
  }
}
```

**409 Conflict（信箱已被註冊）：**
```json
{
  "error": {
    "code": "EMAIL_ALREADY_EXISTS",
    "message": "該電子信箱已被註冊，請直接登入或使用其他信箱"
  }
}
```
---

### `POST /api/auth/login`
**權限**：PUBLIC

登入並取得 HttpOnly Cookie。

**Request Body：**
```json
{
  "email": "student@example.com",
  "password": "SecurePassword123!"
}
```

**Response 200：**
```json
{
  "data": {
    "user_id": "uuid",
    "display_name": "王小明",
    "role": "student",
    "email_verified": true
  }
}
```
> Set-Cookie: `access_token=...; HttpOnly; Secure; SameSite=Lax`

**401 Unauthorized（帳號或密碼錯誤）[cite: 1, 2]：**
```json
{
  "error": {
    "code": "INVALID_CREDENTIALS",
    "message": "電子信箱或密碼錯誤，請重新確認"
  }
}
```

**403 Forbidden（信箱未驗證）：**
```json
{
  "error": {
    "code": "EMAIL_NOT_VERIFIED",
    "message": "帳號尚未完成信箱驗證，請至註冊信箱收取驗證信"
  }
}
```

**403 Forbidden（帳號已停用）：**
```json
{
  "error": {
    "code": "ACCOUNT_SUSPENDED",
    "message": "此帳號因違反使用規範已暫時停用，請聯繫平台管理員"
  }
}
```
---

### `POST /api/auth/logout`
**權限**：任何已登入者

清除登入 Cookie。

**Response 200：**
```json
{ "message": "已登出" }
```

---

### `GET /api/auth/me`
**權限**：任何已登入者

取得當前登入者資訊。

**Response 200：**
```json
{
  "data": {
    "user_id": "uuid",
    "display_name": "王小明",
    "email": "student@example.com",
    "role": "student",
    "email_verified": true,
    "created_at": "2026-08-01T00:00:00Z"
  }
}
```

**401 Unauthorized（憑證過期）：**
```json
{
  "error": {
    "code": "TOKEN_EXPIRED",
    "message": "登入憑證已過期，請重新登入"
  }
}
```

**401 Unauthorized（無效或損毀的憑證）：**
```json
{
  "error": {
    "code": "INVALID_TOKEN",
    "message": "身分驗證失敗，請重新登入"
  }
}
```
---

### `POST /api/auth/forgot-password`
**權限**：PUBLIC

寄送密碼重設信。

**Request Body：**
```json
{ "email": "student@example.com" }
```

**429 Too Many Requests（重設信發送頻率過高）：**
```json
{
  "error": {
    "code": "RATE_LIMIT_EXCEEDED",
    "message": "重設密碼信件發送過於頻繁，請於 60 秒後再試"
  }
}
```
---

## 二、帳號管理 (Users)

### `GET /api/users`
**權限**：HOST, ASSISTANT

取得所有使用者列表（可依課程、角色篩選）。

**Query Parameters：**
- `course_id` (選填)：篩選特定課程的學生
- `role` (選填)：`student` | `assistant` | `host`
- `page`, `limit`：分頁

**403 Forbidden（非主持人或助理存取）：**
```json
{
  "error": {
    "code": "PERMISSION_DENIED",
    "message": "權限不足，僅有課程主持人與助理可查看使用者清單"
  }
}
```
---

### `PATCH /api/users/:user_id/status`
**權限**：HOST

更新帳號狀態。

**Request Body：**
```json
{
  "status": "suspended",
  "reason": "違反使用規範"
}
```
> `status` 可為：`active` | `suspended` | `archived`

**404 Not Found（找不到該使用者）：**
```json
{
  "error": {
    "code": "USER_NOT_FOUND",
    "message": "指定的使用者不存在"
  }
}
```

**400 Bad Request（無法停用自己（防呆：避免主持人不小心把自己的帳號停權））：**
```json
{
  "error": {
    "code": "CANNOT_SUSPEND_SELF",
    "message": "無法變更當前登入者自身的帳號狀態"
  }
}
```

**403 Forbidden（權限不足以修改該角色（例如助理不能去停用主持人的帳號））：**
```json
{
  "error": {
    "code": "INSUFFICIENT_PRIVILEGE",
    "message": "您的權限不足以修改此層級之使用者狀態"
  }
}
```
---

## 三、課程管理 (Courses)

### `POST /api/courses`
**權限**：HOST

建立新課程並產生邀請碼。

**Request Body：**
```json
{
  "name": "學前特殊教育實習課程",
  "description": "IEP 撰寫能力培訓",
  "start_date": "2026-09-01",
  "end_date": "2027-01-31"
}
```

**Response 201：**
```json
{
  "data": {
    "course_id": "uuid",
    "name": "學前特殊教育實習課程"
  },
  "message": "課程建立成功"
}
```

---

### `POST /api/courses/:course_id/invite-codes`
**權限**：HOST

為課程發行不同方案的邀請碼（區分「教師補助免費 Token」與「自費使用」）。

**Request Body：**
```json
{
  "code": "IEP-2026-GRANT", // 代碼名稱（或由後端自動產生）
  "plan_type": "sponsored", // "sponsored"（公費補助/老師提供） | "self_paid"（自費）
  "token_quota": 50000, // 若為 sponsored，指定免費 Token 額度上限（self_paid 則為 0 或 null）
  "max_uses": 35,
  "valid_until": "2026-10-31T23:59:59Z"
}
```

**Response 201：**
```json
{
  "data": {
    "invite_code_id": "uuid",
    "course_id": "uuid",
    "code": "IEP-2026-GRANT",
    "plan_type": "sponsored",
    "token_quota": 50000,
    "max_uses": 35,
    "current_uses": 0,
    "is_active": true
  },
  "message": "成功建立邀請碼"
}
```
---

### `GET /api/courses/:course_id/invite-codes`
**權限**：HOST

取得該課程目前已發行的所有邀請碼清單與名額使用狀況。

**Response 200：**
```json
{
  "data": [
    {
      "invite_code_id": "uuid",
      "code": "IEP-2026-GRANT",
      "plan_type": "sponsored",
      "token_quota": 50000,
      "max_uses": 35,
      "current_uses": 28,
      "is_active": true,
      "valid_until": "2026-10-31T23:59:59Z"
    },
    {
      "invite_code_id": "uuid",
      "code": "IEP-2026-PAID",
      "plan_type": "self_paid",
      "token_quota": 0,
      "max_uses": 100,
      "current_uses": 12,
      "is_active": true,
      "valid_until": "2027-01-31T23:59:59Z"
    }
  ]
}
```
---

### `POST /api/courses/join`
**權限**：STUDENT

使用特定邀請碼加入課程。系統將依據邀請碼自動綁定學生的計費方案（公費補助或自費）。

**Request Body：**
```json
{ "invite_code": "IEP-2026-GRANT" }
```

**Response 200：**
```json
{
  "data": {
    "course_id": "uuid",
    "course_name": "學前特殊教育實習課程",
    "membership": {
      "plan_type": "sponsored", // "sponsored"（公費補助） | "self_paid"（自費）
      "token_quota": 50000, // 總配發額度
      "remaining_tokens": 50000, // 當前剩餘額度
      "is_quota_exhausted": false // 是否已耗盡
    },
    "joined_at": "2026-09-15T08:30:00Z"
  },
  "message": "成功以【公費補助方案】加入課程！"
}
```

**400 Bad Request（格式錯誤）：**
```json
{
  "error": {
    "code": "INVALID_FORMAT",
    "message": "課程邀請碼格式不正確，請確認後重新輸入"
  }
}
```

**403 Forbidden（公費名額已滿）：**
```json
{
  "error": {
    "code": "CODE_MAX_USES_REACHED",
    "message": "該公費補助名額已額滿，請聯繫指導教授或改用自費代碼加入"
  }
}
```

**403 Forbidden（代碼已過期）：**
```json
{
  "error": {
    "code": "CODE_EXPIRED",
    "message": "該課程邀請碼已超過有效使用期限"
  }
}
```

**404 Not Found（找不到代碼）：**
```json
{
  "error": {
    "code": "INVALID_INVITE_CODE",
    "message": "查無此課程代碼，請確認代碼是否輸入正確"
  }
}
```

**409 Conflict（重複加入）：**
```json
{
  "error": {
    "code": "ALREADY_JOINED",
    "message": "您已加入過此課程，系統將為您直接導向學習工作台"
  }
}
```
---

### `GET /api/courses/:course_id/my-quota`
**權限**：STUDENT

查詢學生在該課程內的 Token 配額與計費方案狀態。

**Response 200：**
```json
{
  "data": {
    "course_id": "uuid",
    "plan_type": "sponsored",
    "token_quota": 50000,
    "remaining_tokens": 34200,
    "can_practice": true
  }
}
```
---

### `GET /api/courses/:course_id/students`
**權限**：HOST, ASSISTANT

取得課程學生名單。

**Response 200：**
```json
{
  "data": [
    {
      "user_id": "uuid",
      "display_name": "王小明",
      "email": "student01@example.com",
      "plan_type": "sponsored", // "sponsored" | "self_paid"
      "token_quota": 50000,
      "used_tokens": 15800,
      "remaining_tokens": 34200,
      "joined_at": "2026-09-15T08:30:00Z"
    }
  ]
}
```

---

### `GET /api/courses/:course_id/modules`
**權限**：HOST, ASSISTANT, STUDENT

取得課程的學習模組（步驟）列表及每個學生的完成狀態。

**Response 200：**
```json
{
  "data": {
    "course_id": "uuid",
    "modules": [
      {
        "module_id": "uuid",
        "order": 1,
        "title": "步驟一：現況能力描述",
        "is_required": true,
        "unlock_condition": null,
        "pass_threshold": 75,
        "student_status": "completed"
      },
      {
        "module_id": "uuid",
        "order": 2,
        "title": "步驟二：年度目標設定",
        "is_required": true,
        "unlock_condition": { "module_id": "...", "min_score": 75 },
        "pass_threshold": 75,
        "student_status": "locked"
      }
    ]
  }
}
```

---

## 四、AI 工具管理 (Tools)

### `GET /api/tools`
**權限**：HOST, ASSISTANT, STUDENT

取得可用 AI 工具列表。學生只能看到有權限使用的工具。

---

### `POST /api/tools`
**權限**：HOST

建立新 AI 工具。

**Request Body：**
```json
{
  "name": "IEP 現況描述練習機器人",
  "description": "協助學生練習撰寫學前 IEP 的現況能力描述",
  "target_audience": "師培生、實習教師",
  "teaching_mode": "complete_then_feedback",
  "system_prompt": "你是一位學前特殊教育的教學助理...",
  "opening_message": "歡迎！今天我們來練習撰寫現況描述...",
  "daily_usage_limit": 5,
  "estimated_minutes": 30
}
```
> `teaching_mode`：`guided`（過程引導）| `complete_then_feedback`（作答後回饋）| `scenario`（情境互動）| `mixed`

---

### `PUT /api/tools/:tool_id`
**權限**：HOST

更新 AI 工具設定。若該工具已有學生作答，系統自動建立新版本，舊版本資料保留。

---

### `POST /api/tools/:tool_id/rubric`
**權限**：HOST

建立或更新工具的評分規準（Rubric）。

**Request Body：**
```json
{
  "scale_type": "4_point",
  "pass_threshold_percent": 75,
  "dimensions": [
    {
      "name": "功能性描述",
      "weight": 30,
      "levels": [
        { "score": 4, "description": "完整描述學生的優勢與需求，有具體行為觀察" },
        { "score": 3, "description": "描述大致完整，但缺乏部分細節" },
        { "score": 2, "description": "描述籠統，缺乏行為觀察" },
        { "score": 1, "description": "未描述或僅列障礙類別" }
      ]
    },
    {
      "name": "去標籤化用語",
      "weight": 20,
      "levels": [...]
    }
  ]
}
```
> `scale_type`：`4_point` | `5_point` | `100_point` | `pass_fail`

---

### `POST /api/tools/:tool_id/cases`
**權限**：HOST

新增練習案例（虛構個案情境）。

**Request Body：**
```json
{
  "title": "個案：小明",
  "content": "小明，5歲2個月，診斷為自閉症類群障礙...",
  "task_description": "根據上方個案資料，請撰寫小明的現況能力描述。",
  "order": 1
}
```

---

### `POST /api/tools/:tool_id/hints`
**權限**：HOST

設定分層提示內容。

**Request Body：**
```json
{
  "hints": [
    {
      "level": 1,
      "trigger": "score_below_threshold",
      "content": "試想看看：你在哪一個具體情境下，觀察到小明的哪個行為？"
    },
    {
      "level": 2,
      "trigger": "student_request",
      "content": "功能性描述通常包含：情境（在哪裡）、行為（做什麼）、頻率（多常）"
    },
    {
      "level": 3,
      "trigger": "student_request",
      "content": "例如：在自由遊戲時間，小明能夠獨立完成拼圖（10片）...",
      "is_example": true,
      "warn_copy": true
    }
  ]
}
```

---

### `POST /api/tools/:tool_id/preview`
**權限**：HOST

以「測試學生」身份試用工具，結果不計入正式紀錄。

---

## 五、練習與 AI 評分 (Practice)

### `POST /api/practice/sessions`
**權限**：STUDENT

建立新的練習 Session（開始一次練習）。

**Request Body：**
```json
{
  "tool_id": "uuid",
  "case_id": "uuid",
  "course_id": "uuid"
}
```

**Response 201：**
```json
{
  "data": {
    "session_id": "uuid",
    "tool": { "name": "...", "opening_message": "..." },
    "case": { "title": "個案：小明", "content": "..." },
    "task_description": "根據上方個案資料...",
    "started_at": "2026-08-30T13:00:00Z"
  }
}
```

---

### `POST /api/practice/sessions/:session_id/submit`
**權限**：STUDENT

提交作答，觸發 AI 評分。回傳使用 **SSE（Server-Sent Events）** 串流。

**Request Body：**
```json
{
  "content": "小明目前在語言方面...",
  "version_note": "第一次作答"
}
```

**Response**：`Content-Type: text/event-stream`

```
event: score_start
data: {"session_id": "uuid", "attempt_id": "uuid", "attempt_number": 1}

event: tutor_decision
data: {"action": "evaluate", "attempt_number": 1, "last_score_percent": null}
  → action 可為："evaluate" | "give_hint" | "encourage" | "escalate"

（若 action="evaluate" 或 "encourage"，繼續以下事件）
event: analysis_start
data: {"total_dimensions": 2}

event: dimension_score
data: {"dimension": "功能性描述", "score": 3, "max_score": 4, "reason": "...", "evidence": "...", "progress": "1/2"}

event: score_complete
data: {"total_score": 6, "max_total_score": 8, "percentage": 75.0, "passed": true, "needs_teacher_review": false, "overall_feedback": "...", "confidence": 0.9, "dimension_scores": [...]}

（若 action="give_hint"）
event: hint
data: {"level": 1, "content": "...", "message": "先依照提示修改後，再重新提交作答。"}

（若 action="encourage"）
event: encouragement
data: {"message": "你已經非常接近通過標準了！..."}
接著繼續 analysis_start → dimension_score × N → score_complete

（若 action="escalate"）
event: escalation
data: {"message": "你已嘗試 4 次，建議與老師討論後再繼續練習。", "suggest_teacher_review": true}
```

> **注意**：AI 評分結果（`ai_evaluations` 表）與教師最終判定（`teacher_evaluations` 表）**分開儲存，互不覆蓋**。

---

### `GET /api/practice/sessions/:session_id/hints`
**權限**：STUDENT

取得分層提示（依當前分數與已使用層級決定回傳內容）。

**Response 200：**
```json
{
  "data": {
    "available_level": 2,
    "hint": {
      "level": 2,
      "content": "功能性描述通常包含：情境、行為、頻率..."
    },
    "next_level_available": true,
    "next_level_requires": "student_request"
  }
}
```

---

### `GET /api/practice/sessions/:session_id/history`
**權限**：STUDENT, HOST, ASSISTANT

取得本次練習的所有提交歷程（包含每次提交內容與對應評分）。

---

### `POST /api/practice/modules/:module_id/coherence-check`
**權限**：STUDENT

當學生完成模組所有步驟後呼叫，跨步驟檢查 IEP 前後一致性。回傳 SSE 串流。

**Query Parameters：**
- `user_id`（必填）

**Response**：`Content-Type: text/event-stream`

```
event: coherence_start
data: {"total_checks": 3, "total_steps": 7}

event: coherence_check
data: {"check_name": "優勢與目標對應", "is_ok": false, "problem": "步驟1描述的口語優勢未在步驟3目標中呈現", "suggestion": "修改步驟3，將優勢納入目標設計", "go_to_step_order": 3, "progress": "1/3"}

event: coherence_complete
data: {"overall_coherent": false, "passed_checks": 2, "failed_checks": 1, "strengths": ["需求與策略對應"], "issues": [{"check_name": "...", "problem": "...", "suggestion": "...", "go_to_step_order": 3}], "summary": "發現 1 處跨步驟不一致，建議修改步驟 3。"}
```

---

## 六、教師複核 (Review)

### `GET /api/review/pending`
**權限**：HOST, ASSISTANT

取得待複核清單（AI 已評分、尚未教師判定的作答）。

**Query Parameters：**
- `course_id`（選填）
- `tool_id`（選填）
- `needs_attention`（選填）：`true` 只顯示 AI 信心值低或分數極端的項目

**Response 200：**
```json
{
  "data": [
    {
      "submission_id": "uuid",
      "student": { "display_name": "王小明" },
      "tool_name": "IEP 現況描述練習機器人",
      "submitted_at": "2026-08-30T13:00:00Z",
      "ai_total_percentage": 25.0,
      "ai_confidence": 0.62,
      "flags": ["low_confidence", "possible_pii"]
    }
  ]
}
```

---

### `GET /api/review/submissions/:submission_id`
**權限**：HOST, ASSISTANT

取得單筆作答的詳細資訊（含學生原文、AI 評分各構面、分層提示使用紀錄）。

---

### `POST /api/review/submissions/:submission_id/judge`
**權限**：HOST, ASSISTANT

教師最終判定（接受、修改或否決 AI 評分）。

**Request Body：**
```json
{
  "action": "modify",
  "dimension_overrides": [
    {
      "dimension": "功能性描述",
      "teacher_score": 3,
      "override_reason": "學生在第2行有提到具體情境，AI 誤判"
    }
  ],
  "teacher_comment": "整體方向正確，建議加強去標籤化用語的意識。",
  "require_redo": false
}
```
> `action`：`accept`（接受 AI 評分）| `modify`（修改部分構面）| `reject`（否決，重新評分）

---

### `GET /api/review/dashboard`
**權限**：HOST, ASSISTANT

教師儀表板摘要資料。

**Response 200：**
```json
{
  "data": {
    "pending_reviews": 12,
    "incomplete_tasks": 8,
    "recent_completions": 5,
    "common_errors": [
      { "dimension": "去標籤化用語", "error_rate": 0.68, "suggestion": "建議重講優勢本位語言..." }
    ],
    "monthly_ai_cost_usd": 2.34,
    "ai_service_status": "normal"
  }
}
```

---

## 七、資料匯出 (Export)

### `POST /api/export/teaching`
**權限**：HOST

匯出可識別教學資料（含學生姓名，供教學管理使用）。

**Request Body：**
```json
{
  "course_id": "uuid",
  "tool_id": "uuid",
  "date_from": "2026-09-01",
  "date_to": "2026-12-31",
  "format": "csv"
}
```

**Response**：直接回傳 CSV 檔案下載。

---

### `POST /api/export/research`
**權限**：HOST

匯出去識別化研究資料（自動將姓名、Email 替換為匿名研究編號）。

**Request Body：**
```json
{
  "course_id": "uuid",
  "consent_status": "agreed",
  "include_ai_scores": true,
  "include_teacher_scores": true,
  "include_hint_usage": true,
  "format": "csv"
}
```

> 此操作會記錄匯出紀錄（誰、何時、匯出哪些資料）於稽核日誌中。

---

## 八、個資與隱私 (Privacy)

### `POST /api/privacy/detect`
**權限**：STUDENT（在提交前呼叫）

偵測文字中是否含有疑似個人識別資訊。

**Request Body：**
```json
{ "content": "我實習的學校有個學生叫王小明，電話 0912..." }
```

**Response 200：**
```json
{
  "data": {
    "has_pii_risk": true,
    "detected_types": ["phone_number", "real_name_pattern"],
    "warning": "您的作答可能包含個人識別資訊，請確認是否為虛構內容再提交。"
  }
}
```

---

### `POST /api/privacy/report`
**權限**：任何已登入者

通報疑似個資外洩事件。

---

## 九、同意紀錄 (Consent)

### `POST /api/consent`
**權限**：任何已登入者

記錄使用者的同意狀態（支援三層同意：平台服務條款、AI 使用告知、研究參與同意）。

**Request Body：**
```json
{
  "consent_type": "research",
  "agreed": true,
  "consent_version": "v1.0-2026-08"
}
```
> `consent_type`：`platform_terms` | `ai_usage` | `research`

---

### `DELETE /api/consent/research`
**權限**：任何已登入者

撤回研究參與同意（不影響課程使用，後續資料不再納入研究匯出）。

---

## 十、教授後台監控：學生對答狀況 (Admin Monitoring)

> 📌 **開會新增**：教授後台需能完整查看所有學生與各支機器人的對話紀錄、AI 評分結果與學習歷程，以支持教學決策。

### `GET /api/admin/conversations`
**權限**：HOST, ASSISTANT

取得課程內所有學生與機器人的對答紀錄列表（支援多維度篩選）。

**Query Parameters：**
- `course_id`（必填）：課程 ID
- `tool_id`（選填）：篩選特定機器人
- `student_id`（選填）：篩選特定學生
- `date_from`, `date_to`（選填）：日期範圍
- `status`（選填）：`completed` | `in_progress` | `pending_review`
- `page`, `limit`：分頁（預設 limit=20）

**Response 200：**
```json
{
  "data": {
    "total": 142,
    "items": [
      {
        "session_id": "uuid",
        "student": {
          "user_id": "uuid",
          "display_name": "王小明"
        },
        "tool": {
          "tool_id": "uuid",
          "name": "現況描述練習家教"
        },
        "started_at": "2026-09-01T09:00:00Z",
        "last_activity_at": "2026-09-01T09:42:00Z",
        "submission_count": 3,
        "latest_ai_score_percent": 81.2,
        "hints_used": 2,
        "status": "pending_review",
        "has_pii_flag": false
      }
    ]
  }
}
```

---

### `GET /api/admin/conversations/:session_id`
**權限**：HOST, ASSISTANT

查看單一學生與機器人的完整對答詳情，包含所有提交版本、AI 評分逐構面結果、分層提示使用紀錄與時間戳。

**Response 200：**
```json
{
  "data": {
    "session_id": "uuid",
    "student": { "display_name": "王小明" },
    "tool": { "name": "現況描述練習家教" },
    "case_title": "個案：小明",
    "started_at": "2026-09-01T09:00:00Z",
    "submissions": [
      {
        "submission_id": "uuid",
        "version": 1,
        "content": "小明，5歲，診斷為自閉症...",
        "submitted_at": "2026-09-01T09:15:00Z",
        "time_spent_seconds": 842,
        "ai_evaluation": {
          "total_percent": 25.0,
          "confidence": 0.62,
          "dimensions": [
            {
              "name": "功能性描述",
              "score": 1,
              "max_score": 4,
              "reason": "僅列診斷名稱，無功能性描述",
              "evidence": "「診斷為自閉症」"
            },
            {
              "name": "去標籤化用語",
              "score": 1,
              "max_score": 4,
              "reason": "使用否定句描述缺陷",
              "evidence": "「無法與同伴互動」"
            }
          ]
        },
        "hints_requested": [
          { "level": 1, "requested_at": "2026-09-01T09:20:00Z" }
        ]
      },
      {
        "submission_id": "uuid",
        "version": 2,
        "content": "小明在自由遊戲時能夠...",
        "submitted_at": "2026-09-01T09:38:00Z",
        "time_spent_seconds": 1380,
        "ai_evaluation": {
          "total_percent": 81.2,
          "confidence": 0.89,
          "dimensions": [...]
        },
        "hints_requested": []
      }
    ],
    "teacher_evaluation": null,
    "overall_status": "pending_review"
  }
}
```

---

### `GET /api/admin/tools/:tool_id/analytics`
**權限**：HOST, ASSISTANT

查看特定機器人的整體學習分析（班級層級統計）。

**Response 200：**
```json
{
  "data": {
    "tool_id": "uuid",
    "tool_name": "現況描述練習家教",
    "total_students": 28,
    "completed_students": 19,
    "avg_submissions_to_pass": 2.4,
    "avg_score_first_attempt": 38.5,
    "avg_score_final_attempt": 79.2,
    "avg_time_minutes": 42.1,
    "hint_usage_rate": 0.67,
    "dimension_breakdown": [
      {
        "dimension": "功能性描述",
        "avg_score": 2.8,
        "max_score": 4,
        "low_score_count": 8,
        "common_error": "停留在診斷標籤層面，缺乏行為觀察"
      },
      {
        "dimension": "去標籤化用語",
        "avg_score": 1.9,
        "max_score": 4,
        "low_score_count": 15,
        "common_error": "使用否定句描述缺陷行為"
      }
    ],
    "students_needing_attention": [
      {
        "user_id": "uuid",
        "display_name": "林小華",
        "attempts": 6,
        "latest_score_percent": 42.0,
        "suggestion": "建議教師個別輔導"
      }
    ]
  }
}
```

---

### `GET /api/admin/students/:student_id/profile`
**權限**：HOST, ASSISTANT

查看特定學生的跨機器人整體學習畫像（在當前課程中使用所有機器人的情況）。

**Response 200：**
```json
{
  "data": {
    "student": { "display_name": "王小明", "joined_at": "2026-09-01T00:00:00Z" },
    "course_progress": {
      "completed_tools": 3,
      "total_tools": 10,
      "overall_completion_percent": 30.0
    },
    "tools_summary": [
      {
        "tool_name": "現況描述練習家教",
        "status": "passed",
        "final_score_percent": 81.2,
        "attempts": 2,
        "passed_at": "2026-09-01T09:42:00Z"
      },
      {
        "tool_name": "年度目標撰寫家教",
        "status": "in_progress",
        "latest_score_percent": 55.0,
        "attempts": 3,
        "passed_at": null
      },
      {
        "tool_name": "短期目標設計家教",
        "status": "locked",
        "latest_score_percent": null,
        "attempts": 0,
        "passed_at": null
      }
    ],
    "recurring_weaknesses": [
      "去標籤化用語",
      "與教育目標連結"
    ]
  }
}
```

---

## 附錄：欄位命名慣例

| 慣例 | 說明 |
|------|------|
| `*_id` | UUID 格式 |
| `*_at` | ISO 8601 UTC 時間 |
| `percentage` | 0～100 的浮點數 |
| `confidence` | 0～1 的浮點數（AI 信心值） |
| `version` | 字串，例如 `"v1.0"` |
| `status` | 固定英文小寫，例如 `"active"` |

---

> **此文件為前後端介接的唯一契約。** 任何 API 的新增、修改或廢棄，必須先更新此文件，由前後端確認後才能實作。如有異動請在 PR 中同步更新本文件。

