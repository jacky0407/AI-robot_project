-- ==============================================================================
-- Migration 024：模組可以停在機器人的舊版本（#89 後半，需求說明書 BLD-008）
--
-- 同一支機器人被多個模組使用時，老師修改機器人時可以逐個模組選：
--   - 更新成新版：tool_version = NULL（跟著機器人最新版，原本的行為）
--   - 停在目前這一版：tool_version = 修改前的版本號
-- 停在舊版的關卡，學生作答、提示、聊天室、評分都用 ai_tool_versions 裡那一版的內容，
-- ai_evaluations.tool_version 也會記那一版。機器人的名稱與狀態（發布、封存）仍以最新的為準。
--
--   module_steps.tool_version  ：關卡本身的機器人（作答關卡／聊天室的評分機器人）
--   chat_room_bots.tool_version：聊天室裡的對話機器人
--
-- 需要先跑 022（ai_tool_versions）與 020（chat_room_bots）。全部可重複執行。
-- ==============================================================================

ALTER TABLE public.module_steps
    ADD COLUMN IF NOT EXISTS tool_version INT;   -- NULL = 跟著最新版；有值 = 停在這一版

ALTER TABLE public.chat_room_bots
    ADD COLUMN IF NOT EXISTS tool_version INT;   -- 同上
