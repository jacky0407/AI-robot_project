-- ==============================================================================
-- Migration 020：多機器人聊天室（#68）
--
-- 聊天室取代原本「寫完一整份作答再送出」的關卡：學生一則一則對話，
-- 每則訊息只由一個機器人回答（學生 @ 指名，沒指名由調度員依內容選）。
--
--   module_steps.step_type：answer（原本的作答關卡）／chat（聊天室）
--   module_steps.tool_id  ：聊天室關卡的「評分機器人」——學生 @ 它或按「送出評分」時，
--                           它依自己的評分規準評整段對話，結果照舊寫進 step_attempts／ai_evaluations
--   chat_room_bots        ：聊天室裡其他可以對話的機器人與「什麼時候該它出場」
--   chat_messages         ：對話內容（誰說的、為什麼由這個機器人回答）
--
-- 只給後端（service_role）讀寫：學生與教師都透過 API。全部可重複執行。
-- ==============================================================================

ALTER TABLE public.module_steps
    ADD COLUMN IF NOT EXISTS step_type TEXT NOT NULL DEFAULT 'answer';

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'module_steps_step_type_check') THEN
        ALTER TABLE public.module_steps
            ADD CONSTRAINT module_steps_step_type_check CHECK (step_type IN ('answer', 'chat'));
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS public.chat_room_bots (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    step_id UUID NOT NULL REFERENCES public.module_steps(id) ON DELETE CASCADE,
    tool_id UUID NOT NULL REFERENCES public.ai_tools(id) ON DELETE RESTRICT,
    display_name TEXT NOT NULL,            -- 聊天室裡顯示的名字，學生用 @名字 指名
    when_to_use TEXT NOT NULL DEFAULT '',  -- 給調度員看：學生說什麼時該它出場
    sort_order INT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT TIMEZONE('utc'::text, NOW()) NOT NULL,
    UNIQUE (step_id, tool_id),
    UNIQUE (step_id, display_name)
);

CREATE TABLE IF NOT EXISTS public.chat_messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,  -- 對話的學生
    module_id UUID NOT NULL REFERENCES public.learning_modules(id) ON DELETE CASCADE,
    step_id UUID NOT NULL REFERENCES public.module_steps(id) ON DELETE CASCADE,
    session_id UUID REFERENCES public.practice_sessions(id) ON DELETE SET NULL,
    role TEXT NOT NULL CHECK (role IN ('student', 'bot')),
    tool_id UUID REFERENCES public.ai_tools(id) ON DELETE SET NULL,          -- 回答的機器人（學生訊息為 NULL）
    bot_name TEXT,
    content TEXT NOT NULL,
    routing JSONB,                          -- {"by": "mention" | "router" | "only_bot" | "evaluate", "reason": "..."}
    attempt_id UUID REFERENCES public.step_attempts(id) ON DELETE SET NULL,  -- 評分訊息對應的作答紀錄
    created_at TIMESTAMPTZ DEFAULT TIMEZONE('utc'::text, NOW()) NOT NULL
);

CREATE INDEX IF NOT EXISTS chat_messages_thread_idx
    ON public.chat_messages (user_id, step_id, created_at);

ALTER TABLE public.chat_room_bots ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.chat_messages ENABLE ROW LEVEL SECURITY;
-- 不建立任何政策：anon / authenticated 讀不到，只有 service_role 可以存取
REVOKE ALL ON public.chat_room_bots FROM anon, authenticated;
REVOKE ALL ON public.chat_messages FROM anon, authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.chat_room_bots TO service_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.chat_messages TO service_role;
