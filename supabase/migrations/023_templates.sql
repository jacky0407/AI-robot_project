-- ==============================================================================
-- Migration 023：機器人與模組可以存成範本（#89，需求說明書 BLD-002、BLD-008）
--
-- 範本另外存一張表，跟正式的機器人、模組完全分開：
--   - 學生看不到範本（只給後端讀寫）
--   - 範本存的是「當時內容的完整副本」（content），原本的機器人或模組之後被修改、刪除，範本都不受影響
--   - 從範本建立 = 把 content 複製成一支新的機器人（第 1 版、草稿）或一個新的模組（未發布）
--
--   kind = 'tool'  ：content 是機器人的教學內容（title、system_prompt、rubric_criteria…）
--   kind = 'module'：content 是模組資料與關卡清單；關卡指向現有的機器人（tool_id），不會連機器人一起複製
--
-- 只給後端（service_role）讀寫：教師一律透過 API（backend/api/templates.py）。全部可重複執行。
-- ==============================================================================

CREATE TABLE IF NOT EXISTS public.templates (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    kind TEXT NOT NULL CHECK (kind IN ('tool', 'module')),
    title TEXT NOT NULL,
    description TEXT,
    content JSONB NOT NULL,                -- 存成範本當時的完整內容
    source_id UUID,                        -- 從哪支機器人／哪個模組存的；只是紀錄，不設外鍵（原本的被刪掉，範本照樣留著）
    source_version INT,                    -- 機器人範本：存的是第幾版
    created_by UUID REFERENCES public.profiles(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ DEFAULT TIMEZONE('utc'::text, NOW()) NOT NULL
);

CREATE INDEX IF NOT EXISTS templates_kind_idx ON public.templates (kind, created_at);

ALTER TABLE public.templates ENABLE ROW LEVEL SECURITY;
-- 不建立任何政策：anon / authenticated 讀不到，只有 service_role 可以存取
REVOKE ALL ON public.templates FROM anon, authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.templates TO service_role;
