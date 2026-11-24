"""
複製與範本（#89，需求說明書 BLD-002、BLD-008，migration 023）

兩件事共用同一套「取出內容 → 建立新的一份」：
  - 複製：把一支機器人或一個模組「現在的內容」建立成新的一份
  - 範本：先把內容存進 templates 表（存的是當時的副本），之後「從範本建立」再建立成新的一份

規則：
  - 新的機器人一律從第 1 版、草稿開始，建立者是操作的人（不是原本的建立者）
  - 新的模組一律未發布；只複製啟用中的關卡，順序重新編成 1、2、3…
  - 模組的關卡指向原本的機器人（不會連機器人一起複製）；機器人已封存或已刪除的關卡會略過，並回報給老師
  - 聊天室關卡連對話機器人設定（chat_room_bots）一起複製
  - 學生作答、評分、教師判定都不會被複製（新的一份從零開始）
"""

import copy
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

# 機器人的教學內容（修改時會升版的欄位，api/teacher.py 也用這份）
TOOL_CONTENT_FIELDS = (
    "title",
    "domain",
    "target_competency",
    "role_instruction",
    "system_prompt",
    "teaching_strategy",
    "rubric_criteria",
    "scale_type",
    "error_taxonomy",
)
# 建立機器人時資料庫規定不能空白的欄位
TOOL_REQUIRED_FIELDS = ("title", "domain", "target_competency", "role_instruction", "system_prompt")

# 模組關卡要複製的設定（學生作答相關的都不複製）
STEP_SETTING_FIELDS = (
    "step_title",
    "tool_id",
    "is_required",
    "pass_score",
    "require_teacher_review",
    "pass_mode",
    "step_type",
)
# 這兩個欄位是預設值時不寫進資料庫：還沒跑 019／020 的資料庫照樣能建立
STEP_DEFAULTS = {"pass_mode": "score", "step_type": "answer"}

TITLE_MAX = 100
COPY_SUFFIX = "（複本）"


class TemplateError(Exception):
    """內容不完整、沒有可用的關卡等，API 轉成 400"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


# ── 標題 ─────────────────────────────────────────────────────────────────────


def copy_title(title: Optional[str]) -> str:
    """「原本的名稱（複本）」；太長就把原本的名稱截短，讓後綴一定看得到"""
    base = (title or "").strip() or "未命名"
    return base[: TITLE_MAX - len(COPY_SUFFIX)] + COPY_SUFFIX


def pick_title(requested: Optional[str], fallback: str) -> str:
    """老師有填新名稱就用新名稱，沒填用預設"""
    title = (requested or "").strip() or fallback
    return title[:TITLE_MAX]


# ── 機器人 ──────────────────────────────────────────────────────────────────


def tool_content(tool: dict) -> dict:
    """機器人的教學內容（不含 id、版本、狀態、建立者），深拷貝避免改到原本的資料"""
    return {f: copy.deepcopy(tool[f]) for f in TOOL_CONTENT_FIELDS if f in tool and tool[f] is not None}


def check_tool_content(content: Any) -> dict:
    """範本內容至少要有建立機器人必填的欄位"""
    if not isinstance(content, dict):
        raise TemplateError("TEMPLATE_INVALID", "範本內容不完整，無法建立機器人")
    missing = [f for f in TOOL_REQUIRED_FIELDS if not str(content.get(f) or "").strip()]
    if missing:
        raise TemplateError("TEMPLATE_INVALID", f"範本內容缺少欄位：{'、'.join(missing)}")
    return content


def new_tool_row(content: dict, *, user_id: str, title: str) -> dict:
    """要寫進 ai_tools 的新一列：第 1 版、草稿、建立者是操作的人"""
    return {
        **tool_content(content),
        "title": title,
        "status": "draft",
        "version": 1,
        "created_by": user_id,
    }


def save_tool_version(db, tool: dict, user_id: Optional[str]) -> None:
    """把這一版的完整內容存進 ai_tool_versions；同一版已經存過就略過（migration 022）"""
    try:
        exists = (
            db.table("ai_tool_versions").select("id")
            .eq("tool_id", tool["id"]).eq("version", tool["version"]).limit(1).execute().data
        )
        if exists:
            return
        db.table("ai_tool_versions").insert({
            "tool_id": tool["id"],
            "version": tool["version"],
            "snapshot": tool,
            "edited_by": user_id,
        }).execute()
    except Exception:  # noqa: BLE001 - 版本紀錄失敗不擋住儲存，但一定要留下 log
        logger.exception("ai_tool_versions 寫入失敗（tool_id=%s, version=%s），請確認已執行 migration 022",
                         tool.get("id"), tool.get("version"))


def create_tool(db, content: dict, *, user_id: str, title: str) -> dict:
    """依內容建立一支新的機器人（第 1 版、草稿），並存下第 1 版"""
    res = db.table("ai_tools").insert(new_tool_row(content, user_id=user_id, title=title)).execute()
    if not res.data:
        raise RuntimeError("建立機器人失敗")
    tool = res.data[0]
    save_tool_version(db, tool, user_id)
    return tool


# ── 模組 ──────────────────────────────────────────────────────────────────


def module_content(module: dict, steps: list[dict], bots: list[dict]) -> dict:
    """
    模組要複製的內容：模組資料 + 啟用中的關卡（依順序）+ 聊天室的對話機器人。
    bots 是 chat_room_bots 的列（可以包含其他關卡的，會依 step_id 分好）。
    """
    bots_by_step: dict[str, list[dict]] = {}
    for b in sorted(bots, key=lambda r: r.get("sort_order") or 0):
        bots_by_step.setdefault(b.get("step_id"), []).append({
            "tool_id": b.get("tool_id"),
            "display_name": b.get("display_name"),
            "when_to_use": b.get("when_to_use") or "",
            "use_materials": bool(b.get("use_materials")),
        })

    active = [s for s in steps if s.get("is_active", True)]
    active.sort(key=lambda s: s.get("step_order") or 0)
    return {
        "title": module.get("title"),
        "description": module.get("description"),
        "domain": module.get("domain"),
        "steps": [
            {
                **{f: s.get(f) for f in STEP_SETTING_FIELDS if s.get(f) is not None},
                "chat_bots": bots_by_step.get(s.get("id"), []) if s.get("step_type") == "chat" else [],
            }
            for s in active
        ],
    }


def module_tool_ids(content: dict) -> list[str]:
    """模組內容用到的所有機器人（關卡 + 聊天室對話機器人），不重複、照出現順序"""
    ids: list[str] = []
    for s in content.get("steps") or []:
        for tid in [s.get("tool_id"), *[b.get("tool_id") for b in s.get("chat_bots") or []]]:
            if tid and tid not in ids:
                ids.append(tid)
    return ids


def plan_module_copy(content: dict, tools: dict[str, dict]) -> tuple[list[dict], list[str]]:
    """
    決定哪些關卡可以建立：機器人存在且沒有封存。
    回傳 (要建立的關卡, 略過的關卡名稱)。聊天室裡不能用的對話機器人直接拿掉（關卡照樣建立）。
    """
    def usable(tid: Optional[str]) -> bool:
        tool = tools.get(tid) if tid else None
        return bool(tool) and tool.get("status") != "archived"

    planned, skipped = [], []
    for s in content.get("steps") or []:
        if not usable(s.get("tool_id")):
            skipped.append(s.get("step_title") or "（未命名關卡）")
            continue
        seen: set[str] = set()
        bots = []
        for b in s.get("chat_bots") or []:
            tid = b.get("tool_id")
            if usable(tid) and tid != s.get("tool_id") and tid not in seen and b.get("display_name"):
                seen.add(tid)
                bots.append(b)
        planned.append({**s, "chat_bots": bots})
    return planned, skipped


def _step_row(module_id: str, order: int, step: dict) -> dict:
    row = {
        "module_id": module_id,
        "step_order": order,
        "tool_id": step["tool_id"],
        "step_title": (step.get("step_title") or f"關卡 {order}")[:TITLE_MAX],
        "is_required": step.get("is_required", True),
        "pass_score": step.get("pass_score", 70),
        "require_teacher_review": step.get("require_teacher_review", False),
    }
    for field, default in STEP_DEFAULTS.items():
        value = step.get(field)
        if value and value != default:
            row[field] = value
    return row


def create_module(db, content: dict, *, title: str) -> tuple[dict, list[str]]:
    """
    依內容建立一個新的模組（未發布）與它的關卡。
    回傳 (新模組, 略過的關卡名稱)。中途失敗會把建到一半的模組刪掉（關卡、對話機器人跟著刪）。
    """
    if not isinstance(content, dict):
        raise TemplateError("TEMPLATE_INVALID", "範本內容不完整，無法建立模組")

    tool_ids = module_tool_ids(content)
    tools = {}
    if tool_ids:
        res = db.table("ai_tools").select("id, title, status").in_("id", tool_ids).execute()
        tools = {t["id"]: t for t in res.data or []}
    planned, skipped = plan_module_copy(content, tools)

    module = db.table("learning_modules").insert({
        "title": title,
        "description": content.get("description") or None,
        "domain": (content.get("domain") or "").strip() or "學前IEP",
        "is_published": False,
    }).execute().data[0]

    try:
        for order, step in enumerate(planned, start=1):
            created = db.table("module_steps").insert(_step_row(module["id"], order, step)).execute().data[0]
            if step.get("step_type") == "chat" and step["chat_bots"]:
                db.table("chat_room_bots").insert([
                    {
                        "step_id": created["id"],
                        "tool_id": b["tool_id"],
                        "display_name": b["display_name"],
                        "when_to_use": b.get("when_to_use") or "",
                        "sort_order": i,
                        # 預設值不寫：還沒跑 021 的資料庫照樣能建立
                        **({"use_materials": True} if b.get("use_materials") else {}),
                    }
                    for i, b in enumerate(step["chat_bots"], start=1)
                ]).execute()
    except Exception:
        # 建到一半就失敗：刪掉這個新模組，不留下半套的資料
        try:
            db.table("learning_modules").delete().eq("id", module["id"]).execute()
        except Exception:  # noqa: BLE001
            logger.exception("複製模組失敗後清除建到一半的模組也失敗（module_id=%s）", module["id"])
        raise

    return module, skipped
