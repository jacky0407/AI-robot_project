"""
Supabase 連線與資料表檢查

設定完 .env 之後跑這支，會告訴你哪一步還沒完成：

    cd backend
    py check_setup.py

不會寫入任何資料，只做讀取檢查。
"""

import sys
from pathlib import Path

# 讓這支可以直接從 backend/ 執行
sys.path.insert(0, str(Path(__file__).parent))

OK = "✓"
FAIL = "✗"
WARN = "!"

# schema.sql 應該建立的資料表
EXPECTED_TABLES = [
    "profiles", "courses", "course_members", "tool_permissions",
    "ai_tools", "tool_cases", "learning_modules", "module_steps",
    "step_attempts", "ai_evaluations", "teacher_reviews", "prompt_logs",
    "forms", "form_responses", "research_consents",
]

# migration 001 / 003 之後才有的欄位（新建資料庫跑 schema.sql 就會有）
EXPECTED_COLUMNS = {
    "ai_evaluations": ["confidence", "needs_teacher_review"],
    "ai_tools": ["scale_type", "teaching_strategy", "error_taxonomy"],
    "prompt_logs": ["hint_trigger", "hint_source"],
    "step_attempts": ["tutor_action"],
}

PLACEHOLDERS = {
    "supabase_url": "https://your-project.supabase.co",
    "supabase_service_key": "eyJ...",
    "gemini_api_key": "AIzaSy...",
}


def explain_gemini_error(error: Exception, model: str) -> str:
    """把 Gemini 的錯誤翻成看得懂的原因與處理方式"""
    text = f"{type(error).__name__}: {error}"
    low = text.lower()
    if "proxy" in low or "connect" in low:
        return f"連不到 Gemini 伺服器，檢查網路或公司／學校的防火牆（{text[:120]}）"
    if "not found" in low or "404" in low:
        return f"找不到模型 {model}：確認 GEMINI_MODEL 拼字，或改用 gemini-3.8-flash（{text[:120]}）"
    if "permission" in low or "403" in low:
        hint = "2.5 系列只開放給以前用過的帳號，請換成 3.x 模型" if "2.5" in model else "到 Google AI Studio 確認這把金鑰有開通 Gemini API"
        return f"這把金鑰沒有權限使用 {model}：{hint}（{text[:120]}）"
    if "api key" in low or "api_key" in low or "401" in low or "invalid" in low:
        return f"GEMINI_API_KEY 無效：到 Google AI Studio 重新產生（{text[:120]}）"
    if "quota" in low or "429" in low or "resource_exhausted" in low:
        return f"額度用完或呼叫太頻繁，稍後再試（{text[:120]}）"
    if "timeout" in low:
        return f"呼叫逾時，檢查網路（{text[:120]}）"
    return f"呼叫失敗：{text[:200]}"


def check_gemini(settings, llm=None) -> tuple[bool, str]:
    """
    用設定的 GEMINI_MODEL 實際呼叫一次（只要求回覆 OK，花費極少）。
    llm 參數留給測試注入假物件。
    """
    import asyncio
    from langchain_core.messages import HumanMessage
    from core.ai.llm_utils import invoke_with_retry

    model = settings.gemini_model
    try:
        if llm is None:
            from langchain_google_genai import ChatGoogleGenerativeAI
            llm = ChatGoogleGenerativeAI(model=model, google_api_key=settings.gemini_api_key)
        reply = asyncio.run(invoke_with_retry(
            llm, [HumanMessage(content="只回覆 OK 兩個字母")],
            label="check_setup", max_attempts=1, timeout=30,
        ))
    except Exception as e:  # noqa: BLE001
        return False, explain_gemini_error(e, model)
    return True, f"{model} 可以正常呼叫（回覆：{reply.strip()[:20]}）"


def main() -> int:
    print("\n=== 1. 環境變數 ===\n")

    try:
        from core.config import get_settings
    except ImportError as e:
        print(f"  {FAIL} 無法載入設定：{e}")
        print("     請確認你在 backend/ 目錄下執行，且已 pip install -r requirements.txt")
        return 1

    settings = get_settings()
    problems = []

    for key, placeholder in PLACEHOLDERS.items():
        value = getattr(settings, key, "")
        label = key.upper()

        if not value:
            print(f"  {FAIL} {label:22} 空的")
            problems.append(label)
        elif value == placeholder:
            print(f"  {FAIL} {label:22} 還是 .env.example 的範例值，沒填過真的值")
            problems.append(label)
        else:
            shown = value if key == "supabase_url" else f"（長度 {len(value)}）"
            print(f"  {OK} {label:22} {shown}")

    print(f"  {OK} {'GEMINI_MODEL':22} {settings.gemini_model}")

    # ── Gemini 模型實際呼叫一次 ─────────────────────────────
    print("\n=== 1b. Gemini 模型 ===\n")
    if "GEMINI_API_KEY" in problems:
        print(f"  {WARN} 沒有 GEMINI_API_KEY，跳過")
    else:
        ok, message = check_gemini(settings)
        print(f"  {OK if ok else FAIL} {message}")

    if "SUPABASE_URL" in problems or "SUPABASE_SERVICE_KEY" in problems:
        print("\n  → Supabase 尚未設定，後面的檢查跳過。")
        print("    請看 docs/SETUP.md")
        return 1

    # ── 連線 ────────────────────────────────────────────────
    print("\n=== 2. 連線 ===\n")
    try:
        from database.client import get_supabase
        db = get_supabase()
        db.table("profiles").select("id", count="exact").limit(1).execute()
        print(f"  {OK} 連線成功")
    except Exception as e:
        print(f"  {FAIL} 連線失敗：{type(e).__name__}: {e}")
        print("\n  常見原因：")
        print("    - SUPABASE_URL 打錯（要像 https://xxxxx.supabase.co，結尾沒有斜線）")
        print("    - 用到 anon/publishable key 而不是 service_role/secret key")
        print("    - schema.sql 還沒執行，profiles 表不存在")
        return 1

    # ── 資料表 ──────────────────────────────────────────────
    print("\n=== 3. 資料表 ===\n")
    missing_tables = []
    for table in EXPECTED_TABLES:
        try:
            db.table(table).select("*", count="exact").limit(1).execute()
        except Exception:
            missing_tables.append(table)

    if missing_tables:
        print(f"  {FAIL} 缺少 {len(missing_tables)} 張表：{', '.join(missing_tables)}")
        print("     → 請在 Supabase SQL Editor 執行 supabase/schema.sql")
        return 1
    print(f"  {OK} 15 張表都在")

    # ── 欄位 ────────────────────────────────────────────────
    print("\n=== 4. LLM 邏輯系統需要的欄位 ===\n")
    missing_columns = []
    for table, columns in EXPECTED_COLUMNS.items():
        for column in columns:
            try:
                db.table(table).select(column).limit(1).execute()
            except Exception:
                missing_columns.append(f"{table}.{column}")

    if missing_columns:
        print(f"  {FAIL} 缺少欄位：{', '.join(missing_columns)}")
        print("     → 請依序執行 supabase/migrations/ 底下尚未跑過的 migration")
        return 1
    print(f"  {OK} 欄位齊全")

    # ── 種子資料 ────────────────────────────────────────────
    print("\n=== 5. 種子資料 ===\n")
    counts = {}
    for table in ["profiles", "ai_tools", "tool_cases", "learning_modules", "module_steps"]:
        try:
            res = db.table(table).select("id", count="exact").execute()
            counts[table] = res.count or 0
        except Exception:
            counts[table] = -1

    for table, n in counts.items():
        mark = OK if n > 0 else WARN
        print(f"  {mark} {table:20} {n} 筆")

    if all(n == 0 for n in counts.values()):
        print("\n  → 資料庫是空的。想要示範資料的話，執行 supabase/seed.sql")
        print("    （不跑也可以，只是前端會看不到任何機器人與步驟）")
        return 0

    # ── 教授有沒有設定提示與錯誤分類 ────────────────────────
    if counts.get("ai_tools", 0) > 0:
        print("\n=== 6. 機器人設定 ===\n")
        tools = db.table("ai_tools").select(
            "title, rubric_criteria, teaching_strategy, error_taxonomy"
        ).execute()
        from core.ai.error_detector import parse_error_taxonomy
        from core.ai.hint_engine import parse_teaching_strategy

        for tool in tools.data or []:
            title = (tool.get("title") or "未命名")[:28]
            rubric = tool.get("rubric_criteria") or []
            # 用系統實際的解析器計算，格式不對的項目不算——這樣才看得出「填了但沒生效」
            plan = parse_teaching_strategy(tool.get("teaching_strategy"))
            raw_taxonomy = tool.get("error_taxonomy") or []
            taxonomy = parse_error_taxonomy(raw_taxonomy)
            print(f"  {title}")
            print(f"      Rubric 構面 {len(rubric)}｜分層提示 {plan.total_levels} 層｜錯誤分類 {len(taxonomy)} 項")
            if not plan.total_levels:
                print(f"      {WARN} 沒有可用的分層提示（需要 hints[].content），系統會依 Rubric 即時生成")
            if len(taxonomy) < len(raw_taxonomy):
                print(f"      {WARN} 錯誤分類有 {len(raw_taxonomy) - len(taxonomy)} 項格式無法辨識（需要 code/label 或 error_code/name）")

    print(f"\n{OK} 全部檢查通過，可以啟動後端了：uvicorn main:app --reload\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
