# 系統架構

> 本文描述目前程式碼實際的架構。目標規格請看 [api.md](./api.md)。
> 基準 commit `62f57f4` + 第一批修正（見 [CHANGELOG.md](./CHANGELOG.md)）。

---

## 1. 高層架構

```
                    瀏覽器
                      │
                      ▼
        ┌─────────────────────────────┐
        │  Next.js 16 (App Router)    │   Vercel
        │  frontend/                  │
        └──────┬───────────────┬──────┘
               │               │
    anon key   │               │  fetch (JSON / SSE)
    直接讀寫    │               │
               ▼               ▼
      ┌────────────────┐  ┌──────────────────────┐
      │   Supabase     │◄─┤  FastAPI             │  Render
      │  PostgreSQL    │  │  backend/            │
      │  + Auth        │  │  (Service Role Key)  │
      └────────────────┘  └──────────┬───────────┘
                                     │
                                     ▼
                          ┌─────────────────────┐
                          │  Google Gemini API  │
                          │  gemini-2.5-flash   │
                          └─────────────────────┘
```

**兩條資料路徑並存**，這是目前架構最需要注意的一點：

| 路徑 | 用途 | 金鑰 |
|------|------|------|
| 前端 → Supabase（直連） | 登入、讀 `module_steps` / `ai_tools` / `tool_cases`、新增機器人 | `NEXT_PUBLIC_SUPABASE_ANON_KEY` |
| 前端 → FastAPI → Supabase | 提交作答、AI 評分、刪除機器人、教師複核 | `SUPABASE_SERVICE_KEY`（僅後端） |

> ⚠️ 前端直連路徑目前沒有 RLS 保護，等同於 anon 角色可任意讀寫這些表。見 [KNOWN_GAPS.md](./KNOWN_GAPS.md)。

---

## 2. 目錄結構（實際）

```text
Summer_project/
├── AGENTS.md                      # AI 助手協作規範
├── README.md                      # 專案總覽與需求說明
├── .gitignore
│
├── backend/                       # Python FastAPI
│   ├── main.py                    # 進入點，掛 router + CORS
│   ├── requirements.txt
│   ├── .env.example
│   ├── api/
│   │   ├── practice.py            # 練習、AI 評分、SSE、一致性檢核
│   │   ├── privacy.py             # 個資偵測
│   │   ├── review.py              # 教師複核
│   │   └── teacher.py             # 機器人 CRUD（目前只有 list / delete）
│   ├── core/
│   │   ├── config.py              # pydantic-settings，get_settings()
│   │   ├── privacy.py             # PII 正規表達式偵測
│   │   └── ai/
│   │       ├── base.py            # BaseAIProvider 抽象 + EvaluationResult
│   │       ├── gemini_provider.py # Gemini 實作
│   │       ├── rubric_formatter.py
│   │       ├── agents/
│   │       │   ├── evaluator_agent.py   # Phase 1：逐構面評分
│   │       │   ├── tutor_agent.py       # Phase 2：教學決策
│   │       │   └── coherence_agent.py   # Phase 3：跨步驟檢核
│   │       └── tools/
│   │           └── evaluation_tools.py  # LangChain tool 定義（尚未掛載）
│   ├── database/
│   │   └── client.py              # get_supabase()，Service Role Key
│   └── tests/                     # 96 個單元測試
│
├── frontend/                      # Next.js 16 + React 19 + Tailwind 4
│   ├── package.json
│   ├── AGENTS.md / CLAUDE.md
│   ├── README.md                  # 前端本地架設指引
│   └── src/
│       ├── middleware.ts          # 登入守門 + 角色守門
│       ├── utils/supabase/
│       │   ├── client.ts          # Browser Client
│       │   └── server.ts          # Server Component Client
│       └── app/
│           ├── layout.tsx
│           ├── page.tsx           # 早期純前端原型（未串後端）
│           ├── login/page.tsx
│           ├── student/
│           │   ├── modules/page.tsx
│           │   └── practice/page.tsx
│           └── admin/tools/
│               ├── page.tsx
│               └── new/page.tsx
│
├── supabase/
│   ├── schema.sql                 # 14 張表 + 觸發器
│   ├── seed.sql                   # 示範資料
│   ├── practice_repo.py           # ⚠️ 已停用的早期資料存取層
│   └── README.md
│
└── docs/                          # 本資料夾
```

---

## 3. 核心資料流：學生提交一次作答

以下是 `POST /api/practice/sessions/{session_id}/submit` 的完整路徑，
檔案與行為都對應 `backend/api/practice.py`。

```
[1] 前端 /student/practice
        │ POST { user_id, module_id, step_id, content }
        ▼
[2] core/privacy.py :: detect_pii()
        │ 命中 → 400 PII_DETECTED，流程中止
        ▼
[3] 讀 module_steps JOIN ai_tools
        │ system_prompt、rubric_criteria ← ai_tools
        │ pass_score                     ← module_steps（不是 ai_tools）
        │ normalize_rubric() 把 max_score 轉成 weight，展開 4 級量表
        ▼
[4] 建立 StudentContext
        │ attempt_number ← step_attempts 最大值 + 1
        │ last_score_percent ← 上次 ai_evaluations.total_score（舊資料才換算）
        │ hints_used_count ← 上次 attempt 的 prompt_logs 筆數（無前次則為 0）
        ▼
[5] INSERT step_attempts (status = 'submitted')
        │ 取得 attempt_id
        ▼
[6] SSE 開始：event: score_start
        ▼
[7] TutorAgent.process_stream()
        │
        ├─ decide_action(ctx) → 四選一
        │    event: tutor_decision
        │
        ├─ EVALUATE  ──► EvaluatorAgent.evaluate_stream()
        │                  ├ event: analysis_start
        │                  ├ event: dimension_score × N   （逐構面呼叫 Gemini）
        │                  └ event: score_complete        （加權總分 + 回饋 + 信心值）
        │
        ├─ ENCOURAGE ──► event: encouragement，接著同上評分
        │
        ├─ GIVE_HINT ──► event: hint（不評分）
        │                  └ 同步 INSERT prompt_logs
        │
        └─ ESCALATE  ──► event: escalation（不評分）
        ▼
[8] 若有 score_complete：
        ├ INSERT ai_evaluations
        └ UPDATE step_attempts.status → 'passed' | 'revision_required'
```

### TutorAgent 決策表

| 條件 | 行動 |
|------|------|
| 第一次作答，或無上次分數 | `EVALUATE` |
| 上次分數 ≥ 門檻 | `EVALUATE` |
| 距門檻 < 10% 且嘗試 ≤ 3 次 | `ENCOURAGE`（先鼓勵，仍評分） |
| 分數 60%～門檻，且未用過提示 | `GIVE_HINT`（不評分） |
| 分數 60%～門檻，已用過提示 | `EVALUATE` |
| 分數 < 60%，嘗試 ≤ 3 次 | `GIVE_HINT` |
| 分數 < 60%，嘗試 > 3 次 | `ESCALATE` |

此決策為**純邏輯，不呼叫 API**，因此可完整單元測試（`tests/test_tutor_agent.py`，12 個測試）。

---

## 4. 三層 Agent 的職責切分

```
TutorAgent          ← 練習流程的唯一入口，決定「這次該做什麼」
  └─ EvaluatorAgent ← 決定「這份作答值幾分」，逐構面呼叫 LLM
       └─ Gemini    ← 實際推理

CoherenceAgent      ← 獨立入口，模組完成後才呼叫，跨步驟檢查一致性
```

**為什麼 EvaluatorAgent 要逐構面評分？**
一次把整份 Rubric 丟給 LLM，各構面會互相干擾、分數趨中。拆成一個構面一次呼叫後，每次 prompt 只包含該構面的等級描述，AI 聚焦度高、也更容易引用原文佐證。代價是 N 個構面就有 N+1 次 API 呼叫。

**分數怎麼算？**
每個構面用固定 4 級量表評分，再依教授設定的配分加權：

```
加權總分 = Σ(score_i / 4 × weight_i) / Σ(weight_i) × 100
```

所以 `score_complete.total_score` 是 0–100 的百分制得分，直接與 `module_steps.pass_score` 比較。
`ai_tools.rubric_criteria` 的 `max_score` 是**配分**不是量表上限——這是 `normalize_rubric()` 存在的原因。

**CoherenceAgent 的三條預設規則**（`DEFAULT_COHERENCE_CHECKS`）：

| 規則 | 來源步驟 | 目標步驟 |
|------|---------|---------|
| 優勢與目標對應 | 1（學生優勢描述） | 3（IEP 長期目標） |
| 需求與策略對應 | 2（特殊需求清單） | 5（教學策略說明） |
| 目標與評量對應 | 3（IEP 目標） | 7（評量方式與標準） |

規則可在建構 `CoherenceAgent(checks=[...])` 時覆寫，但目前尚未接到資料庫，教授無法自訂。

---

## 5. 關鍵設計決策

| 決策 | 理由 |
|------|------|
| **SSE 而非 WebSocket** | 評分是單向推送，SSE 更簡單、免額外基礎設施，Render 免費方案也支援 |
| **AI 初評與教師判定分表存放** | `ai_evaluations` 與 `teacher_reviews` 永不互相覆蓋，研究資料需保留 AI 原始判斷 |
| **`BaseAIProvider` 抽象層** | 換 Gemini → OpenAI 只需新增一個 Provider class，教學邏輯不動 |
| **Gemini Client 懶惰初始化** | 沒有 API Key 時後端仍能啟動，方便前端開發與跑測試 |
| **Service Role Key 只在後端** | 後端統一以 service role 操作，繞過 RLS；前端只拿 anon key |
| **`step_attempts` 支援多版本** | `attempt_number` 遞增，保留每次作答原文，供版本比對與研究分析 |
| **個資偵測放在提交流程最前面** | 命中直接 400 中止，不消耗 AI quota，也不會把個資送出平台 |
| **配分與量表分離** | Rubric 的配分是權重，評分一律 4 級量表，兩者在 `normalize_rubric()` 交會 |
| **AI 初評不被任何動作改寫** | 教師判定寫進 `teacher_reviews`；「是否已複核」由有無對應 review 判斷，不在 `ai_evaluations` 加旗標 |

---

## 6. 技術選型

| 層次 | 技術 | 版本 |
|------|------|------|
| 前端框架 | Next.js（App Router） | 16.3.0 |
| 前端 UI | React 19.2.8、Tailwind CSS 4 | — |
| 前端 Supabase | `@supabase/ssr` 0.12.5、`@supabase/supabase-js` 2.114 | — |
| 後端框架 | FastAPI | ≥ 0.111 |
| 後端設定 | pydantic-settings | ≥ 2.3 |
| AI SDK | `google-genai`、`langchain-google-genai` | ≥ 1.65 / ≥ 2.0 |
| 串流 | `sse-starlette`（實際使用 FastAPI `StreamingResponse`） | ≥ 2.1 |
| 資料庫 | Supabase PostgreSQL + pgvector | — |
| 測試 | pytest + `unittest.mock` | — |

> 註：`requirements.txt` 列了 `sse-starlette`，但程式碼實際用的是 FastAPI 內建的 `StreamingResponse` 手動組 SSE 字串（`_sse_event()`）。
