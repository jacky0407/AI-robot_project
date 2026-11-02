"""
單元測試：練習 Session (core/practice_session.py)

重點：
  - session_id 必須是 UUID
  - 第一次出現就建立；自己的沿用；別人的拒絕
  - 兩個請求同時建立同一個 Session 時，不能誤判成別人的
  - 資料表不存在（還沒跑 migration 009）時不擋作答
"""

import pytest
from fastapi import HTTPException

from core.practice_session import ensure_session, parse_session_id

SESSION_ID = "5e551011-0000-4000-8000-000000000001"
USER = "user-1"
OTHER = "user-2"


class FakeError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


class FakeResult:
    def __init__(self, data):
        self.data = data


class FakeQuery:
    def __init__(self, db):
        self.db = db
        self.op = None
        self.payload = None

    def select(self, *args, **kwargs):
        self.op = "select"
        return self

    def insert(self, payload):
        self.op, self.payload = "insert", payload
        return self

    def update(self, payload):
        self.op, self.payload = "update", payload
        return self

    def eq(self, *args):
        return self

    def limit(self, *args):
        return self

    def execute(self):
        self.db.calls.append((self.op, self.payload))
        if self.db.fail:
            raise RuntimeError('relation "practice_sessions" does not exist')
        if self.op == "select":
            owner = self.db.owners.pop(0) if self.db.owners else None
            return FakeResult([{"user_id": owner}] if owner else [])
        if self.op == "insert" and self.db.insert_error:
            raise self.db.insert_error
        return FakeResult([])


class FakeDB:
    def __init__(self, owners=None, *, insert_error=None, fail=False):
        self.owners = list(owners or [])   # 每次 select 依序回傳的擁有者
        self.insert_error = insert_error
        self.fail = fail
        self.calls = []

    def table(self, name):
        assert name == "practice_sessions"
        return FakeQuery(self)

    def ops(self):
        return [op for op, _ in self.calls]


class TestParseSessionId:

    def test_accepts_uuid_and_normalizes_case(self):
        assert parse_session_id(SESSION_ID.upper()) == SESSION_ID

    @pytest.mark.parametrize("bad", ["temp-session-id", "", "123", "s1"])
    def test_rejects_non_uuid(self, bad):
        with pytest.raises(HTTPException) as exc:
            parse_session_id(bad)
        assert exc.value.status_code == 400
        assert exc.value.detail["code"] == "INVALID_SESSION_ID"


class TestEnsureSession:

    def test_new_session_is_created_for_current_user(self):
        db = FakeDB()
        assert ensure_session(db, SESSION_ID, USER) == SESSION_ID
        assert db.calls[1] == ("insert", {"id": SESSION_ID, "user_id": USER})

    def test_own_session_updates_last_active(self):
        db = FakeDB(owners=[USER])
        assert ensure_session(db, SESSION_ID, USER) == SESSION_ID
        assert db.ops() == ["select", "update"]
        assert "last_active_at" in db.calls[1][1]

    def test_other_users_session_is_forbidden(self):
        db = FakeDB(owners=[OTHER])
        with pytest.raises(HTTPException) as exc:
            ensure_session(db, SESSION_ID, USER)
        assert exc.value.status_code == 403
        assert exc.value.detail["code"] == "SESSION_FORBIDDEN"
        assert "update" not in db.ops()

    def test_race_where_same_user_created_it_first_is_allowed(self):
        db = FakeDB(owners=[None, USER], insert_error=FakeError("23505"))
        assert ensure_session(db, SESSION_ID, USER) == SESSION_ID

    def test_race_where_other_user_created_it_first_is_forbidden(self):
        db = FakeDB(owners=[None, OTHER], insert_error=FakeError("23505"))
        with pytest.raises(HTTPException) as exc:
            ensure_session(db, SESSION_ID, USER)
        assert exc.value.status_code == 403

    def test_missing_table_returns_none_instead_of_failing(self):
        assert ensure_session(FakeDB(fail=True), SESSION_ID, USER) is None

    def test_other_insert_errors_return_none(self):
        db = FakeDB(insert_error=FakeError("42501"))
        assert ensure_session(db, SESSION_ID, USER) is None
