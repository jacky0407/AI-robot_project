-- ==============================================================================
-- 1. 啟用擴充套件
-- ==============================================================================
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS vector;

-- 台灣時間的今天（資料庫時區是 UTC，不要用 CURRENT_DATE 判斷期限；migration 012）
CREATE OR REPLACE FUNCTION public.today_taipei()
RETURNS date LANGUAGE sql STABLE AS $$
    SELECT (now() AT TIME ZONE 'Asia/Taipei')::date;
$$;

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
    profession TEXT,                                   -- 職業 / 身分（/onboarding 填寫）
    registration_completed BOOLEAN NOT NULL DEFAULT false, -- 是否已完成 /onboarding；false 時 middleware 導回 /onboarding
    terms_accepted_at TIMESTAMPTZ,                     -- 同意服務條款與隱私權政策的時間
    created_at TIMESTAMPTZ DEFAULT TIMEZONE('utc'::text, NOW()) NOT NULL
);
-- complete_registration() 定義在 supabase/migrations/004_registration.sql
-- profiles 的 RLS 規則（只能讀自己、不能改 role）見本檔第 8 節

-- 課程表 (正式課程模式)
CREATE TABLE IF NOT EXISTS public.courses (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title TEXT NOT NULL,
    description TEXT,
    invite_code TEXT UNIQUE,                -- 已停用（migration 013 起改用 course_invite_codes）
    require_approval BOOLEAN DEFAULT false, -- 加入是否需審核
    is_archived BOOLEAN DEFAULT false,
    created_by UUID REFERENCES public.profiles(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ DEFAULT TIMEZONE('utc'::text, NOW()) NOT NULL
);

-- 課程邀請碼：一門課多組，各自的可用日期與方案（migration 013）
CREATE TABLE IF NOT EXISTS public.course_invite_codes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    course_id UUID NOT NULL REFERENCES public.courses(id) ON DELETE CASCADE,
    code TEXT UNIQUE NOT NULL,
    plan TEXT NOT NULL DEFAULT 'free' CHECK (plan IN ('free', 'self_key')), -- free 平台金鑰每日次數 / self_key 學生自備金鑰
    daily_quota INT CHECK (daily_quota BETWEEN 1 AND 1000),
    access_until DATE NOT NULL,             -- 用這組碼加入的學生可以用到這天（台灣時間，當天仍可用）
    is_active BOOLEAN NOT NULL DEFAULT true, -- false = 老師手動停用，不能再用它加入
    created_by UUID REFERENCES public.profiles(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ DEFAULT TIMEZONE('utc'::text, NOW()) NOT NULL,
    CONSTRAINT course_invite_codes_quota_by_plan CHECK (
        (plan = 'free' AND daily_quota IS NOT NULL) OR (plan = 'self_key' AND daily_quota IS NULL)
    )
);
CREATE INDEX IF NOT EXISTS course_invite_codes_course_id_idx ON public.course_invite_codes (course_id);

-- 課程成員關聯表
CREATE TABLE IF NOT EXISTS public.course_members (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    course_id UUID NOT NULL REFERENCES public.courses(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
    status TEXT CHECK (status IN ('active', 'pending', 'rejected', 'removed')) DEFAULT 'active', -- rejected 見 migrations/007
    joined_at TIMESTAMPTZ DEFAULT TIMEZONE('utc'::text, NOW()) NOT NULL,
    -- 用哪組邀請碼加入，決定方案、每日次數與可用日期；NULL = 後台直接加入，不限期、不限次數（migration 013）
    invite_code_id UUID REFERENCES public.course_invite_codes(id) ON DELETE SET NULL,
    used_today INT NOT NULL DEFAULT 0,     -- 課程免費方案今天已用次數；扣額度用 consume_course_quota()
    used_date DATE,                        -- used_today 是哪一天（台灣時間）的次數
    UNIQUE(course_id, user_id)
);

-- 自主探索者權限與每日額度表
CREATE TABLE IF NOT EXISTS public.tool_permissions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
    tool_id UUID, -- NULL 表示全平台通用額度，或指定特定 tool_id
    daily_quota INT DEFAULT 10,
    used_today INT DEFAULT 0,
    used_date DATE,                        -- used_today 是哪一天（台灣時間）的次數；扣額度用 consume_tool_quota()，見 migrations/008
    start_date DATE DEFAULT public.today_taipei(), -- 台灣時間（migration 012）
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
    rubric_criteria JSONB DEFAULT '[]'::jsonb,   -- Rubric 評分規準與構面（max_score 為配分/權重）
    scale_type TEXT CHECK (scale_type IN ('4_point', '5_point', '100_point', 'pass_fail')) DEFAULT '4_point',
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
    created_at TIMESTAMPTZ DEFAULT TIMEZONE('utc'::text, NOW()) NOT NULL,
    deleted_at TIMESTAMPTZ                 -- 有值 = 已刪除（作答保留），migration 016
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
    is_active BOOLEAN NOT NULL DEFAULT true, -- false = 已停用：學生看不到、不能作答，作答保留（migration 010）
    created_at TIMESTAMPTZ DEFAULT TIMEZONE('utc'::text, NOW()) NOT NULL,
    UNIQUE(module_id, step_order)
);

-- 課程 ↔ 模組 指派：課程中 active 的成員看得到被指派的模組
-- 指派制的 RLS 規則與判斷函式見本檔第 8 節（來源 migrations 005、010、012、013）
CREATE TABLE IF NOT EXISTS public.course_modules (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    course_id UUID NOT NULL REFERENCES public.courses(id) ON DELETE CASCADE,
    module_id UUID NOT NULL REFERENCES public.learning_modules(id) ON DELETE CASCADE,
    assigned_by UUID REFERENCES public.profiles(id) ON DELETE SET NULL,
    assigned_at TIMESTAMPTZ DEFAULT TIMEZONE('utc'::text, NOW()) NOT NULL,
    UNIQUE (course_id, module_id)
);

-- ==============================================================================
-- 5. 學習歷程、多次嘗試與評量 (Attempts, Evaluations, Logs)
-- ==============================================================================

-- 練習 Session：前端產生 id，第一次送出作答時由後端建立（migration 009，只給 service_role）
CREATE TABLE IF NOT EXISTS public.practice_sessions (
    id UUID PRIMARY KEY,                   -- 由前端產生（crypto.randomUUID()），所以沒有 DEFAULT
    user_id UUID NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
    started_at TIMESTAMPTZ DEFAULT TIMEZONE('utc'::text, NOW()) NOT NULL,
    last_active_at TIMESTAMPTZ DEFAULT TIMEZONE('utc'::text, NOW()) NOT NULL
);

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
    tutor_action TEXT CHECK (tutor_action IN ('evaluate', 'give_hint', 'encourage', 'escalate')), -- TutorAgent 決策；escalate 會進教師複核佇列
    session_id UUID REFERENCES public.practice_sessions(id) ON DELETE SET NULL, -- 屬於哪一次練習（migration 009）
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
    confidence FLOAT,                      -- AI 對本次評分的自評信心值 0~1
    needs_teacher_review BOOLEAN DEFAULT false, -- confidence 過低時為 true
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
    hint_trigger TEXT CHECK (hint_trigger IN ('score_below_threshold', 'student_request')), -- 自動給 or 學生主動要
    hint_source TEXT CHECK (hint_source IN ('teacher', 'generated')),  -- 教授撰寫 or AI 依 Rubric 生成
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
-- 新帳號一律是 student：raw_user_meta_data 是使用者註冊時自己送來的資料（anon key 公開），
-- 不能拿來決定角色，否則任何人都能註冊成 owner。教師身分只能由後台或後端設定（migration 014）
CREATE OR REPLACE FUNCTION public.handle_new_platform_user()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    -- 1. 建立 profile：角色固定為 student，不讀 raw_user_meta_data 的 role
    INSERT INTO public.profiles (id, email, full_name, role)
    VALUES (
        NEW.id,
        NEW.email,
        COALESCE(NEW.raw_user_meta_data->>'full_name', ''),
        'student'
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
$$;

DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;
CREATE TRIGGER on_auth_user_created
    AFTER INSERT ON auth.users
    FOR EACH ROW EXECUTE FUNCTION public.handle_new_platform_user();

-- ==============================================================================
-- 8. 權限（RLS）：學生只看得到被指派的內容
--
-- 這一節是 migrations 005、006、010、012、013 的「最終版本」，讓只跑 schema.sql 的新資料庫
-- 也有完整的權限規則（docs/KNOWN_GAPS.md #9）。已經有資料庫的人照樣跑 migrations 即可，
-- 兩邊內容相同、全部可重複執行。⚠️ 之後改權限規則，migration 和這裡要一起改。
--
-- 指派規則（兩種任一成立就看得到）：
--   1. 課程指派：模組指派給課程（course_modules），學生是該課程 active 成員，
--      且加入時用的邀請碼可用日期未過（台灣時間）
--   2. 逐位核准：tool_permissions 核准這支機器人（tool_id 為 NULL = 全部），且在期限內（台灣時間）
--   教師（owner / assistant）看得到全部。後端用 service_role，不受 RLS 限制，另在 API 層檢查。
--
-- 資料表權限（GRANT）在 migrations/002_api_grants.sql，仍然要跑。
-- ==============================================================================

-- ── 所有資料表開啟 RLS（沒有政策的表 = 前端一律讀不到，只有後端 service_role 能讀寫）──
ALTER TABLE public.profiles            ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.courses             ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.course_invite_codes ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.course_members      ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.tool_permissions    ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.ai_tools            ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.tool_cases          ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.learning_modules    ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.module_steps        ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.course_modules      ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.practice_sessions   ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.step_attempts       ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.ai_evaluations      ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.teacher_reviews     ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.prompt_logs         ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.forms               ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.form_responses      ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.research_consents   ENABLE ROW LEVEL SECURITY;

-- ── 判斷函式（SECURITY DEFINER：查指派資料時不受那些表本身的 RLS 限制）────────────

-- 目前登入者是不是教師或助教
CREATE OR REPLACE FUNCTION public.is_staff()
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
    SELECT EXISTS (
        SELECT 1 FROM public.profiles
        WHERE id = auth.uid() AND role IN ('owner', 'assistant')
    );
$$;

-- 模組是否透過課程指派給目前登入者（模組已發布、課程未封存、成員 active、邀請碼可用日期未過）
CREATE OR REPLACE FUNCTION public.module_assigned_via_course(p_module_id uuid)
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
    SELECT EXISTS (
        SELECT 1
        FROM public.course_modules cm
        JOIN public.courses c ON c.id = cm.course_id AND NOT c.is_archived
        JOIN public.course_members mem ON mem.course_id = cm.course_id
        JOIN public.learning_modules m ON m.id = cm.module_id AND m.is_published
        LEFT JOIN public.course_invite_codes ic ON ic.id = mem.invite_code_id
        WHERE cm.module_id = p_module_id
          AND mem.user_id = auth.uid()
          AND mem.status = 'active'
          AND (mem.invite_code_id IS NULL OR ic.access_until >= public.today_taipei())
    );
$$;

-- 目前登入者是否有這支機器人的有效核准（tool_id 為 NULL = 全部機器人；到期日當天仍可使用）
CREATE OR REPLACE FUNCTION public.has_tool_permission(p_tool_id uuid)
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
    SELECT EXISTS (
        SELECT 1 FROM public.tool_permissions tp
        WHERE tp.user_id = auth.uid()
          AND tp.status = 'approved'
          AND (tp.tool_id = p_tool_id OR tp.tool_id IS NULL)
          AND (tp.start_date IS NULL OR tp.start_date <= public.today_taipei())
          AND (tp.expire_date IS NULL OR tp.expire_date >= public.today_taipei())
    );
$$;

-- 以下三個函式把「跨表查詢」包進 SECURITY DEFINER，
-- 避免 learning_modules ↔ module_steps 的政策互相查詢造成無限遞迴

-- 模組已發布，且其中有登入者被核准的機器人
CREATE OR REPLACE FUNCTION public.module_visible_via_permission(p_module_id uuid)
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
    SELECT EXISTS (
        SELECT 1
        FROM public.learning_modules m
        JOIN public.module_steps s ON s.module_id = m.id
        WHERE m.id = p_module_id
          AND m.is_published
          AND public.has_tool_permission(s.tool_id)
    );
$$;

-- 模組是否已發布
CREATE OR REPLACE FUNCTION public.module_is_published(p_module_id uuid)
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
    SELECT EXISTS (SELECT 1 FROM public.learning_modules WHERE id = p_module_id AND is_published);
$$;

-- 機器人是否出現在課程指派給登入者的模組裡
CREATE OR REPLACE FUNCTION public.tool_in_assigned_module(p_tool_id uuid)
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
    SELECT EXISTS (
        SELECT 1 FROM public.module_steps s
        WHERE s.tool_id = p_tool_id
          AND public.module_assigned_via_course(s.module_id)
    );
$$;

REVOKE EXECUTE ON FUNCTION public.today_taipei() FROM PUBLIC, anon;
REVOKE EXECUTE ON FUNCTION public.is_staff() FROM PUBLIC, anon;
REVOKE EXECUTE ON FUNCTION public.module_assigned_via_course(uuid) FROM PUBLIC, anon;
REVOKE EXECUTE ON FUNCTION public.has_tool_permission(uuid) FROM PUBLIC, anon;
REVOKE EXECUTE ON FUNCTION public.module_visible_via_permission(uuid) FROM PUBLIC, anon;
REVOKE EXECUTE ON FUNCTION public.module_is_published(uuid) FROM PUBLIC, anon;
REVOKE EXECUTE ON FUNCTION public.tool_in_assigned_module(uuid) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.today_taipei() TO authenticated, service_role;
GRANT EXECUTE ON FUNCTION public.is_staff() TO authenticated;
GRANT EXECUTE ON FUNCTION public.module_assigned_via_course(uuid) TO authenticated;
GRANT EXECUTE ON FUNCTION public.has_tool_permission(uuid) TO authenticated;
GRANT EXECUTE ON FUNCTION public.module_visible_via_permission(uuid) TO authenticated;
GRANT EXECUTE ON FUNCTION public.module_is_published(uuid) TO authenticated;
GRANT EXECUTE ON FUNCTION public.tool_in_assigned_module(uuid) TO authenticated;

-- ── profiles：只能讀自己那一列，不能新增、修改、刪除（角色只能由後台或後端改；migration 006）──
DROP POLICY IF EXISTS "users can read own profile" ON public.profiles;
CREATE POLICY "users can read own profile" ON public.profiles
    FOR SELECT TO authenticated
    USING (id = auth.uid());

-- ── course_modules：教師看全部；學生看自己課程的指派 ─────────────────────────
DROP POLICY IF EXISTS "read course assignments" ON public.course_modules;
CREATE POLICY "read course assignments" ON public.course_modules
    FOR SELECT TO authenticated
    USING (public.is_staff() OR public.module_assigned_via_course(module_id));

-- ── 模組步驟：課程指派的模組全部步驟，或有核准的機器人所在步驟；學生看不到停用的步驟（migration 010）──
DROP POLICY IF EXISTS "read assigned steps" ON public.module_steps;
CREATE POLICY "read assigned steps" ON public.module_steps
    FOR SELECT TO authenticated
    USING (
        public.is_staff()
        OR (
            is_active
            AND (
                public.module_assigned_via_course(module_id)
                OR (public.has_tool_permission(tool_id) AND public.module_is_published(module_id))
            )
        )
    );

-- ── 學習模組：看得到其中任一步驟就看得到模組 ─────────────────────────────
DROP POLICY IF EXISTS "read assigned modules" ON public.learning_modules;
CREATE POLICY "read assigned modules" ON public.learning_modules
    FOR SELECT TO authenticated
    USING (
        public.is_staff()
        OR public.module_assigned_via_course(id)
        OR public.module_visible_via_permission(id)
    );

-- ── AI 機器人：已發布，且（逐位核准，或出現在課程指派的模組裡）；教師可新增 ─────────
DROP POLICY IF EXISTS "read assigned tools" ON public.ai_tools;
CREATE POLICY "read assigned tools" ON public.ai_tools
    FOR SELECT TO authenticated
    USING (
        public.is_staff()
        OR (
            status = 'published'
            AND (
                public.has_tool_permission(id)
                OR public.tool_in_assigned_module(id)
            )
        )
    );

DROP POLICY IF EXISTS "staff create tools" ON public.ai_tools;
CREATE POLICY "staff create tools" ON public.ai_tools
    FOR INSERT TO authenticated
    WITH CHECK (public.is_staff() AND created_by = auth.uid());

-- ── 案例：目前沒有和機器人綁定，登入者都可讀 ──────────────────────────────
DROP POLICY IF EXISTS "read cases" ON public.tool_cases;
CREATE POLICY "read cases" ON public.tool_cases
    FOR SELECT TO authenticated
    USING (true);

-- ── 作答紀錄：只能讀自己的，教師可讀全部 ─────────────────────────────────
DROP POLICY IF EXISTS "read own attempts" ON public.step_attempts;
CREATE POLICY "read own attempts" ON public.step_attempts
    FOR SELECT TO authenticated
    USING (user_id = auth.uid() OR public.is_staff());