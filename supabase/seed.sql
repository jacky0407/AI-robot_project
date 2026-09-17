-- ==============================================================================
-- 種子資料（僅供本地開發／示範環境）
--
-- ⚠️ 正式環境絕對不要執行這個檔案：裡面有公開的測試帳號密碼。
-- ⚠️ 執行前請先跑過 schema.sql。
--
-- 測試帳號（密碼皆為 Test1234!）：
--   prof.wu@platform.edu        role = owner    教授
--   student.test@platform.edu   role = student  學生
--
-- 若登入仍失敗（GoTrue 版本對 auth.users 的必填欄位要求不同），
-- 請改到 Supabase Dashboard → Authentication → Users 手動設定密碼。
--
-- 固定 UUID 對照：
--   a0000001-… 機器人：步驟1 案例資料整理教練
--   a0000002-… 機器人：步驟2 功能性現況撰寫教練
--   b0000001-… 模組：學前 IEP 逐步撰寫模組
--   c1111111-… 課程：115學年度 學前特教IEP實務工作坊
--   ca5e0001-… 案例A：小明
-- ==============================================================================

-- crypt() / gen_salt() 需要 pgcrypto
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ==============================================================================
-- 1. 建立測試用帳號 (包含教授與學生)
-- ==============================================================================
INSERT INTO auth.users (id, instance_id, aud, role, email, encrypted_password, email_confirmed_at, raw_app_meta_data, raw_user_meta_data, created_at, updated_at)
VALUES 
    (
        'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa',
        '00000000-0000-0000-0000-000000000000',
        'authenticated',
        'authenticated',
        'prof.wu@platform.edu',
        crypt('Test1234!', gen_salt('bf')),
        NOW(),
        '{"provider":"email","providers":["email"]}',
        '{"full_name":"吳佩芳教授","role":"owner"}',
        NOW(),
        NOW()
    ),
    (
        'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb',
        '00000000-0000-0000-0000-000000000000',
        'authenticated',
        'authenticated',
        'student.test@platform.edu',
        crypt('Test1234!', gen_salt('bf')),
        NOW(),
        '{"provider":"email","providers":["email"]}',
        '{"full_name":"測試學生","role":"student"}',
        NOW(),
        NOW()
    )
ON CONFLICT (id) DO NOTHING;

-- 確保 profiles 角色設定對齊
INSERT INTO public.profiles (id, email, full_name, role)
VALUES 
    ('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', 'prof.wu@platform.edu', '吳佩芳教授', 'owner'),
    ('bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb', 'student.test@platform.edu', '測試學生', 'student')
ON CONFLICT (id) DO UPDATE 
SET full_name = EXCLUDED.full_name, role = EXCLUDED.role;

-- ==============================================================================
-- 2. 建立示範課程與邀請碼
-- ==============================================================================
INSERT INTO public.courses (id, title, description, invite_code, created_by)
VALUES 
    (
        'c1111111-0000-0000-0000-000000000001',
        '115學年度 學前特教IEP實務工作坊',
        '針對職前幼教師與早療專業人員之個別化教育計畫撰寫培訓',
        'IEP2026',
        'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'
    )
ON CONFLICT (id) DO NOTHING;

-- 學生加入該課程
INSERT INTO public.course_members (course_id, user_id, status)
VALUES 
    ('c1111111-0000-0000-0000-000000000001', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb', 'active')
ON CONFLICT (course_id, user_id) DO NOTHING;

-- ==============================================================================
-- 3. 建立 1 組標準虛構教學案例 (符合個資保護原則)
-- ==============================================================================
INSERT INTO public.tool_cases (id, title, difficulty, case_background, known_info, is_synthetic)
VALUES 
    (
        'ca5e0001-0000-0000-0000-000000000001',
        '案例A：4歲中度語言發展遲緩幼兒「小明」',
        'basic',
        '小明（4歲2個月），就讀幼兒園中班。在幼兒園自然情境中，常以拉扯同儕或尖叫表達需求；能聽懂2步驟簡單指令，但口語表達僅限單詞（如：要、餅乾、抱）。在積木角能專注建構20分鐘，具備良好的視覺空間優勢與動作協調能力。家長非常期待小明能融入團體並主動用口語表達需求。',
        '{"medical_diagnosis": "中度語言發展遲緩", "hearing_vision": "正常", "family_priority": "期待在幼兒園能自己開口說話要玩具，減少尖叫"}',
        true
    )
ON CONFLICT (id) DO NOTHING;

-- ==============================================================================
-- 4. 建立 AI 能力機器人 (以 IEP 步驟 1 與步驟 2 為例)
-- ==============================================================================
-- 機器人 1: 案例資料整理教練
INSERT INTO public.ai_tools (id, title, domain, target_competency, role_instruction, system_prompt, rubric_criteria, version, status, created_by)
VALUES 
    (
        'a0000001-0000-0000-0000-000000000001',
        '步驟1：案例資料整理教練',
        '學前IEP',
        '客觀事實與推論區分能力',
        '你是一位學前特教督導，引導學生從案例中區分客觀事實、推論與缺漏資訊。',
        '請根據學生對案例小明的整理進行 Rubric 初評，以正向、具體且不浮誇的語氣給予回饋，指出做得好的地方並標註原文證據。',
        '[
            {"dimension": "客觀事實辨識", "max_score": 40, "description": "能準確擷取案例中的具體行為與數據，不加入主觀猜測"},
            {"dimension": "推論與假設區分", "max_score": 30, "description": "能將主觀推論與客觀觀察清楚分開標示"},
            {"dimension": "缺漏資訊提問", "max_score": 30, "description": "能指出評估所需但案例未提供的關鍵訊息"}
        ]'::jsonb,
        1,
        'published',
        'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'
    ),
    (
        'a0000002-0000-0000-0000-000000000002',
        '步驟2：功能性現況撰寫教練',
        '學前IEP',
        '自然情境功能性現況撰寫',
        '你是一位專業特教教練，引導學生撰寫描述幼兒在作息中「參與、能力與支持需求」的功能性現況。',
        '請檢核學生撰寫的現況描述是否包含情境脈絡、可觀察行為及具體支持條件，避免空泛病理標籤。',
        '[
            {"dimension": "情境脈絡完整性", "max_score": 35, "description": "清楚描述在日常活動或作息（如積木角、點心時間）中的表現"},
            {"dimension": "功能性描述", "max_score": 35, "description": "聚焦於參與度與溝通意圖，而非僅列出測驗分數"},
            {"dimension": "支持需求具體性", "max_score": 30, "description": "具體說明需要何種視覺支持或口語提示"}
        ]'::jsonb,
        1,
        'published',
        'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'
    )
ON CONFLICT (id) DO NOTHING;

-- ==============================================================================
-- 5. 建立培訓模組並編排步驟 (Module & Steps)
-- ==============================================================================
INSERT INTO public.learning_modules (id, title, description, domain, is_published)
VALUES 
    (
        'b0000001-0000-0000-0000-000000000001',
        '學前個別化教育計畫 (IEP) 逐步撰寫模組',
        '從案例資料整理到跨步驟一致性檢核的完整專業能力培訓',
        '學前IEP',
        true
    )
ON CONFLICT (id) DO NOTHING;

-- 編排步驟 1 & 2
INSERT INTO public.module_steps (module_id, tool_id, step_order, step_title, pass_score, pass_forward_keys)
VALUES 
    (
        'b0000001-0000-0000-0000-000000000001',
        'a0000001-0000-0000-0000-000000000001',
        1,
        '步驟1：案例資料整理與客觀事實萃取',
        70,
        '["structured_facts", "missing_info"]'::jsonb
    ),
    (
        'b0000001-0000-0000-0000-000000000001',
        'a0000002-0000-0000-0000-000000000002',
        2,
        '步驟2：自然情境功能性現況撰寫',
        70,
        '["functional_present_level"]'::jsonb
    )
ON CONFLICT (module_id, step_order) DO NOTHING;