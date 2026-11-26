-- ==============================================================================
-- Migration 026：操作稽核紀錄（#90，需求說明書第十六節「安全」）
--
-- 記錄教師端「誰、在什麼時間、做了什麼」：改機器人、改評分規準、教師判定、匯出、
-- 核准或撤銷權限、改課程成員狀態、刪除邀請碼、模組與關卡的變更等（backend/core/audit.py）。
--
-- 規則：
--   - 只能新增、不能修改或刪除：只給 service_role SELECT / INSERT，並用觸發器擋掉 UPDATE / DELETE / TRUNCATE
--     （連後端程式寫錯也改不了；真的要清理只能由資料庫管理者先拿掉觸發器）
--   - actor_id 不設外鍵：帳號被刪除時不能去改舊紀錄（外鍵的 ON DELETE SET NULL 會被觸發器擋下）
--   - detail 只放 id、欄位名稱、版本號、數字等，不放學生作答原文或個資
--   - 前端讀不到（不建 RLS 政策），教授透過 GET /api/admin/audit-logs 查詢
--
-- 全部可重複執行。
-- ==============================================================================

CREATE TABLE IF NOT EXISTS public.audit_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    actor_id UUID,                          -- 操作者（profiles.id）；不設外鍵，見上方說明
    actor_role TEXT,                        -- 操作當時的角色（owner / assistant）
    action TEXT NOT NULL,                   -- 例如 tool.update、review.judge、export.research
    target_type TEXT,                       -- 例如 tool、module、step、attempt、course
    target_id TEXT,                         -- 被操作的對象 id
    detail JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT TIMEZONE('utc'::text, NOW()) NOT NULL
);

CREATE INDEX IF NOT EXISTS audit_logs_created_idx ON public.audit_logs (created_at DESC);
CREATE INDEX IF NOT EXISTS audit_logs_actor_idx ON public.audit_logs (actor_id, created_at DESC);
CREATE INDEX IF NOT EXISTS audit_logs_action_idx ON public.audit_logs (action, created_at DESC);

-- 只能新增：任何修改或刪除都直接失敗
CREATE OR REPLACE FUNCTION public.audit_logs_append_only()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '操作紀錄（audit_logs）只能新增，不能修改或刪除';
END;
$$;

DROP TRIGGER IF EXISTS audit_logs_no_update_delete ON public.audit_logs;
CREATE TRIGGER audit_logs_no_update_delete
    BEFORE UPDATE OR DELETE ON public.audit_logs
    FOR EACH ROW EXECUTE FUNCTION public.audit_logs_append_only();

DROP TRIGGER IF EXISTS audit_logs_no_truncate ON public.audit_logs;
CREATE TRIGGER audit_logs_no_truncate
    BEFORE TRUNCATE ON public.audit_logs
    FOR EACH STATEMENT EXECUTE FUNCTION public.audit_logs_append_only();

ALTER TABLE public.audit_logs ENABLE ROW LEVEL SECURITY;
-- 不建立任何政策：anon / authenticated 讀不到
REVOKE ALL ON public.audit_logs FROM anon, authenticated;
REVOKE ALL ON public.audit_logs FROM service_role;
GRANT SELECT, INSERT ON public.audit_logs TO service_role;
