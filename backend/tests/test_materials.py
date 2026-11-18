"""
#67 學習助教（RAG）：core/materials.py、api/materials.py、聊天室裡的學習助教

全部 Mock：向量（embed_texts / embed_query）與 LLM 都換成假的，資料庫用記憶體版。
"""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from postgrest.exceptions import APIError

import main
from api import chat, materials as materials_api
from core import materials
from core.auth import CurrentUser, get_current_user
from tests.inmemory_db import InMemoryDB
from tests.test_chat_api import BOT_A, MOD, SESSION, STEP, make_db as make_chat_db, parse_sse  # noqa: F401
from tests.test_chat_api import student  # noqa: F401 — autouse fixture

COURSE = "c0000000-0000-4000-8000-000000000001"
OTHER_COURSE = "c0000000-0000-4000-8000-000000000002"
PROFESSOR = CurrentUser(id="prof-1", role="owner")

LESSON = "功能性現況要描述孩子在日常作息中的具體表現。\n\n長期目標要可觀察、可測量，並寫出達成標準。"


def fake_vectors(texts, **kwargs):
    return [[0.1] * 768 for _ in texts]


# ──────────────────────────────────────────
# 切段與檢查
# ──────────────────────────────────────────

class TestChunking:

    def test_short_paragraphs_are_merged(self):
        assert materials.chunk_text("第一段\n\n第二段", size=600) == ["第一段\n第二段"]

    def test_long_text_is_split_with_overlap(self):
        chunks = materials.chunk_text("字" * 1300, size=600, overlap=100)
        assert all(len(c) <= 600 for c in chunks)
        assert len(chunks) == 3

    def test_empty_text(self):
        assert materials.chunk_text("  \n ") == []

    def test_pii_in_material_is_blocked(self):
        with pytest.raises(materials.MaterialError) as e:
            materials.check_material_text("個案電話 0912-345-678")
        assert e.value.code == "PII_DETECTED"

    def test_too_long_material_is_blocked(self):
        with pytest.raises(materials.MaterialError) as e:
            materials.check_material_text("字" * (materials.MAX_MATERIAL_CHARS + 1))
        assert e.value.code == "MATERIAL_TOO_LONG"


@pytest.mark.asyncio
class TestAddMaterial:

    async def test_chunks_and_vectors_are_stored(self):
        db = InMemoryDB(course_materials=[], material_chunks=[])
        with patch.object(materials, "embed_texts", AsyncMock(side_effect=fake_vectors)):
            m = await materials.add_material(db, course_id=COURSE, title="IEP 指引", text=LESSON, created_by="prof-1")

        assert m["chunk_count"] == 1
        chunk = db.rows("material_chunks")[0]
        assert chunk["course_id"] == COURSE and len(chunk["embedding"]) == 768

    async def test_embedding_failure_stores_nothing(self):
        db = InMemoryDB(course_materials=[], material_chunks=[])
        with patch.object(materials, "embed_texts", AsyncMock(side_effect=RuntimeError("quota"))):
            with pytest.raises(RuntimeError):
                await materials.add_material(db, course_id=COURSE, title="x", text=LESSON, created_by="p")
        assert db.rows("course_materials") == []

    async def test_chunk_insert_failure_rolls_back_material(self):
        db = InMemoryDB(course_materials=[], material_chunks=[])
        db.errors[("material_chunks", "insert")] = RuntimeError("db down")
        with patch.object(materials, "embed_texts", AsyncMock(side_effect=fake_vectors)):
            with pytest.raises(RuntimeError):
                await materials.add_material(db, course_id=COURSE, title="x", text=LESSON, created_by="p")
        assert db.rows("course_materials") == []


# ──────────────────────────────────────────
# 搜尋範圍：不跨課程、只看自己的作答
# ──────────────────────────────────────────

class TestScope:

    def db(self):
        return InMemoryDB(
            course_modules=[{"course_id": COURSE, "module_id": MOD}, {"course_id": OTHER_COURSE, "module_id": MOD}],
            course_members=[{"course_id": COURSE, "user_id": "student-1", "status": "active"},
                            {"course_id": OTHER_COURSE, "user_id": "student-1", "status": "pending"}],
        )

    def test_student_only_searches_courses_they_are_active_in(self):
        assert materials.courses_for_module(self.db(), "student-1", MOD) == [COURSE]

    def test_staff_searches_all_courses_with_the_module(self):
        assert materials.courses_for_module(self.db(), "prof-1", MOD, is_staff=True) == sorted([COURSE, OTHER_COURSE])

    def test_own_attempts_never_include_other_students(self):
        db = InMemoryDB(step_attempts=[
            {"user_id": "student-1", "module_id": MOD, "attempt_number": 1, "created_at": "2026-11-01",
             "module_steps": {"step_title": "現況"}, "ai_evaluations": [{"total_score": 60, "feedback_text": "補具體行為"}],
             "teacher_reviews": [{"final_score": 75, "final_feedback": "老師說可以", "is_published": True}]},
            {"user_id": "student-2", "module_id": MOD, "attempt_number": 1, "created_at": "2026-11-02",
             "module_steps": {"step_title": "別人的"}, "ai_evaluations": [], "teacher_reviews": []},
        ])
        lines = materials.own_attempt_lines(db, "student-1", MOD)
        assert len(lines) == 1
        assert "老師給 75 分" in lines[0] and "老師說可以" in lines[0]   # 教師判定優先
        assert "別人的" not in lines[0]

    @pytest.mark.asyncio
    async def test_search_drops_unrelated_chunks(self):
        db = InMemoryDB()
        db.rpc_handlers["match_material_chunks"] = lambda db, **p: [
            {"title": "IEP 指引", "chunk_index": 1, "content": "長期目標要可測量", "similarity": 0.82},
            {"title": "IEP 指引", "chunk_index": 5, "content": "無關", "similarity": 0.2},
        ]
        with patch.object(materials, "embed_query", AsyncMock(return_value=[0.1] * 768)):
            found = await materials.search_materials(db, [COURSE], "長期目標怎麼寫")
        assert [c.label for c in found] == ["IEP 指引・第 2 段"]
        assert db.rpc_calls[0][1]["p_course_ids"] == [COURSE]

    @pytest.mark.asyncio
    async def test_no_course_means_no_search(self):
        embed = AsyncMock()
        with patch.object(materials, "embed_query", embed):
            assert await materials.search_materials(InMemoryDB(), [], "問題") == []
        embed.assert_not_awaited()

    def test_context_without_materials_tells_bot_not_to_make_things_up(self):
        assert "教材裡沒有找到" in materials.context_block([], [])


# ──────────────────────────────────────────
# 教師端 API
# ──────────────────────────────────────────

class TestMaterialsApi:

    @pytest.fixture(autouse=True)
    def professor(self, student):
        main.app.dependency_overrides[get_current_user] = lambda: PROFESSOR
        yield

    def call(self, method, path, db, json=None):
        client = TestClient(main.app, raise_server_exceptions=False)
        with patch.object(materials_api, "get_supabase", return_value=db), \
             patch.object(materials, "embed_texts", AsyncMock(side_effect=fake_vectors)):
            return client.request(method, f"/api/courses/{COURSE}{path}", json=json)

    def db(self):
        return InMemoryDB(courses=[{"id": COURSE}], course_materials=[], material_chunks=[])

    def test_create_list_and_delete(self):
        db = self.db()
        res = self.call("POST", "/materials", db, json={"title": "IEP 指引", "content": LESSON})
        assert res.status_code == 201
        material_id = res.json()["data"]["id"]

        assert [m["title"] for m in self.call("GET", "/materials", db).json()["data"]] == ["IEP 指引"]
        assert self.call("GET", f"/materials/{material_id}/chunks", db).json()["data"][0]["chunk_index"] == 0
        assert self.call("DELETE", f"/materials/{material_id}", db).status_code == 200
        assert db.rows("course_materials") == []

    def test_pii_is_rejected(self):
        res = self.call("POST", "/materials", self.db(), json={"title": "個案", "content": "身分證 A123456789"})
        assert res.status_code == 400
        assert res.json()["detail"]["code"] == "PII_DETECTED"

    def test_students_cannot_manage_materials(self):
        main.app.dependency_overrides[get_current_user] = lambda: CurrentUser(id="student-1", role="student")
        res = self.call("GET", "/materials", self.db())
        assert res.status_code == 403

    def test_before_migration_021_returns_503(self):
        db = self.db()
        db.errors[("course_materials", "select")] = APIError(
            {"code": "42P01", "message": "relation course_materials does not exist", "details": "", "hint": None})
        res = self.call("GET", "/materials", db)
        assert res.status_code == 503


# ──────────────────────────────────────────
# 聊天室裡的學習助教
# ──────────────────────────────────────────

class TestTutorInChatRoom:

    def test_tutor_bot_answers_with_materials_and_cites_sources(self):
        db = make_chat_db(bots=("a",))
        db.tables["chat_room_bots"][0]["use_materials"] = True
        db.tables["course_modules"] = [{"course_id": COURSE, "module_id": MOD}]
        db.tables["course_members"] = [{"course_id": COURSE, "user_id": "student-1", "status": "active"}]
        db.rpc_handlers["match_material_chunks"] = lambda db, **p: [
            {"title": "IEP 指引", "chunk_index": 0, "content": "長期目標要可測量", "similarity": 0.9}]

        reply = AsyncMock(return_value="依教材 [IEP 指引・第 1 段]，長期目標要可測量。")
        client = TestClient(main.app)
        with patch.object(chat, "get_supabase", return_value=db), \
             patch.object(chat.chat_bot_agent, "reply", reply), \
             patch.object(materials, "embed_query", AsyncMock(return_value=[0.1] * 768)):
            res = client.post(f"/api/chat/sessions/{SESSION}/steps/{STEP}/messages", json={"content": "長期目標怎麼寫？"})
        events = parse_sse(res.text)

        context = reply.await_args.args[4]
        assert "長期目標要可測量" in context
        assert dict(events)["materials_used"]["sources"] == ["IEP 指引・第 1 段"]
        assert dict(events)["bot_reply"]["message"]["sources"] == ["IEP 指引・第 1 段"]

    def test_normal_bot_does_not_search(self):
        db = make_chat_db(bots=("a",))
        embed = AsyncMock()
        client = TestClient(main.app)
        with patch.object(chat, "get_supabase", return_value=db), \
             patch.object(chat.chat_bot_agent, "reply", AsyncMock(return_value="好")), \
             patch.object(materials, "embed_query", embed):
            client.post(f"/api/chat/sessions/{SESSION}/steps/{STEP}/messages", json={"content": "你好"})
        embed.assert_not_awaited()

    def test_search_failure_does_not_block_reply(self):
        db = make_chat_db(bots=("a",))
        db.tables["chat_room_bots"][0]["use_materials"] = True
        db.tables["course_modules"] = [{"course_id": COURSE, "module_id": MOD}]
        db.tables["course_members"] = [{"course_id": COURSE, "user_id": "student-1", "status": "active"}]
        client = TestClient(main.app)
        with patch.object(chat, "get_supabase", return_value=db), \
             patch.object(chat.chat_bot_agent, "reply", AsyncMock(return_value="教材裡沒有找到")), \
             patch.object(materials, "embed_query", AsyncMock(side_effect=RuntimeError("down"))):
            res = client.post(f"/api/chat/sessions/{SESSION}/steps/{STEP}/messages", json={"content": "問題"})
        names = [e for e, _ in parse_sse(res.text)]
        assert "bot_reply" in names and "error" not in names


# ──────────────────────────────────────────
# 向量（Gemini embedding，Mock）
# ──────────────────────────────────────────

@pytest.mark.asyncio
class TestEmbeddings:

    def client(self, counts):
        from types import SimpleNamespace

        async def embed_content(*, model, contents, config):
            counts.append((len(contents), config.output_dimensionality, config.task_type))
            return SimpleNamespace(embeddings=[SimpleNamespace(values=[0.0] * 768) for _ in contents])

        return SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(embed_content=embed_content)))

    async def test_batches_and_uses_768_dimensions(self):
        from core.ai import embeddings
        calls = []
        with patch.object(embeddings, "_client", lambda: self.client(calls)):
            vectors = await embeddings.embed_texts(["段"] * 120)
        assert len(vectors) == 120
        assert calls == [(50, 768, "RETRIEVAL_DOCUMENT"), (50, 768, "RETRIEVAL_DOCUMENT"), (20, 768, "RETRIEVAL_DOCUMENT")]

    async def test_query_uses_retrieval_query(self):
        from core.ai import embeddings
        calls = []
        with patch.object(embeddings, "_client", lambda: self.client(calls)):
            await embeddings.embed_query("長期目標怎麼寫")
        assert calls == [(1, 768, "RETRIEVAL_QUERY")]

    async def test_missing_key_raises(self, monkeypatch):
        from core.ai import embeddings
        monkeypatch.setattr(embeddings, "get_settings", lambda: type("S", (), {"gemini_api_key": "", "gemini_embedding_model": "m"})())
        with pytest.raises(ValueError):
            await embeddings.embed_texts(["x"])
