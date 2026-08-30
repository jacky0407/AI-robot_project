# AI 支持之學前特殊教育與早期療育專業能力培訓平台

> 平台主持人：吳佩芳教授｜第一版目標交付：2027 年 1 月底

本平台為以 AI 為核心的**專業能力培訓系統**，設計目標是讓學習者（師培生、在職教師）能在安全、可反覆的虛構情境中練習將理論轉化為實務操作，同時為教授提供完整的學習歷程分析與研究資料。

平台並非一般線上教材網站或聊天機器人，而是以「**閱讀案例 → 獨立作答 → AI 依規準分析 → 分層提示 → 學生修正 → 教師複核 → 研究匯出**」為完整學習循環的培訓系統。

---

## 💡 核心概念：每堂課有多支專屬 AI 家教機器人

```
一門課程（例：IEP 撰寫能力訓練）
    │
    ├── 🤖 機器人一：現況描述練習家教
    ├── 🤖 機器人二：年度目標撰寫家教
    ├── 🤖 機器人三：短期目標設計家教
    ├── 🤖 機器人四：去標籤化用語練習家教
    ├── ...（依教授規劃，約 10 支）
    └── 🤖 機器人N：IEP 整體一致性檢核機器人
```

**每支機器人各自擁有：**
- 教授自訂的專屬技能與角色設定（System Prompt）
- 教授撰寫的 Rubric 評量規準（各構面標準與分數）
- 專屬的練習案例（虛構個案情境文本）
- 客製化的分層提示設計（引導問題 → 方向提示 → 局部範例）

**學生的使用方式：**
加入課程後，依序使用各支機器人完成練習，每支機器人針對單一能力深度練習，AI 依教授撰寫的 Rubric 即時評分並引導學生修正，直到達標才解鎖下一關。

**教授的後台工具（類 GPTs 介面）：**
教授透過「AI 工具建構器」（介面設計類似 OpenAI GPTs Builder，多分頁獨立設定）自行新增、調整機器人，**不需工程師介入即可完成日常教學內容的更新**。後台同時提供教授完整的對答狀況監控，可查看任一學生與任一機器人的對話紀錄、AI 評分結果與學習歷程統計。

> **工程師的職責**：維護 AI 串聯邏輯、平台基礎架構、安全機制與部署，不負責教學內容（Rubric、提示、案例）的撰寫，那是教授的工作。

---

## 🎯 第一版核心模組

**學前 IEP 逐步撰寫模組**（第一個完整垂直切片），驗證上述完整學習循環，包含：
- 一門課程含約 10 支 AI 家教機器人，各有專屬技能與 Rubric
- 多步驟學習路徑（現況描述 → 年度目標 → 短期目標 → 一致性檢核）
- AI 依教授撰寫的 Rubric 進行證據式評分與分層提示
- 教授後台可查看所有學生與機器人的對答狀況及 AI 評分結果
- 教師複核與最終判定
- 學習歷程完整紀錄與 CSV 匯出供研究使用

---

## 🏗️ 系統架構

本專案採 **Monorepo**，前後端分離部署：

```
平台使用者 (瀏覽器)
       │
       ▼
  [前端 Next.js]          部署於 Vercel
       │  HTTPS + HttpOnly Cookie
       ▼
  [後端 Python FastAPI]   部署於 Render
       │
       ├── Supabase PostgreSQL (關聯式資料 + pgvector)
       ├── Supabase Storage (上傳文件)
       └── AI API 抽象層 (Gemini / OpenAI 可替換)
```

**技術選型：**

| 層次 | 技術 | 部署 |
|------|------|------|
| 前端 | Next.js (TypeScript), Tailwind CSS, shadcn/ui | Vercel (免費) |
| 後端 | Python FastAPI, LangChain | Render (免費方案) |
| 資料庫 | Supabase PostgreSQL + pgvector | Supabase (BaaS) |
| 檔案儲存 | Supabase Storage (私有 Bucket) | Supabase |
| AI 服務 | Google Gemini API（可替換 OpenAI） | API Key 僅存後端 |

> **資安原則**：AI API Key 僅存於後端環境變數，絕不置於前端或版本控制系統。學生瀏覽器僅連線本平台後端，不直接呼叫任何 AI 服務。

---

## 📂 專案目錄結構

```text
Summer_project/
├── frontend/                   # 前端（Next.js）
│   ├── src/
│   │   └── app/
│   │       ├── (auth)/         # 登入、註冊頁面
│   │       ├── (student)/      # 學生：大廳、練習介面
│   │       └── (admin)/        # 教授/助理：工具建構、複核後台
│   └── components/             # 共用 UI 元件
│
├── backend/                    # 後端（Python FastAPI）
│   ├── main.py                 # API 進入點
│   ├── api/                    # 路由模組
│   │   ├── auth.py             # 登入、登出、驗證
│   │   ├── tools.py            # AI 工具 CRUD
│   │   ├── courses.py          # 課程與邀請碼管理
│   │   ├── practice.py         # 練習提交與 AI 評分
│   │   ├── review.py           # 教師複核
│   │   └── export.py           # 資料匯出
│   ├── core/
│   │   ├── ai/                 # AI 評分引擎（可替換模型抽象層）
│   │   └── privacy.py          # 敏感個資偵測
│   ├── database/               # Supabase 連線與查詢
│   └── requirements.txt
│
├── supabase/
│   └── schema.sql              # 資料庫建置與 RLS 安全規則
│
├── docs/
│   └── api.md                  # API 規格書（本文件）
│
├── README.md                   # 本文件
├── CONTRIBUTING.md             # Git 協作規範
└── TEAM_ROLES.md               # 三人分工表
```

---

## 👥 使用者角色

| 角色 | 說明 |
|------|------|
| **平台主持人（教授）** | 建立 AI 工具、Rubric、課程、案例；管理所有權限；查看完整分析；匯出研究資料 |
| **助理** | 由主持人授權特定功能（如批次核准申請、複核評分）|
| **學習者（學生）** | 加入課程、練習作答、查看 AI 回饋、使用分層提示 |

**學習模式：**
- **正式課程模式**：邀請碼加入，教師最終判定成績
- **自主探索模式**：自行申請，直接取得 AI 回饋（標示非正式評分）

---

## 🚀 開發環境設定

### 前提條件
- Node.js 18+
- Python 3.11+
- 一個 Supabase 專案（免費方案即可）
- Gemini API Key（向 Google AI Studio 申請）

### 1. Clone 專案

```bash
git clone https://github.com/your-org/Summer_project.git
cd Summer_project
```

### 2. 前端設定

```bash
cd frontend
npm install
cp .env.example .env.local
# 編輯 .env.local，填入 Supabase URL 等前端公開設定
npm run dev
# 前端運行於 http://localhost:3000
```

### 3. 後端設定

```bash
cd backend

# 建立虛擬環境
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS/Linux

pip install -r requirements.txt

# 複製並填入環境變數（絕對不要 commit .env！）
cp .env.example .env
# 在 .env 填入：
# SUPABASE_URL, SUPABASE_SERVICE_KEY, GEMINI_API_KEY, ENCRYPTION_KEY 等

uvicorn main:app --reload
# 後端運行於 http://localhost:8000
# API 測試文件於 http://localhost:8000/docs
```

### 4. 資料庫初始化

在 Supabase 儀表板的 SQL Editor 執行：

```bash
supabase/schema.sql
```

---

## 🔐 資安規範

| 規範 | 說明 |
|------|------|
| API Key 管理 | 所有 Secret 僅存後端 `.env`，`.env` 已加入 `.gitignore` |
| Cookie 安全 | 登入 Token 以 `HttpOnly; Secure; SameSite=Lax` Cookie 儲存 |
| 資料隔離 | Supabase RLS 開啟，後端以 Service Role Key 統一操作 |
| 個資保護 | 提交前自動偵測疑似個資（身分證、電話、Email）並警示 |
| 傳輸加密 | 正式環境強制 HTTPS（Vercel + Render 均自動提供） |
| SQL 防護 | 全程使用 Supabase SDK 參數化查詢，禁止手動拼接 SQL |

---

## 🤝 協作規範

請在開始開發前詳細閱讀：
- [CONTRIBUTING.md](./CONTRIBUTING.md)：Git 分支策略、Commit 格式規範、PR 規則
- [TEAM_ROLES.md](./TEAM_ROLES.md)：三人分工與負責模組說明
- [docs/api.md](./docs/api.md)：前後端 API 規格書

> **重要**：所有新功能必須開新分支（`feature/xxx`），嚴禁直接 push 至 `main`。所有變更須透過 Pull Request 並自行確認 Diff 無誤後才可 Merge。