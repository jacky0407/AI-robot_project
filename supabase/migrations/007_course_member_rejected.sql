-- ==============================================================================
-- Migration 007：course_members 新增「rejected」狀態
--
-- 為什麼需要：
--   原本只有 active / pending / removed，老師「拒絕加入申請」和「把成員移出課程」
--   都只能存成 removed，事後分不出來，學生端也無法顯示「申請未通過」。
--   tool_permissions 已經有 rejected，兩種申請用同一套狀態名稱，前端比較好處理。
--
-- 狀態意義：
--   pending   等待老師核准（課程設為需審核時）
--   active    已加入
--   rejected  老師拒絕加入申請
--   removed   老師把已加入（或待審核）的成員移出
--
-- 全部可重複執行。
-- ==============================================================================

ALTER TABLE public.course_members
    DROP CONSTRAINT IF EXISTS course_members_status_check;

ALTER TABLE public.course_members
    ADD CONSTRAINT course_members_status_check
    CHECK (status IN ('active', 'pending', 'rejected', 'removed'));
