"""
單元測試：學生自備 Gemini 金鑰（migration 017、開會紀錄 #49 第 5 項）

  - /api/me/api-key：驗證後加密儲存、狀態只回 key_hint、刪除；沒設加密金鑰回 503
  - 資料庫裡沒有明文；log／回應不帶金鑰
  - 自備金鑰方案存好金鑰 → mode = self_key、不扣平台次數；評分只在那次請求改用學生金鑰
  - 不同請求互不影響（ContextVar）
"""

import asyncio
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

import main
from api import me
from core import user_api_keys as keys
from core.access import StepAccess, consume_quota, quota_status
from core.ai import llm_keys
from core.auth import CurrentUser, get_current_user
from core.config import get_settings
from tests.inmemory_db import InMemoryDB

STUDENT = CurrentUser(id="11111111-1111-4111-8111-111111111111", role="student")
GOOD_KEY = "AIzaSyD-test_key_0123456789abcdefghijk"


@pytest.fixture(autouse=True)
def encryption_key(monkeypatch):
    monkeypatch.setenv("API_KEY_ENCRYPTION_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def as_student():
    main.app.dependency_overrides[get_current_user] = lambda: STUDENT
    yield
    main.app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture
def verify_ok(monkeypatch):
    async def ok(key):
        return None
    monkeypatch.setattr(me, "verify_key", ok)


def call(method, db, json=None):
    client = TestClient(main.app, raise_server_exceptions=False)
    with patch.object(me, "get_supabase", return_value=db):
        return client.request(method, "/api/me/api-key", json=json)


# ──────────────────────────────────────────
# 儲存
# ──────────────────────────────────────────

class TestStorage:

    def test_saved_key_is_encrypted_and_can_be_decrypted(self):
        db = InMemoryDB()
        status = keys.save_key(db, STUDENT.id, GOOD_KEY)

        row = db.rows("user_api_keys")[0]
        assert GOOD_KEY not in str(row)
        assert row["key_hint"] == status["key_hint"] == "AIza…hijk"
        assert keys.load_decrypted_key(db, STUDENT.id) == GOOD_KEY

    def test_saving_again_replaces_the_old_key(self):
        db = InMemoryDB()
        keys.save_key(db, STUDENT.id, GOOD_KEY)
        keys.save_key(db, STUDENT.id, "AIzaSyD-another_key_99999999999999zzzz")
        assert len(db.rows("user_api_keys")) == 1
        assert keys.load_decrypted_key(db, STUDENT.id).endswith("zzzz")

    def test_wrong_encryption_key_cannot_decrypt(self, monkeypatch):
        db = InMemoryDB()
        keys.save_key(db, STUDENT.id, GOOD_KEY)
        monkeypatch.setenv("API_KEY_ENCRYPTION_KEY", Fernet.generate_key().decode())
        get_settings.cache_clear()
        assert keys.load_decrypted_key(db, STUDENT.id) is None

    @pytest.mark.parametrize("raw", ["", "short", "has space in it 1234567890123456789", "中文金鑰" * 10])
    def test_bad_format_is_rejected(self, raw):
        with pytest.raises(keys.InvalidApiKey):
            keys.normalize_key(raw)

    def test_has_key_is_false_before_migration_017(self):
        db = InMemoryDB()
        db.errors[("user_api_keys", "select")] = RuntimeError('relation "user_api_keys" does not exist')
        assert keys.has_key(db, STUDENT.id) is False


# ──────────────────────────────────────────
# /api/me/api-key
# ──────────────────────────────────────────

class TestEndpoints:

    def test_put_get_delete(self, verify_ok):
        db = InMemoryDB()
        res = call("PUT", db, {"api_key": f"  {GOOD_KEY}  "})
        assert res.status_code == 200
        assert GOOD_KEY not in res.text
        assert res.json()["data"]["key_hint"] == "AIza…hijk"

        got = call("GET", db).json()["data"]
        assert got["has_key"] is True and "encrypted_key" not in got and GOOD_KEY not in str(got)

        assert call("DELETE", db).status_code == 200
        assert call("GET", db).json()["data"]["has_key"] is False

    def test_invalid_key_is_400_and_not_saved(self, monkeypatch):
        async def bad(key):
            raise keys.InvalidApiKey("Google 回覆這把金鑰無法使用")
        monkeypatch.setattr(me, "verify_key", bad)
        db = InMemoryDB()
        res = call("PUT", db, {"api_key": GOOD_KEY})

        assert res.status_code == 400 and res.json()["detail"]["code"] == "INVALID_API_KEY"
        assert db.rows("user_api_keys") == []

    def test_google_unreachable_is_502(self, monkeypatch):
        async def down(key):
            raise keys.KeyVerificationUnavailable()
        monkeypatch.setattr(me, "verify_key", down)
        assert call("PUT", InMemoryDB(), {"api_key": GOOD_KEY}).json()["detail"]["code"] == "KEY_VERIFY_FAILED"

    def test_not_configured_is_503(self, verify_ok, monkeypatch):
        monkeypatch.setenv("API_KEY_ENCRYPTION_KEY", "")
        get_settings.cache_clear()
        res = call("PUT", InMemoryDB(), {"api_key": GOOD_KEY})
        assert res.status_code == 503 and res.json()["detail"]["code"] == "KEY_STORAGE_NOT_CONFIGURED"

    def test_requires_login(self):
        main.app.dependency_overrides.pop(get_current_user, None)
        assert call("GET", InMemoryDB()).status_code == 401

    def test_verify_maps_google_errors(self, monkeypatch):
        def invalid(key):
            raise RuntimeError("400 API key not valid. Please pass a valid API key.")
        monkeypatch.setattr(keys, "_list_models", invalid)
        with pytest.raises(keys.InvalidApiKey):
            asyncio.run(keys.verify_key(GOOD_KEY))

        def network(key):
            raise ConnectionError("connection reset")
        monkeypatch.setattr(keys, "_list_models", network)
        with pytest.raises(keys.KeyVerificationUnavailable):
            asyncio.run(keys.verify_key(GOOD_KEY))


# ──────────────────────────────────────────
# 作答權限與額度
# ──────────────────────────────────────────

class TestAccess:

    def test_self_key_mode_is_not_charged(self):
        db = InMemoryDB()
        assert consume_quota(db, StepAccess(mode="self_key")) is None
        assert quota_status(StepAccess(mode="self_key"))["remaining"] is None
        assert db.rpc_calls == []


# ──────────────────────────────────────────
# 評分時用哪把金鑰
# ──────────────────────────────────────────

class TestKeyOverride:

    def test_agents_use_override_only_inside_block(self, monkeypatch):
        from core.ai.agents.evaluator_agent import EvaluatorAgent

        built = []
        monkeypatch.setattr("core.ai.agents.evaluator_agent.build_llm",
                            lambda key, temperature: built.append(key) or f"llm-for-{key[-4:]}")
        agent = EvaluatorAgent()
        agent._llm = "platform-llm"

        with llm_keys.use_api_key(GOOD_KEY):
            assert agent.llm == "llm-for-hijk"
        assert agent.llm == "platform-llm"
        assert built == [GOOD_KEY]

    def test_concurrent_requests_do_not_share_keys(self):
        async def worker(key, seen):
            with llm_keys.use_api_key(key):
                await asyncio.sleep(0.01)
                seen.append((key, llm_keys.override_api_key()))

        async def main_():
            seen = []
            await asyncio.gather(worker("KEY-A", seen), worker(None, seen), worker("KEY-B", seen))
            return seen

        assert sorted(asyncio.run(main_()), key=str) == sorted(
            [("KEY-A", "KEY-A"), (None, None), ("KEY-B", "KEY-B")], key=str)


class TestSubmitWithOwnKey:

    def test_submit_scores_with_students_key(self, monkeypatch):
        from api import practice
        from tests import test_practice_api as t

        monkeypatch.setattr(practice, "check_step_access", lambda db, user, step: StepAccess(mode="self_key"))
        monkeypatch.setattr(practice, "check_step_unlocked", lambda db, user, step: None)
        monkeypatch.setattr(practice, "load_decrypted_key", lambda db, uid: GOOD_KEY)
        llm = t.mock_llm(t.FULL_EVALUATION)
        keys_used = []
        monkeypatch.setattr("core.ai.agents.evaluator_agent.build_llm",
                            lambda key, temperature: keys_used.append(key) or llm)

        fake_db = t.FakeSupabase(t.STEP_ROW, prev_attempts=[])
        client = TestClient(main.app)
        with patch.object(practice, "get_supabase", return_value=fake_db):
            res = client.post(f"/api/practice/sessions/{t.SESSION_ID}/submit", json=t.BODY)
        events = t.parse_sse(res.text)

        assert any(e == "score_complete" for e, _ in events)
        assert keys_used and set(keys_used) == {GOOD_KEY}
        assert llm_keys.override_api_key() is None  # 請求結束就還原

    def test_self_key_without_readable_key_is_403_and_writes_nothing(self, monkeypatch):
        from api import practice
        from tests import test_practice_api as t

        monkeypatch.setattr(practice, "check_step_access", lambda db, user, step: StepAccess(mode="self_key"))
        monkeypatch.setattr(practice, "check_step_unlocked", lambda db, user, step: None)
        monkeypatch.setattr(practice, "load_decrypted_key", lambda db, uid: None)

        fake_db = t.FakeSupabase(t.STEP_ROW, prev_attempts=[])
        res, _ = t.submit(fake_db, [])

        assert res.status_code == 403 and res.json()["detail"]["code"] == "API_KEY_REQUIRED"
        assert fake_db.find("step_attempts", "insert") == []
