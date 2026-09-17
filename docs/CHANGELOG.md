# 變更紀錄

---

## 第一批修正：會在執行時失敗的問題

> 基準 commit：`62f57f4`
> 對應 [KNOWN_GAPS.md](./KNOWN_GAPS.md) 第 1、2、3、4、5、10 項
> 測試：**56 → 96 passed**

### 修了什麼

| # | 問題 | 影響 |
|---|------|------|
| 1 | `api/review.py` 欄位與 `teacher_reviews` schema 完全不符 | 三個複核端點必定失敗 |
| 2 | `practice.py` 用未 select 的欄位查 `prompt_logs` | 第二次提交 `KeyError`；第一次提交 UUID 型別錯誤 |
| 3 | `POST /api/practice/sessions` 讀不存在的欄位 | 機器人名稱與案例內容回傳空字串 |
| 4 | 通過門檻從 `ai_tools` 讀 `pass_score` | 教授設的門檻永遠無效，恆用預設 75 |
| 5 | `max_score` 被當成量表上限展開 `levels` | 40 分配分產生 40 個相同描述的等級 |
| 10 | `seed.sql` 的非法 UUID 與空密碼 | 種子資料無法執行、測試帳號無法登入 |

### 異動檔案

```
backend/api/review.py                      重寫，對齊現有 schema
backend/api/practice.py                    #2 #3 #4 #5
backend/core/ai/rubric_formatter.py        新增 normalize_rubric / build_default_levels
backend/core/ai/agents/evaluator_agent.py  新增 compute_weighted_percentage，改用加權計分
supabase/seed.sql                          #10
backend/tests/test_practice_api.py         新增（16 個測試）
backend/tests/test_rubric_formatter.py     新增 14 個測試
backend/tests/test_evaluator_agent.py      新增 10 個測試
```

**沒有動到**：`schema.sql`、前端任何檔案。

---

### ⚠️ 需要你手動做的事

1. **重跑 `supabase/seed.sql`**（如果之前跑過舊版）
   UUID 全部換掉了，舊資料的 id 對不上。建議先清空這幾張表再重跑：

   ```sql
   DELETE FROM public.module_steps;
   DELETE FROM public.learning_modules;
   DELETE FROM public.ai_tools;
   DELETE FROM public.tool_cases;
   ```

   `schema.sql` **不需要重跑**。

2. **測試帳號密碼現在是 `Test1234!`**
   若 GoTrue 版本仍不接受，改到 Dashboard → Authentication → Users 手動設定。

3. **開分支再提交**
   `AGENTS.md` 規定不可直接在 `main` 開發：

   ```bash
   git switch -c fix/schema-alignment-and-scoring
   git add backend supabase docs
   git commit -m "fix: align review API with schema, fix attempt history KeyError, weighted rubric scoring"
   ```

4. **跑一次測試確認**

   ```bash
   cd backend
   py -m pytest tests/ -v      # 預期 96 passed
   ```

---

### 行為變更：評分改為加權百分制

這是六項裡唯一會改變**既有評分結果**的修正，請留意。

**修正前**

`rubric_criteria` 的 `max_score`（配分）被當成量表上限，展開成同樣多的等級：

```python
"levels": [{"score": lv, "description": r["description"]} for lv in range(1, 40 + 1)]
```

40 個等級、描述一字不差，AI 沒有任何依據可以區分該給幾分。

**修正後**

`max_score` 一律視為**權重**，評分固定用 4 級量表：

```
構面          配分   得分    得分率
客觀事實辨識   40    2/4     0.50
推論與假設     30    4/4     1.00
缺漏資訊提問   30    3/4     0.75

加權總分 = (0.50×40 + 1.00×30 + 0.75×30) / 100 × 100 = 72.5
```

**連帶影響**

| 項目 | 修正前 | 修正後 |
|------|-------|--------|
| `score_complete.total_score` | 各構面分數總和 | 百分制得分（0–100） |
| `score_complete.max_total_score` | 各構面滿分總和 | 固定 `100` |
| `dimension_score` 事件 | 無 `weight` | 多一個 `weight` 欄位 |
| `ai_evaluations.total_score` | 分數總和（截斷取整） | 百分制得分（四捨五入） |

`module_steps.pass_score`（預設 70）本來就是百分制，現在才真正對得上。

> 📌 **給教授的提醒**：工具建構器裡的 Rubric 配分總和建議維持 100，門檻才好對應。
> 建構器畫面已經有即時顯示總配分。

> 📌 **舊資料**：修正前寫入的 `ai_evaluations.total_score` 是分數總和，語意與現在不同。
> `_extract_last_score_percent()` 會優先採用 `total_score`，所以舊紀錄算出的「上次分數」
> 可能偏低，進而讓 TutorAgent 多給一次提示。若在意，清掉測試期間的 `ai_evaluations` 即可。

---

### 新增的測試（40 個）

| 檔案 | 新增 | 重點 |
|------|------|------|
| `tests/test_practice_api.py` | 16 | 個資攔截、第一次／第二次提交、門檻來源、加權計分、寫入內容 |
| `tests/test_rubric_formatter.py` | 14 | `build_default_levels` 4 級且描述互異、`normalize_rubric` 配分轉權重 |
| `tests/test_evaluator_agent.py` | 10 | `compute_weighted_percentage` 各種權重組合、權重由 Rubric 帶入 |

`test_practice_api.py` 用 `FakeSupabase` 取代真實資料庫，會**依 `select()` 指定的欄位投影**，
因此能真實重現「只選了 `attempt_number` 卻讀 `id`」這類錯誤——
這些測試在修正前的程式碼上會失敗 11 個，修正後全數通過。

---

### 還沒處理的

第一批只處理「會炸掉」的問題。以下仍待辦，詳見 [KNOWN_GAPS.md](./KNOWN_GAPS.md)：

- **#8 後端 API 沒有身分驗證** — `user_id` / `teacher_id` 仍由前端傳入
- **#9 Supabase RLS 未啟用**
- **#7 前端沒有正確消費 SSE**
- **#3 的後半** — `session_id` 仍未落庫
- **#6 API 回應格式不一致**、**#11 寫死的設定**、**#12 未使用的欄位**
