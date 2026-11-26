"""
跨步驟結構化資料傳遞（#84，需求說明書 BLD-006）

需求書：「前一步的學生作答、AI 分析、教師確認與結構化欄位可依主持人設定傳給後一步；
須顯示資料來源與版本，不可只靠複製整段對話。」

設定：老師在關卡設定 module_steps.pass_forward_keys，要傳給後面關卡的欄位：
      [{"label": "優勢", "description": "孩子能做到、喜歡的事"}, …]（舊格式的字串清單也接受）
抽出：這一關作答「通過」時，AI 依欄位從作答（聊天室是整段對話）抽出內容，
      存在 step_attempts.structured_data.forward.ai_fields
修正：老師在複核頁修改 → forward.teacher_fields，**教師版本優先**，AI 之後也不會覆蓋
使用：後面的關卡讀「前面每一關通過（或最新）那次作答」的欄位，放進機器人記憶與學生畫面，
      並標示來源：第幾關、第幾次作答、AI 抽出或教師確認

抽出失敗只寫 log，不影響評分與通過。
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from langchain_core.messages import HumanMessage, SystemMessage

from core.ai.llm_keys import chat_llm
from core.ai.llm_utils import invoke_json

logger = logging.getLogger(__name__)

MAX_FIELDS = 10
MAX_VALUE_CHARS = 600
ANSWER_CHARS = 8000


@dataclass
class ForwardField:
    key: str
    label: str
    description: str = ""


@dataclass
class ForwardedStep:
    step_id: str
    step_order: int
    step_title: str
    attempt_number: int
    source: str                      # ai / teacher / mixed
    fields: list[dict] = field(default_factory=list)   # [{"key", "label", "value", "source"}]

    def to_dict(self) -> dict:
        return {
            "step_id": self.step_id, "step_order": self.step_order, "step_title": self.step_title,
            "attempt_number": self.attempt_number, "source": self.source, "fields": self.fields,
        }

    def to_prompt_text(self) -> str:
        source = {"teacher": "教師確認", "ai": "AI 抽出", "mixed": "部分教師確認"}.get(self.source, self.source)
        parts = [f"{f['label']}：{f['value']}" for f in self.fields if f.get("value")]
        return f"（第 {self.attempt_number} 次作答，{source}）" + "；".join(parts)


def parse_fields(raw) -> list[ForwardField]:
    """module_steps.pass_forward_keys → 欄位清單；空白、重複的名稱略過"""
    items = raw if isinstance(raw, list) else []
    fields, seen = [], set()
    for item in items:
        if isinstance(item, str):
            label, description = item.strip(), ""
        elif isinstance(item, dict):
            label = str(item.get("label") or item.get("key") or "").strip()
            description = str(item.get("description") or "").strip()
        else:
            continue
        if not label or label in seen:
            continue
        seen.add(label)
        fields.append(ForwardField(key=str(item.get("key") or label).strip() if isinstance(item, dict) else label,
                                   label=label, description=description))
    return fields[:MAX_FIELDS]


def merged_fields(structured) -> tuple[dict, str]:
    """structured_data → (欄位值, 來源)；教師版本優先"""
    forward = (structured or {}).get("forward") if isinstance(structured, dict) else None
    if not isinstance(forward, dict):
        return {}, ""
    ai = forward.get("ai_fields") if isinstance(forward.get("ai_fields"), dict) else {}
    teacher = forward.get("teacher_fields") if isinstance(forward.get("teacher_fields"), dict) else {}
    values = {**ai, **{k: v for k, v in teacher.items() if v not in (None, "")}}
    if not teacher:
        source = "ai"
    elif set(teacher) >= set(k for k, v in ai.items() if v):
        source = "teacher"
    else:
        source = "mixed"
    return {k: v for k, v in values.items() if v not in (None, "")}, (source if values else "")


# ──────────────────────────────────────────
# AI 抽出
# ──────────────────────────────────────────

class ForwardExtractor:
    async def extract(self, answer: str, fields: list[ForwardField], system_prompt: str = "") -> dict:
        spec = "\n".join(f"- {f.key}：{f.label}" + (f"（{f.description}）" if f.description else "") for f in fields)
        prompt = f"""從學生這一關的作答中，整理出下列欄位，要傳給後面的關卡使用。

【欄位】
{spec}

規則：
- 只根據學生作答裡寫到的內容整理，不要補上作答裡沒有的資訊；找不到就填 null
- 每個欄位 150 字以內，用學生原本的專業用語
- 嚴格以 JSON 回傳，key 用上面的欄位名稱，例如 {{"{fields[0].key}": "..."}}

【學生作答】
{answer[:ANSWER_CHARS]}"""
        messages = [SystemMessage(content=(system_prompt or "你是一位學前特殊教育的助教。").strip()),
                    HumanMessage(content=prompt)]
        data = await invoke_json(chat_llm(0.0), messages, label="extract_forward")
        out = {}
        for f in fields:
            value = data.get(f.key) if isinstance(data, dict) else None
            if value is None and isinstance(data, dict):
                value = data.get(f.label)
            out[f.key] = str(value).strip()[:MAX_VALUE_CHARS] if value not in (None, "", "null") else None
        return out


extractor = ForwardExtractor()


async def extract_and_save(db, attempt_id: str, step: dict, answer: str, structured: Optional[dict] = None) -> Optional[dict]:
    """
    作答通過時呼叫。這一關沒設定欄位、或老師已經修正過，就不抽。
    回傳這次存下的欄位（給 SSE 事件用）；沒抽或失敗回 None。
    """
    fields = parse_fields(step.get("pass_forward_keys"))
    if not fields or not (answer or "").strip():
        return None
    structured = dict(structured or {})
    forward = dict(structured.get("forward") or {})
    if forward.get("teacher_fields"):
        return None
    try:
        tool = step.get("ai_tools") or {}
        values = await extractor.extract(answer, fields, tool.get("system_prompt") or "")
    except Exception as e:  # noqa: BLE001 - 抽不出來不影響通過
        logger.warning(f"跨步驟欄位抽出失敗（attempt {attempt_id}）：{e}")
        return None
    forward.update({
        "ai_fields": values,
        "labels": {f.key: f.label for f in fields},
        "extracted_at": datetime.now(timezone.utc).isoformat(),
    })
    structured["forward"] = forward
    try:
        db.table("step_attempts").update({"structured_data": structured}).eq("id", attempt_id).execute()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"跨步驟欄位寫入失敗（attempt {attempt_id}）：{e}")
        return None
    return values


def set_teacher_fields(structured: Optional[dict], values: dict, teacher_id: str, labels: dict) -> dict:
    """教師修正：寫 teacher_fields（AI 原始的 ai_fields 保留不動）"""
    structured = dict(structured or {})
    forward = dict(structured.get("forward") or {})
    forward["teacher_fields"] = {k: (str(v).strip()[:MAX_VALUE_CHARS] if v not in (None, "") else None) for k, v in values.items()}
    forward["teacher_id"] = teacher_id
    forward["edited_at"] = datetime.now(timezone.utc).isoformat()
    forward.setdefault("labels", labels)
    structured["forward"] = forward
    return structured


# ──────────────────────────────────────────
# 後面的關卡讀前面的欄位
# ──────────────────────────────────────────

FORWARD_SELECT = (
    "id, step_id, attempt_number, status, structured_data, "
    "module_steps(step_order, step_title, pass_score, pass_forward_keys), "
    "teacher_reviews(final_score, is_published)"
)


def forwarded_from_rows(rows: list[dict], before_order: int, passed) -> list[ForwardedStep]:
    """
    每個前面的關卡取一筆：有通過的版本優先、同樣通過取最新；只列有欄位資料的關卡。
    passed(row) → bool：是否通過（呼叫端用 step_unlock 的規則，教師分數優先）
    """
    best: dict[str, dict] = {}
    for row in rows:
        step = row.get("module_steps") or {}
        if isinstance(step, list):
            step = step[0] if step else {}
        order = step.get("step_order")
        if not isinstance(order, int) or order >= before_order:
            continue
        values, _ = merged_fields(row.get("structured_data"))
        if not values:
            continue
        current = best.get(row.get("step_id"))
        key = (passed(row), row.get("attempt_number") or 0)
        if current is None or key > (passed(current), current.get("attempt_number") or 0):
            best[row.get("step_id")] = row

    out = []
    for row in best.values():
        step = row.get("module_steps") or {}
        if isinstance(step, list):
            step = step[0] if step else {}
        values, source = merged_fields(row.get("structured_data"))
        forward = (row.get("structured_data") or {}).get("forward") or {}
        teacher = forward.get("teacher_fields") or {}
        labels = forward.get("labels") or {}
        configured = {f.key: f.label for f in parse_fields(step.get("pass_forward_keys"))}
        fields = [
            {"key": k, "label": configured.get(k) or labels.get(k) or k, "value": v,
             "source": "teacher" if teacher.get(k) not in (None, "") else "ai"}
            for k, v in values.items()
        ]
        out.append(ForwardedStep(
            step_id=row.get("step_id"), step_order=step.get("step_order"), step_title=step.get("step_title") or "",
            attempt_number=row.get("attempt_number") or 0, source=source, fields=fields,
        ))
    return sorted(out, key=lambda s: s.step_order)


def load_forwarded(db, user_id: str, module_id: Optional[str], before_order: int) -> list[ForwardedStep]:
    if not module_id:
        return []
    from core.step_unlock import DEFAULT_PASS_SCORE, attempt_passed

    res = (
        db.table("step_attempts").select(FORWARD_SELECT)
        .eq("user_id", user_id).eq("module_id", module_id).execute()
    )
    rows = [r for r in (res.data or []) if isinstance(r, dict)]

    def passed(row):
        step = row.get("module_steps") or {}
        if isinstance(step, list):
            step = step[0] if step else {}
        return attempt_passed(row, step.get("pass_score") or DEFAULT_PASS_SCORE)

    return forwarded_from_rows(rows, before_order, passed)
