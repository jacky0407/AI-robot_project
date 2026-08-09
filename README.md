# 教授專屬 AI 聊天機器人輔助教學平台

這是一個專為學校教授設計的**智慧教學輔助解決方案**。系統結合了最新的生成式 AI 技術與 RAG（檢索增強生成），讓教授可以輕鬆建立專屬課程的 AI 助教機器人，並上傳專屬的課程教材（如 PDF、Word），為學生提供 24 小時不間斷的精準問答與學習陪伴。

## 🌟 系統核心特色

- **多機器人系統**：教授可針對不同課程（如：微積分、線性代數）建立各自獨立的 AI 機器人。
- **專屬知識庫 (RAG)**：每個機器人擁有獨立的向量知識庫。AI 只會根據該機器人專屬的文件進行回答，避免跨科目錯亂。
- **高標準資安防護**：
  - 採用純後端 HttpOnly Cookie 驗證，100% 免疫 XSS 攻擊。
  - 採用 SSE (Server-Sent Events) 技術串流 AI 回覆，安全且防火牆友善。
  - 資料庫實作 RLS (Row Level Security)，確保學生對話紀錄絕對隔離。
- **即時學習分析**：後台提供詳細的對話統計與歷史調閱，幫助教授隨時掌握學生的學習痛點。

## 🏗️ 技術架構

本專案採用**前後端完全分離**的 Monorepo 架構：

| 模組 | 使用技術 | 部署方案 |
|------|------|------|
| **前端 (Frontend)** | `Next.js` (React), `Tailwind CSS`, `shadcn/ui` | Vercel (免費方案) |
| **後端 (Backend)** | `Python FastAPI`, `LangChain` | Render (免費方案) |
| **資料庫 (DB)** | `Supabase` (PostgreSQL, `pgvector`, Storage) | Supabase (BaaS) |

## 📂 專案目錄結構

```text
Summer_project/
├── frontend/             # 前端專案 (Next.js)
│   ├── app/              # 學生聊天室與教授管理後台路由
│   └── components/       # 共用 UI 元件 (shadcn-ui)
├── backend/              # 後端專案 (Python FastAPI)
│   ├── main.py           # API 進入點
│   └── core/             # AI 邏輯與 RAG 文件向量化引擎
├── supabase/             # 資料庫設計與資安
│   ├── schema.sql        # 資料表建置與 RLS 規則
│   └── README.md
├── docs/                 # 技術規格與架構文件
│   └── architecture_and_api.md
└── CONTRIBUTING.md       # 團隊開發與 GitHub 協作規範
```

## 🚀 開發者指南 (Getting Started)

### 1. 前端環境設定 (Next.js)

請確保您的電腦已安裝 [Node.js](https://nodejs.org/)。

```bash
# 進入前端資料夾
cd frontend

# 安裝依賴套件
npm install

# 啟動本地開發伺服器
npm run dev
```
> 前端伺服器將預設運行於：`http://localhost:3000`

### 2. 後端環境設定 (Python FastAPI)

請確保您的電腦已安裝 Python 3.9+。建議使用虛擬環境 (venv)。

```bash
# 進入後端資料夾
cd backend

# 建立並啟動虛擬環境 (Windows)
python -m venv .venv
.venv\Scripts\activate

# 安裝依賴套件
pip install -r requirements.txt

# 啟動本地開發伺服器
uvicorn main:app --reload
```
> 後端伺服器將預設運行於：`http://localhost:8000`
> API 測試文件 (Swagger UI) 位於：`http://localhost:8000/docs`

## 🤝 貢獻與團隊協作規範

團隊成員在開發新功能前，**請務必詳細閱讀 [CONTRIBUTING.md](./CONTRIBUTING.md)**。
本專案嚴格限制直接推播至 `main` 分支，所有修改均須透過 Pull Request (PR) 並使用 Conventional Commits 慣例。