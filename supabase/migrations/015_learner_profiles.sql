-- ==============================================================================
-- Migration 015：學習者側寫與記憶快照（機器人記憶系統改版提案・階段 1）
--
-- learner_profiles：每位學生 × 每個模組的側寫，每次更新「新增一列」（version + 1），
--   不覆蓋舊版本，研究時可以回推任何時間點的側寫。
--   profile 只放程式計算的數字與錯誤代碼（不放學生原文、不放姓名），內容格式見
--   backend/core/learner_profile.py。
--   只給後端（service_role）讀寫：學生與教師都透過 API 讀取。
--
-- ai_evaluations.memory_snapshot：這次回饋當時實際放進 prompt 的記憶，
--   供教師複核時查看、研究時重現。
--
-- 全部可重複執行。編號說明：012～014 已由其他 PR 使用（時區、多組邀請碼、註冊角色）。
-- ==============================================================================

CREATE TABLE IF NOT EXISTS public.learner_profiles (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
    module_id UUID NOT NULL REFERENCES public.learning_modules(id) ON DELETE CASCADE,
    version INT NOT NULL,
    profile JSONB NOT NULL,
    source_attempt_id UUID REFERENCES public.step_attempts(id) ON DELETE SET NULL, -- 觸發這次更新的作答
    created_at TIMESTAMPTZ DEFAULT TIMEZONE('utc'::text, NOW()) NOT NULL,
    UNIQUE (user_id, module_id, version)
);

CREATE INDEX IF NOT EXISTS learner_profiles_latest_idx
    ON public.learner_profiles (user_id, module_id, version DESC);

ALTER TABLE public.learner_profiles ENABLE ROW LEVEL SECURITY;
-- 不建立任何政策：anon / authenticated 一律讀不到，只有 service_role 可以存取
REVOKE ALL ON public.learner_profiles FROM anon, authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.learner_profiles TO service_role;

ALTER TABLE public.ai_evaluations
    ADD COLUMN IF NOT EXISTS memory_snapshot JSONB; -- 回饋當時參考的記憶（文字 + 摘要）
