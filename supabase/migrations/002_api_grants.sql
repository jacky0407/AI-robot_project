-- ==============================================================================
-- Migration 002：資料表存取權限（GRANT）
--
-- 為什麼需要：
--   Supabase 自 2026-05-30 起，新專案「不再自動」把 public 底下的新資料表
--   開放給 API 角色（anon / authenticated / service_role）；
--   舊專案也會在 2026-10-30 套用同樣規則。
--   沒有 GRANT 時，後端與前端讀資料表都會得到：
--     permission denied for table profiles (42501)
--
-- 誰要跑：
--   所有人。不論新舊資料庫，在 schema.sql（與 seed.sql）之後執行一次。
--   全部可重複執行。
--
-- 權限設計：
--   service_role   → 後端 FastAPI 使用，全部資料表可讀寫
--   authenticated  → 已登入的前端使用者。目前前端直接讀寫資料表，先開放讀寫；
--                    ⚠️ 尚未開 RLS，任何登入者都能讀到所有列，正式上線前必須補 RLS
--   anon           → 未登入者，不開放任何資料表
-- ==============================================================================

GRANT USAGE ON SCHEMA public TO anon, authenticated, service_role;

-- ── 後端（service_role）：全部資料表、序列 ─────────────────────────────────
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO service_role;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO service_role;

-- 之後新增的資料表也自動給後端權限（只給 service_role，不擴及前端角色）
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO service_role;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO service_role;

-- ── 前端已登入使用者（authenticated）：逐表列出 ─────────────────────────────
GRANT SELECT, INSERT, UPDATE, DELETE ON
    public.profiles,
    public.courses,
    public.course_members,
    public.tool_permissions,
    public.ai_tools,
    public.tool_cases,
    public.learning_modules,
    public.module_steps,
    public.step_attempts,
    public.ai_evaluations,
    public.teacher_reviews,
    public.prompt_logs,
    public.forms,
    public.form_responses,
    public.research_consents
TO authenticated;

-- ── 前端呼叫的資料庫函式 ─────────────────────────────────────────────────
-- complete_registration 是在 Supabase 後台建立的（尚未存進 repo），
-- 存在才授權，避免在沒有這個函式的資料庫上報錯。
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
        EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO authenticated, service_role', fn);
    END LOOP;
END $$;
