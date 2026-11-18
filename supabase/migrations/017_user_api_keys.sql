-- ==============================================================================
-- Migration 017：學生自備 Gemini API 金鑰（「邀請碼與自備金鑰設計提案」第二階段、開會紀錄 #49 第 5 項）
--
-- user_api_keys：每位使用者最多一把
--   - encrypted_key：後端用 API_KEY_ENCRYPTION_KEY（Fernet）加密後才寫入，資料庫裡沒有明文
--   - key_hint：畫面上顯示用的前 4 碼與末 4 碼（例如 AIza…xY3k）
--   - 只給後端（service_role）讀寫；學生透過 /api/me/api-key 設定，API 永遠不回傳金鑰本身
--
-- 全部可重複執行。
-- ==============================================================================

CREATE TABLE IF NOT EXISTS public.user_api_keys (
    user_id UUID PRIMARY KEY REFERENCES public.profiles(id) ON DELETE CASCADE,
    encrypted_key TEXT NOT NULL,
    key_hint TEXT NOT NULL,
    verified_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT TIMEZONE('utc'::text, NOW()) NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT TIMEZONE('utc'::text, NOW()) NOT NULL
);

ALTER TABLE public.user_api_keys ENABLE ROW LEVEL SECURITY;
-- 不建立任何政策：anon / authenticated 讀不到（連加密後的值都不給前端）
REVOKE ALL ON public.user_api_keys FROM anon, authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.user_api_keys TO service_role;
