-- ==============================================================================
-- 1. 啟用擴充套件
-- ==============================================================================
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS vector;

-- ==============================================================================
-- 2. 帳號與權限模組 (Profiles, Courses, Permissions)
-- ==============================================================================

-- 使用者基本資料 (綁定 Supabase Auth)
CREATE TABLE IF NOT EXISTS public.profiles (
    id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    email TEXT NOT NULL,
    full_name TEXT,
    role TEXT CHECK (role IN ('owner', 'assistant', 'student', 'explorer')) DEFAULT 'student',
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMPTZ DEFAULT TIMEZONE('utc'::text, NOW()) NOT NULL
);

-- 課程表 (正式課程模式)
CREATE TABLE IF NOT EXISTS public.courses (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title TEXT NOT NULL,
    description TEXT,
    invite_code TEXT UNIQUE NOT NULL,       -- 課程邀請碼
    require_approval BOOLEAN DEFAULT false, -- 加入是否需審核
    is_archived BOOLEAN DEFAULT false,
    created_by UUID REFERENCES public.profiles(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ DEFAULT TIMEZONE('utc'::text, NOW()) NOT NULL
);

-- 課程成員關聯表
CREATE TABLE IF NOT EXISTS public.course_members (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    course_id UUID NOT NULL REFERENCES public.courses(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
    status TEXT CHECK (status IN ('active', 'pending', 'removed')) DEFAULT 'active',
    joined_at TIMESTAMPTZ DEFAULT TIMEZONE('utc'::text, NOW()) NOT NULL,
    UNIQUE(course_id, user_id)
);

-- 自主探索者權限與每日額度表
CREATE TABLE IF NOT EXISTS public.tool_permissions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
    tool_id UUID, -- NULL 表示全平台通用額度，或指定特定 tool_id
    daily_quota INT DEFAULT 10,
    used_today INT DEFAULT 0,
    start_date DATE DEFAULT CURRENT_DATE,
    expire_date DATE,
    status TEXT CHECK (status IN ('pending', 'approved', 'rejected', 'expired')) DEFAULT 'pending',
    created_at TIMESTAMPTZ DEFAULT TIMEZONE('utc'::text, NOW()) NOT NULL
);

-- ==============================================================================
-- 3. 類 GPTs 工具與案例模組 (AI Tools & Cases)
-- ==============================================================================

-- AI 能力機器人本體 (支援多版本不可覆寫)
CREATE TABLE IF NOT EXISTS public.ai_tools (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title TEXT NOT NULL,
    domain TEXT NOT NULL,                  -- 專業領域 (如: 學前IEP, IFSP, 正向行為支持)
    target_competency TEXT NOT NULL,       -- 目標能力
    role_instruction TEXT NOT NULL,        -- AI 角色與目標
    system_prompt TEXT NOT NULL,           -- 詳細系統指令
    teaching_strategy JSONB DEFAULT '{}'::jsonb, -- 教學策略 (引導時點、提示層級設定)
    rubric_criteria JSONB DEFAULT '[]'::jsonb,   -- Rubric 評分規準與構面
    error_taxonomy JSONB DEFAULT '[]'::jsonb,    -- 錯誤分類定義
    max_cost_limit FLOAT DEFAULT 0.5,      -- 單次任務成本上限 ($)
    version INT DEFAULT 1,
    status TEXT CHECK (status IN ('draft', 'published', 'paused', 'archived')) DEFAULT 'draft',
    created_by UUID REFERENCES public.profiles(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ DEFAULT TIMEZONE('utc'::text, NOW()) NOT NULL
);

-- 虛構教學案例表 (嚴禁真實個資)
CREATE TABLE IF NOT EXISTS public.tool_cases (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title TEXT NOT NULL,
    difficulty TEXT CHECK (difficulty IN ('basic', 'intermediate', 'advanced')) DEFAULT 'basic',
    case_background TEXT NOT NULL,         -- 案例背景
    known_info JSONB DEFAULT '{}'::jsonb,  -- AI 掌握的背景資訊與揭露條件
    is_synthetic BOOLEAN DEFAULT true,     -- 標記為合成/虛構案例
    created_at TIMESTAMPTZ DEFAULT TIMEZONE('utc'::text, NOW()) NOT NULL
);

-- ==============================================================================
-- 4. 模組編排與跨步驟流程 (Pipeline & Steps)
-- ==============================================================================

-- 培訓模組 (如: 學前IEP逐步撰寫模組)
CREATE TABLE IF NOT EXISTS public.learning_modules (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title TEXT NOT NULL,
    description TEXT,
    domain TEXT NOT NULL,
    is_published BOOLEAN DEFAULT false,
    created_at TIMESTAMPTZ DEFAULT TIMEZONE('utc'::text, NOW()) NOT NULL
);

-- 模組步驟編排 (定義 1~9 步驟、前置解鎖與資料傳遞)
CREATE TABLE IF NOT EXISTS public.module_steps (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    module_id UUID NOT NULL REFERENCES public.learning_modules(id) ON DELETE CASCADE,
    tool_id UUID NOT NULL REFERENCES public.ai_tools(id) ON DELETE RESTRICT,
    step_order INT NOT NULL,               -- 步驟順序 (1 ~ 9)
    step_title TEXT NOT NULL,              -- 步驟名稱 (如: 步驟1 案例資料整理)
    is_required BOOLEAN DEFAULT true,
    pass_score INT DEFAULT 70,             -- 通過分數門檻
    require_teacher_review BOOLEAN DEFAULT false, -- 是否為教師強制審核點
    pass_forward_keys JSONB DEFAULT '[]'::jsonb,  -- 需傳給下一步的欄位 key
    created_at TIMESTAMPTZ DEFAULT TIMEZONE('utc'::text, NOW()) NOT NULL,
    UNIQUE(module_id, step_order)
);

-- ==============================================================================
-- 5. 學習歷程、多次嘗試與評量 (Attempts, Evaluations, Logs)
-- ==============================================================================

-- 學生作答嘗試紀錄 (支援反覆練習與版本對比)
CREATE TABLE IF NOT EXISTS public.step_attempts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
    module_id UUID NOT NULL REFERENCES public.learning_modules(id) ON DELETE CASCADE,
    step_id UUID NOT NULL REFERENCES public.module_steps(id) ON DELETE CASCADE,
    case_id UUID REFERENCES public.tool_cases(id) ON DELETE SET NULL,
    attempt_number INT DEFAULT 1,          -- 第幾次嘗試 (1, 2, 3...)
    user_input_content TEXT NOT NULL,      -- 學生獨立作答內容 / 結構化輸出
    structured_data JSONB DEFAULT '{}'::jsonb, -- 結構化欄位 (傳遞給下一步用)
    status TEXT CHECK (status IN ('draft', 'submitted', 'passed', 'revision_required')) DEFAULT 'draft',
    created_at TIMESTAMPTZ DEFAULT TIMEZONE('utc'::text, NOW()) NOT NULL
);

-- AI 依 Rubric 初評結果 (構面分數、總分、證據、錯誤分類)
CREATE TABLE IF NOT EXISTS public.ai_evaluations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    attempt_id UUID NOT NULL REFERENCES public.step_attempts(id) ON DELETE CASCADE,
    total_score INT,
    dimension_scores JSONB NOT NULL,       -- 各構面分數 (JSON 陣列/物件)
    evidence_text TEXT,                    -- AI 引用的學生作答原文證據
    detected_errors JSONB DEFAULT '[]'::jsonb, -- 偵測到的錯誤類型清單
    feedback_text TEXT NOT NULL,           -- AI 正向具體回饋
    suggested_next_step TEXT,              -- 建議下一步
    ai_cost FLOAT DEFAULT 0.0,             -- 單次呼叫估算成本
    created_at TIMESTAMPTZ DEFAULT TIMEZONE('utc'::text, NOW()) NOT NULL
);

-- 教師最終複核判定 (與 AI 初評分離保存)
CREATE TABLE IF NOT EXISTS public.teacher_reviews (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    attempt_id UUID NOT NULL REFERENCES public.step_attempts(id) ON DELETE CASCADE,
    teacher_id UUID NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
    decision TEXT CHECK (decision IN ('accept_ai', 'modify', 'override', 'request_retry')) NOT NULL,
    final_score INT,
    final_feedback TEXT,
    is_published BOOLEAN DEFAULT false,    -- 是否已發布給學生看見
    reviewed_at TIMESTAMPTZ DEFAULT TIMEZONE('utc'::text, NOW()) NOT NULL
);

-- 分層提示使用紀錄 (記錄學生在何時用了第幾層提示)
CREATE TABLE IF NOT EXISTS public.prompt_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    attempt_id UUID NOT NULL REFERENCES public.step_attempts(id) ON DELETE CASCADE,
    hint_level INT NOT NULL,               -- 提示層級 (1:重新思考, 2:方向, 3:結構, 4:局部範例...)
    hint_content TEXT NOT NULL,            -- 提示內容
    student_reaction TEXT,                 -- 學生查看後的反應或下一步
    created_at TIMESTAMPTZ DEFAULT TIMEZONE('utc'::text, NOW()) NOT NULL
);

-- ==============================================================================
-- 6. 表單與研究倫理 (Forms & Ethics)
-- ==============================================================================

-- 工具專屬表單 (前後測、質性反思題)
CREATE TABLE IF NOT EXISTS public.forms (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tool_id UUID REFERENCES public.ai_tools(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    trigger_timing TEXT CHECK (trigger_timing IN ('pre_practice', 'post_practice', 'after_feedback', 'post_revision')) DEFAULT 'after_feedback',
    schema_json JSONB NOT NULL,            -- 題型與結構 (單選、複選、量表、簡答)
    version INT DEFAULT 1,
    created_at TIMESTAMPTZ DEFAULT TIMEZONE('utc'::text, NOW()) NOT NULL
);

-- 學生表單填答紀錄
CREATE TABLE IF NOT EXISTS public.form_responses (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    form_id UUID NOT NULL REFERENCES public.forms(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
    attempt_id UUID REFERENCES public.step_attempts(id) ON DELETE SET NULL,
    responses JSONB NOT NULL,              -- 填答結果
    submitted_at TIMESTAMPTZ DEFAULT TIMEZONE('utc'::text, NOW()) NOT NULL
);

-- 研究倫理知情同意與匿名編號對照表
CREATE TABLE IF NOT EXISTS public.research_consents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
    anonymous_research_id TEXT UNIQUE NOT NULL, -- 隨機匿名研究編號 (如: SPED-2026-X892)
    consent_version TEXT NOT NULL,
    is_consented BOOLEAN DEFAULT false,
    consented_at TIMESTAMPTZ,
    withdrawn_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT TIMEZONE('utc'::text, NOW()) NOT NULL
);

-- ==============================================================================
-- 7. 自動觸發器：註冊自動同步 profiles & 生成匿名研究編號
-- ==============================================================================
CREATE OR REPLACE FUNCTION public.handle_new_platform_user()
RETURNS TRIGGER AS $$
BEGIN
    -- 1. 建立 profile
    INSERT INTO public.profiles (id, email, full_name, role)
    VALUES (
        NEW.id,
        NEW.email,
        COALESCE(NEW.raw_user_meta_data->>'full_name', ''),
        COALESCE(NEW.raw_user_meta_data->>'role', 'student')
    );

    -- 2. 自動預先配置一組隨機匿名研究編號
    INSERT INTO public.research_consents (user_id, anonymous_research_id, consent_version, is_consented)
    VALUES (
        NEW.id,
        'SPED-' || SUBSTRING(gen_random_uuid()::text, 1, 8),
        'v1.0',
        false
    );

    RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;
CREATE TRIGGER on_auth_user_created
    AFTER INSERT ON auth.users
    FOR EACH ROW EXECUTE FUNCTION public.handle_new_platform_user();