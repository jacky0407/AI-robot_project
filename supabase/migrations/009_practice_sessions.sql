-- ==============================================================================
-- Migration 009：練習 Session 落庫（practice_sessions）
--
-- 為什麼需要：
--   session_id 原本只是前端隨機產生的字串，後端不存也不檢查，
--   研究分析時分不出「哪幾次作答是同一次坐下來練習」，
--   也擋不住有人把作答掛到別人的 session_id 底下。
--
-- 運作方式（後端 core/practice_session.py）：
--   前端進入學生工作台時自己產生 UUID 當 session_id（不需先呼叫 POST /sessions），
--   第一次用這個 id 送出作答時才建立一列，記下是誰的；
--   之後的作答都寫進 step_attempts.session_id。別人的 Session 一律 403。
--   還沒跑這支 migration 時，後端只記 log，作答照常、不連結 Session。
--
-- 權限：只給後端（service_role）。前端不直接讀寫這張表。
--
-- 全部可重複執行。
-- ==============================================================================

CREATE TABLE IF NOT EXISTS public.practice_sessions (
    id UUID PRIMARY KEY,                   -- 由前端產生（crypto.randomUUID()），所以沒有 DEFAULT
    user_id UUID NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
    started_at TIMESTAMPTZ DEFAULT TIMEZONE('utc'::text, NOW()) NOT NULL,
    last_active_at TIMESTAMPTZ DEFAULT TIMEZONE('utc'::text, NOW()) NOT NULL
);

CREATE INDEX IF NOT EXISTS practice_sessions_user_id_idx
    ON public.practice_sessions (user_id);

-- 作答屬於哪一次練習；Session 被刪除時作答保留
ALTER TABLE public.step_attempts
    ADD COLUMN IF NOT EXISTS session_id UUID
    REFERENCES public.practice_sessions(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS step_attempts_session_id_idx
    ON public.step_attempts (session_id);

ALTER TABLE public.practice_sessions ENABLE ROW LEVEL SECURITY;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.practice_sessions TO service_role;
REVOKE ALL ON public.practice_sessions FROM anon, authenticated;
