-- ==============================================================================
-- Migration 011：AI 評分失敗時退回自主模式的每日額度（KNOWN_GAPS #18）
--
-- 為什麼需要：
--   consume_tool_quota()（migration 008）在送出作答時就扣 1 次，
--   之後 Gemini 逾時、429 或回傳格式錯誤導致評分失敗，次數不會退回，
--   學生會因為系統的錯誤被白扣次數。
--
-- refund_tool_quota()：退回 1 次。只退「同一天」扣的次數（used_date = p_today），
--   跨日後 used_today 本來就會歸零，不需要退；used_today 不會被退成負數。
--   只開放給 service_role（後端），學生不能自己呼叫。
--
-- 回傳退回後的 used / quota；沒有可退的（跨日、已是 0、紀錄被刪）回傳空集合。
-- 全部可重複執行。
-- ==============================================================================

CREATE OR REPLACE FUNCTION public.refund_tool_quota(p_permission_id uuid, p_today date)
RETURNS TABLE (used integer, quota integer)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
BEGIN
    RETURN QUERY
    UPDATE public.tool_permissions tp
       SET used_today = tp.used_today - 1
     WHERE tp.id = p_permission_id
       AND tp.used_date = p_today
       AND COALESCE(tp.used_today, 0) > 0
    RETURNING tp.used_today, COALESCE(tp.daily_quota, 10);
END;
$$;

REVOKE EXECUTE ON FUNCTION public.refund_tool_quota(uuid, date) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.refund_tool_quota(uuid, date) TO service_role;
