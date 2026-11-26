-- ==============================================================================
-- Migration 030：一個模組一個聊天室（#108，老師 2026-11-26 決定）
--
-- 模組全部改成聊天：學生畫面把整個模組合成一間連續的聊天室，原本的關卡變成聊天室裡的「階段」。
-- 資料結構保留關卡：訊息、評分、教師複核、解鎖、表單、匯出都照舊記在各關卡（module_steps）底下。
--
--   module_steps.step_type  既有的 answer 關卡一律改成 chat，預設值改成 chat
--                           （評分機器人沿用原本的 tool_id；沒有對話機器人的階段，由評分機器人陪學生對話）
--   chat_room_bots.module_id  新增：module_id 有值、step_id 為 NULL = 整個模組共用的對話機器人（例如學習助教）
--                           step_id 有值 = 只在這個階段出場（原本的用法）
--
-- 全部可重複執行。
-- ==============================================================================

UPDATE public.module_steps SET step_type = 'chat' WHERE step_type IS DISTINCT FROM 'chat';
ALTER TABLE public.module_steps ALTER COLUMN step_type SET DEFAULT 'chat';

ALTER TABLE public.chat_room_bots
    ADD COLUMN IF NOT EXISTS module_id UUID REFERENCES public.learning_modules(id) ON DELETE CASCADE;
ALTER TABLE public.chat_room_bots ALTER COLUMN step_id DROP NOT NULL;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chat_room_bots_scope_check') THEN
        ALTER TABLE public.chat_room_bots
            ADD CONSTRAINT chat_room_bots_scope_check CHECK ((step_id IS NULL) <> (module_id IS NULL));
    END IF;
END $$;

-- 模組共用的機器人：同一個模組內機器人與名稱不重複（階段的唯一性沿用 020 的 UNIQUE(step_id, …)）
CREATE UNIQUE INDEX IF NOT EXISTS chat_room_bots_module_tool_idx
    ON public.chat_room_bots (module_id, tool_id) WHERE module_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS chat_room_bots_module_name_idx
    ON public.chat_room_bots (module_id, display_name) WHERE module_id IS NOT NULL;

-- 讀整個模組的對話（學生的模組聊天室、老師監控）
CREATE INDEX IF NOT EXISTS chat_messages_module_thread_idx
    ON public.chat_messages (user_id, module_id, created_at);
