-- ==============================================================================
-- Migration 027：帳號停用、去識別化與永久刪除（#87，需求說明書 ACC-006、PRI-005）
--
-- 帳號狀態 profiles.account_status：
--   active            使用中
--   disabled          停用：不能登入，資料全部保留，可以隨時恢復（教授、助理都可以操作）
--   deidentified      去識別化：姓名、Email 換成匿名研究編號，學習歷程保留給研究，不能復原（只限教授）
--   pending_deletion  待刪除：教授申請永久刪除後的 30 天回收期，不能登入，期間可以復原；
--                     到期後由教授在後台按「清除到期帳號」真正刪除（連同他自己的學習歷程）
--
-- profiles.is_active 保留，並與 account_status 同步（account_status = 'active' 才是 true）：
-- 後端 core/auth.py 與現有程式都看 is_active，不用全部改。
--
-- ⚠️ 研究資料保護：teacher_reviews.teacher_id 原本是 ON DELETE CASCADE，
--    刪掉一位老師會連帶刪掉他寫過的所有教師判定（學生的研究資料）。改成 SET NULL，判定保留。
--    其他連到 profiles 的 CASCADE 都是「這個人自己的資料」（自己的作答、選課、同意書…），
--    永久刪除學生時本來就要一起刪（#87 決定：永久刪除 = 全部刪除；要保留研究資料請用去識別化）。
--
-- ⚠️ 部署順序：先跑這支 migration，再部署後端。全部可重複執行。
-- ==============================================================================

-- ── 1. 帳號狀態 ─────────────────────────────────────────────────────────────
ALTER TABLE public.profiles
    ADD COLUMN IF NOT EXISTS account_status TEXT NOT NULL DEFAULT 'active',
    ADD COLUMN IF NOT EXISTS status_reason TEXT,                   -- 停用、去識別化、刪除的原因（必填）
    ADD COLUMN IF NOT EXISTS status_changed_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS status_changed_by UUID REFERENCES public.profiles(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS deletion_scheduled_for TIMESTAMPTZ,   -- 待刪除：這個時間之後才能真正刪除
    ADD COLUMN IF NOT EXISTS status_before_deletion TEXT;          -- 申請刪除前的狀態；30 天內復原時回到這個狀態

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profiles_account_status_check') THEN
        ALTER TABLE public.profiles ADD CONSTRAINT profiles_account_status_check
            CHECK (account_status IN ('active', 'disabled', 'deidentified', 'pending_deletion'));
    END IF;
END $$;

-- 舊資料：原本 is_active = false 的帳號視為停用
UPDATE public.profiles SET account_status = 'disabled'
WHERE is_active IS FALSE AND account_status = 'active';

-- ── 2. 刪老師不能連帶刪掉教師判定 ─────────────────────────────────────────────
ALTER TABLE public.teacher_reviews ALTER COLUMN teacher_id DROP NOT NULL;

-- 外鍵的名稱不一定是預設的，查出 teacher_id 上所有外鍵再刪，避免留下舊的 CASCADE
DO $$
DECLARE
    c RECORD;
BEGIN
    FOR c IN
        SELECT con.conname
        FROM pg_constraint con
        JOIN pg_attribute att ON att.attrelid = con.conrelid AND att.attnum = ANY (con.conkey)
        WHERE con.conrelid = 'public.teacher_reviews'::regclass
          AND con.contype = 'f'
          AND att.attname = 'teacher_id'
    LOOP
        EXECUTE format('ALTER TABLE public.teacher_reviews DROP CONSTRAINT %I', c.conname);
    END LOOP;
END $$;

ALTER TABLE public.teacher_reviews
    ADD CONSTRAINT teacher_reviews_teacher_id_fkey
    FOREIGN KEY (teacher_id) REFERENCES public.profiles(id) ON DELETE SET NULL;
