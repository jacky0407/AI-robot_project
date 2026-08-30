from supabase import create_client, Client
from core.config import get_settings
from functools import lru_cache


@lru_cache()
def get_supabase() -> Client:
    """
    取得 Supabase Client（使用 Service Role Key）。
    Service Role Key 可繞過 RLS，只在後端使用，絕不傳到前端。
    """
    settings = get_settings()
    return create_client(settings.supabase_url, settings.supabase_service_key)
