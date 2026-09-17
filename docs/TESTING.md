# 單元測試

> 對應目錄：`backend/tests/`｜基準 commit `62f57f4` + 第一批修正

---

## 1. 執行

```bash
cd backend
py -m pytest tests/ -v          # Windows
# python -m pytest tests/ -v    # macOS / Linux
```

**PR 送出前必須確認輸出是 `X passed, 0 failed`。**

`test_practice_api.py` 需要 `httpx`（`requirements.txt` 已含），以及 `pytest`、`pytest-asyncio`。

只跑單一檔案：

```bash
py -m pytest tests/test_tutor_agent.py -v
```

---

## 2. 現有測試（共 96 個）

| 檔案 | 測試數 | 覆蓋對象 |
|------|-------|---------|
| `test_privacy.py` | 13 | `core/privacy.py` — 個資偵測 |
| `test_rubric_formatter.py` | 22 | `core/ai/rubric_formatter.py` — 含正規化與預設量表 |
| `test_gemini_provider.py` | 7 | `core/ai/gemini_provider.py` |
| `test_evaluator_agent.py` | 16 | `core/ai/agents/evaluator_agent.py` — 含加權計分 |
| `test_tutor_agent.py` | 12 | `core/ai/agents/tutor_agent.py` |
| `test_coherence_agent.py` | 10 | `core/ai/agents/coherence_agent.py` |
| `test_practice_api.py` | 16 | `api/practice.py` — 提交流程端對端（FakeSupabase）|

測試類別（依語意分組）：

```
TestShouldDetectPII          # 應該被偵測到的個資
TestShouldNotFalsePositive   # 不該誤判的正常文字
TestFormatRubricToText       # Rubric 轉純文字
TestParseResponse            # Gemini JSON 解析與降級
TestEvaluateStream           # GeminiProvider 串流
TestEvaluatorAgentStream     # EvaluatorAgent 逐構面串流
TestTutorDecision            # decide_action() 決策表（純邏輯）
TestTutorProcessStream       # TutorAgent 四種行動的事件序列
TestCoherenceStream          # CoherenceAgent 規則篩選與事件序列
TestBuildSummary             # 一致性檢核摘要文字

TestBuildDefaultLevels       # 預設 4 級量表，描述必須互異
TestNormalizeRubric          # 配分 → 權重，不可展開成 N 個等級
TestComputeWeightedPercentage  # 加權計分
TestWeightedScoreComplete    # score_complete 的加權結果

TestPIIGate                  # 個資攔截（含不得寫入資料庫）
TestFirstAttempt             # 第一次提交
TestSecondAttempt            # 第二次提交（原本會 KeyError）
TestPassThresholdAndWeighting  # 門檻來源與加權
TestPersistence              # 寫入 ai_evaluations / 更新 status
```

---

## 3. 測試規則（來自 `AGENTS.md`）

> **新增或修改 `backend/core/` 下的任何業務邏輯，都必須同步新增或更新對應的單元測試。**

| 規則 | 說明 |
|------|------|
| 不呼叫真實外部服務 | 不得消耗 Gemini quota 或讀寫 Supabase，一律 mock |
| 覆蓋正常 + 異常情境 | 每個功能至少兩個測試：成功情況 + 預期錯誤情況 |
| 測試命名要清楚 | `test_taiwan_id_should_be_detected` 比 `test_1` 好 |
| async 函式加裝飾器 | `@pytest.mark.asyncio` |

---

## 4. Mock 慣例

測試一律 patch Agent 的 `llm` property 或 `ChatGoogleGenerativeAI`，回傳假的 `AIMessage`：

```python
from unittest.mock import AsyncMock, patch
from langchain_core.messages import AIMessage

@pytest.mark.asyncio
async def test_score_dimension_should_parse_json():
    agent = EvaluatorAgent()
    fake_llm = AsyncMock()
    fake_llm.ainvoke.return_value = AIMessage(content=json.dumps({
        "dimension": "功能性描述", "score": 3, "max_score": 4,
        "reason": "描述具體", "evidence": "小明在點心時間…",
    }))
    with patch.object(EvaluatorAgent, "llm", fake_llm):
        result = await agent._score_dimension(...)
    assert result["score"] == 3
```

`TutorAgent.decide_action()` 是純函式，**不需要 mock**，直接組 `StudentContext` 斷言即可——
這是把決策邏輯抽離 API 呼叫的主要好處。

---

## 5. 降級行為也要測

三個 Agent 都設計成「AI 回傳格式錯誤時降級而非中斷」，這些路徑同樣要覆蓋：

| 情境 | 預期行為 |
|------|---------|
| `_score_dimension()` JSON 解析失敗 | 回 `score=1, reason="評分失敗，請教師複核"` |
| `_synthesize()` 例外 | `confidence=0.6`，`overall_feedback` 為預設文字 |
| `_check_single_rule()` 例外 | `is_ok=False, problem="檢核失敗，建議人工複核"` |
| `GeminiProvider._parse_response()` JSON 錯誤 | 拋 `ValueError("AI 回傳格式錯誤，請稍後再試")` |

---

## 6. 尚未覆蓋的區域

| 區域 | 現況 |
|------|------|
| `api/practice.py` | ✅ 已覆蓋提交流程；`/hints`、`/history`、`coherence-check` 仍無測試 |
| `api/review.py` | 無測試（可沿用 `test_practice_api.py` 的 FakeSupabase）|
| `api/teacher.py` | 無測試 |
| `api/privacy.py` | 無測試（底層 `core/privacy.py` 有 13 個） |
| `database/client.py` | 無測試 |
| 前端 | 完全無測試（無 Jest / Vitest / Playwright 設定） |
| 端對端 | 無 |

**建議補強順序**：

1. ~~測 `api/practice.py` 的提交流程~~ ✅ 已完成（`test_practice_api.py`）
2. 測 `api/review.py` — 直接沿用 `test_practice_api.py` 裡的 `FakeSupabase`
3. 測 `api/practice.py` 剩下的三個端點
4. 前端加 Vitest，先測 SSE 解析函式

### FakeSupabase 的使用方式

`tests/test_practice_api.py` 裡的 `FakeSupabase` 可直接重複使用：

```python
fake_db = FakeSupabase(STEP_ROW, prev_attempts=[], hints_count=0)
with patch.object(practice, "get_supabase", return_value=fake_db):
    ...
fake_db.find("step_attempts", "insert")   # 取出實際寫入的內容做斷言
```

它會**依 `select()` 指定的欄位投影**，所以「查詢時沒選到、程式卻去讀」這類錯誤
在測試裡就會重現，而不是等到連上真資料庫才爆。
