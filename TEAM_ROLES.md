# 專案團隊三人分工表 (Team Roles & Responsibilities)

本專案採用前後端分離 (Next.js + FastAPI) 與微服務架構概念，將「資料庫層」與「AI 邏輯層」獨立拆開。團隊三人分工如下：

---

## 👩‍💻 成員一：前端工程師 (Frontend Engineer)
**職責**：專注於使用者介面與操作體驗，不碰觸複雜的伺服器與資料庫底層邏輯。
**使用技術**：Next.js, React, Tailwind CSS, shadcn/ui

**核心任務清單**：
- **UI 介面開發**：切出「教授管理後台」與「學生聊天大廳」的畫面。
- **登入畫面與導向**：實作登入按鈕頁面，並接上驗證邏輯。
- **對話介面 (Chat UI)**：實作聊天室對話框，需支援 Markdown 語法渲染，並負責接收處理從後端傳來的 SSE (Server-Sent Events) 打字機文字串流。
- **資料綁定**：串接資料庫提供的 API 或 AI 後端，將機器人列表、歷史對話紀錄、與統計數據呈現在畫面上。

---

## 🗄️ 成員二：資料庫與驗證工程師 (Database & Auth Engineer)
**職責**：負責系統的基底建設，包含建置資料庫、設定身分驗證，並提供「純資料操作」的 API 介面供其他兩人呼叫。
**使用技術**：Supabase (PostgreSQL, Auth), 或是負責編寫基礎的 CRUD API 路由

**核心任務清單**：
- **身分驗證 (Auth) 建置**：實作 Google OAuth 登入驗證機制，並確保 Token 的安全性管理。
- **資料庫建置與維護**：設計並執行 `supabase/schema.sql`，建立所有關聯資料表 (Users, Bots, Messages 等)。
- **提供資料庫 API**：設計並開放資料存取 API，例如：讓前端能撈取機器人列表，讓 AI 後端能讀寫歷史對話紀錄。
- **資料庫安全防護**：實作 Supabase RLS (Row Level Security)，確保無論是誰來呼叫 API，資料讀寫都受到權限控管。

---

## 🧠 成員三：後端 AI 應用工程師 (Backend / AI Engineer)
**職責**：不處理資料庫底層怎麼儲存的，專心寫 Python 處理 AI 大腦邏輯。負責串接 LLM、開發 Agent 功能，並在過程中「實際呼叫成員二提供的資料庫 API」來取得或寫入資料。
**使用技術**：Python FastAPI, Google Gemini API, LangChain (或其他 AI 開發框架)

**核心任務清單**：
- **LLM 串接與 Agent 實作**：實作與 Gemini API 的連線，將教授在 Google AI Studio 測好的 Prompt 與參數精準實裝到程式中，定義 AI Agent 的行為邏輯。
- **對話串流與資料整合**：當收到學生發問時，**先呼叫資料庫 API 撈取該學生的歷史對話**，打包發送給 Gemini 推論後，將回覆以 SSE 格式串流吐給前端，同時再**呼叫資料庫 API 把新的對話寫進去**。
- **RAG 知識庫處理**：接收上傳的教學文件，進行解析、文字切塊 (Chunking)，並呼叫資料庫 API 將向量存入 pgvector。
- **二次摘要機制**：實作摘要邏輯，呼叫 LLM 總結長篇對話後，再呼叫資料庫 API 將摘要寫回系統中作為長期記憶。
