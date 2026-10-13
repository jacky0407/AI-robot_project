-- ==============================================================================
-- Migration 006：profiles 的 RLS 規則（原本只建在 Supabase 後台，補進 repo）
--
-- 規則：登入者只能讀自己那一列，不能新增、修改、刪除。
--   - 新使用者的 profiles 列由 on_auth_user_created trigger 建立
--   - 註冊補資料走 complete_registration()（SECURITY DEFINER）
--   - 角色（role）只能由後台或後端（service_role）修改，學生無法把自己改成 owner
--
-- 全部可重複執行。
-- ==============================================================================

ALTER TABLE public.profiles ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "users can read own profile" ON public.profiles;
CREATE POLICY "users can read own profile"
    ON public.profiles
    FOR SELECT
    TO authenticated
    USING (id = auth.uid());

-- 002 開放了 authenticated 的寫入權限；前端用不到，收回以免日後加錯規則讓學生改到 role
REVOKE INSERT, UPDATE, DELETE ON public.profiles FROM authenticated;