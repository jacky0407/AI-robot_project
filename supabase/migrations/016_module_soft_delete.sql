-- ==============================================================================
-- Migration 016：已有學生作答的模組也能「刪除」（移到已刪除，作答保留）— 開會紀錄 #49 第 2 項
--
-- 為什麼不直接刪：step_attempts.module_id / step_id 都是 ON DELETE CASCADE，
--   真的刪掉模組會連學生作答、AI 評分、教師判定一起消失，研究資料就沒了。
--
-- 做法：learning_modules.deleted_at 有值 = 已刪除
--   - 後端刪除時同時設 is_published = false、取消所有課程指派 → 學生端（RLS 只看已發布且有指派的模組）看不到
--   - 教師端的模組清單、指派選單都不再列出
--   - 作答紀錄完整保留，研究匯出仍可使用
--   沒有任何作答的模組照舊直接刪除。
--
-- 全部可重複執行。
-- ==============================================================================

ALTER TABLE public.learning_modules
    ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ; -- 有值 = 已刪除（作答保留），見 api/modules.py
