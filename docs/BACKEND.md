# 後端實作說明（FastAPI）

> 對應程式碼：`backend/`｜基準 commit `62f57f4` + 第一批修正（見 [CHANGELOG.md](./CHANGELOG.md)）

---

## 1. 進入點：`main.py`

```python
app = FastAPI(title="AI 專業能力培訓平台 API", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=[settings.frontend_url], allow_credentials=True, ...)

app.include_router(practice_router)   # /api/practice
app.include_router(privacy_router)    # /api/privacy
app.include_router(review_router)     # /api/review
app.include_router(teacher.router)    # /api/teacher
```

已預留但**檔案尚未存在**的 router（`main.py` 中為註解）：`auth`、`courses`、`tools`、`export`。

內建端點：

| 端點 | 用途 |
|------|------|
| `GET /` | 版本資訊 |
| `GET /api/health` | 健康檢查，回傳 `{"status": "ok", "environment": ...}` |
| `GET /docs` | FastAPI 自動產生的 Swagger UI |

---

## 2. 設定與連線

### `core/config.py`

用 `pydantic-settings` 讀 `backend/.env`，以 `@lru_cache` 包成單例：

| 變數 | 預設 | 說明 |
|------|------|------|
| `SUPABASE_URL` | `""` | Supabase 專案網址 |
| `SUPABASE_SERVICE_KEY` | `""` | Service Role Key，**絕不外流** |
| `GEMINI_API_KEY` | `""` | Google AI Studio 金鑰 |
| `ENVIRONMENT` | `development` | `development` / `production` |
| `FRONTEND_URL` | `http://localhost:3000` | CORS 白名單 |

### `database/client.py`

```python
@lru_cache()
def get_supabase() -> Client:
    return create_client(settings.supabase_url, settings.supabase_service_key)
```

所有後端資料存取都經過這個函式，統一使用 Service Role Key（繞過 RLS）。

---

## 3. AI 引擎（`core/ai/`）

### 3.1 抽象層 `base.py`

```python
@dataclass
class EvaluationResult:
    dimension_scores: list[dict]   # [{name, score, max_score, reason, evidence}]
    total_score: float
    max_total_score: float
    percentage: float
    overall_feedback: str
    confidence: float              # 0~1，AI 自評信心
    needs_teacher_review: bool     # confidence < 0.7 時為 True
```

`BaseAIProvider` 定義三個抽象方法：`evaluate()`、`evaluate_stream()`、`generate_hint()`。
**換 AI 廠商時只需新增一個子類別，教學邏輯與 API 層完全不動。**

### 3.2 `gemini_provider.py`

- 模型：`gemini-2.5-flash`
- `client` 為 property，**第一次存取時才建立**（懶惰初始化）——沒有 API Key 時後端仍可啟動
- `evaluate()`：單次呼叫，用 `response_mime_type="application/json"` 強制 JSON、`temperature=0.2`
- `evaluate_stream()`：取得完整結果後逐構面 yield（**模擬**串流，非真實 token 串流）
- `generate_hint()`：找出得分率最低的構面，據此生成 ≤100 字的引導問題
- `_parse_response()`：JSON 解析失敗會拋 `ValueError("AI 回傳格式錯誤，請稍後再試")`

> 現況：`practice.py` 走的是 `TutorAgent` → `EvaluatorAgent` 路線，`GeminiProvider` 目前主要由單元測試覆蓋，是向下相容的備援實作。

### 3.3 `rubric_formatter.py`

兩個職責。

**`normalize_rubric(rubric_criteria, pass_threshold_percent)`**
把資料庫 `ai_tools.rubric_criteria` 轉成 Agent 需要的結構：

```
[{"dimension": "客觀事實辨識", "max_score": 40, "description": "..."}]
          ↓
{"pass_threshold_percent": 70,
 "dimensions": [{"name": "客觀事實辨識", "weight": 40, "levels": [4級量表]}]}
```

`max_score` 是**配分（權重）**，不是量表上限。沒有自訂 `levels` 時，
由 `build_default_levels(description)` 展開成描述各不相同的 4 級量表
（常數 `DEFAULT_SCALE_MAX = 4`）。全部構面都沒配分時改為等權重。

**`format_rubric_to_text(rubric)`**
把標準結構轉成 Prompt 用的純文字：

```
【構面1：功能性描述（權重 30%）】
  4分：完整描述學生的優勢與需求，有具體行為觀察
  3分：描述大致完整，但缺乏部分細節
```

等級由高分到低分排列。

### 3.4 `agents/evaluator_agent.py` — Phase 1

多步驟評分，使用 `langchain_google_genai.ChatGoogleGenerativeAI`（`temperature=0.2`）。

| 方法 | 行為 |
|------|------|
| `evaluate()` | 逐構面評分 → 整合，回傳 `EvaluationResult` |
| `evaluate_stream()` | 同上，但每完成一構面就 yield 一次 SSE chunk |
| `_score_dimension()` | 單一構面評分。JSON 解析失敗時**降級**為 `score=1, reason="評分失敗，請教師複核"`，不中斷流程。權重一律由 Rubric 帶入，不採信 AI 回傳的值 |
| `_synthesize()` | 整合各構面，請 AI 生成整體回饋與 `confidence`；失敗時降級為 `confidence=0.6` |
| `compute_weighted_percentage()` | 模組層級函式。各構面先算得分率（`score / max_score`），再依 `weight` 加權平均 |

**計分方式**

```
構面          配分   得分    得分率
客觀事實辨識   40    2/4     0.50
推論與假設     30    4/4     1.00
缺漏資訊提問   30    3/4     0.75

加權總分 = (0.50×40 + 1.00×30 + 0.75×30) / 100 × 100 = 72.5
```

因此 `score_complete` 的 `total_score` 是**百分制得分**、`max_total_score` 固定 `100`，
剛好對得上 `module_steps.pass_score`。
沒有 `weight` 的舊路徑（`GeminiProvider`）會退回「總分 / 總滿分」，維持向下相容。

### 3.5 `agents/tutor_agent.py` — Phase 2

練習流程的決策入口。`decide_action()` 是純函式，決策表見 [ARCHITECTURE.md](./ARCHITECTURE.md#tutoragent-決策表)。

```python
@dataclass
class StudentContext:
    attempt_number: int
    last_score_percent: float | None
    hints_used_count: int
    pass_threshold: float
```

`process_stream()` 接受選填的 `hint_generator`（async callable）；未提供時使用 `_default_hint()` 的四層寫死提示。

### 3.6 `agents/coherence_agent.py` — Phase 3

對應需求書 BLD-007。`temperature=0.3`。

```python
@dataclass
class StepAnswer:      # 輸入
    step_order: int; step_title: str; content: str; passed: bool

@dataclass
class CoherenceIssue:  # 每條規則的結果
    check_name: str; is_ok: bool; problem: str; suggestion: str; go_to_step_order: int
```

只檢核「來源與目標步驟都有作答」的規則。單條規則 AI 呼叫失敗時降級為 `is_ok=False, problem="檢核失敗，建議人工複核"`。

### 3.7 `tools/evaluation_tools.py`

四個以 `@tool` 裝飾的 LangChain 工具定義：`analyze_answer_structure`、`score_single_dimension`、`extract_key_evidence`、`synthesize_feedback`。

> **目前狀態**：這些工具**尚未被任何 Agent 掛載使用**，Agent 走的是直接組 prompt 的路線。此檔案是為未來改用 Function Calling 預留的結構定義。

---

## 4. 個資偵測（`core/privacy.py`）

對應需求 PRI-002。六條正規表達式規則：

| 類型 | 說明 |
|------|------|
| `taiwan_id` | 台灣身分證字號 |
| `phone_tw` | 手機號碼 |
| `phone_landline` | 市話 |
| `email` | 電子郵件 |
| `address_tw` | 台灣地址片段（縣市區鄉鎮村里路街巷弄號樓連續 2 字以上） |
| `birth_date` | 民國年月日或 `YYYY/MM/DD` |

回傳 `PIIDetectionResult(has_risk, detected_types, warning_message)`。
警告訊息會列出中文類型名稱，並提醒「系統自動偵測不保證完整」。

> **規則只能加嚴，不得放寬**（見 `AGENTS.md`）。

---

## 5. API Router 逐一說明

### 5.1 `api/practice.py` — 練習與評分

| 端點 | 狀態 | 說明 |
|------|------|------|
| `POST /api/practice/sessions` | ⚠️ 半成品 | 讀 `ai_tools` 與 `tool_cases`，回傳一個**即時產生、未落庫**的 `session_id`。欄位已對齊 schema（`title` / `role_instruction` / `case_background`）|
| `POST /api/practice/sessions/{session_id}/submit` | ✅ | 核心流程，見下 |
| `GET /api/practice/sessions/{session_id}/hints` | ❌ 假資料 | 回傳寫死的第 1 層提示 |
| `GET /api/practice/sessions/{session_id}/history` | ✅ | 讀 `step_attempts` JOIN `ai_evaluations`，依 `attempt_number` 升冪 |
| `POST /api/practice/modules/{module_id}/coherence-check` | ✅ | 需 query `user_id`；少於 2 個步驟有作答時回 400 |

**`submit` 的 Request Body：**

```json
{
  "user_id": "uuid",
  "module_id": "uuid",
  "step_id": "uuid",
  "case_id": "uuid | null",
  "content": "學生作答原文",
  "version_note": ""
}
```

> ⚠️ `user_id` 由前端傳入，後端**沒有驗證**。見 [KNOWN_GAPS.md](./KNOWN_GAPS.md)。

**SSE 事件總表（`submit`）：**

| 事件 | 何時發出 | data 欄位 |
|------|---------|----------|
| `score_start` | 一律最先 | `session_id`, `attempt_id`, `attempt_number` |
| `tutor_decision` | 決策後 | `action`, `attempt_number`, `last_score_percent` |
| `analysis_start` | action = evaluate/encourage | `total_dimensions` |
| `dimension_score` | 每個構面評完 | `dimension`, `score`, `max_score`(=4), `weight`, `reason`, `evidence`, `progress` |
| `score_complete` | 評分結束 | `total_score`(0–100), `max_total_score`(=100), `percentage`, `passed`, `overall_feedback`, `confidence`, `needs_teacher_review`, `dimension_scores` |
| `encouragement` | action = encourage | `message` |
| `hint` | action = give_hint | `level`, `content`, `message` |
| `escalation` | action = escalate | `message`, `suggest_teacher_review` |
| `error` | 串流中發生例外 | `message`, `detail` |

**SSE 事件總表（`coherence-check`）：**

| 事件 | data 欄位 |
|------|----------|
| `coherence_start` | `total_checks`, `total_steps` |
| `coherence_check` | `check_name`, `is_ok`, `problem`, `suggestion`, `go_to_step_order`, `progress` |
| `coherence_complete` | `overall_coherent`, `passed_checks`, `failed_checks`, `strengths`, `issues`, `summary` |
| `error` | `message`, `detail` |

事件格式由 `_sse_event()` 手動組成：

```python
f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
```

回應標頭包含 `Cache-Control: no-cache` 與 `X-Accel-Buffering: no`（避免反向代理緩衝）。

**資料庫寫入時機：**

| 時機 | 動作 |
|------|------|
| 個資檢查通過後 | `INSERT step_attempts` (`status='submitted'`) |
| 收到 `hint` chunk | `INSERT prompt_logs` |
| 收到 `score_complete` | `INSERT ai_evaluations` + `UPDATE step_attempts.status` |

> `ai_evaluations.evidence_text` 目前存的是**學生作答前 200 字**（`body.content[:200]`），而非 AI 引用的佐證。
> AI 的佐證其實在 `dimension_scores[].evidence` 裡。
>
> `ai_evaluations.total_score` 寫入前以 `round()` 轉整數（欄位型別是 INT）。

### 5.2 `api/privacy.py`

| 端點 | 狀態 |
|------|------|
| `POST /api/privacy/detect` | ✅ 直接包裝 `detect_pii()`，回傳 `{data: {has_pii_risk, detected_types, warning}}` |

### 5.3 `api/teacher.py`

| 端點 | 狀態 | 說明 |
|------|------|------|
| `GET /api/teacher/tools` | ✅ | 回傳 `ai_tools` 全部資料（**注意：直接回陣列，未包 `{data: ...}`**） |
| `DELETE /api/teacher/tools/{tool_id}` | ✅ | 刪除，找不到回 404 |

尚未實作：建立、更新、版本控管、發布狀態切換。
（前端目前是用 Supabase 直連新增機器人，沒走這個 router。）

### 5.4 `api/review.py` — 教師複核

| 端點 | 狀態 | 說明 |
|------|------|------|
| `GET /api/review/pending` | ✅ | 列出「有 AI 初評、但無教師判定」的作答 |
| `GET /api/review/submissions/{attempt_id}` | ✅ | 讀作答 + AI 評分 + 提示紀錄 + 既有判定 |
| `POST /api/review/submissions/{attempt_id}/judge` | ✅ | 寫入 `teacher_reviews` |

**`judge` 的 Request Body：**

```json
{
  "teacher_id": "uuid",
  "decision": "accept_ai | modify | override | request_retry",
  "final_score": 85,
  "final_feedback": "...",
  "is_published": false
}
```

`decision` 會先驗證是否為四個合法值之一，不合法回 400 `INVALID_DECISION`。

設計要點：

- **不修改 `ai_evaluations`** — AI 初評永遠保留原始判斷
- 「待複核」以「`step_attempts` 有 `ai_evaluations` 但沒有 `teacher_reviews`」判斷
- 只有 `decision = request_retry` 時才把 `step_attempts.status` 改回 `revision_required`
- `pending` 支援 `tool_id` 與 `needs_attention` 篩選；**不支援 course_id**，因為 schema 中
  `learning_modules` 並未關聯到 `courses`

> ⚠️ `teacher_id` 仍由前端傳入，等身分驗證做完要改成從 JWT 取得。見 [KNOWN_GAPS.md](./KNOWN_GAPS.md) 第 8 節。

---

## 6. 錯誤處理慣例

- 業務錯誤用 `HTTPException`，`detail` 可為字串或 dict（個資偵測用 dict 帶 `code` / `message` / `detected_types`）
- SSE 串流內部的例外**不會**變成 HTTP 錯誤碼（標頭已送出），改以 `event: error` 推送，並 `logger.error(traceback)`
- Agent 內部的 JSON 解析失敗一律**降級而非中斷**，讓學生至少拿到部分回饋，並標記需教師複核

---

## 7. 新增一個 API Router 的步驟

1. 在 `backend/api/` 建立 `xxx.py`，用 `APIRouter(prefix="/api/xxx", tags=["xxx"])`
2. 資料存取一律透過 `from database.client import get_supabase`
3. 若含 `core/` 業務邏輯，**必須**在 `backend/tests/` 補對應測試（見 [TESTING.md](./TESTING.md)）
4. 在 `main.py` `include_router`
5. 更新 [api.md](./api.md) 規格與本文件的端點表
