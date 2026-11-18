-- ==============================================================================
-- Migration 019：機器人可以修改，並保留每一版的內容（11/16 會議第 6 項）
--
-- 規則：
--   - 機器人的新增、修改一律走後端 API（backend/api/teacher.py），前端不能再直接寫 ai_tools
--   - 每次修改 ai_tools.version + 1；每一版的完整內容存一列在 ai_tool_versions，不覆蓋
--   - 每筆 ai_evaluations 記下評分當時用的機器人版本（tool_version），研究時對得上
--     查某次評分用的規準：ai_tool_versions WHERE tool_id = … AND version = ai_evaluations.tool_version
--
-- 做了什麼：
--   1. 新表 ai_tool_versions（只給後端讀寫）
--   2. ai_tools 加 updated_at / updated_by；version 補成不可為空
--   3. 現有機器人把目前內容存成第一筆版本紀錄
--   4. ai_evaluations 加 tool_version
--   5. 拿掉 005 的「教師可從前端新增機器人」政策
--
-- ⚠️ 部署順序：先部署後端（Render Manual Deploy），再跑這支 migration。
--    跑完之後，舊版前端「直接寫資料表」的建立方式就會失效。
-- 全部可重複執行。
-- ==============================================================================

-- ── 1. 每一版的內容 ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.ai_tool_versions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tool_id UUID NOT NULL REFERENCES public.ai_tools(id) ON DELETE CASCADE,
    version INT NOT NULL,
    snapshot JSONB NOT NULL,               -- 這一版 ai_tools 整列的內容（含 system_prompt、rubric_criteria 等）
    edited_by UUID REFERENCES public.profiles(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ DEFAULT TIMEZONE('utc'::text, NOW()) NOT NULL,
    UNIQUE (tool_id, version)
);

ALTER TABLE public.ai_tool_versions ENABLE ROW LEVEL SECURITY;
-- 不建立任何政策：只有後端（service_role）讀寫
REVOKE ALL ON public.ai_tool_versions FROM anon, authenticated;
GRANT SELECT, INSERT ON public.ai_tool_versions TO service_role;

-- ── 2. 誰、何時最後修改；version 一定有值 ─────────────────────────────────────
ALTER TABLE public.ai_tools
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS updated_by UUID REFERENCES public.profiles(id) ON DELETE SET NULL;

UPDATE public.ai_tools SET version = 1 WHERE version IS NULL;
ALTER TABLE public.ai_tools ALTER COLUMN version SET NOT NULL;

-- ── 3. 現有機器人：目前的內容就是它這一版 ────────────────────────────────────
INSERT INTO public.ai_tool_versions (tool_id, version, snapshot, edited_by)
SELECT t.id, t.version, to_jsonb(t), t.created_by
  FROM public.ai_tools t
ON CONFLICT (tool_id, version) DO NOTHING;

-- ── 4. 評分當時的機器人版本（NULL = 這支 migration 之前的評分）────────────────
ALTER TABLE public.ai_evaluations
    ADD COLUMN IF NOT EXISTS tool_version INT;

COMMENT ON COLUMN public.ai_evaluations.tool_version IS
    '評分當時的 ai_tools.version；內容查 ai_tool_versions。NULL = migration 019 之前的評分';

-- ── 5. 前端不能再直接新增機器人（改走 POST /api/teacher/tools）───────────────
DROP POLICY IF EXISTS "staff create tools" ON public.ai_tools;
