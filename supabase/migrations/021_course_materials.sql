-- ==============================================================================
-- Migration 021：學習助教的教材庫（RAG，#67）
--
-- 老師把教材講義放進課程（course_materials），後端切段、算向量存在 material_chunks；
-- 聊天室裡勾了「會查教材」的機器人（chat_room_bots.use_materials）回答前先搜尋這門課的教材，
-- 回答時標出引用的段落。學生自己的作答紀錄不進向量庫，由後端依登入身分直接查。
--
-- 只給後端（service_role）讀寫。需要 pgvector（Supabase 內建，CREATE EXTENSION 即可）。
-- 向量維度 768：後端呼叫 Gemini embedding 時指定 output_dimensionality = 768（core/ai/embeddings.py）。
-- 全部可重複執行；要先跑 020。
-- ==============================================================================

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS public.course_materials (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    course_id UUID NOT NULL REFERENCES public.courses(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    source_type TEXT NOT NULL DEFAULT 'text',   -- text：貼上或 .txt/.md；pdf/docx/pptx 由 #66 的上傳流程補上
    char_count INT NOT NULL DEFAULT 0,
    chunk_count INT NOT NULL DEFAULT 0,
    created_by UUID REFERENCES public.profiles(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ DEFAULT TIMEZONE('utc'::text, NOW()) NOT NULL
);

CREATE TABLE IF NOT EXISTS public.material_chunks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    material_id UUID NOT NULL REFERENCES public.course_materials(id) ON DELETE CASCADE,
    course_id UUID NOT NULL REFERENCES public.courses(id) ON DELETE CASCADE,  -- 冗餘一份，搜尋時直接過濾
    chunk_index INT NOT NULL,
    content TEXT NOT NULL,
    embedding vector(768) NOT NULL,
    UNIQUE (material_id, chunk_index)
);

CREATE INDEX IF NOT EXISTS material_chunks_course_idx ON public.material_chunks (course_id);

-- 搜尋：只搜指定課程的教材（不跨課程），依餘弦相似度由高到低
CREATE OR REPLACE FUNCTION public.match_material_chunks(
    p_course_ids UUID[],
    p_query vector(768),
    p_match_count INT DEFAULT 5
)
RETURNS TABLE (chunk_id UUID, material_id UUID, title TEXT, chunk_index INT, content TEXT, similarity FLOAT)
LANGUAGE sql STABLE
SET search_path = public
AS $$
    SELECT c.id, c.material_id, m.title, c.chunk_index, c.content, 1 - (c.embedding <=> p_query) AS similarity
    FROM public.material_chunks c
    JOIN public.course_materials m ON m.id = c.material_id
    WHERE c.course_id = ANY (p_course_ids)
    ORDER BY c.embedding <=> p_query
    LIMIT GREATEST(1, LEAST(p_match_count, 20));
$$;

ALTER TABLE public.chat_room_bots
    ADD COLUMN IF NOT EXISTS use_materials BOOLEAN NOT NULL DEFAULT false;  -- 學習助教：回答前先查教材

ALTER TABLE public.course_materials ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.material_chunks ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.course_materials FROM anon, authenticated;
REVOKE ALL ON public.material_chunks FROM anon, authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.course_materials TO service_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.material_chunks TO service_role;
REVOKE ALL ON FUNCTION public.match_material_chunks(UUID[], vector, INT) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.match_material_chunks(UUID[], vector, INT) TO service_role;
