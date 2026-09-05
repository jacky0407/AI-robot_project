# 學前特教與早療 AI 能力培育基地 - 本地開發與架設指引

本專案為學前特教與早期療育領域之情境化 AI 培訓前端系統，提供雙身分分流入口、個資保護條款簽署、首發「學前 IEP 逐步撰寫」工作台雛形與教師管理後台。

---

## 1. 環境需求與預先安裝套件

### 系統環境需求
在架設本系統前，請確認本機已安裝以下環境：
- Node.js：v20.x 或以上版本
- 套件管理工具：npm（安裝 Node.js 時已內建）、pnpm 或 yarn

---

### 專案相依套件清單

#### 核心相依套件 (Dependencies)
- next：Next.js 核心應用程式框架
- react & react-dom：React 介面函式庫
- @supabase/supabase-js：Supabase 客戶端 SDK（用於後續帳號驗證與資料庫存取）
- @supabase/ssr：Next.js App Router 伺服器端認證相依套件

#### 開發與樣式套件 (DevDependencies)
- typescript：靜態型別支援
- @types/node、@types/react、@types/react-dom：TypeScript 型別定義檔
- tailwindcss：原子化 CSS 樣式框架
- postcss：CSS 後處理器
- autoprefixer：自動添加瀏覽器前綴

---

## 2. 本地網站架設與終端機操作

請開啟終端機（Terminal 或 PowerShell），依序執行下列步驟：

### 步驟 1：進入前端專案目錄
確認終端機路徑位於含有 package.json 的前端資料夾：

cd frontend

---

### 步驟 2：安裝所有依賴套件
執行以下指令，自動安裝專案所需的完整套件：

npm install

補充說明：若日後需要手動補裝 Supabase 串接套件，可執行：
npm install @supabase/supabase-js @supabase/ssr

---

### 步驟 3：配置環境變數檔（可選 / 後續串接用）
在 frontend 資料夾根目錄下建立名為 .env.local 的檔案，填入以下設定（本地純介面測試階段若未填寫亦不影響畫面載入）：

NEXT_PUBLIC_SUPABASE_URL=your_supabase_project_url
NEXT_PUBLIC_SUPABASE_ANON_KEY=your_supabase_anon_key

---

### 步驟 4：啟動本地開發伺服器
執行本機開發伺服器啟動指令：

npm run dev

終端機顯示以下資訊即代表啟動成功：
▲ Next.js 15.x.x
- Local:        http://localhost:3000
- Network:      http://...
✓ Ready in 1.5s

---

### 步驟 5：瀏覽與介面測試
1. 開啟瀏覽器並造訪：http://localhost:3000
2. 進入後可透過首頁畫面進行：
   - 身分通道切換：點擊「學生」或「教師 / 工作人員」卡片進入登入與註冊表單。
   - 開發者免驗證測試捷徑：直接點擊畫面頂部藍色按鈕（進入學生 IEP 工作區）或綠色按鈕（進入教師管理儀表板）進行功能預覽。

---

## 3. 常用終端機維護指令

- 停止伺服器：在執行中的終端機視窗按下 Ctrl + C，輸入 Y 後按 Enter 即可結束。
- 重新編譯快取（若遇到樣式或路由異常）：
  Remove-Item -Recurse -Force .next
  npm run dev