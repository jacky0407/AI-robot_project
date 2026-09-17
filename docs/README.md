# 專案文件索引

> 本資料夾收錄「AI 支持之學前特殊教育與早期療育專業能力培訓平台」的全部技術文件。
> 最後更新：2026-09-17｜基準 commit：`62f57f4`（`main`）+ 第一批修正

---

## 📌 先讀這幾份

| 文件 | 什麼時候看 |
|------|-----------|
| [IMPLEMENTATION_STATUS.md](./IMPLEMENTATION_STATUS.md) | 想知道「現在做到哪裡、哪些是假資料、下一步做什麼」 |
| [ARCHITECTURE.md](./ARCHITECTURE.md) | 想知道「一次練習從按下送出到寫進資料庫，中間經過哪些檔案」 |
| [KNOWN_GAPS.md](./KNOWN_GAPS.md) | 接手開發前必讀：程式碼與資料庫 schema 對不上的地方（✅ 為已修正）|
| [CHANGELOG.md](./CHANGELOG.md) | 最近改了什麼、你需要手動做什麼 |

---

## 📚 全部文件

### 現況與規劃

- **[IMPLEMENTATION_STATUS.md](./IMPLEMENTATION_STATUS.md)** — 實作進度統整（功能逐項標記完成度）、Git 分支現況、建議的下一步
- **[KNOWN_GAPS.md](./KNOWN_GAPS.md)** — 已知落差與技術債清單（schema 欄位不符、寫死的 URL、未掛載的 router…）
- **[CHANGELOG.md](./CHANGELOG.md)** — 變更紀錄與後續手動步驟
- **[TEAM_ROLES.md](./TEAM_ROLES.md)** — 三人分工表與 P0 範圍定義

### 設計與規格

- **[ARCHITECTURE.md](./ARCHITECTURE.md)** — 系統架構、目錄結構、完整學習循環資料流、關鍵設計決策
- **[api.md](./api.md)** — API 規格書（目標規格，含尚未實作的端點）
- **[DATABASE.md](./DATABASE.md)** — 資料表逐表說明、關聯圖、RLS 現況

### 實作說明

- **[BACKEND.md](./BACKEND.md)** — FastAPI 後端：三層 Agent 架構、各 router 實作狀態、SSE 事件表
- **[FRONTEND.md](./FRONTEND.md)** — Next.js 前端：路由表、各頁面實作狀態、認證與角色導流

### 開發流程

- **[DEVELOPMENT.md](./DEVELOPMENT.md)** — 從零把整套跑起來（前端 + 後端 + Supabase）
- **[TESTING.md](./TESTING.md)** — 單元測試現況（96 個測試）與撰寫規範
- **[CONTRIBUTING.md](./CONTRIBUTING.md)** — Git 分支策略、Commit 格式、PR 規則

---

## 文件與程式碼的關係

`api.md` 是**目標規格**，描述第一版完成時應有的樣子；`BACKEND.md`、`FRONTEND.md`、`DATABASE.md` 描述**現在程式碼實際長什麼樣**。兩者不一致時，以程式碼為準，並把差異記到 `KNOWN_GAPS.md`。

改程式碼時請同步更新對應文件，特別是：

- 新增／修改 API 端點 → `api.md` + `BACKEND.md`
- 改動 `supabase/schema.sql` → `DATABASE.md`
- 新增前端路由 → `FRONTEND.md`
- 改動 `backend/core/` 業務邏輯 → 同步補測試（見 `TESTING.md`）
