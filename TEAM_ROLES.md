# 專案團隊三人分工表 (Team Roles & Responsibilities)

本專案採用前後端分離 (Next.js + FastAPI) 與微服務架構概念，將「資料庫層」與「AI 邏輯層」獨立拆開。

---

## 🎯 第一版 (P0) 範圍說明

> **第一版只做一門課：學前 IEP 逐步撰寫**
>
> 但所有介面與資料庫的設計必須「預留擴充彈性」，確保未來新增第二門課、第三門課時，**只需在後台新增資料，不需要動程式碼**。
>
> 換句話說：第一版做「一門課的完整功能」，但架構要能支撐「N 門課」。

**P0 包含功能：**
- ✅ 一門課程（IEP）、約 10 支 AI 家教機器人
- ✅ 帳號系統（Email 註冊、邀請碼加入課程）
- ✅ 類 GPTs 的 AI 工具建構器（教授自行新增/設定機器人）
- ✅ 學生練習介面（作答 → AI 評分 → 分層提示 → 修正）
- ✅ 教授後台：查看所有學生與機器人的對答狀況
- ✅ 教師複核與最終判定
- ✅ 基礎 CSV 資料匯出

**P0 預留但不實作的功能：**
- ⏳ 多課程管理（介面留著，第一版只有一門課）
- ⏳ 去識別化研究資料匯出（資料表欄位先建，功能 P1 再做）
- ⏳ 帳號停用與刪除（欄位預留，P1 再做）
- ⏳ AI 成本監控儀表板（P1）

---

## 👩‍💻 成員一：前端工程師 (Frontend Engineer)

**職責**：專注於使用者介面與操作體驗，不碰觸複雜的伺服器與資料庫底層邏輯。

**使用技術**：Next.js (TypeScript), React, Tailwind CSS, shadcn/ui

**負責模組：**

### 🔵 學生端介面
- **登入 / 註冊頁面**：Email 登入表單、邀請碼加入課程流程
- **學習路徑地圖**：顯示目前課程的所有機器人（步驟）與鎖定狀態
- **練習介面（核心）**：
  - 案例閱讀區 + 作答文字框
  - 送出後接收 SSE 串流，以打字機效果逐步顯示 AI 評分結果
  - 各構面分數卡片、AI 理由、原文佐證高亮標示
  - 分層提示按鈕（第一層免費出現，第二、三層需手動點擊）
  - 修改作答並重新送出
- **版本歷程查看**：學生可查看自己每次作答的版本比較

### 🔴 教授後台介面
- **AI 工具建構器（類 GPTs 介面）**：
  - 六個獨立分頁：基本資訊 / 教學設定 / 評分規準 / 練習案例 / 分層提示 / 進階設定
  - 右側即時預覽（學生端視角）
  - 儲存草稿 / 發布按鈕
- **學生對答監控後台**：
  - 所有學生 × 所有機器人的對答狀況總覽列表
  - 點進去看單一學生與單一機器人的完整對話詳情
  - 待複核清單（AI 已評分、等教師確認）
  - 複核操作介面（接受 / 修改 / 否決 AI 評分）
- **班級分析儀表板**：各機器人通過率、常見錯誤、需關注學生

---

## 🗄️ 成員二：資料庫與驗證工程師 (Database & Auth Engineer)

**職責**：負責系統的基底建設，包含建置資料庫、設定身分驗證，並提供「純資料操作」的 API 介面供其他兩人呼叫。

**使用技術**：Supabase (PostgreSQL), Python FastAPI (CRUD 路由)

**負責模組：**

### 帳號與驗證
- Email 註冊、驗證信發送、登入、登出
- HttpOnly Cookie 的 Token 發放與驗證
- 邀請碼產生與課程加入邏輯
- 角色權限（HOST / STUDENT）的路由保護 Middleware

### 資料庫設計與維護
- 維護 `supabase/schema.sql`，建立所有資料表
- **P0 需建立（含預留欄位）的資料表**：
  - `users`（含 `status` 欄位預留停用功能）
  - `courses`（P0 只有一門，但表結構支援多門）
  - `ai_tools`（機器人定義，含版本欄位）
  - `rubrics` + `rubric_dimensions`（Rubric 結構）
  - `cases`（練習案例）
  - `hints`（分層提示）
  - `practice_sessions`（每次練習 Session）
  - `submissions`（作答版本歷程）
  - `ai_evaluations`（AI 初評，含 confidence）
  - `teacher_evaluations`（教師最終判定，獨立存放）
  - `consent_records`（同意紀錄，P0 先建，功能 P1）
- Supabase RLS 設定（全部封閉公開存取，後端用 Service Role Key 操作）

### 提供後端 API（供前端與 AI 工程師呼叫）
- Auth API：`/api/auth/register`, `login`, `logout`, `me`
- 課程 API：`/api/courses` CRUD、邀請碼加入
- AI 工具 API：`/api/tools` CRUD（機器人設定讀寫）
- Rubric API：`/api/tools/:id/rubric` 讀寫
- 案例 API：`/api/tools/:id/cases` 讀寫
- 資料匯出 API：`/api/export/teaching`（CSV）
- 監控 API：`/api/admin/conversations`、`students/:id/profile`

---

## 🧠 成員三：後端 AI 應用工程師 (Backend / AI Engineer)

**職責**：不處理資料庫底層怎麼儲存，專心寫 Python 處理 AI 評分邏輯。負責串接 Gemini API，並在過程中呼叫成員二提供的資料庫 API 取得或寫入資料。

**使用技術**：Python FastAPI, Google Gemini API, LangChain（或 Gemini SDK 直接串接）

**負責模組：**

### AI 評分引擎（核心）
- 從資料庫讀出 Rubric，動態組裝成 Prompt 送給 Gemini
- 要求 Gemini 以 JSON 格式回傳（各構面分數、理由、原文佐證）
- JSON 解析與錯誤處理（格式錯誤時重試）
- **AI 模型抽象層**：將 Gemini 呼叫封裝為可替換介面，未來換 OpenAI 只需改一個設定

### SSE 串流回傳
- 將 AI 評分結果以 SSE (Server-Sent Events) 格式逐步串流給前端
- 確保前端能以打字機效果呈現評分出現的過程

### 分層提示邏輯
- 依學生分數與已使用提示層級，決定回傳哪一層提示
- 記錄每次提示使用（時間、層級）

### Practice API（練習提交的 AI 邏輯部分）
- `POST /api/practice/sessions`：建立練習 Session
- `POST /api/practice/sessions/:id/submit`：接收作答 → 呼叫 AI 評分引擎 → SSE 回傳
- `GET /api/practice/sessions/:id/hints`：分層提示邏輯

### 個資偵測
- 在作答提交前，以 Regex 偵測疑似個人識別資訊（身分證、電話、Email）
- 觸發時回傳警告，不直接阻擋但記錄事件

---

## 📋 介面預留規範（所有成員共同遵守）

> 以下規範確保第一版雖只做一門課，但未來擴充不需大改程式碼：

| 規範 | 說明 |
|------|------|
| 所有資料必須有 `course_id` 欄位 | 即使 P0 只有一門課，也要帶著這個欄位，讓 SQL 查詢可以直接加 WHERE |
| 版本欄位 | `ai_tools`, `rubrics` 等都要有 `version` 欄位，已發布版本不可覆蓋 |
| 角色判斷用 `role` 欄位，不寫死邏輯 | 不要在程式裡直接比對 Email，要比對 `users.role` |
| API 路由設計保持通用 | 例如 `/api/courses/:course_id/tools` 而非 `/api/iep/tools` |
| 禁止把內容寫死在程式碼 | Rubric 內容、提示文字、案例文本全部存資料庫 |
