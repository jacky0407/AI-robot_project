# AI Agent 開發規範 (AGENTS.md)

本文件供所有 AI 助手（Copilot、Claude、Gemini、Cursor 等）在此專案協作時閱讀與遵守。

---

## 專案背景

這是一個學前特殊教育 AI 培訓平台，採用：
- **前端**：Next.js（`frontend/`）
- **後端**：Python FastAPI（`backend/`）
- **資料庫**：Supabase（PostgreSQL）
- **AI 引擎**：Google Gemini API（`backend/core/ai/`）

詳細分工請讀：`TEAM_ROLES.md`
API 規格請讀：`docs/api.md`
Git 規範請讀：`CONTRIBUTING.md`

---

## ⚠️ 絕對禁止的行為

1. **禁止把任何 API Key、密碼、Service Role Key 寫進程式碼或文件**
   - 所有 Secret 只存在 `.env`（已在 `.gitignore`）或部署平台的環境變數
2. **禁止使用 `main` 分支直接開發**，一律開 `feature/xxx` 分支
3. **禁止在個資偵測（`privacy.py`）上放寬規則**，只能加嚴
4. **禁止讓 AI 評分結果覆蓋教師最終判定**，兩者必須分開存放

---

## 單元測試強制要求

> **新增或修改 `backend/core/` 下的任何業務邏輯，都必須同步新增或更新對應的單元測試。**

### 執行測試

```bash
cd backend
py -m pytest tests/ -v
```

**PR 送出前，必須確認輸出是 `X passed, 0 failed`。**

### 測試規則

| 規則 | 說明 |
|------|------|
| 不呼叫真實外部服務 | 測試不得消耗 Gemini API quota 或讀寫 Supabase，一律 Mock |
| 覆蓋正常 + 異常情境 | 每個功能至少兩個測試：成功情況 + 預期錯誤情況 |
| 測試命名要清楚 | `test_taiwan_id_should_be_detected` 比 `test_1` 好 |
| async 函式加裝飾器 | `@pytest.mark.asyncio` |

### 現有測試檔案

```
backend/tests/
├── test_privacy.py          ← 個資偵測（13 個測試）
├── test_rubric_formatter.py ← Rubric 格式化（8 個測試）
└── test_gemini_provider.py  ← AI 評分邏輯（7 個測試）
```

---

## 後端架構說明（給 AI 快速定位用）

```
backend/
├── main.py                  ← FastAPI 進入點，只負責掛 router
├── core/
│   ├── config.py            ← 環境變數（用 get_settings() 取得）
│   ├── privacy.py           ← 個資偵測，提交作答前必須先過這關
│   └── ai/
│       ├── base.py          ← AI Provider 抽象層（換廠商不改邏輯）
│       ├── gemini_provider.py ← Gemini 實作（懶惰初始化）
│       └── rubric_formatter.py ← Rubric dict → Prompt 文字
├── database/
│   └── client.py            ← get_supabase()，使用 Service Role Key
└── api/
    ├── practice.py          ← 學生練習、AI 評分、SSE 串流
    └── teacher.py           ← 教師後台（機器人 CRUD）
```

### 關鍵設計決策

- **SSE 而非 WebSocket**：AI 評分結果以 SSE 串流回傳，不用 WebSocket
- **評分結果與教師判定分開存放**：`ai_evaluations` ≠ `teacher_reviews`，永不覆蓋
- **AI 模型可替換**：所有 Gemini 呼叫透過 `BaseAIProvider` 抽象，換模型只改 `gemini_provider.py`
- **Supabase 只用 Service Role Key**：後端一律用 `get_supabase()` 存取，前端不得直接寫敏感資料表

---

## 資料庫重要資料表

| 表名 | 用途 |
|------|------|
| `profiles` | 使用者基本資料與角色（student / owner）|
| `ai_tools` | 機器人設定（含 system_prompt、rubric_criteria）|
| `module_steps` | 課程步驟編排，關聯 ai_tools |
| `step_attempts` | 學生每次作答記錄（支援多版本）|
| `ai_evaluations` | AI 初評結果（不得被覆蓋）|
| `teacher_reviews` | 教師最終判定（獨立存放）|
| `prompt_logs` | 分層提示使用紀錄 |
| `research_consents` | 研究倫理同意書與匿名編號 |
