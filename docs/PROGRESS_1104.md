# 進度盤點與 11/16 雲端 Demo 計畫

> 盤點日期：2026-11-04（基準 main `e465eb3`）
> 目標：**11/16 前**把後端（與前端）部署到雲端，給教授看初步 Demo
> 下次開會：**11/9**，會議要決定的事項見第 5 節

---

## 1. 目前進度總覽

| 區塊 | 狀態 | 說明 |
|------|------|------|
| 後端單元測試 | ✅ | `pytest tests/` **542 passed, 0 failed** |
| 帳號／註冊／忘記密碼／Onboarding | ✅ | 走 Supabase Auth，前端直連 |
| 課程、邀請碼加入、成員審核 | ✅ | `api/courses.py`、`/admin/courses` |
| 模組與步驟管理、指派、必修解鎖 | ✅ | `api/modules.py`、`api/course_modules.py`、`/admin/modules`（PR #32） |
| 機器人申請與每日額度（自主模式） | ✅ | `api/tool_requests.py`、`core/access.py` |
| 練習作答 → AI 評分（SSE）→ 分層提示 | ✅ | `api/practice.py`、`core/ai/*`，前端 `/student/modules` |
| 練習 Session 落庫 | ✅ | migration 009（PR #30） |
| 機器人記憶、錯誤分類、Coherence 檢核 | ✅ | 細節見 LLM_SYSTEM.md |
| 個資偵測 | ✅ | 送出前檢查，有風險回警告 |
| 教師複核（AI 初評與教師判定分開存） | ✅ | `api/review.py`、`/admin/review/[id]` |
| API 身分驗證、RLS | ✅ | `core/auth.py`、migration 005／006 |
| 機器人建構器 | 🟡 | 只能**新增**，不能編輯；案例／提示分頁未完成 |
| 共用登入 Portal（XR 整合） | 🟡 | PR #31 待審 |
| **雲端部署** | 🔴 | **完全尚未設定**（無 render.yaml / Procfile / CI） |
| 教授監控總覽、班級分析 | 🔴 | 未實作 |
| CSV 資料匯出（P0） | 🔴 | 未實作 |

待合併的 PR：#31（Portal 登入）、#34（課程 API UUID 驗證 + pytest 進 requirements）、#35（文件更新）。

---

## 2. 架構缺漏檢查

### 2.1 🔴 會擋住 11/16 Demo 的問題

| # | 問題 | 影響 | 建議處理 |
|---|------|------|---------|
| D1 | ✅ ~~沒有任何部署設定~~ | — | 已新增根目錄 `render.yaml`（Python 3.12、`/api/health` 健康檢查），步驟見 SETUP.md 第 6 節；剩下到 Render 建服務 |
| D2 | **Gemini 免費額度一天 20 次**（KNOWN_GAPS #17） | 一次評分用 3～6 次請求，Demo 當天幾次提交就 429 | Demo 前改付費方案或換額度高的模型（待 11/9 決定）；✅ 逾時已改為 `LLM_TIMEOUT_SECONDS`，預設 90 秒 |
| D3 | ✅ ~~CORS 只允許單一網址~~ | — | `FRONTEND_URL` 已支援逗號分隔多個網址 |
| D4 | **正式環境資料庫未建立** | 雲端後端沒資料可讀 | 開 Demo 用的 Supabase 專案，依序跑 `schema.sql` + migrations 002、004–011（見 SETUP.md 第 6 節）；Auth → URL Configuration 加入 Vercel 網址（驗證信、重設密碼才會導回正確網站） |
| D5 | **Demo 資料** | 教授看到空畫面 | 準備 1 門課、九步驟模組、至少 2–3 個機器人的完整 Rubric／案例／提示、示範學生與教師帳號（密碼不要用 seed 的 `Test1234!`） |
| D6 | **Render 免費方案會休眠** | 第一次開啟要等 30–60 秒，SSE 可能逾時 | Demo 前 5 分鐘先打 `/api/health` 喚醒，或用付費方案 |

### 2.2 🟠 Demo 前建議處理（不處理也能上，但容易出事）

| # | 問題 | 建議 |
|---|------|------|
| H1 | **根目錄 `node_modules/` 有 152 個檔案被 commit 進 git**，另有空檔案 `git`、`backend/uvicorn`，以及根目錄沒用到的 `package.json`、`utils/supabase.ts` | 從 git 移除，`.gitignore` 加 `node_modules/` |
| H2 | `requirements.txt` 全用 `>=`，沒有鎖版本 | 雲端每次建置可能裝到新版 langchain 而壞掉；鎖定目前本機能跑的版本 |
| H3 | ✅ ~~沒有 CI~~ | 已新增 `.github/workflows/backend-tests.yml`，PR 自動跑 pytest |
| H4 | Migration 編號：整合方案文件寫的角色 claim 叫 `009_role_claim.sql`，但 009 已被 `009_practice_sessions.sql` 用掉、011 被額度退回用掉 | 角色 claim 建檔時用 **`012_role_claim.sql`** |
| H5 | ✅ ~~AI 評分失敗不退回額度~~（KNOWN_GAPS #18） | 已完成，需跑 migration 011 |
| H6 | `ENVIRONMENT=production` 時 `/docs` 仍公開 | 正式環境可關閉 Swagger，或 Demo 時保留給教授看也可以，會議決定 |

### 2.3 🟡 功能缺漏（對照 TEAM_ROLES.md P0 範圍）

| 功能 | P0? | 現況 | 建議時程 |
|------|-----|------|---------|
| 教授後台：所有學生 × 機器人對答總覽 | P0 | 後端 `/api/admin/conversations` 與前端頁面都沒有；目前只有待複核清單 | **Demo 前做簡易版**（教授最想看的畫面） |
| 機器人編輯（GPTs Builder 編輯／案例／提示分頁） | P0 | 只能新增 | Demo 後 |
| 基礎 CSV 匯出 `/api/export/teaching` | P0 | 未實作 | Demo 後（11 月底） |
| 學生作答版本比對畫面 | P0 | 未實作（資料已有 `attempt_number`） | Demo 後 |
| 班級分析儀表板（通過率、常見錯誤） | P0 | 未實作（`detected_errors` 已有資料） | Demo 後 |
| 機器人記憶畫面 | — | 後端 API 已有，前端沒用 | Demo 後 |
| `require_teacher_review` 擋解鎖 | — | 欄位有讀，但不會擋下一步 | 會議決定是否需要 |
| Coherence 規則入庫 | — | 寫死在程式 | 需教授確認內容 |
| AI 成本記錄（`ai_cost` 恆為 0） | P1 | 未實作 | P1 |
| 研究同意書、去識別化匯出、前後測 | P1 | 只有資料表 | P1 |

---

## 3. 分工與待辦（依 TEAM_ROLES.md）

> 專案負責人：@Yorukidzuki、@KafuuBob、@jacky0407。

### 成員一 前端 — @Yorukidzuki

- [ ] PR #31（Portal 登入）處理審查意見並合併
- [ ] Vercel 部署：建專案、設定 `NEXT_PUBLIC_SUPABASE_URL`、`NEXT_PUBLIC_SUPABASE_ANON_KEY`、`NEXT_PUBLIC_API_URL`（= Render 網址）
- [ ] 確認 `npm run build` 在乾淨環境可過
- [ ] 教授監控總覽頁（簡易版）：學生 × 步驟表格，點進去看作答與 AI 評分（配合成員二的 API）
- [ ] 前端移除已不使用的 `user_id`、`teacher_id` 傳送（KNOWN_GAPS #8）
- [ ] 寫 Demo 腳本：學生加入課程 → 作答 → 看 AI 評分與提示 → 修改 → 教師複核
- [ ] Demo 後：機器人編輯頁、版本比對頁

### 成員二 資料庫與驗證 — @KafuuBob

- [ ] PR #34 合併
- [ ] 建立 Demo 用 Supabase 專案：跑 `schema.sql` + migrations 002、004–011（見 SETUP.md 第 6 節），Auth Redirect URL 加 Vercel 網址（D4）
- [ ] 把 005／006 的 RLS 政策寫回 `schema.sql`（KNOWN_GAPS #9）
- [ ] 鎖定 `requirements.txt` 版本（H2）
- [ ] 教授監控總覽 API：`GET /api/admin/conversations`（學生 × 步驟、最新分數、狀態），需 `require_staff` + 單元測試
- [ ] 清掉 git 裡的 `node_modules/` 與空檔案（H1）
- [ ] Demo 帳號與資料匯入正式 Supabase（密碼不要用 seed 的 `Test1234!`）（D5）
- [ ] Demo 後：`/api/export/teaching` CSV 匯出

### 成員三 後端 AI — @jacky0407

- [x] 部署設定：`render.yaml`（D1）
- [ ] 在 Render 用 Blueprint 建服務並填秘密（等 Supabase 正式專案）
- [x] `FRONTEND_URL` 支援多個網址（D3）
- [x] Gemini 逾時改為可設定，預設 90 秒（D2）
- [ ] Gemini 額度：與教授決定付費方案或換模型（D2）
- [x] 評分失敗退回額度（H5，migration 011）
- [x] GitHub Actions 跑 pytest（H3）
- [ ] 合併 PR #35
- [ ] 和教授確認 2–3 個步驟的 Rubric、練習案例、分層提示文字，以及 Coherence 三條規則（D5、KNOWN_GAPS #11）

### 全員

- [ ] 部署後（11/13）依 `docs/SETUP.md` 第 4 節驗收清單在雲端完整測一輪，問題開 Issue

### 對外協作（不屬於本專案分工）

- **教授（RachelWU843）**：Demo 對象；Rubric、案例、提示文字、Coherence 規則的內容由教授確認
- **nick5wu**：負責把教授的內容（XR 系統）接到我們網站的 `/xr` 底下

要讓 XR 在雲端接上，我們這邊要先做好：

| 項目 | 現況 | 負責 |
|------|------|------|
| PR #31（Portal + `/xr` 登入保護） | 待合併 | 前端 |
| 角色 claim（`012_role_claim.sql` + Supabase Access Token Hook），XR 端讀 token 的 `user_role` 要用 | 未做 | 資料庫 |
| XR 必須和我們用**同一個** Supabase 專案才能共用登入 → Demo 用哪個 Supabase 要通知 nick5wu | 待 11/9 決定 | 資料庫 |
| Vercel 設定 `XR_FRONTEND_URL`、`XR_BACKEND_URL`（需 XR 端給雲端網址） | 未做 | 前端 |
| XR 若用 Unity WebGL，`next.config.ts` 的 CSP 要加 `'wasm-unsafe-eval'` | 等 XR 端回覆 | 前端 |

---

## 4. 時程

| 日期 | 里程碑 |
|------|--------|
| 11/4–11/8 | 合併 #31 #34 #35；清理 repo；鎖版本；寫部署設定；準備 Demo 內容 |
| **11/9（一）開會** | 決定第 5 節事項；確認分工 |
| 11/10–11/12 | Supabase 正式專案 + Render 後端 + Vercel 前端上線；監控總覽 API 與頁面 |
| 11/13 | 在雲端完整跑一次 Demo 腳本，修 bug |
| 11/14 | 凍結功能（只修 bug）；彩排 |
| 11/15 | 緩衝日 |
| **11/16（一）** | 給教授 Demo |

---

## 5. 11/9 會議要決定的事

1. **Gemini 方案**：付費？用哪個模型？預算多少？（D2）
2. **部署平台**：後端 Render（免費會休眠）或其他（Cloud Run / Railway）；是否付費
3. **Demo 範圍**：要展示哪幾個步驟／機器人；監控總覽頁是否列入 Demo
4. **Demo 帳號與資料**：用新的 Supabase 專案，還是沿用開發用的
5. `require_teacher_review` 是否要擋下一步解鎖
6. `/docs`（Swagger）正式環境要不要關
7. **XR 整合要不要列入 11/16 Demo**：要的話，XR 端也要在 11/12 前上雲並給網址
