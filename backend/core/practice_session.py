"""
練習 Session（practice_sessions，migration 009）

前端進入學生工作台時自己產生 session_id（crypto.randomUUID()），不會先呼叫
POST /api/practice/sessions；所以 Session 在「第一次用這個 id 送出作答」時才建立，
記下是誰的練習，之後的作答都掛在同一個 Session 底下（step_attempts.session_id），
研究分析時才分得出「哪幾次作答是同一次坐下來練習」。

規則：
  - session_id 必須是 UUID，否則 400 INVALID_SESSION_ID
  - 第一次出現 → 建立，屬於目前登入者
  - 已存在且是自己的 → 更新 last_active_at
  - 已存在但屬於別人 → 403 SESSION_FORBIDDEN（不能把作答混進別人的練習）
  - 讀寫失敗（例如還沒跑 migration 009）→ 只記 log，作答照常、不連結 Session
"""

import logging
import uuid
from datetime import datetime, timezone

from fastapi import HTTPException, status

logger = logging.getLogger(__name__)

# PostgreSQL 主鍵重複：兩個請求同時用同一個新 id 送出
UNIQUE_VIOLATION = "23505"


def parse_session_id(session_id: str) -> str:
    """確認 session_id 是 UUID，回傳標準格式（小寫、含連字號）。"""
    try:
        return str(uuid.UUID(str(session_id)))
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "INVALID_SESSION_ID",
                "message": "練習 Session 編號格式不正確，請重新整理頁面後再試",
            },
        )


def ensure_session(db, session_id: str, user_id: str) -> str | None:
    """
    確保 Session 存在且屬於這位使用者。

    Returns:
        可以寫進 step_attempts.session_id 的 id；
        Session 功能無法使用（例如資料表不存在）時回 None，呼叫端不要寫這個欄位。

    Raises:
        HTTPException 403 SESSION_FORBIDDEN：這個 Session 屬於別人
    """
    try:
        owner = _find_owner(db, session_id)
        if owner is None:
            try:
                db.table("practice_sessions").insert(
                    {"id": session_id, "user_id": user_id}
                ).execute()
                return session_id
            except Exception as e:  # noqa: BLE001
                # 同時送出兩次時，另一個請求可能剛建好；重查一次再判斷是誰的
                if getattr(e, "code", None) != UNIQUE_VIOLATION:
                    raise
                owner = _find_owner(db, session_id)
                if owner is None:
                    raise

        if owner != user_id:
            raise _forbidden()

        db.table("practice_sessions").update(
            {"last_active_at": datetime.now(timezone.utc).isoformat()}
        ).eq("id", session_id).execute()
        return session_id

    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        logger.warning(f"練習 Session 無法寫入（是否尚未執行 migration 009？），作答照常但不連結 Session：{e}")
        return None


def _find_owner(db, session_id: str) -> str | None:
    res = (
        db.table("practice_sessions")
        .select("user_id")
        .eq("id", session_id)
        .limit(1)
        .execute()
    )
    rows = res.data if isinstance(res.data, list) else []
    return rows[0].get("user_id") if rows else None


def _forbidden() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={
            "code": "SESSION_FORBIDDEN",
            "message": "這個練習 Session 不屬於你，請重新整理頁面後再試",
        },
    )
