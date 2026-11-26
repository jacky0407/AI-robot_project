-- ==============================================================================
-- Migration 025：自主探索改成「申請模組」（#86，11/12 會議 #49 第 10 項）
--
-- 學生在自主探索申請的是「模組」，核准後可以做模組裡所有已發布、啟用中的關卡。
-- 沿用 tool_permissions（一筆 = 一次申請，期限與每日次數的規則都不變），加一欄 module_id：
--
--   module_id 有值              → 核准使用這個模組（新的申請都是這種）
--   tool_id 有值                → 舊的「申請機器人」，照常有效到原本的到期日（不轉換、不刪除）
--   tool_id、module_id 都是 NULL → 全部機器人（只能從後台設定）
--
-- ⚠️ 重要：原本「tool_id IS NULL = 全部機器人」，現在模組申請的 tool_id 也是 NULL，
--    所以 has_tool_permission() 要改成「兩欄都是 NULL」才算全部，否則核准一個模組會變成全部機器人都能用。
--
-- ⚠️ 部署順序：先跑這支 migration，再部署後端（Render Manual Deploy）。
--    舊版後端不會建立模組申請，先跑 migration 不影響現有功能。
-- 全部可重複執行。
-- ==============================================================================

-- ── 1. 申請的模組 ───────────────────────────────────────────────────────────
-- 模組被刪除時，申請紀錄一起刪掉：若改成 SET NULL，會變成「兩欄都是 NULL = 全部機器人」
-- （有學生作答的模組本來就不能刪，見 backend/api/modules.py）
ALTER TABLE public.tool_permissions
    ADD COLUMN IF NOT EXISTS module_id UUID REFERENCES public.learning_modules(id) ON DELETE CASCADE;

CREATE INDEX IF NOT EXISTS tool_permissions_user_status_idx ON public.tool_permissions (user_id, status);

-- ── 2. 判斷函式 ─────────────────────────────────────────────────────────────

-- 有沒有被核准使用這支機器人（舊的機器人申請，或全部機器人）；模組申請不算在這裡
CREATE OR REPLACE FUNCTION public.has_tool_permission(p_tool_id uuid)
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
    SELECT EXISTS (
        SELECT 1 FROM public.tool_permissions tp
        WHERE tp.user_id = auth.uid()
          AND tp.status = 'approved'
          AND (tp.tool_id = p_tool_id OR (tp.tool_id IS NULL AND tp.module_id IS NULL))
          AND (tp.start_date IS NULL OR tp.start_date <= public.today_taipei())
          AND (tp.expire_date IS NULL OR tp.expire_date >= public.today_taipei())
    );
$$;

-- 有沒有被核准使用這個模組（#86）
CREATE OR REPLACE FUNCTION public.has_module_permission(p_module_id uuid)
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
    SELECT EXISTS (
        SELECT 1 FROM public.tool_permissions tp
        WHERE tp.user_id = auth.uid()
          AND tp.status = 'approved'
          AND tp.module_id = p_module_id
          AND (tp.start_date IS NULL OR tp.start_date <= public.today_taipei())
          AND (tp.expire_date IS NULL OR tp.expire_date >= public.today_taipei())
    );
$$;

-- 模組已發布，且（被核准使用這個模組，或其中有被核准的機器人）
CREATE OR REPLACE FUNCTION public.module_visible_via_permission(p_module_id uuid)
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
    SELECT EXISTS (
        SELECT 1 FROM public.learning_modules m
        WHERE m.id = p_module_id
          AND m.is_published
          AND (
              public.has_module_permission(m.id)
              OR EXISTS (
                  SELECT 1 FROM public.module_steps s
                  WHERE s.module_id = m.id AND public.has_tool_permission(s.tool_id)
              )
          )
    );
$$;

-- 機器人是否出現在登入者被核准的模組裡（啟用中的關卡）
CREATE OR REPLACE FUNCTION public.tool_in_permitted_module(p_tool_id uuid)
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
    SELECT EXISTS (
        SELECT 1 FROM public.module_steps s
        WHERE s.tool_id = p_tool_id
          AND s.is_active
          AND public.module_is_published(s.module_id)
          AND public.has_module_permission(s.module_id)
    );
$$;

REVOKE EXECUTE ON FUNCTION public.has_module_permission(uuid) FROM PUBLIC, anon;
REVOKE EXECUTE ON FUNCTION public.tool_in_permitted_module(uuid) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.has_module_permission(uuid) TO authenticated;
GRANT EXECUTE ON FUNCTION public.tool_in_permitted_module(uuid) TO authenticated;

-- ── 3. 讀取規則：被核准的模組，學生看得到它的關卡與機器人 ───────────────────────

DROP POLICY IF EXISTS "read assigned steps" ON public.module_steps;
CREATE POLICY "read assigned steps" ON public.module_steps
    FOR SELECT TO authenticated
    USING (
        public.is_staff()
        OR (
            is_active
            AND (
                public.module_assigned_via_course(module_id)
                OR (public.has_tool_permission(tool_id) AND public.module_is_published(module_id))
                OR (public.has_module_permission(module_id) AND public.module_is_published(module_id))
            )
        )
    );

-- learning_modules 的 "read assigned modules" 用 module_visible_via_permission()，上面已更新，不用改政策

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
                OR public.tool_in_permitted_module(id)
            )
        )
    );
