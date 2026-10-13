"""
單元測試：登入驗證 (core/auth.py)

用 MagicMock 取代 Supabase，不連線。
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from supabase_auth.errors import AuthApiError

from core import auth
from core.auth import CurrentUser, get_current_user, require_staff


def fake_db(*, user_id: str | None = "u1", profile: list | None = None,
            auth_error: Exception | None = None) -> MagicMock:
    db = MagicMock()
    if auth_error is not None:
        db.auth.get_user.side_effect = auth_error
    elif user_id is None:
        db.auth.get_user.return_value = SimpleNamespace(user=None)
    else:
        db.auth.get_user.return_value = SimpleNamespace(user=SimpleNamespace(id=user_id))

    query = db.table.return_value
    for method in ("select", "eq", "limit"):
        getattr(query, method).return_value = query
    query.execute.return_value = MagicMock(data=profile if profile is not None else [])
    return db


def resolve(authorization: str | None, db: MagicMock):
    with patch.object(auth, "get_supabase", return_value=db):
        return get_current_user(authorization)


def error_of(excinfo) -> tuple[int, str]:
    return excinfo.value.status_code, excinfo.value.detail["code"]


class TestGetCurrentUser:

    def test_valid_token_returns_user_and_role(self):
        db = fake_db(profile=[{"role": "owner", "is_active": True}])
        user = resolve("Bearer token-abc", db)

        assert user == CurrentUser(id="u1", role="owner")
        db.auth.get_user.assert_called_once_with("token-abc")

    def test_missing_header_is_401_without_calling_supabase(self):
        """空 token 不能交給 get_user()，否則它會改讀 client 自己的 session"""
        db = fake_db()
        with pytest.raises(HTTPException) as excinfo:
            resolve(None, db)

        assert error_of(excinfo) == (401, "UNAUTHORIZED")
        db.auth.get_user.assert_not_called()

    @pytest.mark.parametrize("header", ["token-abc", "Basic token-abc", "Bearer ", "Bearer"])
    def test_malformed_header_is_401(self, header):
        db = fake_db()
        with pytest.raises(HTTPException) as excinfo:
            resolve(header, db)

        assert error_of(excinfo) == (401, "UNAUTHORIZED")
        db.auth.get_user.assert_not_called()

    def test_expired_token_is_401(self):
        db = fake_db(auth_error=AuthApiError("invalid JWT", 401, "bad_jwt"))
        with pytest.raises(HTTPException) as excinfo:
            resolve("Bearer expired", db)

        assert error_of(excinfo) == (401, "UNAUTHORIZED")

    def test_token_without_user_is_401(self):
        with pytest.raises(HTTPException) as excinfo:
            resolve("Bearer t", fake_db(user_id=None))

        assert error_of(excinfo) == (401, "UNAUTHORIZED")

    def test_missing_profile_is_403(self):
        with pytest.raises(HTTPException) as excinfo:
            resolve("Bearer t", fake_db(profile=[]))

        assert error_of(excinfo) == (403, "PROFILE_NOT_FOUND")

    def test_disabled_account_is_403(self):
        db = fake_db(profile=[{"role": "student", "is_active": False}])
        with pytest.raises(HTTPException) as excinfo:
            resolve("Bearer t", db)

        assert error_of(excinfo) == (403, "ACCOUNT_DISABLED")

    def test_empty_role_defaults_to_student(self):
        user = resolve("Bearer t", fake_db(profile=[{"role": None, "is_active": True}]))
        assert user.role == "student"
        assert user.is_staff is False


class TestRequireStaff:

    @pytest.mark.parametrize("role", ["owner", "assistant"])
    def test_staff_roles_pass(self, role):
        user = CurrentUser(id="u1", role=role)
        assert require_staff(user) is user

    @pytest.mark.parametrize("role", ["student", "explorer"])
    def test_student_roles_are_403(self, role):
        with pytest.raises(HTTPException) as excinfo:
            require_staff(CurrentUser(id="u1", role=role))

        assert error_of(excinfo) == (403, "PERMISSION_DENIED")
