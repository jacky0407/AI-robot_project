# LLM 邏輯系統

> 對應程式碼：`backend/core/ai/`
> 這份文件說明教授要填什麼、系統怎麼用它、以及失敗時會怎麼降級。

---

## 1. 全貌

```
提交作答
   │
   ├─ core/privacy.py ────────── 個資偵測（命中就中止，不燒 API）
   │
   ├─ rubric_formatter.py ────── normalize_rubric()
   │                              rubric_criteria → 標準結構（配分 vs 量表分離）
   │
   ├─ agents/tutor_agent.py ──── decide_action()：評分 / 提示 / 鼓勵 / 求助
   │     │
   │     ├─ agents/evaluator_agent.py ── 逐構面評分 → 加權總分 + 信心值
   │     └─ hint_engine.py ───────────── 分層提示（教授寫的優先）
   │
   ├─ error_detector.py ──────── 錯誤分類（教授沒定義就完全不呼叫）
   │
   └─ llm_utils.py ───────────── 所有 LLM 呼叫的逾時、重試、JSON 清理
```

---

## 2. 教授要填的三個欄位

全部在 `ai_tools` 表，由工具建構器維護。`supabase/seed.sql` 有完整範例可直接照抄。

### 2.1 `rubric_criteria` — 評分規準

```json
[
  {"dimension": "客觀事實辨識", "max_score": 40, "description": "能準確擷取具體行為與數據"},
  {"dimension": "推論與假設區分", "max_score": 30, "description": "能將推論與觀察分開標示"}
]
```

> ⚠️ `max_score` 是**配分（權重）**，不是量表上限。建議總和維持 100。
> 量表級距由 `ai_tools.scale_type` 決定，兩者是不同的概念。

搭配 `scale_type`：

| 值 | 行為 |
|----|------|
| `4_point`（預設） | 展開成 4 級，描述互異 |
| `5_point` | 展開成 5 級 |
| `pass_fail` | 達成 / 未達成 兩級 |
| `100_point` | 不展開等級，直接請 AI 給 0–100 分 |

教授若自己寫了 `levels`，就完全沿用他的，不套用上表。

### 2.2 `teaching_strategy` — 分層提示

```json
{
  "hints": [
    {"level": 1, "trigger": "score_below_threshold",
     "content": "回到案例文字：哪幾句是你看到的，哪幾句是你推測的？"},
    {"level": 2, "trigger": "score_below_threshold",
     "content": "客觀事實帶有可觀察的行為、次數或時間長度。"},
    {"level": 3, "trigger": "student_request",
     "content": "示範：事實—「在積木角專注20分鐘」；推論—「可能偏好結構性活動」。",
     "is_example": true, "warn_copy": true}
  ],
  "max_auto_hints": 2,
  "personalize": true
}
```

| 欄位 | 意義 |
|------|------|
| `trigger` | `score_below_threshold` = 分數未達門檻時系統自動給；`student_request` = 學生主動按才給 |
| `is_example` | 這是示範型提示。**不做個人化**，原樣呈現 |
| `warn_copy` | 附上「請用自己的話改寫，不要直接複製」的警語 |
| `max_auto_hints` | 自動提示最多給幾層，再往下要學生主動要求 |
| `personalize` | 是否讓 AI 依學生的實際作答改寫提示（示範型永遠不改寫） |

**設計原則：提示內容是教學設計，不是工程師的事。**
教授沒設定時，系統依 Rubric 最弱構面即時生成，並標記 `source: "generated"`，
讓教授在後台分得出哪些提示不是他寫的。**不會回傳寫死的假提示假裝是教學設計。**

### 2.3 `error_taxonomy` — 錯誤分類

```json
[
  {"code": "diagnosis_only", "label": "僅列診斷名稱",
   "description": "只寫出障礙類別，沒有描述具體可觀察的行為",
   "severity": "high", "related_dimension": "客觀事實辨識"}
]
```

> ⚠️ 這個結構是本專案自訂的——`error_taxonomy` 在 schema 一直存在，
> 但 `docs/api.md` 從未定義過它的格式。若教授有不同想法，
> 改 `error_detector.parse_error_taxonomy()` 即可，不影響評分流程。

分數告訴教授「這個學生做得好不好」，錯誤分類才告訴教授「全班最常卡在哪一關」。

---

## 3. 計分方式

各構面先算得分率，再依配分加權：

```
構面          配分   得分    得分率
客觀事實辨識   40    2/4     0.50
推論與假設     30    4/4     1.00
缺漏資訊提問   30    3/4     0.75

加權總分 = (0.50×40 + 1.00×30 + 0.75×30) / 100 × 100 = 72.5
```

- `score_complete.total_score` 是 **0–100 的百分制得分**
- `max_total_score` 固定 `100`
- 直接與 `module_steps.pass_score`（預設 70）比較

權重一律由 Rubric 帶入，**不採信 AI 自己回傳的 weight**；分數超出量表範圍會被夾回。

---

## 4. 失敗時怎麼降級

這套系統的原則是：**寧可少給，不要給假的。**

| 情境 | 行為 |
|------|------|
| Gemini 回傳包了 markdown 圍籬 | `llm_utils` 自動剝掉，不算失敗 |
| 回應夾雜說明文字 | 括號配對擷取第一個完整 JSON |
| 單次呼叫逾時（預設 30 秒） | 指數退避重試，最多 3 次 |
| 某構面重試後仍失敗 | 該構面給最低分 + 標記 `failed`，**整體信心值壓到 0.5 以下強制教師複核** |
| 整體回饋生成失敗 | 用預設文字，信心值 0.6 |
| 提示個人化失敗 | **退回教授原始模板**，學生不會看到空白 |
| 提示生成失敗（教授未設定） | 回 `None`，不給提示 |
| 提示已給完 / 下一層要學生主動要 | 改為直接評分，不硬擠提示 |
| 錯誤分類失敗 | 回空陣列，不影響評分寫入 |
| AI 自創清單外的錯誤 code | 丟掉 |
| AI 標記了錯誤但引不出原文 | 丟掉——教授看到的每一項都要能回溯原文 |

---

## 5. SSE 事件（提交流程）

| 事件 | data |
|------|------|
| `score_start` | `session_id`, `attempt_id`, `attempt_number` |
| `tutor_decision` | `action`, `attempt_number`, `last_score_percent` |
| `analysis_start` | `total_dimensions` |
| `dimension_score` | `dimension`, `score`, `max_score`, `weight`, `reason`, `evidence`, `progress` |
| `score_complete` | `total_score`(0–100), `max_total_score`(100), `percentage`, `passed`, `overall_feedback`, `confidence`, `needs_teacher_review`, `dimension_scores` |
| `hint` | `level`, `content`, `trigger`, `source`, `is_example`, `warning?`, `dimension?` |
| `encouragement` | `message` |
| `escalation` | `message`, `suggest_teacher_review` |
| `errors_detected` | `errors[]`（`code`, `label`, `severity`, `evidence`, `explanation`）|
| `error` | `message`, `detail` |

---

## 6. `/hints` 端點

```
GET /api/practice/sessions/{session_id}/hints?user_id=&step_id=&reveal=false
```

`reveal` 分開兩件事：

- `reveal=false`（預設）：只回報「還有沒有下一層」，**不消耗提示額度、不呼叫 LLM**
- `reveal=true`：真的取出提示內容，並寫入 `prompt_logs`

前端因此可以顯示「還有 2 層提示可用」而不自動扣掉，學生按下去才算數。

---

## 7. 學習歷程記錄

`prompt_logs` 每則提示都記下：

| 欄位 | 用途 |
|------|------|
| `hint_level` | 第幾層 |
| `hint_trigger` | 自動給的 vs 學生主動要的 |
| `hint_source` | 教授撰寫 vs AI 生成 |

「系統自動給的提示」和「學生自己判斷需要而要求的提示」在學習歷程分析上意義完全不同，
所以分開記錄。

`ai_evaluations` 記下 `confidence` 與 `needs_teacher_review`，
教師複核佇列可以優先排「AI 自己沒把握」的那些。

---

## 8. 成本控制

目前已做到的：

- 個資命中 → 在呼叫任何 LLM 之前中止
- `error_taxonomy` 未定義 → 完全不呼叫錯誤偵測
- `teaching_strategy` 有設定且 `personalize: false` → 不呼叫 LLM
- 示範型提示 → 不呼叫 LLM
- `reveal=false` 的提示查詢 → 不呼叫 LLM

**尚未做**（屬於後續批次）：token 用量記錄到 `ai_evaluations.ai_cost`、
`ai_tools.max_cost_limit` 的實際檢查。目前 `ai_cost` 恆為 0。

一次完整評分的 LLM 呼叫次數：**構面數 + 1**（整體回饋），
有定義 `error_taxonomy` 再 +1，需要個人化提示再 +1。

---

## 9. 測試

| 檔案 | 測試數 |
|------|-------|
| `test_llm_utils.py` | 21 |
| `test_hint_engine.py` | 31 |
| `test_error_detector.py` | 16 |
| `test_rubric_formatter.py` | 22 |
| `test_evaluator_agent.py` | 16 |
| `test_tutor_agent.py` | 14 |
| `test_practice_api.py` | 26 |

全部 mock LLM，不消耗 Gemini 額度、不碰資料庫。詳見 [CONTRIBUTING.md](./CONTRIBUTING.md) 的測試章節。

---

## 10. 與舊版的差異（有舊資料時要注意）

2026-09 做過兩輪修正，以下行為與早期版本不同。**資料庫裡如果有修正前寫入的紀錄，語意會對不上**，測試期資料建議直接清掉。

| 項目 | 舊版 | 現在 |
|------|------|------|
| `rubric_criteria[].max_score` | 被當量表上限，40 分展開成 40 個相同描述的等級 | 視為配分（權重），量表由 `scale_type` 決定 |
| `ai_evaluations.total_score` | 各構面分數總和（截斷取整） | 加權百分制 0–100（四捨五入） |
| `score_complete.max_total_score` | 各構面滿分總和 | 固定 100 |
| `ai_evaluations.evidence_text` | 學生作答前 200 字 | AI 各構面實際引用的原文，`[構面] 原文` 每行一筆 |
| 通過門檻來源 | `ai_tools.pass_score`（不存在，恆為 75） | `module_steps.pass_score` |
| 提示內容 | `_default_hint()` 四句寫死的文字 | 教授的 `teaching_strategy`，未設定才依 Rubric 生成 |
| 決策是 GIVE_HINT 但沒提示可給 | 仍吐寫死提示 | 改為直接評分 |

> 舊的 `total_score` 是總和、偏低，`_extract_last_score_percent()` 會把它當百分比用，
> 可能讓 TutorAgent 多給一次提示。
