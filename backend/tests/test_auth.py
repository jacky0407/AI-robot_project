"""
單元測試：登入驗證 (core/auth.py)

用 MagicMock 取代 Supabase，不連線。
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import Depends, FastAPI, HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.testclient import TestClient
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
    """直接呼叫 dependency；authorization 為 "Bearer xxx" 形式，None 代表沒帶 header"""
    credentials = None
    if authorization:
        scheme, _, token = authorization.partition(" ")
        credentials = HTTPAuthorizationCredentials(scheme=scheme, credentials=token)
    with patch.object(auth, "get_supabase", return_value=db):
        return get_current_user(credentials)


def call_protected(headers: dict, db: MagicMock):
    """經過 FastAPI 實際解析 header，確認格式錯誤也會變成 401"""
    app = FastAPI()

    @app.get("/me")
    def me(user: CurrentUser = Depends(get_current_user)):
        return {"id": user.id}

    with patch.object(auth, "get_supabase", return_value=db):
        return TestClient(app).get("/me", headers=headers)


def error_of(excinfo) -> tuple[int, str]:
    return excinfo.value.status_code, excinfo.value.detail["code"]


class TestGetCurrentUser:

    def test_valid_token_returns_user_and_role(self):
        db = fake_db(profile=[{"role": "owner", "is_active": True}])
        user = resolve("Bearer token-abc", db)

        # token-abc 不是 JWT，讀不到 aal → 當作只有密碼（aal1）
        assert user == CurrentUser(id="u1", role="owner", aal="aal1")
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
        res = call_protected({"Authorization": header}, db)

        assert res.status_code == 401
        assert res.json()["detail"]["code"] == "UNAUTHORIZED"
        db.auth.get_user.assert_not_called()

    def test_valid_header_through_fastapi(self):
        db = fake_db(profile=[{"role": "student", "is_active": True}])
        res = call_protected({"Authorization": "Bearer token-abc"}, db)

        assert res.json() == {"id": "u1"}
        db.auth.get_user.assert_called_once_with("token-abc")

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



# ──────────────────────────────────────────
# 兩步驟驗證（#90）
# ──────────────────────────────────────────

import base64
import json

from core.auth import require_owner, token_aal


def jwt_with(claims: dict) -> str:
    """只做出格式正確的 JWT（不簽章）；驗簽由 supabase.auth.get_user 負責，這裡只讀 aal"""
    def b64(data: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")
    return f"{b64({'alg': 'HS256'})}.{b64(claims)}.signature"


class TestTokenAal:

    def test_aal2_token(self):
        assert token_aal(jwt_with({"sub": "u1", "aal": "aal2"})) == "aal2"

    def test_aal1_token(self):
        assert token_aal(jwt_with({"sub": "u1", "aal": "aal1"})) == "aal1"

    @pytest.mark.parametrize("token", ["not-a-jwt", "a.b.c", "", jwt_with({"sub": "u1"}), jwt_with({"aal": "AAL2"})])
    def test_anything_else_is_treated_as_aal1(self, token):
        """讀不到或不認得一律當 aal1（最嚴格），不會因此放行"""
        assert token_aal(token) == "aal1"

    def test_current_user_carries_aal_from_token(self):
        db = fake_db(profile=[{"role": "owner", "is_active": True}])
        user = resolve(f"Bearer {jwt_with({'sub': 'u1', 'aal': 'aal2'})}", db)
        assert user.aal == "aal2"


class TestStaffMfa:

    @pytest.fixture(autouse=True)
    def mfa_on(self, monkeypatch):
        monkeypatch.setattr(auth, "get_settings", lambda: SimpleNamespace(staff_mfa_required=True))

    @pytest.mark.parametrize("role", ["owner", "assistant"])
    def test_staff_with_password_only_is_403_mfa_required(self, role):
        with pytest.raises(HTTPException) as excinfo:
            require_staff(CurrentUser(id="u1", role=role, aal="aal1"))
        assert error_of(excinfo) == (403, "MFA_REQUIRED")

    @pytest.mark.parametrize("role", ["owner", "assistant"])
    def test_staff_with_mfa_passes(self, role):
        user = CurrentUser(id="u1", role=role, aal="aal2")
        assert require_staff(user) is user

    def test_student_is_still_permission_denied(self):
        """學生不用兩步驟驗證；教師端 API 照樣回 PERMISSION_DENIED"""
        with pytest.raises(HTTPException) as excinfo:
            require_staff(CurrentUser(id="u1", role="student", aal="aal1"))
        assert error_of(excinfo) == (403, "PERMISSION_DENIED")

    def test_can_be_turned_off_in_emergency(self, monkeypatch):
        monkeypatch.setattr(auth, "get_settings", lambda: SimpleNamespace(staff_mfa_required=False))
        user = CurrentUser(id="u1", role="owner", aal="aal1")
        assert require_staff(user) is user

    def test_through_fastapi_password_only_token_is_rejected(self):
        app = FastAPI()

        @app.get("/staff")
        def staff(user: CurrentUser = Depends(require_staff)):
            return {"id": user.id}

        db = fake_db(profile=[{"role": "owner", "is_active": True}])
        token = jwt_with({"sub": "u1", "aal": "aal1"})
        with patch.object(auth, "get_supabase", return_value=db):
            res = TestClient(app).get("/staff", headers={"Authorization": f"Bearer {token}"})
        assert res.status_code == 403
        assert res.json()["detail"]["code"] == "MFA_REQUIRED"


class TestRequireOwner:

    def test_owner_passes(self):
        user = CurrentUser(id="u1", role="owner", aal="aal2")
        assert require_owner(user) is user

    def test_assistant_is_403(self):
        with pytest.raises(HTTPException) as excinfo:
            require_owner(CurrentUser(id="u1", role="assistant", aal="aal2"))
        assert error_of(excinfo) == (403, "PERMISSION_DENIED")
