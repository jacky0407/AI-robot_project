-- ==============================================================================
-- Migration 003：記錄 TutorAgent 對每次作答的決策
--
-- 為什麼需要：
--   TutorAgent 決定「給提示」或「建議找老師（escalate）」時不會評分，
--   所以不會有 ai_evaluations。教師複核佇列原本只列「有 AI 初評」的作答，
--   被建議找老師的學生因此完全不會出現在老師面前。
--   記下決策後，/api/review/pending 就能把 escalate 的作答列進來。
--
-- 誰要跑：
--   已經跑過舊版 schema.sql 的資料庫。全新資料庫直接跑 schema.sql 即可。
--   使用 IF NOT EXISTS，重複執行安全。
-- ==============================================================================

ALTER TABLE public.step_attempts
    ADD COLUMN IF NOT EXISTS tutor_action TEXT
        CHECK (tutor_action IN ('evaluate', 'give_hint', 'encourage', 'escalate'));

COMMENT ON COLUMN public.step_attempts.tutor_action IS
    'TutorAgent 的決策：evaluate | give_hint | encourage | escalate；escalate 會進教師複核佇列';

CREATE INDEX IF NOT EXISTS idx_step_attempts_escalate
    ON public.step_attempts (tutor_action)
    WHERE tutor_action = 'escalate';
