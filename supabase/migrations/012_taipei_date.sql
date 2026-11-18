-- ==============================================================================
-- Migration 012：機器人使用期限改用台灣時間判斷
--
-- 為什麼需要：
--   005 的 has_tool_permission() 用 CURRENT_DATE 判斷「今天」，但資料庫時區是 UTC。
--   後端（api/tool_requests.py、core/access.py）都用台灣時間（UTC+8），
--   兩邊差 8 小時：到期日隔天的 00:00–08:00，API 顯示「已過期」、作答也被擋，
--   學生卻還看得到機器人。
--
-- 做了什麼：
--   1. 新增 today_taipei()：回傳台灣時間的今天，之後的 SQL 都用它，不要再用 CURRENT_DATE
--   2. has_tool_permission() 改用 today_taipei()，其餘邏輯與 005 相同
--   3. tool_permissions.start_date 的預設值改成台灣時間的今天
--
-- 全部可重複執行。
-- ==============================================================================

-- ── 1. 台灣時間的今天 ─────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION public.today_taipei()
RETURNS date LANGUAGE sql STABLE AS $$
    SELECT (now() AT TIME ZONE 'Asia/Taipei')::date;
$$;

REVOKE EXECUTE ON FUNCTION public.today_taipei() FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.today_taipei() TO authenticated, service_role;

-- ── 2. 有效核准的判斷改用台灣時間（到期日當天仍可使用）────────────────────────
CREATE OR REPLACE FUNCTION public.has_tool_permission(p_tool_id uuid)
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
    SELECT EXISTS (
        SELECT 1 FROM public.tool_permissions tp
        WHERE tp.user_id = auth.uid()
          AND tp.status = 'approved'
          AND (tp.tool_id = p_tool_id OR tp.tool_id IS NULL)
          AND (tp.start_date IS NULL OR tp.start_date <= public.today_taipei())
          AND (tp.expire_date IS NULL OR tp.expire_date >= public.today_taipei())
    );
$$;

REVOKE EXECUTE ON FUNCTION public.has_tool_permission(uuid) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.has_tool_permission(uuid) TO authenticated;

-- ── 3. 申請當天的預設值也用台灣時間 ─────────────────────────────────────────
ALTER TABLE public.tool_permissions
    ALTER COLUMN start_date SET DEFAULT public.today_taipei();
