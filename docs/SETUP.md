# 本地開發環境

> 從零把前端、後端、Supabase 跑起來。每一步做完都可以跑 `cd backend && py check_setup.py` 看進度。

---

## 0. 需要

| 項目 | 版本 |
|------|------|
| Node.js | 20 以上 |
| Python | 3.11 以上 |
| Supabase 專案 | 免費方案即可 |
| Gemini API Key | [Google AI Studio](https://aistudio.google.com) |

> 資料庫由成員二負責。**開新 Supabase 專案前先問他有沒有現成的**，兩個資料庫各存各的之後很難收。

---

## 1. Supabase

### 建專案

[supabase.com](https://supabase.com) 用 GitHub 登入 → **New project** → Region 選東京或新加坡 → 等 2～3 分鐘。

> 免費方案閒置 7 天會自動暫停，回來按 **Restore** 即可，資料還在。

### 拿金鑰

**Settings → API Keys**：

| 用途 | 金鑰 | 放哪 |
|------|------|------|
| 前端 | `sb_publishable_...` | `frontend/.env.local` |
| 後端 | `sb_secret_...` | `backend/.env`，**絕對不能外流、不能貼進聊天或 Git** |

舊版 `anon` / `service_role`（`eyJ...` 開頭）仍可用，但 Supabase 會在 2026 年底停用，直接用新版。

**Project URL** 長得像 `https://xxxxx.supabase.co`——**結尾到 `.co` 就停**，不要帶 `/rest/v1/`。

### 建表

SQL Editor → New query → 貼上 `supabase/schema.sql` 整份 → **Run** → 看到 `Success. No rows returned`。

> 全新資料庫**不用**跑 `001_llm_logic.sql`、`003_tutor_action.sql`，那是給已經建過舊版資料庫的人補欄位用的。
> 已經有資料庫的人：`001`、`003` 都要跑（可重複執行）；`py check_setup.py` 會列出還缺哪些欄位。

### 開放資料表權限（必跑）

接著跑 `supabase/migrations/002_api_grants.sql`。Supabase 自 2026-05-30 起新專案不再自動開放資料表給 API（舊專案 2026-10-30 起也一樣），沒跑會出現 `permission denied for table profiles (42501)`。重複跑沒關係；之後若重建資料表，要再跑一次。

### 指派制權限（必跑）

接著跑 `supabase/migrations/005_rls_policies.sql`（全新資料庫也要跑，要在 `seed.sql` 之前）。學生只看得到老師指派的模組與機器人，兩種指派方式擇一：

- **課程指派**：在 `course_modules` 加一筆（課程 ↔ 模組），該課程狀態為 active 的成員都看得到。
- **逐位核准**：`tool_permissions` 中該學生 `status = 'approved'`，且在 `start_date`～`expire_date` 期間內（`tool_id` 留空代表全部機器人）。

教師（owner／assistant）看得到全部。目前還沒有指派介面，要在 SQL Editor 或 Table Editor 操作。

> 後端用 service_role 連線，不受這些規則限制；後端的指派檢查會另外補上。

### 示範資料（建議）

同樣方式跑 `supabase/seed.sql`。會建立兩個測試帳號（密碼皆為 `Test1234!`）、一門課、一個案例、兩支附完整分層提示與錯誤分類範例的機器人。

> ⚠️ 裡面有公開的測試密碼，**只在開發環境跑**。

若 **Authentication → Users** 看不到那兩個帳號，或登入失敗：手動 **Add user** 建一樣的 email，再到 **Table Editor → profiles** 把教授那筆 `role` 改成 `owner`。

---

## 2. 後端

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS / Linux
pip install -r requirements.txt
```

`backend/.env`（複製 `.env.example` 修改）：

```env
SUPABASE_URL=https://xxxxx.supabase.co
SUPABASE_SERVICE_KEY=sb_secret_...
GEMINI_API_KEY=...
GEMINI_MODEL=gemini-3.5-flash   # 可省略；換模型只改這一行
ENVIRONMENT=development
FRONTEND_URL=http://localhost:3000
```

檢查、啟動：

```bash
py check_setup.py            # 依序檢查 env → 連線 → 15 張表 → 欄位 → 種子資料
uvicorn main:app --reload    # http://localhost:8000/docs
```

---

## 3. 前端

```bash
cd frontend
npm install
```

新建 `frontend/.env.local`：

```env
NEXT_PUBLIC_SUPABASE_URL=https://xxxxx.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=sb_publishable_...
```

> `NEXT_PUBLIC_` 開頭的變數會被打包進瀏覽器，**這裡只能放 publishable key**。

```bash
npm run dev    # http://localhost:3000
```

後端網址目前寫死 `http://127.0.0.1:8000`，後端換 port 要一起改 `student/practice/page.tsx` 與 `admin/tools/page.tsx`。

---

## 4. 驗收

| 動作 | 預期 |
|------|------|
| 開 http://localhost:8000/api/health | `{"status":"ok",...}` |
| 開 http://localhost:3000/student/modules | 未登入被導到 `/login` |
| `student.test@platform.edu` 登入 | 看到兩個步驟 |
| `prof.wu@platform.edu` 登入 | 看到兩支機器人 |
| 作答裡寫一組手機號碼送出 | 400 `PII_DETECTED` |
| `cd backend && py -m pytest tests/ -v` | 224 passed |

---

## 5. 常見問題

| 症狀 | 原因 |
|------|------|
| `check_setup.py` 說連線失敗 | 後端填成 publishable key（要 secret）；或 URL 帶了 `/rest/v1/` |
| `relation "public.profiles" does not exist` | `schema.sql` 沒跑成功 |
| `permission denied for table ... (42501)` | 沒跑 `002_api_grants.sql` |
| 學生模組頁顯示「目前沒有已發布的培訓模組」 | 沒跑 `005_rls_policies.sql`，或該學生沒被指派（`course_modules` / `tool_permissions`） |
| `seed.sql` 報 `duplicate key` | 跑過了。先 `DELETE FROM module_steps / learning_modules / ai_tools / tool_cases` 再重跑 |
| 登入一直被踢回 `/login` | `.env.local` 沒建，或改完沒重啟 `npm run dev` |
| 進 `/admin` 被踢到學生頁 | `profiles.role` 不是 owner / assistant |
| CORS 錯誤 | `FRONTEND_URL` 與瀏覽器網址不一致（`localhost` 和 `127.0.0.1` 是不同來源）|
| `ModuleNotFoundError: No module named 'core'` | `uvicorn` 要在 `backend/` 目錄下執行 |
| 評分回 `event: error` | 看後端 traceback；多半是 `GEMINI_API_KEY` 無效或 Rubric 是空陣列 |
| 樣式或路由怪怪的 | `Remove-Item -Recurse -Force .next` 後重跑 `npm run dev` |

---

## 6. 部署（尚未設定）

| 層 | 平台 | 環境變數 |
|----|------|---------|
| 前端 | Vercel | `NEXT_PUBLIC_SUPABASE_URL`、`NEXT_PUBLIC_SUPABASE_ANON_KEY`、`NEXT_PUBLIC_API_URL`（待建立） |
| 後端 | Render | `SUPABASE_URL`、`SUPABASE_SERVICE_KEY`、`GEMINI_API_KEY`、`GEMINI_MODEL`、`ENVIRONMENT=production`、`FRONTEND_URL` |

部署前必須先完成：網址改環境變數、API 身分驗證、開啟 RLS（KNOWN_GAPS #8 #9 #11）。
