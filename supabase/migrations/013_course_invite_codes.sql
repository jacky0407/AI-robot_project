-- ==============================================================================
-- Migration 013：一門課多組邀請碼（「邀請碼與自備金鑰設計提案」第一階段）
--
-- 規則：
--   - 建立課程時不再自動產生邀請碼，由老師另外產生，一門課可以有多組
--   - 每組碼設定「可用日期」access_until：用這組碼加入的學生可以用到這天（台灣時間，當天仍可用）
--   - 方案 plan：free（平台金鑰，每天 daily_quota 次）或 self_key（學生自備金鑰，不限次數）
--   - 邀請碼本身不會自動失效；老師可以手動停用（is_active = false），停用後不能再用它加入
--   - 不設名額上限
--
-- 做了什麼：
--   1. 新表 course_invite_codes（前端不能直接讀寫，一律走後端 API）
--   2. course_members 記錄用哪組碼加入（invite_code_id）與課程模式的每日用量（used_today / used_date）
--   3. courses.invite_code 改成可以是空的（舊欄位，不再使用）；舊碼搬進新表，已加入的學生掛在搬過去的碼下
--   4. module_assigned_via_course()：可用日期過了的學生看不到課程模組
--   5. consume_course_quota() / refund_course_quota()：免費方案的扣次數與退次數（與 008 / 011 相同規則）
--
-- 需要先跑 012（today_taipei()）；本檔也會建立同一個函式，單獨跑也不會壞。
-- 全部可重複執行。
-- ==============================================================================

CREATE OR REPLACE FUNCTION public.today_taipei()
RETURNS date LANGUAGE sql STABLE AS $$
    SELECT (now() AT TIME ZONE 'Asia/Taipei')::date;
$$;

-- ── 1. 邀請碼 ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.course_invite_codes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    course_id UUID NOT NULL REFERENCES public.courses(id) ON DELETE CASCADE,
    code TEXT UNIQUE NOT NULL,
    plan TEXT NOT NULL DEFAULT 'free' CHECK (plan IN ('free', 'self_key')),
    daily_quota INT CHECK (daily_quota BETWEEN 1 AND 1000),
    access_until DATE NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT true,
    created_by UUID REFERENCES public.profiles(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ DEFAULT TIMEZONE('utc'::text, NOW()) NOT NULL,
    -- 免費方案一定要有每日次數；自備金鑰不限次數
    CONSTRAINT course_invite_codes_quota_by_plan CHECK (
        (plan = 'free' AND daily_quota IS NOT NULL) OR (plan = 'self_key' AND daily_quota IS NULL)
    )
);

CREATE INDEX IF NOT EXISTS course_invite_codes_course_id_idx ON public.course_invite_codes (course_id);

ALTER TABLE public.course_invite_codes ENABLE ROW LEVEL SECURITY;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.course_invite_codes TO service_role;
-- 不 GRANT 給 authenticated：學生不該列出別門課的邀請碼

-- ── 2. 成員記錄用哪組碼加入、課程模式的每日用量 ───────────────────────────────
ALTER TABLE public.course_members
    ADD COLUMN IF NOT EXISTS invite_code_id UUID REFERENCES public.course_invite_codes(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS used_today INT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS used_date DATE;  -- used_today 是哪一天（台灣時間）的次數

COMMENT ON COLUMN public.course_members.invite_code_id IS
    '用哪組邀請碼加入；決定方案、每日次數與可用日期。NULL = 舊資料或後台直接加入，不限期、不限次數';

-- ── 3. 舊的單一邀請碼搬進新表 ────────────────────────────────────────────────
ALTER TABLE public.courses ALTER COLUMN invite_code DROP NOT NULL;
COMMENT ON COLUMN public.courses.invite_code IS '已停用（migration 013 起改用 course_invite_codes）';

-- 舊碼一律搬成免費方案、每天 10 次、可用到 2027-01-31（上學期結束），老師之後可以在課程管理頁修改
INSERT INTO public.course_invite_codes (course_id, code, plan, daily_quota, access_until, created_by)
SELECT c.id, c.invite_code, 'free', 10, DATE '2027-01-31', c.created_by
  FROM public.courses c
 WHERE c.invite_code IS NOT NULL
ON CONFLICT (code) DO NOTHING;

UPDATE public.course_members mem
   SET invite_code_id = ic.id
  FROM public.courses c
  JOIN public.course_invite_codes ic ON ic.code = c.invite_code AND ic.course_id = c.id
 WHERE mem.course_id = c.id
   AND mem.invite_code_id IS NULL;

-- ── 4. 可用日期過了，就看不到課程指派的模組 ──────────────────────────────────
CREATE OR REPLACE FUNCTION public.module_assigned_via_course(p_module_id uuid)
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
    SELECT EXISTS (
        SELECT 1
        FROM public.course_modules cm
        JOIN public.courses c ON c.id = cm.course_id AND NOT c.is_archived
        JOIN public.course_members mem ON mem.course_id = cm.course_id
        JOIN public.learning_modules m ON m.id = cm.module_id AND m.is_published
        LEFT JOIN public.course_invite_codes ic ON ic.id = mem.invite_code_id
        WHERE cm.module_id = p_module_id
          AND mem.user_id = auth.uid()
          AND mem.status = 'active'
          AND (mem.invite_code_id IS NULL OR ic.access_until >= public.today_taipei())
    );
$$;

REVOKE EXECUTE ON FUNCTION public.module_assigned_via_course(uuid) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.module_assigned_via_course(uuid) TO authenticated;

-- ── 5. 免費方案的每日次數（規則與 008 consume_tool_quota / 011 refund_tool_quota 相同）──────
-- 每日上限讀成員加入時用的那組碼，後端不用（也不能）自己傳上限進來
CREATE OR REPLACE FUNCTION public.consume_course_quota(p_member_id uuid, p_today date)
RETURNS TABLE (allowed boolean, used integer, quota integer)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
BEGIN
    -- 還有額度：跨日就從 1 開始，同一天就 +1
    RETURN QUERY
    WITH consumed AS (
        UPDATE public.course_members mem
           SET used_today = CASE WHEN mem.used_date IS DISTINCT FROM p_today
                                 THEN 1 ELSE mem.used_today + 1 END,
               used_date  = p_today
          FROM public.course_invite_codes ic
         WHERE mem.id = p_member_id
           AND ic.id = mem.invite_code_id
           AND ic.daily_quota IS NOT NULL
           AND (CASE WHEN mem.used_date IS DISTINCT FROM p_today
                     THEN 0 ELSE mem.used_today END) < ic.daily_quota
        RETURNING mem.used_today AS new_used, ic.daily_quota AS new_quota
    )
    SELECT true, c.new_used, c.new_quota FROM consumed c;

    -- 額度用完：不扣，回傳目前用量讓後端組錯誤訊息
    IF NOT FOUND THEN
        RETURN QUERY
        SELECT false,
               CASE WHEN mem.used_date IS DISTINCT FROM p_today THEN 0 ELSE mem.used_today END,
               ic.daily_quota
          FROM public.course_members mem
          JOIN public.course_invite_codes ic ON ic.id = mem.invite_code_id
         WHERE mem.id = p_member_id;
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION public.refund_course_quota(p_member_id uuid, p_today date)
RETURNS TABLE (used integer, quota integer)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
BEGIN
    RETURN QUERY
    UPDATE public.course_members mem
       SET used_today = mem.used_today - 1
      FROM public.course_invite_codes ic
     WHERE mem.id = p_member_id
       AND ic.id = mem.invite_code_id
       AND mem.used_date = p_today
       AND mem.used_today > 0
    RETURNING mem.used_today, ic.daily_quota;
END;
$$;

REVOKE EXECUTE ON FUNCTION public.consume_course_quota(uuid, date) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.consume_course_quota(uuid, date) TO service_role;
REVOKE EXECUTE ON FUNCTION public.refund_course_quota(uuid, date) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.refund_course_quota(uuid, date) TO service_role;
