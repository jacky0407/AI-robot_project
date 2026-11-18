-- ==============================================================================
-- Migration 018：資料匯出稽核紀錄（DAT-001、開會紀錄 #49 第 7 項）
--
-- 每次匯出（教學用含姓名、研究用去識別化）記一筆：誰、何時、哪門課、什麼條件、匯出幾筆。
-- 只給後端（service_role）寫入與讀取。
-- 全部可重複執行。
-- ==============================================================================

CREATE TABLE IF NOT EXISTS public.export_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    exported_by UUID REFERENCES public.profiles(id) ON DELETE SET NULL,
    kind TEXT NOT NULL CHECK (kind IN ('teaching', 'research')),
    course_id UUID REFERENCES public.courses(id) ON DELETE SET NULL,
    params JSONB NOT NULL DEFAULT '{}'::jsonb,
    row_count INT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT TIMEZONE('utc'::text, NOW()) NOT NULL
);

ALTER TABLE public.export_logs ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.export_logs FROM anon, authenticated;
GRANT SELECT, INSERT ON public.export_logs TO service_role;
