"""
資料匯出 API（DAT-001、開會紀錄 #49 第 7 項；docs/api.md 第七節）

  POST /api/export/research   研究用：去識別化（姓名、Email 換成匿名研究編號），只含已同意研究使用的學生
  POST /api/export/teaching   教學用：含姓名與 Email，供老師教學管理

兩者都只限教授（owner），回傳 UTF-8（含 BOM，Excel 開中文不亂碼）的 CSV 檔，
並在 export_logs 記一筆稽核紀錄（migration 018；寫入失敗只記 log，不擋匯出）。

兩種資料集（dataset）：
  attempts       一列 = 一次作答：作答者、步驟、AI 評分（逐構面）、錯誤分類、提示使用、教師最終判定
  chat_messages  一列 = 一則聊天室訊息：誰說的、哪個機器人回答、怎麼選的、學習助教引用與是否給答案（#83）
  form_responses 一列 = 一份表單填答的一題（略過的表單一列）：表單版本、出現時點、當時的 AI 分數、答案（#80）
格式（format）：csv 一次一個資料集；xlsx 一個檔三個工作表（作答、聊天訊息、表單填答）。
表單的簡答與長文字算作答內容：沒勾 include_content 時不匯出；單選、複選、量尺一律匯出。
作答原文與訊息內容預設不匯出（include_content），研究用時就算勾選也會再過一次個資遮罩。
欄位定義見 docs/DATA_DICTIONARY.md。
"""

import csv
import io
import json
import logging
import re
from datetime import date, datetime, timezone
from typing import Literal, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from pydantic import BaseModel

from core.audit import record
from core.auth import CurrentUser, require_staff
from core.privacy import PII_PATTERNS
from database.client import get_supabase

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/export", tags=["Export"])

ATTEMPT_SELECT = (
    "id, user_id, module_id, step_id, attempt_number, status, tutor_action, created_at, user_input_content, "
    "module_steps(step_order, step_title, pass_score, ai_tools(title)), "
    "ai_evaluations(total_score, confidence, dimension_scores, detected_errors), "
    "teacher_reviews(decision, final_score, is_published, reviewed_at), "
    "prompt_logs(hint_level, hint_trigger)"
)

COMMON_COLUMNS = [
    "module_title", "step_order", "step_title", "tool_title", "attempt_number", "submitted_at",
    "status", "tutor_action", "answer_chars", "ai_total_score", "ai_confidence", "ai_dimensions",
    "detected_errors", "hints_used", "hint_levels", "teacher_decision", "teacher_final_score",
    "teacher_published", "teacher_reviewed_at",
]

CHAT_SELECT = (
    "id, user_id, module_id, step_id, role, bot_name, content, routing, attempt_id, created_at, "
    "module_steps(step_order, step_title)"
)
CHAT_COLUMNS = [
    "module_title", "step_order", "step_title", "sent_at", "role", "bot_name", "routed_by", "routing_reason",
    "is_tutor", "answer_mode", "times_asked", "sources", "evaluation_attempt_number", "message_chars",
]
FORM_SELECT = (
    "id, user_id, module_id, step_id, form_version, timing, skipped, ai_total_score, responses, submitted_at, "
    "forms(title, version, schema_json), module_steps(step_order, step_title), ai_tools(title), "
    "step_attempts(attempt_number)"
)
FORM_COLUMNS = [
    "module_title", "step_order", "step_title", "tool_title", "form_title", "form_version", "form_timing",
    "form_submitted_at", "form_skipped", "attempt_number", "ai_total_score", "question_id", "question_type",
    "question_label", "answer", "answer_na",
]
FORM_TEXT_TYPES = ("short", "long")
SHEET_TITLES = {"attempts": "作答", "chat_messages": "聊天訊息", "form_responses": "表單填答"}
DATASETS = ("attempts", "chat_messages", "form_responses")


class ExportRequest(BaseModel):
    course_id: UUID
    module_id: Optional[UUID] = None
    date_from: Optional[date] = None
    date_to: Optional[date] = None
    include_content: bool = False
    # attempts：一列一次作答；chat_messages：一列一則聊天訊息（#83）。xlsx 會兩個都放（各一個工作表）
    # form_responses：一列一份表單填答的一題（#80）
    dataset: Literal["attempts", "chat_messages", "form_responses"] = "attempts"
    format: Literal["csv", "xlsx"] = "csv"


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


def _require_owner(user: CurrentUser) -> None:
    if user.role != "owner":
        raise _error(status.HTTP_403_FORBIDDEN, "PERMISSION_DENIED", "只有教授可以匯出資料")


def _as_list(value) -> list[dict]:
    if isinstance(value, list):
        return [v for v in value if isinstance(v, dict)]
    return [value] if isinstance(value, dict) else []


def _first(value) -> dict:
    items = _as_list(value)
    return items[0] if items else {}


def mask_pii(text: str) -> str:
    """研究匯出的作答原文：符合個資規則（core/privacy.PII_PATTERNS，與送出作答前的檢查同一組）的片段換成 [已遮罩]"""
    masked = text or ""
    for _, pattern in PII_PATTERNS:
        masked = re.sub(pattern, "[已遮罩]", masked)
    return masked


def _row(attempt: dict, modules: dict[str, str]) -> dict:
    step = _first(attempt.get("module_steps"))
    evaluation = _first(attempt.get("ai_evaluations"))
    # 同一次作答有多筆教師判定時取最新一筆（依 reviewed_at）
    review = max(_as_list(attempt.get("teacher_reviews")), key=lambda r: r.get("reviewed_at") or "", default={})
    hints = sorted(_as_list(attempt.get("prompt_logs")), key=lambda h: h.get("hint_level") or 0)
    dims = ";".join(
        f"{d.get('dimension')}:{d.get('score')}/{d.get('max_score')}"
        for d in evaluation.get("dimension_scores") or [] if isinstance(d, dict)
    )
    errors = ";".join(
        str(e.get("code") or e.get("label")) for e in evaluation.get("detected_errors") or [] if isinstance(e, dict)
    )
    return {
        "module_title": modules.get(attempt.get("module_id"), ""),
        "step_order": step.get("step_order"),
        "step_title": step.get("step_title") or "",
        "tool_title": _first(step.get("ai_tools")).get("title") or "",
        "attempt_number": attempt.get("attempt_number"),
        "submitted_at": attempt.get("created_at"),
        "status": attempt.get("status"),
        "tutor_action": attempt.get("tutor_action") or "",
        "answer_chars": len(attempt.get("user_input_content") or ""),
        "ai_total_score": evaluation.get("total_score"),
        "ai_confidence": evaluation.get("confidence"),
        "ai_dimensions": dims,
        "detected_errors": errors,
        "hints_used": len(hints),
        "hint_levels": ";".join(str(h.get("hint_level")) for h in hints),
        "teacher_decision": review.get("decision") or "",
        "teacher_final_score": review.get("final_score"),
        "teacher_published": review.get("is_published") if review else "",
        "teacher_reviewed_at": review.get("reviewed_at") or "",
    }


def _chat_row(msg: dict, modules: dict[str, str], attempt_numbers: dict[str, int]) -> dict:
    step = _first(msg.get("module_steps"))
    routing = msg.get("routing") if isinstance(msg.get("routing"), dict) else {}
    return {
        "module_title": modules.get(msg.get("module_id"), ""),
        "step_order": step.get("step_order"),
        "step_title": step.get("step_title") or "",
        "sent_at": msg.get("created_at"),
        "role": msg.get("role"),
        "bot_name": msg.get("bot_name") or "",
        "routed_by": routing.get("by") or "",
        "routing_reason": routing.get("reason") or "",
        # 學習助教的回覆才有 answer_mode（guide / answer）
        "is_tutor": bool(routing.get("answer_mode")) if msg.get("role") == "bot" else "",
        "answer_mode": routing.get("answer_mode") or "",
        "times_asked": routing.get("times_asked") if routing.get("times_asked") is not None else "",
        "sources": ";".join(str(x) for x in routing.get("sources") or []),
        "evaluation_attempt_number": attempt_numbers.get(msg.get("attempt_id"), "") if msg.get("attempt_id") else "",
        "message_chars": len(msg.get("content") or ""),
    }


def _load_chat(db, body: ExportRequest, user_ids: list[str], modules: dict[str, str]) -> list[dict]:
    """聊天室訊息；還沒跑 migration 020（沒有 chat_messages）時當作沒有"""
    if not user_ids or not modules:
        return []
    try:
        rows = (
            db.table("chat_messages").select(CHAT_SELECT)
            .in_("module_id", list(modules)).in_("user_id", user_ids).execute()
        ).data or []
    except Exception as e:  # noqa: BLE001
        logger.warning(f"讀取聊天訊息失敗（是否尚未執行 migration 020？）：{e}")
        return []
    return sorted(_date_filter(rows, body), key=lambda m: (m.get("user_id") or "", m.get("created_at") or ""))


def _form_rows(resp: dict, modules: dict[str, str], include_content: bool, mask: bool) -> list[dict]:
    """一份填答展開成一題一列；略過的表單一列（題目欄位空白）"""
    form = _first(resp.get("forms"))
    step = _first(resp.get("module_steps"))
    base = {
        "module_title": modules.get(resp.get("module_id"), ""),
        "step_order": step.get("step_order"),
        "step_title": step.get("step_title") or "",
        "tool_title": _first(resp.get("ai_tools")).get("title") or "",
        "form_title": form.get("title") or "",
        "form_version": resp.get("form_version") or form.get("version"),
        "form_timing": resp.get("timing") or "",
        "form_submitted_at": resp.get("submitted_at"),
        "form_skipped": bool(resp.get("skipped")),
        "attempt_number": _first(resp.get("step_attempts")).get("attempt_number"),
        "ai_total_score": resp.get("ai_total_score"),
    }
    if resp.get("skipped"):
        return [base]
    saved = resp.get("responses") if isinstance(resp.get("responses"), dict) else {}
    answers = saved.get("answers") if isinstance(saved.get("answers"), dict) else {}
    na = set(saved.get("na") or [])
    rows = []
    # 題目用「填答當時那一版」的（forms 一列 = 一個版本）
    for q in (form.get("schema_json") or {}).get("questions") or []:
        if not isinstance(q, dict):
            continue
        qid, qtype = q.get("id"), q.get("type")
        value = answers.get(qid)
        if qtype in FORM_TEXT_TYPES:
            text = value if isinstance(value, str) else ""
            answer = (mask_pii(text) if mask else text) if include_content else ""
        elif isinstance(value, list):
            answer = ";".join(str(v) for v in value)
        else:
            answer = "" if value is None else value
        rows.append({
            **base,
            "question_id": qid,
            "question_type": qtype,
            "question_label": q.get("label") or "",
            "answer": answer,
            "answer_na": qid in na,
        })
    return rows


def _load_forms(db, body: ExportRequest, user_ids: list[str], modules: dict[str, str]) -> list[dict]:
    """表單填答；還沒跑 migration 029 時當作沒有"""
    if not user_ids or not modules:
        return []
    try:
        rows = (
            db.table("form_responses").select(FORM_SELECT)
            .in_("module_id", list(modules)).in_("user_id", user_ids).execute()
        ).data or []
    except Exception as e:  # noqa: BLE001
        logger.warning(f"讀取表單填答失敗（是否尚未執行 migration 029？）：{e}")
        return []
    rows = _date_filter(rows, body, "submitted_at")
    return sorted(rows, key=lambda r: (r.get("user_id") or "", r.get("submitted_at") or ""))


def _date_filter(rows: list[dict], body: ExportRequest, field: str = "created_at") -> list[dict]:
    if body.date_from:
        rows = [r for r in rows if (r.get(field) or "")[:10] >= body.date_from.isoformat()]
    if body.date_to:
        rows = [r for r in rows if (r.get(field) or "")[:10] <= body.date_to.isoformat()]
    return rows


def _load(db, body: ExportRequest) -> tuple[dict, list[dict], list[dict], dict[str, str]]:
    """課程、成員、作答、模組名稱"""
    course_id = str(body.course_id)
    course = db.table("courses").select("id, title").eq("id", course_id).limit(1).execute().data or []
    if not course:
        raise _error(status.HTTP_404_NOT_FOUND, "COURSE_NOT_FOUND", "找不到這門課程")

    assigned = (
        db.table("course_modules").select("module_id, learning_modules(title)").eq("course_id", course_id).execute()
    ).data or []
    modules = {a["module_id"]: _first(a.get("learning_modules")).get("title") or "" for a in assigned}
    if body.module_id:
        modules = {k: v for k, v in modules.items() if k == str(body.module_id)}
        if not modules:
            raise _error(status.HTTP_404_NOT_FOUND, "MODULE_NOT_IN_COURSE", "這個模組沒有指派給這門課程")

    members = (
        db.table("course_members").select("user_id, status, profiles(full_name, email, role)")
        .eq("course_id", course_id).execute()
    ).data or []
    # 只匯出學生；被移出的學生先前的作答仍屬於這門課的學習歷程
    members = [m for m in members if _first(m.get("profiles")).get("role") not in ("owner", "assistant")
               and m.get("status") in ("active", "removed")]
    user_ids = [m["user_id"] for m in members]

    attempts: list[dict] = []
    if user_ids and modules:
        attempts = (
            db.table("step_attempts").select(ATTEMPT_SELECT)
            .in_("module_id", list(modules)).in_("user_id", user_ids).execute()
        ).data or []
    attempts = _date_filter(attempts, body)
    attempts.sort(key=lambda a: (a.get("user_id") or "", a.get("created_at") or ""))
    return course[0], members, attempts, modules


def _csv(columns: list[str], rows: list[dict]) -> bytes:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return ("﻿" + buf.getvalue()).encode("utf-8")


def _audit(db, user: CurrentUser, kind: str, body: ExportRequest, row_count: int) -> None:
    # 操作紀錄（#90）：跟其他教師端操作放在同一個地方查；export_logs 照舊保留
    record(db, user, f"export.{kind}", target_type="course", target_id=body.course_id, detail={
        "row_count": row_count,
        "include_content": bool(getattr(body, "include_content", False)),
    })
    try:
        db.table("export_logs").insert({
            "exported_by": user.id,
            "kind": kind,
            "course_id": str(body.course_id),
            "params": json.loads(body.model_dump_json()),
            "row_count": row_count,
        }).execute()
    except Exception as e:  # noqa: BLE001 - 稽核寫入失敗不擋匯出，但要留下紀錄
        logger.warning(f"匯出稽核紀錄寫入失敗（請確認已執行 migration 018）：{e}")


def _xlsx(sheets: list[tuple[str, list[str], list[dict]]]) -> bytes:
    """一個檔多個工作表；第一列是欄位名稱（與 CSV 相同，對照 docs/DATA_DICTIONARY.md）"""
    from openpyxl import Workbook

    wb = Workbook(write_only=True)
    for title, columns, rows in sheets:
        ws = wb.create_sheet(title)
        ws.append(columns)
        for row in rows:
            ws.append([_cell(row.get(c)) for c in columns])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _cell(value):
    if value is None:
        return ""
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _download(content: bytes, kind: str, fmt: str, counts: dict[str, int], dataset: str) -> Response:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    suffix = "" if fmt == "xlsx" or dataset == "attempts" else ("-chat" if dataset == "chat_messages" else "-forms")
    media = ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" if fmt == "xlsx"
             else "text/csv; charset=utf-8")
    return Response(
        content=content,
        media_type=media,
        headers={
            "Content-Disposition": f'attachment; filename="{kind}-export{suffix}-{stamp}.{fmt}"',
            # 主要資料集的列數（csv 是選的那個；xlsx 是作答），聊天訊息另外給
            "X-Export-Row-Count": str(counts["attempts"] if fmt == "xlsx" else counts[dataset]),
            "X-Export-Chat-Count": str(counts["chat_messages"]),
            "X-Export-Form-Count": str(counts["form_responses"]),
            "Access-Control-Expose-Headers": "Content-Disposition, X-Export-Row-Count, X-Export-Chat-Count, X-Export-Form-Count",
        },
    )


def _respond(db, user, kind: str, body: ExportRequest, identity, attempts, chat, modules, *, mask: bool,
             forms: list[dict] | None = None) -> Response:
    """
    identity(user_id) → 識別欄位 dict（研究用：匿名編號；教學用：姓名與 Email），回 None 表示這位學生不匯出。
    """
    id_columns = ["research_id"] if kind == "research" else ["student_name", "student_email"]
    numbers = {a.get("id"): a.get("attempt_number") for a in attempts}

    attempt_rows, chat_rows = [], []
    for a in attempts:
        who = identity(a.get("user_id"))
        if who is None:
            continue
        row = {**who, **_row(a, modules)}
        if body.include_content:
            text = a.get("user_input_content") or ""
            row["answer_text"] = mask_pii(text) if mask else text
        attempt_rows.append(row)
    for m in chat:
        who = identity(m.get("user_id"))
        if who is None:
            continue
        row = {**who, **_chat_row(m, modules, numbers)}
        if body.include_content:
            text = m.get("content") or ""
            row["message_text"] = mask_pii(text) if mask else text
        chat_rows.append(row)

    form_rows = []
    for r in forms or []:
        who = identity(r.get("user_id"))
        if who is None:
            continue
        form_rows.extend({**who, **row} for row in _form_rows(r, modules, body.include_content, mask))

    attempt_columns = [*id_columns, *COMMON_COLUMNS] + (["answer_text"] if body.include_content else [])
    chat_columns = [*id_columns, *CHAT_COLUMNS] + (["message_text"] if body.include_content else [])
    form_columns = [*id_columns, *FORM_COLUMNS]
    counts = {"attempts": len(attempt_rows), "chat_messages": len(chat_rows), "form_responses": len(form_rows)}
    sheets = {
        "attempts": (attempt_columns, attempt_rows),
        "chat_messages": (chat_columns, chat_rows),
        "form_responses": (form_columns, form_rows),
    }

    if body.format == "xlsx":
        content = _xlsx([(SHEET_TITLES[d], *sheets[d]) for d in DATASETS])
    else:
        content = _csv(*sheets[body.dataset])

    _audit(db, user, kind, body, counts["attempts"] if body.format == "xlsx" else counts[body.dataset])
    return _download(content, kind, body.format, counts, body.dataset)


def _wants(body: ExportRequest, dataset: str) -> bool:
    return body.dataset == dataset or body.format == "xlsx"


@router.post("/research")
async def export_research(body: ExportRequest, user: CurrentUser = Depends(require_staff)):
    """去識別化：匿名研究編號取代姓名與 Email，只含 research_consents.is_consented 且未撤回的學生"""
    _require_owner(user)
    db = get_supabase()
    _, members, attempts, modules = _load(db, body)

    user_ids = [m["user_id"] for m in members]
    consents = (
        db.table("research_consents").select("user_id, anonymous_research_id, is_consented, withdrawn_at")
        .in_("user_id", user_ids).execute()
    ).data if user_ids else []
    anon = {
        c["user_id"]: c["anonymous_research_id"]
        for c in consents or []
        if c.get("is_consented") and not c.get("withdrawn_at") and c.get("anonymous_research_id")
    }
    chat = _load_chat(db, body, list(anon), modules) if _wants(body, "chat_messages") else []
    forms = _load_forms(db, body, list(anon), modules) if _wants(body, "form_responses") else []

    def identity(uid):
        rid = anon.get(uid)
        return {"research_id": rid} if rid else None  # 沒同意或已撤回：不匯出

    return _respond(db, user, "research", body, identity, attempts, chat, modules, mask=True, forms=forms)


@router.post("/teaching")
async def export_teaching(body: ExportRequest, user: CurrentUser = Depends(require_staff)):
    """教學用：含學生姓名與 Email（可識別），只供老師教學管理，不可用於研究發表"""
    _require_owner(user)
    db = get_supabase()
    _, members, attempts, modules = _load(db, body)
    people = {m["user_id"]: _first(m.get("profiles")) for m in members}
    chat = _load_chat(db, body, list(people), modules) if _wants(body, "chat_messages") else []
    forms = _load_forms(db, body, list(people), modules) if _wants(body, "form_responses") else []

    def identity(uid):
        p = people.get(uid)
        if p is None:
            return None
        return {"student_name": p.get("full_name") or "", "student_email": p.get("email") or ""}

    return _respond(db, user, "teaching", body, identity, attempts, chat, modules, mask=False, forms=forms)
