import threading

from supabase import create_client, Client
from core.config import get_settings

# 每個執行緒各自一個 Supabase Client。
#
# 為什麼不共用一個：FastAPI 會把同步函式（例如 core/auth.py 的 get_current_user）
# 丟到執行緒池同時執行，而 supabase-py 底層的 httpx 用 HTTP/2 共用同一條連線，
# 多個執行緒同時讀寫那條連線就會壞掉——Windows 上的症狀是
# httpx.ReadError: [WinError 10035]，前端同時發兩個請求（例如課程管理頁一次載入
# 課程與成員）就會 500。各執行緒用自己的 client 就不會互相干擾。
_local = threading.local()


def get_supabase() -> Client:
    """
    取得 Supabase Client（使用 Service Role Key）。
    Service Role Key 可繞過 RLS，只在後端使用，絕不傳到前端。
    """
    client = getattr(_local, "client", None)
    if client is None:
        settings = get_settings()
        client = create_client(settings.supabase_url, settings.supabase_service_key)
        _local.client = client
    return client
