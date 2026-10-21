-- ==============================================================================
-- Migration 005：指派制的 RLS 政策（學生要被老師指派後才看得到機器人）
--
-- 為什麼需要：
--   資料庫所有資料表都已開啟 RLS，但除了 profiles 之外沒有任何政策，
--   前端用登入者身分讀資料一律拿到空結果（學生模組頁顯示「目前沒有已發布的培訓模組」）。
--   後端用 service_role 連線，不受 RLS 影響。
--
-- 指派規則（兩種任一成立就看得到）：
--   1. 課程指派：老師把「模組」指派給「課程」（新表 course_modules），
--      該課程狀態為 active 的成員看得到模組內所有步驟與機器人。
--   2. 逐位核准：老師在 tool_permissions 核准某位學生使用某支機器人
--      （tool_id 為 NULL 代表全部機器人），在 start_date ~ expire_date 期間內有效。
--      學生看得到這支機器人、使用它的步驟，以及這些步驟所屬的已發布模組。
--   教師（owner / assistant）看得到全部。
--
-- 只開放前端程式實際用到的存取（2026-10-07 盤點 frontend/src）：
--   讀 learning_modules / module_steps / ai_tools / tool_cases、讀自己的 step_attempts、
--   教師新增 ai_tools。其他操作（刪除、複核、評分）都走後端 API。
--
-- ⚠️ 後端 API 用 service_role，不受這些規則限制；
--    後端的「學生是否被指派」檢查要另外在 practice API 補上。
--
-- 全部可重複執行。
-- ==============================================================================

-- ── 新表：課程 ↔ 模組 指派 ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.course_modules (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    course_id UUID NOT NULL REFERENCES public.courses(id) ON DELETE CASCADE,
    module_id UUID NOT NULL REFERENCES public.learning_modules(id) ON DELETE CASCADE,
    assigned_by UUID REFERENCES public.profiles(id) ON DELETE SET NULL,
    assigned_at TIMESTAMPTZ DEFAULT TIMEZONE('utc'::text, NOW()) NOT NULL,
    UNIQUE (course_id, module_id)
);

ALTER TABLE public.course_modules ENABLE ROW LEVEL SECURITY;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.course_modules TO service_role;
GRANT SELECT ON public.course_modules TO authenticated;

-- ── 判斷函式（SECURITY DEFINER：查指派資料時不受那些表本身的 RLS 限制） ──────

-- 目前登入者是不是教師或助教
CREATE OR REPLACE FUNCTION public.is_staff()
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
    SELECT EXISTS (
        SELECT 1 FROM public.profiles
        WHERE id = auth.uid() AND role IN ('owner', 'assistant')
    );
$$;

-- 模組是否透過課程指派給目前登入者（模組已發布、課程未封存、成員狀態 active）
CREATE OR REPLACE FUNCTION public.module_assigned_via_course(p_module_id uuid)
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
    SELECT EXISTS (
        SELECT 1
        FROM public.course_modules cm
        JOIN public.courses c ON c.id = cm.course_id AND NOT c.is_archived
        JOIN public.course_members mem ON mem.course_id = cm.course_id
        JOIN public.learning_modules m ON m.id = cm.module_id AND m.is_published
        WHERE cm.module_id = p_module_id
          AND mem.user_id = auth.uid()
          AND mem.status = 'active'
    );
$$;

-- 目前登入者是否有這支機器人的有效核准（tool_id 為 NULL 代表全部機器人）
CREATE OR REPLACE FUNCTION public.has_tool_permission(p_tool_id uuid)
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
    SELECT EXISTS (
        SELECT 1 FROM public.tool_permissions tp
        WHERE tp.user_id = auth.uid()
          AND tp.status = 'approved'
          AND (tp.tool_id = p_tool_id OR tp.tool_id IS NULL)
          AND (tp.start_date IS NULL OR tp.start_date <= CURRENT_DATE)
          AND (tp.expire_date IS NULL OR tp.expire_date >= CURRENT_DATE)
    );
$$;

-- 以下三個函式把「跨表查詢」包進 SECURITY DEFINER，
-- 避免 learning_modules ↔ module_steps 的政策互相查詢造成無限遞迴。

-- 模組已發布，且其中有登入者被核准的機器人
CREATE OR REPLACE FUNCTION public.module_visible_via_permission(p_module_id uuid)
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
    SELECT EXISTS (
        SELECT 1
        FROM public.learning_modules m
        JOIN public.module_steps s ON s.module_id = m.id
        WHERE m.id = p_module_id
          AND m.is_published
          AND public.has_tool_permission(s.tool_id)
    );
$$;

-- 模組是否已發布
CREATE OR REPLACE FUNCTION public.module_is_published(p_module_id uuid)
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
    SELECT EXISTS (SELECT 1 FROM public.learning_modules WHERE id = p_module_id AND is_published);
$$;

-- 機器人是否出現在課程指派給登入者的模組裡
CREATE OR REPLACE FUNCTION public.tool_in_assigned_module(p_tool_id uuid)
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
    SELECT EXISTS (
        SELECT 1 FROM public.module_steps s
        WHERE s.tool_id = p_tool_id
          AND public.module_assigned_via_course(s.module_id)
    );
$$;

REVOKE EXECUTE ON FUNCTION public.is_staff() FROM PUBLIC, anon;
REVOKE EXECUTE ON FUNCTION public.module_assigned_via_course(uuid) FROM PUBLIC, anon;
REVOKE EXECUTE ON FUNCTION public.has_tool_permission(uuid) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.is_staff() TO authenticated;
GRANT EXECUTE ON FUNCTION public.module_assigned_via_course(uuid) TO authenticated;
GRANT EXECUTE ON FUNCTION public.has_tool_permission(uuid) TO authenticated;
REVOKE EXECUTE ON FUNCTION public.module_visible_via_permission(uuid) FROM PUBLIC, anon;
REVOKE EXECUTE ON FUNCTION public.module_is_published(uuid) FROM PUBLIC, anon;
REVOKE EXECUTE ON FUNCTION public.tool_in_assigned_module(uuid) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.module_visible_via_permission(uuid) TO authenticated;
GRANT EXECUTE ON FUNCTION public.module_is_published(uuid) TO authenticated;
GRANT EXECUTE ON FUNCTION public.tool_in_assigned_module(uuid) TO authenticated;

-- ── course_modules：教師看全部；學生看自己課程的指派 ─────────────────────────
DROP POLICY IF EXISTS "read course assignments" ON public.course_modules;
CREATE POLICY "read course assignments" ON public.course_modules
    FOR SELECT TO authenticated
    USING (public.is_staff() OR public.module_assigned_via_course(module_id));

-- ── 模組步驟：課程指派的模組全部步驟，或有核准的機器人所在步驟（模組須已發布） ──
DROP POLICY IF EXISTS "read assigned steps" ON public.module_steps;
CREATE POLICY "read assigned steps" ON public.module_steps
    FOR SELECT TO authenticated
    USING (
        public.is_staff()
        OR public.module_assigned_via_course(module_id)
        OR (public.has_tool_permission(tool_id) AND public.module_is_published(module_id))
    );

-- ── 學習模組：看得到其中任一步驟就看得到模組 ─────────────────────────────
DROP POLICY IF EXISTS "read assigned modules" ON public.learning_modules;
CREATE POLICY "read assigned modules" ON public.learning_modules
    FOR SELECT TO authenticated
    USING (
        public.is_staff()
        OR public.module_assigned_via_course(id)
        OR public.module_visible_via_permission(id)
    );

-- ── AI 機器人：已發布，且（逐位核准，或出現在課程指派的模組裡） ─────────────
DROP POLICY IF EXISTS "read assigned tools" ON public.ai_tools;
CREATE POLICY "read assigned tools" ON public.ai_tools
    FOR SELECT TO authenticated
    USING (
        public.is_staff()
        OR (
            status = 'published'
            AND (
                public.has_tool_permission(id)
                OR public.tool_in_assigned_module(id)
            )
        )
    );

DROP POLICY IF EXISTS "staff create tools" ON public.ai_tools;
CREATE POLICY "staff create tools" ON public.ai_tools
    FOR INSERT TO authenticated
    WITH CHECK (public.is_staff() AND created_by = auth.uid());

-- ── 案例：目前沒有和機器人綁定，登入者都可讀 ──────────────────────────────
DROP POLICY IF EXISTS "read cases" ON public.tool_cases;
CREATE POLICY "read cases" ON public.tool_cases
    FOR SELECT TO authenticated
    USING (true);

-- ── 作答紀錄：只能讀自己的，教師可讀全部 ─────────────────────────────────
DROP POLICY IF EXISTS "read own attempts" ON public.step_attempts;
CREATE POLICY "read own attempts" ON public.step_attempts
    FOR SELECT TO authenticated
    USING (user_id = auth.uid() OR public.is_staff());
