# 專案開發協作規範 (GitHub Workflow)

為確保專案程式碼品質與團隊協作順暢，請所有成員遵守以下 Git 與 GitHub 操作規範。

## 1. 分支策略 (Branching)

- **`main` 為主分支**：代表隨時可部署至正式環境的穩定版本。**絕對禁止**直接 `git push` 程式碼到 `main` 分支。
- **開發新功能或修復 Bug**：一律從最新的 `main` 建立新分支。
  
建立分支的指令流程：
```bash
git checkout main
git pull origin main
# 開發新功能
git checkout -b feature/<簡短描述>
# 或 修復 Bug
git checkout -b fix/<簡短描述>
```

- **分支命名規範**：使用全小寫英文與連字號 `-`，避免使用空白或底線。
  - 範例：`feature/chat-ui`、`feature/admin-dashboard`、`fix/login-error`。

## 2. Commit 規範

我們採用 [Conventional Commits](https://www.conventionalcommits.org/) 慣例。每次 Commit 都必須清楚表達修改目的。

格式：
```text
<type>(<scope>): <subject>
```

- **`type` (必須)**：依修改性質選用以下標籤：
  - `feat`: 新增功能
  - `fix`: 修正 bug
  - `docs`: 只修改文件 (如 README, 說明文件)
  - `style`: 程式碼格式調整 (排版、縮排等不影響程式邏輯的變動)
  - `refactor`: 重構 (不改變外部行為的程式碼優化)
  - `test`: 新增或修改測試程式碼
  - `chore`: 雜項 (如套件依賴更新、設定檔異動)
- **`scope` (選填)**：標明影響的模組範圍，例如 `feat(auth): ...`。
- **`subject` (必須)**：用英文撰寫，使用祈使句，結尾不加句號。
  - 範例：`feat(chatbot): add file upload support`
  - 範例：`fix(db): resolve connection timeout`
- **原則**：一個 commit 只做一件事。若同時涵蓋不同性質的修改（如加功能和修文件），請拆分成多個 commit。

## 3. Pull Request (PR) 與 Merge 規範

**任何程式碼的變動都必須透過 Pull Request 進行，嚴禁隨意 Merge 進 main 分支。**

- 分支開發完成後，推上遠端並在 GitHub 建立 PR (Pull Request) 指向 `main`。
- PR 標題以英文撰寫（首字大寫），並清楚說明本次變更的目的。
- **合併前必須確認以下事項**（不可草率 merge）：
  1. 自己已重新看過一次 Code Diff，確保沒有夾帶無關的程式碼或測試用 `console.log`。
  2. **絕對沒有**將金鑰 (API Keys)、憑證或含有個資隱私的檔案 Commit 進去（如 `.env` 檔案必須在 `.gitignore` 內）。
  3. 若程式邏輯或 API 規格有變動，必須同步更新相關的技術文件。
  4. 分支已對齊最新的 `main` 分支進度，確保沒有版本衝突 (Merge Conflicts)。
- 確認上述事項無誤後（若團隊有要求互相 Code Review 則須等 Review 通過），才可進行 Merge。

## 4. 單元測試規範 (Unit Tests)

**所有新增的後端業務邏輯，都必須附上對應的單元測試才能合併進 `main`。**

### 測試位置

```
backend/
└── tests/
    ├── test_privacy.py          ← 個資偵測模組
    ├── test_rubric_formatter.py ← Rubric 格式化工具
    └── test_gemini_provider.py  ← AI Provider 評分邏輯
```

### 執行測試指令

```bash
cd backend
py -m pytest tests/ -v
```

所有測試必須 **全數通過（0 failed）** 才可送出 PR。

### 什麼情況必須寫測試

| 情況 | 要求 |
|------|------|
| 新增 `core/` 下的任何模組 | ✅ 必須附測試 |
| 修改評分邏輯、個資偵測規則 | ✅ 必須更新對應測試 |
| 修改 API 路由（`api/`）| ✅ 建議附測試（可 Mock DB）|
| 只改文件、設定檔 | ❌ 不需要 |

### 測試撰寫原則

1. **不依賴外部服務**：測試不得真正呼叫 Gemini API 或 Supabase，一律使用 `unittest.mock`。
2. **同時覆蓋正常與異常情境**：每個功能至少要有一個「應該成功」和一個「應該失敗/報錯」的測試。
3. **測試名稱用中文描述意圖**：讓隊友一眼看出這個測試在驗證什麼。
4. **async 測試用 `@pytest.mark.asyncio`** 標記。


### FakeSupabase：測 API 層不用連資料庫

`backend/tests/test_practice_api.py` 裡的 `FakeSupabase` 可直接重複使用，
要測 `api/review.py`、`api/teacher.py` 時不必重寫：

```python
fake_db = FakeSupabase(STEP_ROW, prev_attempts=[], hints_count=0)
with patch.object(practice, "get_supabase", return_value=fake_db):
    ...
fake_db.find("step_attempts", "insert")   # 取出實際寫入的內容做斷言
```

它會**依 `select()` 指定的欄位投影**——查詢時沒選到、程式卻去讀的錯誤，
在測試裡就會重現，而不是等到連上真資料庫才爆。
（這個錯誤實際發生過兩次，見 KNOWN_GAPS #2。）

### 降級行為也要測

三個 Agent 都設計成「AI 回傳格式錯誤時降級而非中斷」，這些路徑同樣要覆蓋：

| 情境 | 預期行為 |
|------|---------|
| `_score_dimension()` JSON 解析失敗 | 給最低分 + 標記 `failed`，整體信心值壓到 0.5 以下 |
| `_synthesize()` 例外 | `confidence=0.6`，`overall_feedback` 為預設文字 |
| 提示個人化失敗 | 退回教授原始模板，不可顯示空白 |
| 錯誤分類失敗 | 回空陣列，不影響評分寫入 |

### 尚未覆蓋的區域

`api/review.py`、`api/teacher.py`、`api/privacy.py`、`database/client.py`、
前端（完全無測試設定）、端對端。
