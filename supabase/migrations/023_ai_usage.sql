-- ==============================================================================
-- Migration 023：AI 用量與費用記錄、每月預算（#81，需求說明書 AI-001、AI-002）
--
-- ai_usage_logs：每一次 AI 呼叫一列（評分、回饋、提示、錯誤偵測、跨步驟檢核、聊天調度與回覆、向量）。
--   模型、輸入／輸出 token、估計費用（依 backend/core/config.py 的價目表）、延遲、成功與否、
--   用的是平台金鑰還是學生自備金鑰（學生自己的費用不算進平台預算）。
-- courses.monthly_ai_budget_usd：這門課每月平台金鑰的費用上限（美元，空白 = 不限）。
--   用到 80% 先提醒，用完就不能開始新的作答或聊天訊息（已開始的評分不中斷）。
--
-- 只給後端（service_role）讀寫。全部可重複執行。
-- ==============================================================================

CREATE TABLE IF NOT EXISTS public.ai_usage_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at TIMESTAMPTZ DEFAULT TIMEZONE('utc'::text, NOW()) NOT NULL,
    user_id UUID REFERENCES public.profiles(id) ON DELETE SET NULL,
    course_id UUID REFERENCES public.courses(id) ON DELETE SET NULL,      -- 自主探索時為 NULL
    module_id UUID REFERENCES public.learning_modules(id) ON DELETE SET NULL,
    step_id UUID REFERENCES public.module_steps(id) ON DELETE SET NULL,
    tool_id UUID REFERENCES public.ai_tools(id) ON DELETE SET NULL,
    access_mode TEXT,                       -- course / permission / self_key / staff / teacher
    key_source TEXT NOT NULL DEFAULT 'platform' CHECK (key_source IN ('platform', 'student')),
    feature TEXT NOT NULL,                  -- score_dimension / synthesize / chat_bot / embedding …
    model TEXT,
    input_tokens INT NOT NULL DEFAULT 0,
    output_tokens INT NOT NULL DEFAULT 0,   -- 含思考 token
    cost_usd NUMERIC(12, 6) NOT NULL DEFAULT 0,
    latency_ms INT,
    success BOOLEAN NOT NULL DEFAULT true,
    error TEXT
);

CREATE INDEX IF NOT EXISTS ai_usage_logs_course_month_idx ON public.ai_usage_logs (course_id, created_at);
CREATE INDEX IF NOT EXISTS ai_usage_logs_created_idx ON public.ai_usage_logs (created_at);

ALTER TABLE public.courses
    ADD COLUMN IF NOT EXISTS monthly_ai_budget_usd NUMERIC(10, 2);  -- 空白 = 不限

-- 一段期間內平台金鑰的費用合計（course_id 為 NULL = 自主探索，不分課程）
CREATE OR REPLACE FUNCTION public.ai_usage_total(p_course_id UUID, p_since TIMESTAMPTZ, p_autonomous BOOLEAN DEFAULT false)
RETURNS NUMERIC
LANGUAGE sql STABLE
SET search_path = public
AS $$
    SELECT COALESCE(SUM(cost_usd), 0)
    FROM public.ai_usage_logs
    WHERE created_at >= p_since
      AND key_source = 'platform'
      AND (
        (p_autonomous AND access_mode = 'permission')
        OR (NOT p_autonomous AND course_id = p_course_id)
      );
$$;

-- 用量明細：依日期、功能、模型、機器人、金鑰來源分組（老師後台的「AI 用量」）
CREATE OR REPLACE FUNCTION public.ai_usage_summary(p_course_id UUID, p_since TIMESTAMPTZ, p_until TIMESTAMPTZ)
RETURNS TABLE (day DATE, feature TEXT, model TEXT, tool_id UUID, key_source TEXT,
               calls BIGINT, failed BIGINT, input_tokens BIGINT, output_tokens BIGINT, cost_usd NUMERIC)
LANGUAGE sql STABLE
SET search_path = public
AS $$
    SELECT (created_at AT TIME ZONE 'Asia/Taipei')::date, feature, model, tool_id, key_source,
           COUNT(*), COUNT(*) FILTER (WHERE NOT success), SUM(input_tokens), SUM(output_tokens), SUM(cost_usd)
    FROM public.ai_usage_logs
    WHERE course_id = p_course_id AND created_at >= p_since AND created_at < p_until
    GROUP BY 1, 2, 3, 4, 5;
$$;

ALTER TABLE public.ai_usage_logs ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.ai_usage_logs FROM anon, authenticated;
GRANT SELECT, INSERT ON public.ai_usage_logs TO service_role;
REVOKE ALL ON FUNCTION public.ai_usage_total(UUID, TIMESTAMPTZ, BOOLEAN) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.ai_usage_summary(UUID, TIMESTAMPTZ, TIMESTAMPTZ) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.ai_usage_total(UUID, TIMESTAMPTZ, BOOLEAN) TO service_role;
GRANT EXECUTE ON FUNCTION public.ai_usage_summary(UUID, TIMESTAMPTZ, TIMESTAMPTZ) TO service_role;
