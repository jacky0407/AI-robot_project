# 前端實作說明（Next.js）

> 對應程式碼：`frontend/`｜commit `62f57f4`

---

## 1. 技術組成

| 項目 | 版本 |
|------|------|
| Next.js（App Router） | 16.3.0 |
| React / React DOM | 19.2.8 |
| Tailwind CSS | 4（透過 `@tailwindcss/postcss`） |
| `@supabase/ssr` | 0.12.5 |
| `@supabase/supabase-js` | 2.114.0 |
| TypeScript | 5 |

環境變數（`frontend/.env.local`）：

```
NEXT_PUBLIC_SUPABASE_URL=...
NEXT_PUBLIC_SUPABASE_ANON_KEY=...
```

> ⚠️ 後端網址目前**寫死**在頁面中（`http://127.0.0.1:8000`），尚未抽成 `NEXT_PUBLIC_API_URL`。

---

## 2. 路由表

| 路徑 | 檔案 | 角色 | 狀態 |
|------|------|------|------|
| `/` | `app/page.tsx` | 公開 | ⚠️ 早期原型，未串後端 |
| `/login` | `app/login/page.tsx` | 公開 | ✅ 可用 |
| `/student/modules` | `app/student/modules/page.tsx` | 學生 | ✅ 可用 |
| `/student/practice?step_id=&module_id=` | `app/student/practice/page.tsx` | 學生 | ⚠️ 未正確消費 SSE |
| `/admin/tools` | `app/admin/tools/page.tsx` | owner / assistant | ✅ 可用 |
| `/admin/tools/new` | `app/admin/tools/new/page.tsx` | owner / assistant | ✅ 可用 |

尚未建立：註冊、忘記密碼、課程加入、教師複核後台、機器人編輯頁、案例管理、提示設定。

---

## 3. Supabase 連線

### `src/utils/supabase/client.ts`

Client Component 用的 Browser Client（`createBrowserClient`）。目前所有頁面都是 `'use client'`，都走這條。

### `src/utils/supabase/server.ts`

Server Component 用的 Server Client，從 `next/headers` 的 `cookies()` 讀寫 session。
**目前尚無頁面使用**，僅 `middleware.ts` 走類似邏輯。

---

## 4. 認證與角色導流

### `src/middleware.ts`

```ts
export const config = { matcher: ['/admin/:path*', '/student/:path*'] }
```

兩道守門：

1. **未登入**且不在 `/login` → 導向 `/login`
2. **已登入且進入 `/admin`** → 查 `profiles.role`，非 `owner` / `assistant` → 導向 `/student/modules`

> 注意：`matcher` 不含 `/`，所以首頁原型（`page.tsx`）不受守門限制，任何人都能點進去看假的工作區。

### 登入流程（`/login`）

```
signInWithPassword(email, password)
   └─ 成功 → 查 profiles.role
        ├─ owner / assistant → /admin/tools
        └─ 其他             → /student/modules
```

錯誤分兩種訊息：「登入失敗：{message}」與「無法讀取使用者角色權限」。

`seed.sql` 建立的測試帳號（密碼欄位為空，需在 Supabase Dashboard 自行設定密碼）：

- `prof.wu@platform.edu` — role `owner`
- `student.test@platform.edu` — role `student`

---

## 5. 各頁面說明

### `/student/modules` — 模組步驟清單

- 直接以 anon key 查 `module_steps`，依 `step_order` 升冪
- 每張卡片顯示步驟序號、標題、`pass_score`
- 連結帶 `step_id` 與 `module_id` 到練習室

**限制**：查的是**全部** `module_steps`，未依課程或模組篩選；也沒有「解鎖狀態」——需求書要求「達標才解鎖下一關」，目前每一步都可直接進入。

### `/student/practice` — 練習室

用 `<Suspense>` 包住，因為使用了 `useSearchParams()`。

載入時：

- 查 `module_steps` JOIN `ai_tools`（顯示步驟標題與助教名稱）
- 查 `tool_cases` 取 **`.limit(1)` 的第一筆**（非依步驟指定案例 ⚠️）

提交時：

```ts
fetch('http://127.0.0.1:8000/api/practice/sessions/temp-session-id/submit', {
  method: 'POST',
  body: JSON.stringify({ step_id, module_id, content: answer, user_id: user.id }),
})
const textResponse = await res.text()   // ⚠️ 一次讀完，非串流
```

接著把回應切行、找 `data: ` 開頭的行解析，最後 `JSON.stringify` 原樣顯示在畫面上。

**三個問題：**

1. `res.text()` 會等整個串流結束才回來，完全失去「逐構面即時顯示」的體驗
2. session id 寫死成 `temp-session-id`，未先呼叫 `POST /api/practice/sessions`
3. 評分結果直接以 JSON 原文呈現，沒有構面分數卡片、沒有提示 UI

**建議改法**（供接手者參考）：

```ts
const res = await fetch(url, { method: 'POST', body: ... })
const reader = res.body!.getReader()
const decoder = new TextDecoder()
let buffer = ''
while (true) {
  const { done, value } = await reader.read()
  if (done) break
  buffer += decoder.decode(value, { stream: true })
  const blocks = buffer.split('\n\n')
  buffer = blocks.pop() ?? ''
  for (const block of blocks) {
    const event = block.match(/^event: (.+)$/m)?.[1]
    const data  = block.match(/^data: (.+)$/m)?.[1]
    if (event && data) handleEvent(event, JSON.parse(data))
  }
}
```

（不能用 `EventSource`，因為它只支援 GET。）

### `/admin/tools` — 機器人清單

- 以 anon key 查 `ai_tools`，依 `created_at` 降冪
- 顯示領域、發布狀態、版本號、目標能力、建立時間
- 刪除走後端 `DELETE http://127.0.0.1:8000/api/teacher/tools/{id}`（帶 `confirm()` 確認）

### `/admin/tools/new` — AI 工具建構器

目前是**單頁表單**（需求書期望的是類似 GPTs Builder 的多分頁介面）。

三個區塊：

1. **基本設定** — 機器人名稱、所屬領域（下拉：學前IEP / 家庭IFSP / 正向行為支持）、目標培訓能力
2. **角色定位與系統指令** — `role_instruction` 單行、`system_prompt` 多行
3. **Rubric 評量規準** — 可動態新增／刪除構面，即時顯示總配分

送出時**直接以 anon key `insert` 進 `ai_tools`**，`status` 固定 `'published'`、`version` 固定 `1`。

**缺少的分頁**（對照需求）：教學策略與分層提示設定、專屬案例管理、錯誤分類定義、成本上限、預覽測試。

### `/` — 早期原型（`app/page.tsx`）

約 22KB 的單檔 React 元件，用 inline style 寫成，**完全不串接後端或 Supabase**。包含：

- 雙身分入口卡片（學生 / 教師）
- 登入註冊表單（`handleSubmit` 只是切換畫面狀態）
- 學生工作區三欄式版面（案例 / 作答 / 回饋），九個步驟標籤
- 教師管理儀表板

**這是 PR #1 的視覺原型**，與 `/login` + `/student` + `/admin` 這條真實路線並存且職責重疊。建議盡早決定：改成純行銷首頁，或移除。

---

## 6. 樣式慣例

- `/login`、`/student/*`、`/admin/*` 使用 **Tailwind utility class**
- `/` 原型使用 **inline style 物件**

兩種風格並存。新頁面請一律使用 Tailwind。

`layout.tsx` 的 metadata 仍是 `create-next-app` 的預設值（`title: "Create Next App"`），應改成專案名稱。

---

## 7. 開發指令

```bash
cd frontend
npm install
npm run dev     # http://localhost:3000
npm run build
npm run lint
```

遇到樣式或路由異常時清快取：

```powershell
Remove-Item -Recurse -Force .next
npm run dev
```
