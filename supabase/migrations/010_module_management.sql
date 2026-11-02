-- ==============================================================================
-- Migration 010：模組與步驟管理（教授在後台建立模組、排步驟）
--
-- 為什麼需要：
--   原本沒有任何頁面或 API 能建立模組、把機器人排成步驟 1、2、3…，
--   只能直接寫資料庫。新的 /api/modules（backend/api/modules.py）需要：
--
--   1. module_steps.is_active：停用步驟
--      step_attempts.step_id 是 ON DELETE CASCADE，刪掉步驟會連學生作答一起刪掉。
--      所以已有作答的步驟不能刪，只能停用：學生看不到也不能作答，作答與 AI 評分完整保留。
--
--   2. 學生看不到停用的步驟（重建 005 的 "read assigned steps" 政策，多一個 is_active 條件）
--      教師端仍看得到全部步驟。
--
--   3. reorder_module_steps()：一次重排整個模組的步驟順序
--      UNIQUE(module_id, step_order) 讓兩個步驟無法直接互換號碼，
--      函式在同一個交易裡先把順序移到負數再寫回 1、2、3…。只開放給 service_role（後端）。
--
-- 全部可重複執行。需要先跑過 005。
-- ==============================================================================

ALTER TABLE public.module_steps
    ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT true;  -- false = 已停用

-- ── 學生看不到停用的步驟 ──────────────────────────────────────────────────
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
            )
        )
    );

-- ── 重排步驟順序 ──────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION public.reorder_module_steps(p_module_id uuid, p_step_ids uuid[])
RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
BEGIN
    -- 傳進來的必須剛好是這個模組的全部步驟，不多不少、不重複
    IF cardinality(p_step_ids) <> (SELECT count(*) FROM public.module_steps WHERE module_id = p_module_id)
       OR cardinality(p_step_ids) <> (SELECT count(DISTINCT s) FROM unnest(p_step_ids) AS s)
       OR EXISTS (
            SELECT 1 FROM unnest(p_step_ids) AS s(id)
             WHERE NOT EXISTS (
                SELECT 1 FROM public.module_steps m WHERE m.id = s.id AND m.module_id = p_module_id
             )
       )
    THEN
        RAISE EXCEPTION 'step ids do not match module %', p_module_id USING ERRCODE = '22023';
    END IF;

    -- 先移到負數，避開 UNIQUE(module_id, step_order)
    UPDATE public.module_steps
       SET step_order = -step_order
     WHERE module_id = p_module_id;

    UPDATE public.module_steps m
       SET step_order = s.ord
      FROM unnest(p_step_ids) WITH ORDINALITY AS s(id, ord)
     WHERE m.id = s.id;
END;
$$;

REVOKE EXECUTE ON FUNCTION public.reorder_module_steps(uuid, uuid[]) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.reorder_module_steps(uuid, uuid[]) TO service_role;
