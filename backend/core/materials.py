"""
學習助教的資料（RAG，#67，migration 021）

老師確認：助教要接「老師的教材講義」＋「學生自己的作答紀錄」。

  教材講義：老師放進課程 → 個資檢查 → 切段 → 向量（core/ai/embeddings.py）→ material_chunks
            助教回答前用學生的問題搜尋這門課的教材（match_material_chunks，不跨課程）
  作答紀錄：不進向量庫；後端依登入身分直接查自己的作答與回饋，看不到別人的

個資：教材取出的文字先過 core/privacy.detect_pii，命中就擋下請老師修改（規則只能加嚴，不在這裡放寬）。
"""

import logging
import re
from dataclasses import dataclass

from core.ai.embeddings import cosine, embed_query, embed_texts
from core.privacy import detect_pii

logger = logging.getLogger(__name__)

CHUNK_CHARS = 600        # 每段約 600 字：中文教材一段落左右，搜尋結果不會太長
CHUNK_OVERLAP = 100      # 前後段重疊，避免一句話被切在兩段而搜不到
MAX_MATERIAL_CHARS = 200_000
MAX_CHUNKS = 400
TOP_K = 4
MIN_SIMILARITY = 0.55    # 太不相關的段落不給助教，避免硬引用
OWN_ATTEMPTS = 5


class MaterialError(ValueError):
    def __init__(self, code: str, message: str, detail: dict | None = None):
        super().__init__(message)
        self.code, self.message, self.detail = code, message, detail or {}


@dataclass
class RetrievedChunk:
    title: str
    chunk_index: int
    content: str
    similarity: float

    @property
    def label(self) -> str:
        return f"{self.title}・第 {self.chunk_index + 1} 段"


def chunk_text(text: str, size: int = CHUNK_CHARS, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """
    依段落切：段落累積到 size 字就收成一段；單一段落太長再硬切（保留 overlap 字重疊）。
    """
    text = re.sub(r"\r\n?", "\n", text or "").strip()
    if not text:
        return []
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]

    pieces: list[str] = []
    for p in paragraphs:
        if len(p) <= size:
            pieces.append(p)
            continue
        step = max(size - overlap, 1)
        pieces.extend(p[i:i + size] for i in range(0, len(p), step) if p[i:i + size].strip())

    chunks, current = [], ""
    for piece in pieces:
        if current and len(current) + 1 + len(piece) > size:
            chunks.append(current)
            current = piece
        else:
            current = f"{current}\n{piece}" if current else piece
    if current:
        chunks.append(current)
    return chunks


def check_material_text(text: str) -> None:
    """教材文字的檢查：空白、太長、含個資都擋下（不存任何東西）"""
    if not (text or "").strip():
        raise MaterialError("EMPTY_MATERIAL", "教材沒有文字內容；掃描的 PDF 請改上傳文字檔")
    if len(text) > MAX_MATERIAL_CHARS:
        raise MaterialError("MATERIAL_TOO_LONG", f"教材太長（上限 {MAX_MATERIAL_CHARS:,} 字），請分成幾份上傳")
    pii = detect_pii(text)
    if pii.has_risk:
        raise MaterialError("PII_DETECTED", pii.warning_message, {"detected_types": pii.detected_types})


async def add_material(db, *, course_id: str, title: str, text: str, created_by: str, source_type: str = "text") -> dict:
    """檢查 → 切段 → 向量 → 寫入。向量算失敗就整份不存（不留下半份教材）。"""
    check_material_text(text)
    chunks = chunk_text(text)
    if len(chunks) > MAX_CHUNKS:
        raise MaterialError("MATERIAL_TOO_LONG", "教材切出來的段落太多，請分成幾份上傳")
    vectors = await embed_texts(chunks)

    material = db.table("course_materials").insert({
        "course_id": course_id, "title": title, "source_type": source_type,
        "char_count": len(text), "chunk_count": len(chunks), "created_by": created_by,
    }).execute().data[0]
    try:
        db.table("material_chunks").insert([
            {"material_id": material["id"], "course_id": course_id, "chunk_index": i,
             "content": chunk, "embedding": vector}
            for i, (chunk, vector) in enumerate(zip(chunks, vectors))
        ]).execute()
    except Exception:
        db.table("course_materials").delete().eq("id", material["id"]).execute()
        raise
    return material


def courses_for_module(db, user_id: str, module_id: str, *, is_staff: bool = False) -> list[str]:
    """
    這次對話可以搜哪些課程的教材：這個模組被指派到的課程 ∩ 學生是 active 成員的課程。
    教師端試用時：模組被指派到的所有課程。
    """
    assigned = {r.get("course_id") for r in (
        db.table("course_modules").select("course_id").eq("module_id", module_id).execute().data or []
    )}
    if is_staff:
        return sorted(c for c in assigned if c)
    mine = {r.get("course_id") for r in (
        db.table("course_members").select("course_id").eq("user_id", user_id).eq("status", "active").execute().data or []
    )}
    return sorted(c for c in assigned & mine if c)


async def search_materials(db, course_ids: list[str], question: str, top_k: int = TOP_K) -> list[RetrievedChunk]:
    """用學生的問題搜尋教材；沒有課程或沒有教材就回空清單"""
    if not course_ids or not (question or "").strip():
        return []
    vector = await embed_query(question)
    res = db.rpc("match_material_chunks", {
        "p_course_ids": course_ids, "p_query": vector, "p_match_count": top_k,
    }).execute()
    return [
        RetrievedChunk(title=r.get("title") or "教材", chunk_index=int(r.get("chunk_index") or 0),
                       content=r.get("content") or "", similarity=float(r.get("similarity") or 0))
        for r in (res.data or [])
        if float(r.get("similarity") or 0) >= MIN_SIMILARITY
    ]


def own_attempt_lines(db, user_id: str, module_id: str, limit: int = OWN_ATTEMPTS) -> list[str]:
    """學生自己在這個模組最近的作答與回饋（只查 user_id = 自己；教師已發布的判定優先）"""
    res = (
        db.table("step_attempts")
        .select("attempt_number, status, created_at, module_steps(step_title), "
                "ai_evaluations(total_score, feedback_text), teacher_reviews(final_score, final_feedback, is_published)")
        .eq("user_id", user_id)
        .eq("module_id", module_id)
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    lines = []
    for row in res.data or []:
        step = (row.get("module_steps") or {}).get("step_title") or "某一關"
        evaluation = _first(row.get("ai_evaluations")) or {}
        review = next((r for r in _as_list(row.get("teacher_reviews")) if r.get("is_published")), None)
        parts = [f"「{step}」第 {row.get('attempt_number')} 次"]
        if review and review.get("final_score") is not None:
            parts.append(f"老師給 {review['final_score']} 分")
        elif evaluation.get("total_score") is not None:
            parts.append(f"AI 初評 {evaluation['total_score']} 分")
        feedback = (review or {}).get("final_feedback") or evaluation.get("feedback_text") or ""
        if feedback:
            parts.append(f"回饋：{feedback.strip()[:120]}")
        lines.append("，".join(parts))
    return lines


# ──────────────────────────────────────────
# 不直接給答案，同一個問題問到第 N 次才給（老師 11/24 確認）
# ──────────────────────────────────────────

SAME_QUESTION_SIMILARITY = 0.85  # 兩則提問的向量相似度達到這個值，就當成「同一個問題」
RECENT_QUESTIONS = 10            # 只比對最近幾次問助教的問題


def prior_questions_to(history_rows: list[dict], tool_id: str) -> list[str]:
    """之前問過這個助教的問題：學生訊息的下一則是這個助教回答的（舊 → 新，最多 RECENT_QUESTIONS 則）"""
    questions = []
    for current, following in zip(history_rows, history_rows[1:]):
        if (current.get("role") == "student" and following.get("role") == "bot"
                and str(following.get("tool_id")) == str(tool_id) and (current.get("content") or "").strip()):
            questions.append(current["content"].strip())
    return questions[-RECENT_QUESTIONS:]


async def count_same_question(question: str, previous: list[str]) -> int:
    """之前問過幾次「同一個問題」（向量相似度）。算不出來就回 0：寧可繼續引導，也不要提早給答案。"""
    if not previous or not (question or "").strip():
        return 0
    try:
        vectors = await embed_texts([question] + previous, task_type="SEMANTIC_SIMILARITY")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"比對重複提問失敗，當作第一次問：{e}")
        return 0
    target = vectors[0]
    return sum(1 for v in vectors[1:] if cosine(target, v) >= SAME_QUESTION_SIMILARITY)


def answer_policy(times_asked: int, answer_after: int) -> tuple[str, str]:
    """
    回傳 (模式, 放進 prompt 的規則)。times_asked = 包含這次在內，同一個問題問了幾次。
      guide  ：不直接給答案，用提問、提示、指出教材段落引導
      answer ：已經問了 answer_after 次以上，可以直接給答案並說明理由
    """
    if times_asked >= max(answer_after, 1):
        return "answer", (
            f"【回答方式】學生已經第 {times_asked} 次問同一個問題，引導後仍卡住。"
            "這次可以直接給出答案（或完整的示範寫法），並說明為什麼這樣寫、對應教材哪一段，"
            "最後請學生用自己的話再寫一次。"
        )
    return "guide", (
        "【回答方式】不要直接給答案、不要替學生寫出可以直接交的內容。"
        "用反問、提示重點、指出教材哪一段可以參考的方式，引導學生自己想出來。"
        "不要告訴學生問幾次之後會給答案。"
    )


def context_block(chunks: list[RetrievedChunk], attempt_lines: list[str]) -> str:
    """放進助教 system prompt 的參考資料；回答要標出 [教材名・第 N 段]"""
    parts = []
    if chunks:
        parts.append("【課程教材（回答時引用，標出來源，例如 [教材名・第 2 段]）】")
        parts.extend(f"[{c.label}]\n{c.content}" for c in chunks)
    else:
        parts.append("【課程教材】這個問題在教材裡沒有找到相關段落。如果需要教材內容才能回答，請直接說「教材裡沒有找到」，不要自己編。")
    if attempt_lines:
        parts.append("【這位學生自己的作答紀錄（只有他本人的）】")
        parts.extend(f"- {line}" for line in attempt_lines)
    return "\n\n".join(parts)


def _as_list(value) -> list[dict]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _first(value):
    items = _as_list(value)
    return items[0] if items else None
