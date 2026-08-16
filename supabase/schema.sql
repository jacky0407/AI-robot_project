-- supabase/schema.sql

-- Enable pgvector extension
create extension if not exists vector;

-- Create USERS table
create table public.users (
  id uuid primary key default gen_random_uuid(),
  email text not null unique,
  role text not null check (role in ('admin', 'user')),
  created_at timestamp with time zone default timezone('utc'::text, now()) not null
);

-- Create BOTS table
create table public.bots (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  avatar_url text,
  system_prompt text,
  created_by uuid references public.users(id) on delete set null,
  created_at timestamp with time zone default timezone('utc'::text, now()) not null
);

-- Create DOCUMENTS table (Knowledge Base Files)
create table public.documents (
  id uuid primary key default gen_random_uuid(),
  bot_id uuid not null references public.bots(id) on delete cascade,
  filename text not null,
  file_url text not null,
  uploaded_at timestamp with time zone default timezone('utc'::text, now()) not null
);

-- Create DOCUMENT_CHUNKS table (for pgvector RAG)
create table public.document_chunks (
  id uuid primary key default gen_random_uuid(),
  document_id uuid not null references public.documents(id) on delete cascade,
  content text not null,
  -- Assuming OpenAI's text-embedding-3-small or text-embedding-ada-002 which outputs 1536 dimensions
  embedding vector(1536) 
);

-- Create CHAT_SESSIONS table
create table public.chat_sessions (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.users(id) on delete cascade,
  bot_id uuid not null references public.bots(id) on delete cascade,
  title text,
  summary text,
  created_at timestamp with time zone default timezone('utc'::text, now()) not null
);

-- Create MESSAGES table
create table public.messages (
  id uuid primary key default gen_random_uuid(),
  session_id uuid not null references public.chat_sessions(id) on delete cascade,
  role text not null check (role in ('user', 'assistant', 'system')),
  content text not null,
  created_at timestamp with time zone default timezone('utc'::text, now()) not null
);

-- Create STUDENT_PROFILES table (Long-Term Memory)
create table public.student_profiles (
  user_id uuid primary key references public.users(id) on delete cascade,
  overall_summary text,
  last_updated_at timestamp with time zone default timezone('utc'::text, now()) not null
);

-- ==========================================
-- ROW LEVEL SECURITY (RLS)
-- ==========================================

-- 啟用所有資料表的 RLS (預設拒絕所有存取)
alter table public.users enable row level security;
alter table public.bots enable row level security;
alter table public.documents enable row level security;
alter table public.document_chunks enable row level security;
alter table public.chat_sessions enable row level security;
alter table public.messages enable row level security;
alter table public.student_profiles enable row level security;

-- 因為我們採取純後端驗證 (FastAPI Backend)，
-- 資料庫操作將由後端透過 Service Role Key 統一進行代理存取。
-- 所以資料庫端預設完全封閉對外 (Public) 存取，這是最嚴格的資安配置。
create policy "Deny all public access" on public.users for all using (false);
create policy "Deny all public access" on public.bots for all using (false);
create policy "Deny all public access" on public.documents for all using (false);
create policy "Deny all public access" on public.document_chunks for all using (false);
create policy "Deny all public access" on public.chat_sessions for all using (false);
create policy "Deny all public access" on public.messages for all using (false);
create policy "Deny all public access" on public.student_profiles for all using (false);
