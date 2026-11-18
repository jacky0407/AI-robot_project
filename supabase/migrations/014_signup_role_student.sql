-- ==============================================================================
-- Migration 014：註冊時一律建立成學生，不再相信註冊資料裡的 role（安全修正）
--
-- 問題：
--   handle_new_platform_user() 用 NEW.raw_user_meta_data->>'role' 決定新帳號的角色。
--   raw_user_meta_data 是註冊時「使用者自己送來」的資料，前端用的 Supabase anon key 又是公開的，
--   任何人都可以自己呼叫 supabase.auth.signUp({ ..., options: { data: { role: 'owner' } } })，
--   註冊完就是教授：看得到所有學生作答、核准申請、刪除機器人。
--
-- 修正：
--   新帳號一律是 student。教師身分只能由 Supabase 後台或後端（service_role）設定，
--   與 006「學生不能改自己的 role」的設計一致。full_name 仍然從註冊資料帶入（不影響權限）。
--   另外補上 SET search_path（SECURITY DEFINER 函式的建議寫法）。
--
-- 執行後請跑本檔最下方的查詢，檢查有沒有不該是教師的帳號。
-- 全部可重複執行。
-- ==============================================================================

CREATE OR REPLACE FUNCTION public.handle_new_platform_user()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    -- 1. 建立 profile：角色固定為 student，不讀 raw_user_meta_data 的 role
    INSERT INTO public.profiles (id, email, full_name, role)
    VALUES (
        NEW.id,
        NEW.email,
        COALESCE(NEW.raw_user_meta_data->>'full_name', ''),
        'student'
    );

    -- 2. 自動預先配置一組隨機匿名研究編號
    INSERT INTO public.research_consents (user_id, anonymous_research_id, consent_version, is_consented)
    VALUES (
        NEW.id,
        'SPED-' || SUBSTRING(gen_random_uuid()::text, 1, 8),
        'v1.0',
        false
    );

    RETURN NEW;
END;
$$;

-- trigger 本身不用重建：它呼叫的就是上面這個函式，CREATE OR REPLACE 後立即生效

-- ── 檢查：目前所有教師帳號（請逐一確認都是你們認識的人）──────────────────────────
-- 若有不認識的帳號，用：UPDATE public.profiles SET role = 'student' WHERE id = '<那個帳號的 id>';
--
-- SELECT p.id, p.email, p.full_name, p.role, p.created_at,
--        u.raw_user_meta_data->>'role' AS role_claimed_at_signup
--   FROM public.profiles p
--   JOIN auth.users u ON u.id = p.id
--  WHERE p.role IN ('owner', 'assistant')
--  ORDER BY p.created_at DESC;
