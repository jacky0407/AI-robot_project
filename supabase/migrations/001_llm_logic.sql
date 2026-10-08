-- ==============================================================================
-- Migration 001：LLM 邏輯系統所需欄位
--
-- 適用對象：已經執行過舊版 schema.sql 的資料庫。
-- 全新建立的資料庫直接跑 schema.sql 即可，不需要再跑這支。
--
-- 全部使用 IF NOT EXISTS，重複執行安全。
-- ==============================================================================

-- ── A1：AI 自評信心值落庫 ─────────────────────────────────────────────────
-- api.md 的 /api/admin/conversations 規格有 confidence，但原本 schema 沒有欄位，
-- Agent 算出來就被丟掉，教師端看不到「AI 對這次評分沒把握」的訊號。
ALTER TABLE public.ai_evaluations
    ADD COLUMN IF NOT EXISTS confidence FLOAT,
    ADD COLUMN IF NOT EXISTS needs_teacher_review BOOLEAN DEFAULT false;

COMMENT ON COLUMN public.ai_evaluations.confidence IS
    'AI 對本次評分的自評信心值 0~1';
COMMENT ON COLUMN public.ai_evaluations.needs_teacher_review IS
    'confidence 低於門檻時為 true，供教師複核佇列排序';

CREATE INDEX IF NOT EXISTS idx_ai_evaluations_needs_review
    ON public.ai_evaluations (needs_teacher_review)
    WHERE needs_teacher_review = true;

-- ── A3：Rubric 量表類型 ───────────────────────────────────────────────────
-- rubric_criteria 的 max_score 是「配分（權重）」，量表級距另外定義。
ALTER TABLE public.ai_tools
    ADD COLUMN IF NOT EXISTS scale_type TEXT
        CHECK (scale_type IN ('4_point', '5_point', '100_point', 'pass_fail'))
        DEFAULT '4_point';

COMMENT ON COLUMN public.ai_tools.scale_type IS
    '評分量表：4_point | 5_point | 100_point | pass_fail';

-- ── B5：提示的觸發方式 ────────────────────────────────────────────────────
-- 區分「系統依分數自動給的提示」與「學生主動要求的提示」，
-- 這兩者在學習歷程分析上意義完全不同。
ALTER TABLE public.prompt_logs
    ADD COLUMN IF NOT EXISTS hint_trigger TEXT
        CHECK (hint_trigger IN ('score_below_threshold', 'student_request')),
    ADD COLUMN IF NOT EXISTS hint_source TEXT
        CHECK (hint_source IN ('teacher', 'generated'));

COMMENT ON COLUMN public.prompt_logs.hint_trigger IS
    '這則提示是自動給的還是學生主動要的';
COMMENT ON COLUMN public.prompt_logs.hint_source IS
    'teacher = 教授在 teaching_strategy 寫的；generated = 教授未設定時由 AI 依 Rubric 生成';
