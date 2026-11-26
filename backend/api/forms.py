"""
工具專屬表單與質性反思 API（#80，規則見 core/tool_forms.py，migration 029）

教師端（owner / assistant；修改限機器人建立者或教授，同 api/teacher.can_edit_tool）：
  GET    /api/teacher/tools/{tool_id}/forms            這支機器人的表單（啟用中；?include_old=true 含舊版本）
  POST   /api/teacher/tools/{tool_id}/forms            新增表單
  PUT    /api/teacher/forms/{form_id}                  修改 → 產生新版本，舊版停用、舊答案保留原題目
  DELETE /api/teacher/forms/{form_id}                  停用（不刪，已有的填答還要能匯出）
  POST   /api/teacher/forms/{form_id}/release          教師發布（trigger_timing = teacher_release）；body {"released": false} 收回
  GET    /api/teacher/forms/{form_id}/responses        這份表單（所有版本）的填答

學生端（要有這一關的作答權限，同 core/access.check_step_access；不扣次數、不呼叫 AI）：
  GET    /api/practice/steps/{step_id}/forms           現在要填的表單（pending）與擋關狀態
  POST   /api/practice/forms/{form_id}/responses       填答或略過（skipped = true）
"""

import logging
from datetime import datetime, timezone
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from api.teacher import can_edit_tool
from core.access import check_step_access
from core.ai_usage import course_for_access
from core.audit import record
from core.auth import CurrentUser, get_current_user, require_staff
from core.step_unlock import attempt_passed, DEFAULT_PASS_SCORE
from core.tool_forms import (
    FORM_SELECT,
    STEP_TIMINGS,
    FormError,
    StepState,
    active_forms,
    blocks_completion,
    is_due,
    normalize_schema,
    public_form,
    slot_answered,
    user_step_responses,
    validate_answers,
    validate_settings,
)
from database.client import get_supabase

logger = logging.getLogger(__name__)

teacher_router = APIRouter(prefix="/api/teacher", tags=["Tool Forms"])
student_router = APIRouter(prefix="/api/practice", tags=["Tool Forms"])

Timing = Literal["pre_practice", "post_practice", "after_feedback", "post_revision", "every_n_attempts", "teacher_release"]
Gate = Literal["none", "completion", "full_feedback"]


def _error(status_code: int, code: str, message: str, **extra: Any) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message, **extra})


def _form_error(e: FormError) -> HTTPException:
    return _error(status.HTTP_400_BAD_REQUEST, e.code, e.message, **e.extra)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class FormBody(BaseModel):
    title: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=1000)
    trigger_timing: Timing = "after_feedback"
    trigger_every_n: Optional[int] = Field(default=None, ge=1, le=100)
    is_required: bool = False
    gate: Gate = "none"
    # 題目（core/tool_forms.normalize_schema），存成 forms.schema_json = {"questions": [...]}
    questions: list[dict] = Field(min_length=1, max_length=30)


class ReleaseBody(BaseModel):
    released: bool = True


class ResponseBody(BaseModel):
    step_id: str
    attempt_id: Optional[str] = None
    skipped: bool = False
    responses: dict = Field(default_factory=dict)


# ── 共用 ────────────────────────────────────────────────────────────────────


def _get_tool(db, tool_id: str) -> dict:
    rows = db.table("ai_tools").select("id, title, created_by").eq("id", tool_id).limit(1).execute().data or []
    if not rows:
        raise _error(status.HTTP_404_NOT_FOUND, "TOOL_NOT_FOUND", "找不到該 AI 機器人")
    return rows[0]


def _get_form(db, form_id: str) -> dict:
    rows = db.table("forms").select(FORM_SELECT).eq("id", form_id).limit(1).execute().data or []
    if not rows:
        raise _error(status.HTTP_404_NOT_FOUND, "FORM_NOT_FOUND", "找不到這份表單")
    return rows[0]


def _require_editor(db, user: CurrentUser, tool_id: str) -> dict:
    tool = _get_tool(db, tool_id)
    if not can_edit_tool(user, tool):
        raise _error(status.HTTP_403_FORBIDDEN, "PERMISSION_DENIED", "只有建立這支機器人的老師或教授可以修改表單")
    return tool


def _settings(body: FormBody) -> dict:
    try:
        validate_settings(body.trigger_timing, body.trigger_every_n, body.gate)
        schema = normalize_schema({"questions": body.questions})
    except FormError as e:
        raise _form_error(e) from None
    return {
        "title": body.title.strip(),
        "description": body.description.strip(),
        "trigger_timing": body.trigger_timing,
        "trigger_every_n": body.trigger_every_n if body.trigger_timing == "every_n_attempts" else None,
        "is_required": body.is_required,
        "gate": body.gate,
        "schema_json": schema,
    }


def _response_count(db, form_key: str) -> int:
    return len(db.table("form_responses").select("id").eq("form_key", form_key).execute().data or [])


# ── 教師端 ──────────────────────────────────────────────────────────────────


@teacher_router.get("/tools/{tool_id}/forms")
async def list_tool_forms(tool_id: str, include_old: bool = False, user: CurrentUser = Depends(require_staff)):
    db = get_supabase()
    tool = _get_tool(db, tool_id)
    query = db.table("forms").select(FORM_SELECT).eq("tool_id", tool_id)
    if not include_old:
        query = query.eq("is_active", True)
    forms = query.execute().data or []
    forms.sort(key=lambda f: (f.get("created_at") or "", f.get("version") or 0))
    data = [{**f, "response_count": _response_count(db, f["form_key"])} for f in forms] if forms else []
    return {"data": data, "can_edit": can_edit_tool(user, tool)}


@teacher_router.post("/tools/{tool_id}/forms", status_code=status.HTTP_201_CREATED)
async def create_tool_form(tool_id: str, body: FormBody, user: CurrentUser = Depends(require_staff)):
    db = get_supabase()
    _require_editor(db, user, tool_id)
    row = {**_settings(body), "tool_id": tool_id, "version": 1, "is_active": True, "created_by": user.id}
    created = db.table("forms").insert(row).execute().data or []
    if not created:
        raise _error(status.HTTP_500_INTERNAL_SERVER_ERROR, "INTERNAL_ERROR", "表單建立失敗")
    form = created[0]
    if not form.get("form_key"):
        # 資料庫預設值會給 form_key；測試的記憶體資料庫沒有預設值，就用自己的 id
        form = (db.table("forms").update({"form_key": form["id"]}).eq("id", form["id"]).execute().data or [form])[0]
    record(db, user, "form.create", target_type="ai_tool", target_id=tool_id,
           detail={"form_id": form["id"], "title": form.get("title")})
    return {"data": form}


@teacher_router.put("/forms/{form_id}")
async def update_tool_form(form_id: str, body: FormBody, user: CurrentUser = Depends(require_staff)):
    """
    修改 = 新增一個版本（同一個 form_key、version + 1）；舊版停用但不刪，舊的填答仍指向舊版。
    只能改目前啟用中的那一版。教師發布的狀態（released_at）帶到新版本。
    """
    db = get_supabase()
    old = _get_form(db, form_id)
    if not old.get("is_active"):
        raise _error(status.HTTP_409_CONFLICT, "FORM_NOT_CURRENT", "這是舊版本，請修改最新的版本")
    _require_editor(db, user, old["tool_id"])

    versions = db.table("forms").select("version").eq("form_key", old["form_key"]).execute().data or []
    next_version = max((v.get("version") or 0 for v in versions), default=0) + 1
    row = {
        **_settings(body),
        "form_key": old["form_key"],
        "tool_id": old["tool_id"],
        "version": next_version,
        "is_active": True,
        "released_at": old.get("released_at"),
        "created_by": user.id,
    }
    db.table("forms").update({"is_active": False, "updated_at": _now()}).eq("id", form_id).execute()
    try:
        created = db.table("forms").insert(row).execute().data or []
    except Exception:
        # 新版寫入失敗：把舊版恢復啟用，不要讓表單消失
        db.table("forms").update({"is_active": True}).eq("id", form_id).execute()
        raise
    record(db, user, "form.update", target_type="ai_tool", target_id=old["tool_id"],
           detail={"form_key": old["form_key"], "version": next_version})
    return {"data": created[0] if created else row}


@teacher_router.delete("/forms/{form_id}")
async def delete_tool_form(form_id: str, user: CurrentUser = Depends(require_staff)):
    """停用這份表單（所有版本）；不刪資料，已有的填答仍可匯出"""
    db = get_supabase()
    form = _get_form(db, form_id)
    _require_editor(db, user, form["tool_id"])
    db.table("forms").update({"is_active": False, "updated_at": _now()}).eq("form_key", form["form_key"]).execute()
    record(db, user, "form.delete", target_type="ai_tool", target_id=form["tool_id"],
           detail={"form_key": form["form_key"]})
    return {"data": {"form_key": form["form_key"], "is_active": False}}


@teacher_router.post("/forms/{form_id}/release")
async def release_tool_form(form_id: str, body: ReleaseBody | None = None, user: CurrentUser = Depends(require_staff)):
    db = get_supabase()
    form = _get_form(db, form_id)
    _require_editor(db, user, form["tool_id"])
    if form.get("trigger_timing") != "teacher_release":
        raise _error(status.HTTP_400_BAD_REQUEST, "NOT_RELEASE_FORM", "只有出現時點是「教師發布」的表單需要發布")
    released = body.released if body else True
    value = _now() if released else None
    updated = db.table("forms").update({"released_at": value, "updated_at": _now()}).eq("id", form_id).execute().data or []
    record(db, user, "form.release" if released else "form.unrelease", target_type="ai_tool",
           target_id=form["tool_id"], detail={"form_key": form["form_key"]})
    return {"data": updated[0] if updated else {**form, "released_at": value}}


@teacher_router.get("/forms/{form_id}/responses")
async def list_form_responses(form_id: str, user: CurrentUser = Depends(require_staff)):
    """這份表單所有版本的填答（含當時的題目版本），附填答者姓名；研究分析請用匯出（會去識別化）"""
    db = get_supabase()
    form = _get_form(db, form_id)
    versions = {
        v["id"]: v for v in db.table("forms").select(FORM_SELECT).eq("form_key", form["form_key"]).execute().data or []
    }
    rows = db.table("form_responses").select("*").eq("form_key", form["form_key"]).execute().data or []
    user_ids = sorted({r.get("user_id") for r in rows if r.get("user_id")})
    people = {
        p["id"]: p.get("full_name") or ""
        for p in (db.table("profiles").select("id, full_name").in_("id", user_ids).execute().data or [] if user_ids else [])
    }
    rows.sort(key=lambda r: r.get("submitted_at") or "")
    return {
        "data": {
            "form_key": form["form_key"],
            "versions": sorted(versions.values(), key=lambda v: v.get("version") or 0),
            "responses": [{**r, "student_name": people.get(r.get("user_id"), "")} for r in rows],
        }
    }


# ── 學生端 ──────────────────────────────────────────────────────────────────


def _load_step(db, step_id: str) -> dict:
    rows = (
        db.table("module_steps").select("*, ai_tools(id, status, title)").eq("id", step_id).limit(1).execute()
    ).data or []
    if not rows or not rows[0].get("ai_tools"):
        raise _error(status.HTTP_404_NOT_FOUND, "STEP_NOT_FOUND", "找不到這個關卡")
    return rows[0]


def _step_attempts(db, user_id: str, step_id: str) -> list[dict]:
    rows = (
        db.table("step_attempts")
        .select("id, attempt_number, status, tutor_action, structured_data, "
                "ai_evaluations(id, total_score), teacher_reviews(final_score, is_published)")
        .eq("user_id", user_id).eq("step_id", step_id).execute()
    ).data or []
    return sorted(rows, key=lambda a: a.get("attempt_number") or 0)


def _first(value) -> dict:
    if isinstance(value, list):
        return value[0] if value and isinstance(value[0], dict) else {}
    return value if isinstance(value, dict) else {}


def _has_feedback(attempt: dict) -> bool:
    structured = attempt.get("structured_data") if isinstance(attempt.get("structured_data"), dict) else {}
    return bool(_first(attempt.get("ai_evaluations")) or structured.get("ai_feedback") or attempt.get("tutor_action")
                or attempt.get("status") not in (None, "submitted"))


def _state(step: dict, attempts: list[dict]) -> StepState:
    latest = attempts[-1] if attempts else None
    pass_score = step.get("pass_score") or DEFAULT_PASS_SCORE
    return StepState(
        latest_attempt_id=latest.get("id") if latest else None,
        latest_attempt_number=(latest.get("attempt_number") or 0) if latest else 0,
        latest_has_feedback=_has_feedback(latest) if latest else False,
        step_passed=any(attempt_passed(a, pass_score) for a in attempts),
    )


@student_router.get("/steps/{step_id}/forms")
async def get_step_forms(step_id: str, user: CurrentUser = Depends(get_current_user)):
    """
    這一關現在要填的表單。前端在進入關卡、每次拿到回饋後各查一次。

    回傳：
      pending          現在該出現的表單（含題目）；is_required = true 的不能略過
      completion_pending  有「填了才算完成」的表單還沒填答：這一關還不算通過
      feedback_locked  有「填了才顯示完整回饋」的表單正在等填答：前端先只顯示簡短回饋
    """
    db = get_supabase()
    step = _load_step(db, step_id)
    check_step_access(db, user, step)
    forms = active_forms(db, step["tool_id"])
    if not forms:
        return {"data": {"pending": [], "completion_pending": False, "feedback_locked": False}}

    attempts = _step_attempts(db, user.id, step_id)
    state = _state(step, attempts)
    responses = user_step_responses(db, user.id, step_id)

    pending = []
    for f in forms:
        mine = responses.get(f["form_key"], [])
        if is_due(f, state, slot_answered(f, mine, state)):
            pending.append({
                **public_form(f),
                "attempt_id": None if f["trigger_timing"] in STEP_TIMINGS else state.latest_attempt_id,
            })
    return {
        "data": {
            "pending": pending,
            "completion_pending": any(blocks_completion(f, responses.get(f["form_key"], [])) for f in forms),
            "feedback_locked": any(f.get("gate") == "full_feedback" for f in pending),
        }
    }


@student_router.post("/forms/{form_id}/responses", status_code=status.HTTP_201_CREATED)
async def submit_form_response(form_id: str, body: ResponseBody, user: CurrentUser = Depends(get_current_user)):
    """
    填答或略過。填答連到：表單版本、使用者、機器人、關卡、模組、課程、作答與當時的 AI 結果、時間。
    簡答與長文字會先過個資偵測（400 PII_DETECTED）。
    """
    db = get_supabase()
    form = _get_form(db, form_id)
    if not form.get("is_active"):
        raise _error(status.HTTP_409_CONFLICT, "FORM_NOT_CURRENT", "這份表單已經更新，請重新整理後再填")
    step = _load_step(db, body.step_id)
    if step.get("tool_id") != form.get("tool_id"):
        raise _error(status.HTTP_400_BAD_REQUEST, "FORM_NOT_FOR_STEP", "這份表單不屬於這個關卡的機器人")
    access = check_step_access(db, user, step)

    if body.skipped and form.get("is_required"):
        raise _error(status.HTTP_400_BAD_REQUEST, "FORM_REQUIRED", "這份表單是必填的，不能略過")

    attempt = None
    if body.attempt_id:
        attempts = [a for a in _step_attempts(db, user.id, body.step_id) if a.get("id") == body.attempt_id]
        if not attempts:
            raise _error(status.HTTP_404_NOT_FOUND, "ATTEMPT_NOT_FOUND", "找不到這次作答")
        attempt = attempts[0]
    elif form.get("trigger_timing") not in STEP_TIMINGS:
        raise _error(status.HTTP_400_BAD_REQUEST, "ATTEMPT_REQUIRED", "這份表單要跟著某一次作答填寫")

    if body.skipped:
        saved = {"answers": {}, "na": []}
    else:
        try:
            saved = validate_answers(form.get("schema_json") or {}, body.responses)
        except FormError as e:
            raise _form_error(e) from None

    # 同一個時點不重複記：一關一次的看關卡，跟著作答的看那一次作答
    existing = user_step_responses(db, user.id, body.step_id).get(form["form_key"], [])
    if form["trigger_timing"] in STEP_TIMINGS:
        duplicate = bool(existing)
    else:
        duplicate = any(r.get("attempt_id") == body.attempt_id for r in existing)
    if duplicate:
        raise _error(status.HTTP_409_CONFLICT, "ALREADY_ANSWERED", "這份表單這次已經填過了")

    evaluation = _first(attempt.get("ai_evaluations")) if attempt else {}
    row = {
        "form_id": form["id"],
        "form_key": form["form_key"],
        "form_version": form.get("version"),
        "user_id": user.id,
        "tool_id": form["tool_id"],
        "step_id": body.step_id,
        "module_id": step.get("module_id"),
        "course_id": course_for_access(db, user.id, step.get("module_id"), access),
        "attempt_id": body.attempt_id,
        "ai_evaluation_id": evaluation.get("id"),
        "ai_total_score": evaluation.get("total_score"),
        "timing": form.get("trigger_timing"),
        "skipped": body.skipped,
        "responses": saved,
    }
    created = db.table("form_responses").insert(row).execute().data or []
    return {"data": created[0] if created else row}
