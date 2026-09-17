# 本地開發環境建置

> 從零把整套（前端 + 後端 + Supabase）跑起來。
> 前端單獨開發的簡版說明見 `frontend/README.md`。

---

## 0. 前提條件

| 項目 | 版本 | 備註 |
|------|------|------|
| Node.js | 20.x 以上 | Next.js 16 需要 |
| Python | 3.11 以上 | |
| Git | — | |
| Supabase 專案 | 免費方案即可 | https://supabase.com |
| Gemini API Key | — | https://aistudio.google.com |

---

## 1. Clone 專案

```bash
git clone https://github.com/jacky0407/AI-robot_project.git
cd AI-robot_project
```

---

## 2. 建立 Supabase 專案與資料庫

1. 在 [supabase.com](https://supabase.com) 建立新專案，記下 **Project URL**
2. Dashboard → **Settings → API**，取得：
   - `anon public` key → 給前端
   - `service_role` key → 給後端（**絕不放前端**）
3. Dashboard → **SQL Editor** → New query
4. 貼上 `supabase/schema.sql` 全部內容並執行
5. （可選）執行 `supabase/seed.sql` 建立示範資料

> ⚠️ `seed.sql` **只在本地／示範環境執行**，裡面有公開的測試帳號密碼。
>
> ⚠️ 若之前跑過舊版，UUID 已全部更換，請先清掉舊資料再重跑：
> ```sql
> DELETE FROM public.module_steps;
> DELETE FROM public.learning_modules;
> DELETE FROM public.ai_tools;
> DELETE FROM public.tool_cases;
> ```

---

## 3. 後端（FastAPI）

```bash
cd backend

# 建立虛擬環境
python -m venv .venv
.venv\Scripts\activate          # Windows PowerShell
# source .venv/bin/activate     # macOS / Linux

pip install -r requirements.txt
```

建立 `backend/.env`（複製 `.env.example` 後填值）：

```env
SUPABASE_URL=https://xxxxx.supabase.co
SUPABASE_SERVICE_KEY=eyJ...          # service_role key
GEMINI_API_KEY=AIzaSy...
ENVIRONMENT=development
FRONTEND_URL=http://localhost:3000
```

啟動：

```bash
uvicorn main:app --reload
```

| 網址 | 用途 |
|------|------|
| http://localhost:8000 | 版本資訊 |
| http://localhost:8000/api/health | 健康檢查 |
| http://localhost:8000/docs | Swagger UI（可直接測試端點） |

> 💡 沒填 `GEMINI_API_KEY` 也能啟動（Gemini Client 是懶惰初始化），
> 只有真正觸發評分時才會報 `GEMINI_API_KEY 未設定`。

---

## 4. 前端（Next.js）

**另開一個終端機**：

```bash
cd frontend
npm install
```

建立 `frontend/.env.local`：

```env
NEXT_PUBLIC_SUPABASE_URL=https://xxxxx.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=eyJ...     # anon public key
```

啟動：

```bash
npm run dev      # http://localhost:3000
```

> ⚠️ 後端網址目前寫死在 `student/practice/page.tsx` 與 `admin/tools/page.tsx` 中的
> `http://127.0.0.1:8000`。若後端改用其他 port，需一併修改這兩個檔案。

---

## 5. 驗證整套跑通

| # | 動作 | 預期結果 |
|---|------|---------|
| 1 | 開 http://localhost:8000/api/health | `{"status":"ok","environment":"development"}` |
| 2 | 開 http://localhost:3000/student/modules | 未登入 → 被導到 `/login` |
| 3 | 用 `student.test@platform.edu` / `Test1234!` 登入 | 導向 `/student/modules`，列出兩個步驟 |
| 4 | 用 `prof.wu@platform.edu` / `Test1234!` 登入 | 導向 `/admin/tools`，列出兩支機器人 |
| 5 | 學生身分進入練習室、輸入作答、送出 | 後端終端機出現 TutorAgent 決策 log，畫面顯示評分 JSON |
| 6 | 作答中故意寫一組手機號碼 | 回傳 400，`code: PII_DETECTED` |

---

## 6. 跑測試

```bash
cd backend
py -m pytest tests/ -v          # Windows
# python -m pytest tests/ -v    # macOS / Linux
```

預期：`96 passed`。測試全部 mock 外部服務，**不會消耗 Gemini quota、不會碰資料庫**。

詳見 [TESTING.md](./TESTING.md)。

---

## 7. 常見問題

### 後端啟動報 `ModuleNotFoundError: No module named 'core'`

`uvicorn` 必須在 `backend/` 目錄下執行（`main.py` 用的是相對於 backend 的絕對 import）。

### 前端 fetch 後端被 CORS 擋

檢查 `backend/.env` 的 `FRONTEND_URL` 是否與瀏覽器網址完全一致。
`main.py` 的 CORS 白名單只放 `settings.frontend_url` 這一個來源，
`http://localhost:3000` 與 `http://127.0.0.1:3000` 是**不同來源**。

### 登入後一直被踢回 `/login`

- 確認 `.env.local` 的 anon key 正確
- 確認 `profiles` 表中有該使用者的 row（觸發器應自動建立；若手動在 Dashboard 建帳號前 schema 還沒跑，就不會有）

### 進 `/admin` 被踢到 `/student/modules`

該帳號的 `profiles.role` 不是 `owner` 或 `assistant`。到 Dashboard 的 Table Editor 改 `profiles.role`。

### 評分失敗、SSE 回傳 `event: error`

看後端終端機的 traceback。常見原因：

- `GEMINI_API_KEY` 未設定或無效
- `ai_tools.rubric_criteria` 是空陣列（教授還沒設定 Rubric）
- Gemini 回傳非 JSON——Agent 會降級為「評分失敗，請教師複核」而非中斷

### 登入成功但看不到步驟／機器人

`seed.sql` 沒跑，或跑的是舊版（UUID 已更換）。見上面第 2 節。

### 前端樣式或路由異常

```powershell
Remove-Item -Recurse -Force .next
npm run dev
```

---

## 8. 部署（規劃中，尚未設定）

| 層 | 平台 | 需要設定的環境變數 |
|----|------|------------------|
| 前端 | Vercel | `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`, `NEXT_PUBLIC_API_URL`（待建立） |
| 後端 | Render | `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `GEMINI_API_KEY`, `ENVIRONMENT=production`, `FRONTEND_URL` |
| 資料庫 | Supabase | — |

部署前必須先完成：把寫死的 `127.0.0.1:8000` 改成環境變數、補 API 身分驗證、開啟 RLS。
見 [KNOWN_GAPS.md](./KNOWN_GAPS.md) 第 8、9、11 節。
