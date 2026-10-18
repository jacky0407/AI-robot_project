-- ==============================================================================
-- Migration 004：註冊流程（/onboarding）需要的欄位與函式
--
-- 為什麼需要：
--   前端的 middleware、/auth/callback、首頁與 /onboarding 都會讀
--   profiles.registration_completed，/onboarding 會呼叫 complete_registration()。
--   這些原本只建在 Supabase 後台，沒有存進 repo——照 schema.sql 建的新資料庫
--   會在登入後查詢失敗（導回 /?error=profile）或卡在 /onboarding。
--
-- 誰要跑：
--   所有人，在 schema.sql 之後執行。全部可重複執行。
--
-- ⚠️ 已經在後台手動建過 complete_registration() 的資料庫：
--   本檔「不會覆蓋」既有的函式（見第 2 段），以免蓋掉後台版本的額外邏輯。
--   若要改用本檔的版本，請先 DROP FUNCTION public.complete_registration(TEXT, TEXT); 再執行。
-- ==============================================================================

-- ── 1. profiles 補欄位 ────────────────────────────────────────────────────
ALTER TABLE public.profiles
    ADD COLUMN IF NOT EXISTS profession TEXT,
    ADD COLUMN IF NOT EXISTS registration_completed BOOLEAN NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS terms_accepted_at TIMESTAMPTZ;

COMMENT ON COLUMN public.profiles.profession IS
    '職業 / 身分（preschool_teacher、special_ed_teacher、early_intervention、student、other）';
COMMENT ON COLUMN public.profiles.registration_completed IS
    '是否已在 /onboarding 補完基本資料並同意條款；false 時 middleware 會導回 /onboarding';
COMMENT ON COLUMN public.profiles.terms_accepted_at IS
    '同意服務條款與隱私權政策的時間';

-- ── 2. 完成註冊的函式（給 /onboarding 呼叫）─────────────────────────────────
-- 只能更新「自己」那一列（auth.uid()），不能指定別人的 id，也不能改 role。
-- 函式只在不存在時建立，避免覆蓋後台既有版本。
DO $outer$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_proc p
        JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = 'public' AND p.proname = 'complete_registration'
    ) THEN
        EXECUTE $fn$
            CREATE FUNCTION public.complete_registration(p_full_name TEXT, p_profession TEXT)
            RETURNS void
            LANGUAGE plpgsql
            SECURITY DEFINER
            SET search_path = public
            AS $body$
            BEGIN
                IF auth.uid() IS NULL THEN
                    RAISE EXCEPTION '尚未登入';
                END IF;

                IF coalesce(btrim(p_full_name), '') = '' THEN
                    RAISE EXCEPTION '姓名不可空白';
                END IF;

                UPDATE public.profiles
                SET full_name = btrim(p_full_name),
                    profession = p_profession,
                    terms_accepted_at = now(),
                    registration_completed = true
                WHERE id = auth.uid();
            END;
            $body$
        $fn$;
    END IF;
END
$outer$;

-- ── 3. 權限：只有登入者能呼叫 ─────────────────────────────────────────────
-- 逐一處理所有同名函式（後台版本的參數可能不同），不寫死參數型別
DO $$
DECLARE
    fn regprocedure;
BEGIN
    FOR fn IN
        SELECT p.oid::regprocedure
        FROM pg_proc p
        JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = 'public' AND p.proname = 'complete_registration'
    LOOP
        EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC, anon', fn);
        EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO authenticated, service_role', fn);
    END LOOP;
END $$;
