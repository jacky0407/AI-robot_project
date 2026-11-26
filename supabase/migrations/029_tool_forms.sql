-- ==============================================================================
-- Migration 029：工具專屬表單與質性反思（#80，需求說明書第十二節）
--
-- 原本 schema 就有 forms、form_responses，但沒有功能使用；這裡補上需要的欄位：
--
-- forms（每支機器人自己的表單；一列 = 一個版本）
--   form_key        同一份表單所有版本共用的編號；修改 = 新增一列（version + 1），舊版 is_active = false，
--                   舊的填答仍指向舊版那一列，題目與選項都保留原樣
--   trigger_timing  多兩種：every_n_attempts（每 N 次作答）、teacher_release（教師發布後出現）
--   trigger_every_n every_n_attempts 的 N
--   released_at     teacher_release：教師按「發布」的時間；NULL = 還沒發布
--   is_required     必填（不能略過）；不必填時學生可以按「略過」，也會記一筆 skipped
--   gate            none：不影響進度／completion：填了這一關才算通過／full_feedback：填了才顯示完整回饋
--   description、is_active、created_by、updated_at
--
-- form_responses（一列 = 一次填答或略過）
--   連到表單版本（form_id、form_key、form_version）、使用者、機器人、關卡、模組、課程（自主探索為 NULL）、
--   作答（attempt_id）、當時的 AI 結果（ai_evaluation_id、ai_total_score），以及出現的時點 timing、是否略過 skipped。
--   responses = {"answers": {題目 id: 答案}, "na": [選「不適用」的題目 id]}
--
-- 只給後端（service_role）讀寫（RLS 開著、沒有 policy）。全部可重複執行。
-- ==============================================================================

ALTER TABLE public.forms
    ADD COLUMN IF NOT EXISTS form_key UUID,
    ADD COLUMN IF NOT EXISTS description TEXT NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS trigger_every_n INT,
    ADD COLUMN IF NOT EXISTS released_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS is_required BOOLEAN NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS gate TEXT NOT NULL DEFAULT 'none',
    ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT true,
    ADD COLUMN IF NOT EXISTS created_by UUID REFERENCES public.profiles(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT TIMEZONE('utc'::text, NOW()) NOT NULL;

-- 舊資料（如果有）：每列自己是一份表單
UPDATE public.forms SET form_key = id WHERE form_key IS NULL;
ALTER TABLE public.forms ALTER COLUMN form_key SET DEFAULT gen_random_uuid();
ALTER TABLE public.forms ALTER COLUMN form_key SET NOT NULL;
UPDATE public.forms SET version = 1 WHERE version IS NULL;
ALTER TABLE public.forms ALTER COLUMN version SET NOT NULL;

ALTER TABLE public.forms DROP CONSTRAINT IF EXISTS forms_trigger_timing_check;
ALTER TABLE public.forms ADD CONSTRAINT forms_trigger_timing_check CHECK (trigger_timing IN (
    'pre_practice', 'post_practice', 'after_feedback', 'post_revision', 'every_n_attempts', 'teacher_release'
));
ALTER TABLE public.forms DROP CONSTRAINT IF EXISTS forms_gate_check;
ALTER TABLE public.forms ADD CONSTRAINT forms_gate_check CHECK (gate IN ('none', 'completion', 'full_feedback'));
ALTER TABLE public.forms DROP CONSTRAINT IF EXISTS forms_every_n_check;
ALTER TABLE public.forms ADD CONSTRAINT forms_every_n_check CHECK (
    trigger_timing <> 'every_n_attempts' OR (trigger_every_n IS NOT NULL AND trigger_every_n >= 1)
);

CREATE UNIQUE INDEX IF NOT EXISTS forms_key_version_idx ON public.forms (form_key, version);
CREATE INDEX IF NOT EXISTS forms_tool_active_idx ON public.forms (tool_id, is_active);

ALTER TABLE public.form_responses
    ADD COLUMN IF NOT EXISTS form_key UUID,
    ADD COLUMN IF NOT EXISTS form_version INT,
    ADD COLUMN IF NOT EXISTS tool_id UUID REFERENCES public.ai_tools(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS step_id UUID REFERENCES public.module_steps(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS module_id UUID REFERENCES public.learning_modules(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS course_id UUID REFERENCES public.courses(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS ai_evaluation_id UUID REFERENCES public.ai_evaluations(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS ai_total_score INT,
    ADD COLUMN IF NOT EXISTS timing TEXT,
    ADD COLUMN IF NOT EXISTS skipped BOOLEAN NOT NULL DEFAULT false;

CREATE INDEX IF NOT EXISTS form_responses_user_step_idx ON public.form_responses (user_id, step_id, form_key);
CREATE INDEX IF NOT EXISTS form_responses_form_idx ON public.form_responses (form_id);
CREATE INDEX IF NOT EXISTS form_responses_module_idx ON public.form_responses (module_id);

ALTER TABLE public.forms ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.form_responses ENABLE ROW LEVEL SECURITY;
