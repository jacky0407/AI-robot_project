# 實作進度統整

> 掃描時間：2026-09-17
> 基準 commit：`62f57f4` — `feat(api): add privacy detect, review, session creation endpoints; update SSE docs`
> 掃描時本機工作目錄與 `origin/main` 內容一致；之後套用了**第一批修正**（尚未提交，見 [CHANGELOG.md](./CHANGELOG.md)）

---

## 一、Git 現況

**Repository**：`https://github.com/jacky0407/AI-robot_project.git`

| 分支 | 最新 commit | 狀態 |
|------|------------|------|
| `main` | `62f57f4` | 本機與遠端同步，為目前開發主線 |
| `feature/tool-builder-and-practice-engine` | `009e207` | 已併入 main |
| `feature/auth-role-routing` | `946abaa` | 已透過 PR #5 併入 main |
| `feature/ai-evaluation-engine` | `00172ae` | 落後 main，已被 `feature/integrate-ai-with-db` 取代 |
| `frontend-auth-ui` | `9d90a5e` | 已透過 PR #3 / #4 併入 main |

**已合併的 PR**：#1 雙身分登入介面原型、#2 工具建構器與練習引擎、#3 前端 README、#4 API 規格書補齊、#5 身分驗證與角色導流。

**開發歷程摘要**（依時間）：

1. 建立 monorepo 骨架與資料庫 schema
2. 補齊需求文件（README / api.md / TEAM_ROLES）
3. 後端 AI 引擎骨架、練習 API、SSE、個資偵測
4. 前端雙身分登入介面與工作區原型
5. 工具建構器（教授後台）與練習引擎串接真實資料庫
6. 三階段 Agent 升級：EvaluatorAgent → TutorAgent → CoherenceAgent（共 56 個單元測試）
7. 補上 privacy / review / session 端點

---

## 二、整體完成度

```
需求循環：閱讀案例 → 獨立作答 → AI 依規準分析 → 分層提示 → 學生修正 → 教師複核 → 研究匯出
狀態：     ✅ 可用    ✅ 可用    ✅ 已實作      ⚠️ 半成品   ✅ 可用    ⚠️ 半成品   ❌ 未開始
```

| 模組 | 完成度 | 說明 |
|------|--------|------|
| 資料庫 schema | 🟢 90% | 14 張表全部建好，含自動觸發器；**RLS 規則尚未撰寫** |
| AI 評分引擎 | 🟢 90% | 三層 Agent 完整，加權計分已修正，96 個測試通過；尚未接 `error_taxonomy`、成本控管 |
| 練習 API（後端） | 🟡 75% | 提交／評分／歷程可用；`hints` 端點仍為假資料，Session 未落庫 |
| 教師複核 API | 🟢 75% | 三個端點已對齊 schema 且可運作；尚無前端介面、無權限檢查 |
| 教授工具建構器 | 🟡 60% | 可新增／列出／刪除機器人；無編輯、無版本控管、無案例與提示設定分頁 |
| 學生練習介面 | 🟡 50% | 可載入步驟與案例、提交作答；**未解析 SSE 串流**，結果以 JSON 原文呈現 |
| 身分驗證與導流 | 🟢 80% | Supabase Auth + middleware 角色守門完成；無註冊頁、無忘記密碼 |
| 課程與邀請碼 | 🔴 5% | 只有資料表，無任何 API 與介面 |
| 個資偵測 | 🟢 95% | 6 類規則 + 13 個測試，已掛在提交流程前 |
| 表單／前後測 | 🔴 0% | 只有資料表 |
| 研究匯出 CSV | 🔴 0% | 未開始 |
| 研究倫理同意 | 🔴 10% | 註冊時自動產生匿名編號；無同意書流程 |

---

## 三、逐項清單

### ✅ 已完成且可運作

**後端 AI 引擎（`backend/core/ai/`）**

- `base.py` — `BaseAIProvider` 抽象層與 `EvaluationResult` 標準格式，換 AI 廠商不動教學邏輯
- `gemini_provider.py` — Gemini 2.5 Flash 實作（懶惰初始化，啟動不需 API Key）
- `rubric_formatter.py` — Rubric dict → Prompt 純文字
- `agents/evaluator_agent.py` — **逐構面評分**，一次只評一個構面再整合，附信心值
- `agents/tutor_agent.py` — **教學決策**：evaluate / give_hint / encourage / escalate 四種行動
- `agents/coherence_agent.py` — **跨步驟一致性檢核**，三條預設規則，指回問題步驟
- `tools/evaluation_tools.py` — LangChain tool 定義（結構已備妥，Agent 尚未實際掛載使用）

**後端 API**

- `POST /api/practice/sessions` — 建立練習 Session
- `POST /api/practice/sessions/{id}/submit` — 個資偵測 → 讀 Rubric → 寫 `step_attempts` → TutorAgent SSE 串流 → 寫 `ai_evaluations` 與 `prompt_logs`
- `GET /api/practice/sessions/{id}/history` — 讀歷次嘗試與評分
- `POST /api/practice/modules/{id}/coherence-check` — 跨步驟檢核 SSE
- `POST /api/privacy/detect` — 個資偵測
- `GET /api/teacher/tools`、`DELETE /api/teacher/tools/{id}`
- `GET /api/review/pending`、`GET /api/review/submissions/{id}`、`POST /api/review/submissions/{id}/judge`

**前端頁面**

- `/login` — Supabase Auth 登入 + 依 `profiles.role` 導流
- `/student/modules` — 從 `module_steps` 列出步驟
- `/student/practice` — 載入步驟與案例、提交作答到後端
- `/admin/tools` — 機器人清單 + 刪除
- `/admin/tools/new` — 建立機器人（含動態 Rubric 編輯器）
- `middleware.ts` — 未登入導向 `/login`；非管理角色不得進 `/admin`

**資料庫**

- `supabase/schema.sql` — 14 張表 + `handle_new_platform_user()` 觸發器（自動建 profile 與匿名研究編號）
- `supabase/seed.sql` — 1 位教授、1 位學生、1 門課、1 個虛構案例、2 支機器人、2 個模組步驟

**測試**：96 個單元測試（詳見 [TESTING.md](./TESTING.md)），
含 `test_practice_api.py` 對提交流程的端對端覆蓋

---

### ⚠️ 已實作但是假資料或半成品

| 位置 | 問題 |
|------|------|
| `GET /api/practice/sessions/{id}/hints` | 完全寫死的回傳值，未讀資料庫、未接 `TutorAgent` |
| `TutorAgent._default_hint()` | 四層提示是寫死的通用文字，未讀教授設定的 `teaching_strategy` |
| `POST /api/practice/sessions` 回傳 | `session_id` 為即時產生的 UUID，未寫入任何資料表，後續 `submit` 也不驗證它（欄位已修正，Session 落庫未做）|
| `ai_evaluations` 未存 `confidence` | Agent 算出了信心值，但 schema 無此欄位，教師端看不到「AI 沒把握」的訊號 |
| `/student/practice` 的 SSE 處理 | 用 `res.text()` 一次讀完再切行，失去串流效果；結果以 `JSON.stringify` 原文顯示 |
| `frontend/src/app/page.tsx` | 22KB 的純前端原型（inline style、無後端串接），與 `/login`＋`/student` 路線並存，職責重疊 |
| `supabase/practice_repo.py` | 早期版本的資料存取函式，已被 `backend/api/practice.py` 內嵌邏輯取代，目前沒有任何地方 import |

---

### ❌ 尚未實作

**後端**

- `api/auth.py`、`api/courses.py`、`api/tools.py`、`api/export.py` — `main.py` 中以 TODO 註解預留，檔案不存在
- 課程與邀請碼（建立、加入、審核、每日額度）
- 自主探索模式的額度控管（`tool_permissions` 表無人使用）
- 表單與前後測（`forms` / `form_responses`）
- 研究資料 CSV 匯出（`/api/export/*`）
- 研究倫理同意書流程（`/api/consent`）
- 教授後台監控 API（`/api/admin/conversations`、`/api/admin/tools/{id}/analytics`）
- AI 成本記錄（`ai_evaluations.ai_cost` 恆為 0；`ai_tools.max_cost_limit` 無人檢查）
- 錯誤分類（`ai_tools.error_taxonomy` 定義好但 Agent 未使用）
- RAG／pgvector（schema 已啟用 extension，無任何向量欄位與檢索邏輯）

**前端**

- 註冊頁、忘記密碼
- 課程加入（輸入邀請碼）
- 分層提示 UI（逐層解鎖）
- 教師複核後台介面
- 機器人編輯頁、案例管理、提示設定（GPTs Builder 的其他分頁）
- 作答版本比對介面

**安全與部署**

- Supabase RLS 規則（`schema.sql` 完全沒有 `ENABLE ROW LEVEL SECURITY`）
- 後端 API 的身分驗證（目前所有端點無任何權限檢查，`user_id` 由前端傳入）
- Vercel / Render 部署設定

---

## 四、建議的下一步（依風險排序）

~~0. 修正會在執行時失敗的問題~~ ✅ 已完成（見 [CHANGELOG.md](./CHANGELOG.md)）

1. **補 API 權限驗證** — 目前 `submit` 的 `user_id`、`judge` 的 `teacher_id` 都由前端 request body 傳入，任何人都能冒用他人身分。應改為後端驗證 Supabase JWT 取得。
2. **撰寫 RLS 規則** — README 的資安規範寫了「Supabase RLS 開啟」，但實際未開；前端用 anon key 直接讀寫 `ai_tools`、`module_steps`。
3. **前端正確消費 SSE** — 改用 `fetch` + `ReadableStream`，逐構面顯示分數，這是「AI 即時評分」體驗的關鍵。
4. **把寫死的 `http://127.0.0.1:8000` 改成環境變數** — 否則無法部署。
5. **做教師複核的前端介面** — 後端三個端點已經可用，但沒有畫面可以操作。
6. **釐清 `page.tsx` 原型的去留** — 決定是砍掉，還是改成純行銷首頁。
7. **接上分層提示的真實資料** — 讓 `hints` 端點與 `TutorAgent` 讀 `ai_tools.teaching_strategy`。
