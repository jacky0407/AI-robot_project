-- ==============================================================================
-- Migration 019：關卡「怎麼算通過」（#65 評分規準改為可選）
--
-- module_steps.pass_mode：
--   score          AI（或教師）分數 >= pass_score 才通過（原本的做法，預設值）
--   complete       送出作答即通過；機器人有評分規準時仍會評分給學生參考
--   teacher_review 教師複核後才通過；AI 結果只給學生參考，不會讓關卡通過
--
-- 機器人沒有評分規準（ai_tools.rubric_criteria 為空）時不評分、只給文字回饋；
-- 這種關卡若仍是 score，後端當作 complete 處理（沒有分數可以比門檻）。
--
-- 全部可重複執行。
-- ==============================================================================

ALTER TABLE public.module_steps
    ADD COLUMN IF NOT EXISTS pass_mode TEXT NOT NULL DEFAULT 'score';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'module_steps_pass_mode_check'
    ) THEN
        ALTER TABLE public.module_steps
            ADD CONSTRAINT module_steps_pass_mode_check
            CHECK (pass_mode IN ('score', 'complete', 'teacher_review'));
    END IF;
END $$;
