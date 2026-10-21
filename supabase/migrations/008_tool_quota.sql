-- ==============================================================================
-- Migration 008：自主模式的每日作答額度（tool_permissions）
--
-- 為什麼需要：
--   tool_permissions 只有 used_today，沒有記錄「是哪一天的次數」，
--   無法判斷該不該歸零。新增 used_date，跨日時把 used_today 視為 0。
--
-- consume_tool_quota()：在同一個 UPDATE 裡完成「跨日歸零、檢查額度、扣 1」，
--   學生同時按兩次送出也不會多扣或超用。
--   p_today 由後端傳入台灣時間的日期（與 api/tool_requests.py 判斷到期日的方式一致）。
--   只開放給 service_role（後端），學生不能自己呼叫。
--
-- 全部可重複執行。
-- ==============================================================================

ALTER TABLE public.tool_permissions
    ADD COLUMN IF NOT EXISTS used_date DATE;  -- used_today 是哪一天（台灣時間）的次數

CREATE OR REPLACE FUNCTION public.consume_tool_quota(p_permission_id uuid, p_today date)
RETURNS TABLE (allowed boolean, used integer, quota integer)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
BEGIN
    -- 還有額度：跨日就從 1 開始，同一天就 +1
    RETURN QUERY
    WITH consumed AS (
        UPDATE public.tool_permissions tp
           SET used_today = CASE WHEN tp.used_date IS DISTINCT FROM p_today
                                 THEN 1 ELSE COALESCE(tp.used_today, 0) + 1 END,
               used_date  = p_today
         WHERE tp.id = p_permission_id
           AND (CASE WHEN tp.used_date IS DISTINCT FROM p_today
                     THEN 0 ELSE COALESCE(tp.used_today, 0) END) < COALESCE(tp.daily_quota, 10)
        RETURNING tp.used_today AS new_used, COALESCE(tp.daily_quota, 10) AS new_quota
    )
    SELECT true, c.new_used, c.new_quota FROM consumed c;

    -- 額度用完：不扣，回傳目前用量讓後端組錯誤訊息
    IF NOT FOUND THEN
        RETURN QUERY
        SELECT false,
               CASE WHEN tp.used_date IS DISTINCT FROM p_today
                    THEN 0 ELSE COALESCE(tp.used_today, 0) END,
               COALESCE(tp.daily_quota, 10)
          FROM public.tool_permissions tp
         WHERE tp.id = p_permission_id;
    END IF;
END;
$$;

REVOKE EXECUTE ON FUNCTION public.consume_tool_quota(uuid, date) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.consume_tool_quota(uuid, date) TO service_role;
