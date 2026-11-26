"""
模組停在機器人的舊版本（#89 後半，migration 024）

module_steps.tool_version / chat_room_bots.tool_version：
  - NULL：跟著機器人最新版（原本的行為）
  - 有值：停在這一版，內容從 ai_tool_versions 的 snapshot 讀

什麼時候會停在舊版：老師修改一支被多個模組使用的機器人時，逐個模組選
「更新成新版」或「停在目前這一版」（api/teacher.py 的 update_modules）。

套用舊版內容時只換「教學內容」（system_prompt、rubric_criteria…），
名稱與狀態（發布、封存）仍以機器人最新的為準：
  - 名稱：聊天室用名稱 @ 機器人，改名不該讓舊的關卡對不上
  - 狀態：機器人封存了，停在舊版的關卡也一樣不能用
"""

import logging
from typing import Optional

from core.templates import TOOL_CONTENT_FIELDS

logger = logging.getLogger(__name__)

# 套用舊版時保留最新值、不用舊版覆蓋的欄位
KEEP_LIVE_FIELDS = ("title",)
LOCKED_FIELDS = tuple(f for f in TOOL_CONTENT_FIELDS if f not in KEEP_LIVE_FIELDS)


def locked_version(row: Optional[dict]) -> Optional[int]:
    """這個關卡（或聊天室機器人）停在第幾版；跟著最新版回 None"""
    value = (row or {}).get("tool_version")
    return value if isinstance(value, int) and value > 0 else None


def load_snapshot(db, tool_id: str, version: int, cache: Optional[dict] = None) -> Optional[dict]:
    """讀某支機器人某一版的完整內容；找不到回 None（同一次請求可傳 cache 避免重複查）"""
    key = (tool_id, version)
    if cache is not None and key in cache:
        return cache[key]
    snapshot = None
    try:
        res = (
            db.table("ai_tool_versions").select("snapshot")
            .eq("tool_id", tool_id).eq("version", version).limit(1).execute()
        )
        if res.data:
            snapshot = res.data[0].get("snapshot")
    except Exception:  # noqa: BLE001 - 讀不到舊版就退回最新版，不擋住學生
        logger.exception("讀取 ai_tool_versions 失敗（tool_id=%s, version=%s）", tool_id, version)
    if not isinstance(snapshot, dict):
        snapshot = None
    if cache is not None:
        cache[key] = snapshot
    return snapshot


def apply_locked_version(db, row: Optional[dict], *, tool_key: str = "ai_tools", cache: Optional[dict] = None) -> Optional[dict]:
    """
    row 是 module_steps 或 chat_room_bots 的一列（含 tool_id、tool_version 與帶出來的 ai_tools）。
    停在舊版時，把 row[tool_key] 的教學內容換成那一版，並把 version 改成那一版（評分時記下的版本才對）。
    直接修改並回傳 row；找不到那一版的內容時保持最新版並留下 log。
    """
    version = locked_version(row)
    if version is None:
        return row
    tool = row.get(tool_key)
    if isinstance(tool, list):  # PostgREST 有時把一對一關聯包成陣列
        tool = tool[0] if tool else None
    if not isinstance(tool, dict) or tool.get("version") == version:
        return row

    tool_id = row.get("tool_id") or tool.get("id")
    snapshot = load_snapshot(db, str(tool_id), version, cache) if tool_id else None
    if snapshot is None:
        logger.warning("關卡停在第 %s 版，但找不到那一版的內容（tool_id=%s），改用最新版", version, tool_id)
        return row

    row[tool_key] = {
        **tool,
        **{f: snapshot[f] for f in LOCKED_FIELDS if f in snapshot},
        "version": version,
    }
    return row


# ── 修改機器人時：哪些模組在用、逐個模組選要不要更新 ─────────────────────────

UNDEFINED_COLUMN = "42703"
MISSING_TABLE_CODES = ("42P01", "PGRST205")


class LockMigrationRequired(Exception):
    """還沒跑 migration 024（沒有 tool_version 欄位），不能停在舊版"""


def load_tool_usages(db, tool_id: str) -> list[dict]:
    """
    這支機器人被哪些關卡用到（關卡本身或聊天室的對話機器人）：
    [{"kind": "step" | "chat_bot", "id", "module_id", "step_id", "step_title", "tool_version"}]
    """
    steps = db.table("module_steps").select("*").eq("tool_id", tool_id).execute().data or []
    usages = [
        {"kind": "step", "id": s["id"], "module_id": s.get("module_id"), "step_id": s["id"],
         "step_title": s.get("step_title") or "", "tool_version": locked_version(s)}
        for s in steps
    ]

    try:
        bots = db.table("chat_room_bots").select("*").eq("tool_id", tool_id).execute().data or []
    except Exception as e:  # noqa: BLE001 - 還沒跑 020：沒有聊天室
        if getattr(e, "code", None) not in MISSING_TABLE_CODES + (UNDEFINED_COLUMN,):
            raise
        bots = []
    if bots:
        step_ids = sorted({b["step_id"] for b in bots})
        bot_steps = {s["id"]: s for s in db.table("module_steps").select("*").in_("id", step_ids).execute().data or []}
        for b in bots:
            step = bot_steps.get(b["step_id"]) or {}
            usages.append({
                "kind": "chat_bot", "id": b["id"], "module_id": step.get("module_id"), "step_id": b["step_id"],
                "step_title": step.get("step_title") or "", "tool_version": locked_version(b),
            })
    return [u for u in usages if u.get("module_id")]


def summarize_usages(db, usages: list[dict]) -> list[dict]:
    """依模組整理給編輯頁看：模組名稱、用到的關卡、目前跟著最新版還是停在哪一版"""
    module_ids = sorted({u["module_id"] for u in usages})
    if not module_ids:
        return []
    modules = {
        m["id"]: m
        for m in db.table("learning_modules").select("id, title, is_published").in_("id", module_ids).execute().data or []
    }
    result = []
    for module_id in module_ids:
        own = [u for u in usages if u["module_id"] == module_id]
        module = modules.get(module_id) or {}
        result.append({
            "module_id": module_id,
            "title": module.get("title") or "（未命名模組）",
            "is_published": bool(module.get("is_published")),
            "steps": [{"step_title": u["step_title"], "kind": u["kind"], "tool_version": u["tool_version"]} for u in own],
            # 有任何一處跟著最新版 → 修改後這個模組會用到新版
            "follows_latest": any(u["tool_version"] is None for u in own),
            "locked_versions": sorted({u["tool_version"] for u in own if u["tool_version"] is not None}),
        })
    return sorted(result, key=lambda m: m["title"])


def plan_lock_changes(usages: list[dict], update_module_ids: set[str], current_version: int) -> list[dict]:
    """
    老師選了要更新成新版的模組（update_module_ids）：
      - 選了的模組：停在舊版的改成跟著最新版（tool_version → None）
      - 沒選的模組：跟著最新版的改成停在目前這一版（tool_version → current_version）；已經停在更舊版本的不動
    回傳要改的 [{"kind", "id", "tool_version"}]
    """
    changes = []
    for u in usages:
        if u["module_id"] in update_module_ids:
            if u["tool_version"] is not None:
                changes.append({"kind": u["kind"], "id": u["id"], "tool_version": None})
        elif u["tool_version"] is None:
            changes.append({"kind": u["kind"], "id": u["id"], "tool_version": current_version})
    return changes


def apply_lock_changes(db, changes: list[dict]) -> None:
    """寫進 module_steps / chat_room_bots；還沒跑 024 時拋 LockMigrationRequired"""
    tables = {"step": "module_steps", "chat_bot": "chat_room_bots"}
    for c in changes:
        try:
            db.table(tables[c["kind"]]).update({"tool_version": c["tool_version"]}).eq("id", c["id"]).execute()
        except Exception as e:  # noqa: BLE001
            if getattr(e, "code", None) == UNDEFINED_COLUMN:
                raise LockMigrationRequired() from e
            raise
