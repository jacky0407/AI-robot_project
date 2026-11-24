"""
文字 → 向量（學習助教 RAG，#67）

用 Gemini embedding（GEMINI_EMBEDDING_MODEL，預設 gemini-embedding-001），
output_dimensionality 固定 768，與 migration 021 的 vector(768) 一致。
教材切段用 RETRIEVAL_DOCUMENT、學生的問題用 RETRIEVAL_QUERY。

金鑰與聊天一樣：自備金鑰的學生用自己的（core/ai/llm_keys.py），否則用平台金鑰。
"""

import logging

from core.ai.llm_keys import override_api_key
from core.config import get_settings

logger = logging.getLogger(__name__)

EMBEDDING_DIM = 768
BATCH_SIZE = 50


def _client():
    from google import genai

    key = override_api_key() or get_settings().gemini_api_key
    if not key:
        raise ValueError("GEMINI_API_KEY 未設定")
    return genai.Client(api_key=key)


async def embed_texts(texts: list[str], *, task_type: str = "RETRIEVAL_DOCUMENT") -> list[list[float]]:
    """依序回傳每段文字的向量（每批最多 BATCH_SIZE 段）"""
    from google.genai import types

    if not texts:
        return []
    client = _client()
    model = get_settings().gemini_embedding_model
    vectors: list[list[float]] = []
    for start in range(0, len(texts), BATCH_SIZE):
        batch = texts[start:start + BATCH_SIZE]
        res = await client.aio.models.embed_content(
            model=model,
            contents=batch,
            config=types.EmbedContentConfig(task_type=task_type, output_dimensionality=EMBEDDING_DIM),
        )
        got = [list(e.values) for e in (res.embeddings or [])]
        if len(got) != len(batch):
            raise RuntimeError(f"embedding 數量不符：送 {len(batch)} 段，回 {len(got)} 個")
        vectors.extend(got)
    return vectors


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


async def embed_query(text: str) -> list[float]:
    return (await embed_texts([text], task_type="RETRIEVAL_QUERY"))[0]
