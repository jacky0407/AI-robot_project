"""
單元測試：課程 API (api/courses.py)

用 MagicMock 取代 Supabase，不連線、不寫入。
登入者用 dependency_overrides 直接指定，不經過真正的 token 驗證（另見 test_auth.py）。
「今天」固定為 2026-11-11（台灣時間），可用日期的判斷才不會隨執行日期改變。
"""

from datetime import date
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from postgrest.exceptions import APIError

import main
from api import courses
from core.auth import CurrentUser, get_current_user

TEACHER = CurrentUser(id="teacher-1", role="owner")
STUDENT = CurrentUser(id="student-1", role="student")

TODAY = date(2026, 11, 11)

# 網址裡的 id 必須是 UUID 格式（格式錯誤 FastAPI 直接回 422）
C1 = "c0000000-0000-0000-0000-000000000001"
NOPE = "c0000000-0000-0000-0000-00000000dead"
S1 = "50000000-0000-0000-0000-000000000001"
CODE1 = "1c000000-0000-0000-0000-000000000001"
CODE2 = "1c000000-0000-0000-0000-000000000002"

COURSE = {"id": C1, "title": "學前特教實習", "require_approval": False, "is_archived": False}


def invite(**overrides) -> dict:
    """find_invite_code() 查到的樣子：碼的設定 + 它的課程"""
    row = {
        "id": CODE1, "plan": "free", "daily_quota": 10, "access_until": "2027-01-31",
        "is_active": True, "courses": dict(COURSE),
    }
    row.update(overrides)
    return row


def code_row(**overrides) -> dict:
    """course_invite_codes 一列"""
    row = {
        "id": CODE1, "course_id": C1, "code": "K7MX4QPA", "plan": "free", "daily_quota": 10,
        "access_until": "2027-01-31", "is_active": True, "created_at": "2026-11-01T00:00:00Z",
    }
    row.update(overrides)
    return row


def db_error(code: str) -> APIError:
    return APIError({"code": code, "message": "db error", "details": "", "hint": None})


def fake_db(*results) -> MagicMock:
    """每次 .execute() 依序回傳 results 中的一個；Exception 會被拋出"""
    db = MagicMock()
    query = db.table.return_value
    for method in ("select", "insert", "update", "eq", "order", "limit"):
        getattr(query, method).return_value = query
    query.execute.side_effect = [
        r if isinstance(r, Exception) else MagicMock(data=r) for r in results
    ]
    return db


@pytest.fixture
def as_user():
    def _login(user: CurrentUser):
        main.app.dependency_overrides[get_current_user] = lambda: user
    yield _login
    main.app.dependency_overrides.clear()


def call(method: str, path: str, db: MagicMock, json=None):
    client = TestClient(main.app, raise_server_exceptions=False)
    with patch.object(courses, "get_supabase", return_value=db), \
         patch.object(courses, "today_taipei", return_value=TODAY):
        return client.request(method, path, json=json)


def written(db: MagicMock, method: str) -> dict:
    """取出最後一次 insert / update 寫入的資料"""
    return getattr(db.table.return_value, method).call_args.args[0]


def selected(db: MagicMock) -> list[str]:
    """所有 .select() 的欄位字串"""
    return [c.args[0] for c in db.table.return_value.select.call_args_list]


# ──────────────────────────────────────────
# 邀請碼格式
# ──────────────────────────────────────────

class TestInviteCodeFormat:

    def test_generated_code_avoids_confusing_characters(self):
        for _ in range(200):
            code = courses.generate_invite_code()
            assert len(code) == 8
            assert not set(code) & set("0O1IL")
            assert courses.INVITE_CODE_PATTERN.match(code)

    def test_custom_code_with_dash_is_accepted(self):
        assert courses.INVITE_CODE_PATTERN.match("IEP-2026-GRANT")


# ──────────────────────────────────────────
# 建立課程
# ──────────────────────────────────────────

class TestCreateCourse:

    def test_course_is_created_without_invite_code(self, as_user):
        """設計提案：建立課程時不產生邀請碼，老師另外產生"""
        as_user(TEACHER)
        db = fake_db([{**COURSE, "description": None, "created_by": "teacher-1",
                       "created_at": "t", "invite_code": None}])
        res = call("POST", "/api/courses", db, json={"title": "  學前特教實習 ", "require_approval": True})

        assert res.status_code == 201
        assert "invite_code" not in res.json()["data"]
        assert "產生邀請碼" in res.json()["message"]
        row = written(db, "insert")
        assert row == {"title": "學前特教實習", "description": None,
                       "require_approval": True, "created_by": "teacher-1"}

    def test_blank_title_is_rejected(self, as_user):
        as_user(TEACHER)
        res = call("POST", "/api/courses", fake_db(), json={"title": "   "})
        assert res.status_code == 422

    def test_student_cannot_create_course(self, as_user):
        as_user(STUDENT)
        db = fake_db()
        res = call("POST", "/api/courses", db, json={"title": "課程"})

        assert res.status_code == 403
        db.table.assert_not_called()

    def test_not_logged_in_is_401(self):
        res = call("POST", "/api/courses", fake_db(), json={"title": "課程"})
        assert res.status_code == 401


# ──────────────────────────────────────────
# 課程列表與修改
# ──────────────────────────────────────────

class TestListAndUpdateCourses:

    def course_row(self):
        return {
            **COURSE,
            "course_members": [
                {"status": "active", "invite_code_id": CODE1},
                {"status": "active", "invite_code_id": CODE1},
                {"status": "pending", "invite_code_id": CODE2},
                {"status": "rejected", "invite_code_id": CODE2},
            ],
            "course_invite_codes": [
                code_row(id=CODE1, code="OLDCODE1", created_at="2026-09-01T00:00:00Z",
                         access_until="2026-11-10"),
                code_row(id=CODE2, code="NEWCODE2", created_at="2026-11-01T00:00:00Z",
                         plan="self_key", daily_quota=None),
            ],
        }

    def test_list_counts_members_and_embeds_invite_codes(self, as_user):
        as_user(TEACHER)
        res = call("GET", "/api/courses", fake_db([self.course_row()]))

        course = res.json()["data"][0]
        assert course["active_count"] == 2
        assert course["pending_count"] == 1
        assert "course_members" not in course and "course_invite_codes" not in course

        codes = course["invite_codes"]
        assert [c["code"] for c in codes] == ["NEWCODE2", "OLDCODE1"]  # 新的在前
        assert codes[1]["member_count"] == 2      # 只算 active
        assert codes[0]["member_count"] == 0      # pending / rejected 不算
        assert codes[1]["expired"] is True        # 11/10 < 今天 11/11
        assert codes[0]["expired"] is False
        assert codes[0]["plan"] == "self_key" and codes[0]["daily_quota"] is None

    def test_list_names_foreign_keys_to_avoid_ambiguous_embed(self, as_user):
        """course_members 連到 courses 與 course_invite_codes 兩邊，embed 不指定外鍵 Supabase 會回 PGRST201"""
        as_user(TEACHER)
        db = fake_db([])
        call("GET", "/api/courses", db)

        select = selected(db)[0]
        assert "course_members!course_members_course_id_fkey(" in select
        assert "course_invite_codes!course_invite_codes_course_id_fkey(" in select

    def test_list_hides_archived_by_default(self, as_user):
        as_user(TEACHER)
        db = fake_db([])
        call("GET", "/api/courses", db)
        db.table.return_value.eq.assert_called_once_with("is_archived", False)

    def test_update_require_approval(self, as_user):
        as_user(TEACHER)
        db = fake_db([{"id": C1, "require_approval": True}])
        res = call("PATCH", f"/api/courses/{C1}", db, json={"require_approval": True})

        assert res.status_code == 200
        assert written(db, "update") == {"require_approval": True}

    def test_update_missing_course_is_404(self, as_user):
        as_user(TEACHER)
        res = call("PATCH", f"/api/courses/{NOPE}", fake_db([]), json={"title": "新名稱"})
        assert res.status_code == 404

    def test_update_without_fields_is_400(self, as_user):
        as_user(TEACHER)
        res = call("PATCH", f"/api/courses/{C1}", fake_db(), json={})
        assert res.json()["detail"]["code"] == "NO_CHANGES"


# ──────────────────────────────────────────
# 產生邀請碼
# ──────────────────────────────────────────

class TestCreateInviteCode:

    PATH = f"/api/courses/{C1}/invite-codes"

    def test_free_code_with_default_quota(self, as_user):
        as_user(TEACHER)
        db = fake_db([COURSE], [code_row()])
        res = call("POST", self.PATH, db, json={"access_until": "2027-01-31"})

        assert res.status_code == 201
        assert res.json()["data"]["member_count"] == 0
        row = written(db, "insert")
        assert row["plan"] == "free"
        assert row["daily_quota"] == 10
        assert row["access_until"] == "2027-01-31"
        assert row["course_id"] == C1
        assert row["created_by"] == "teacher-1"
        assert courses.INVITE_CODE_PATTERN.match(row["code"]) and len(row["code"]) == 8

    def test_free_code_with_custom_quota(self, as_user):
        as_user(TEACHER)
        db = fake_db([COURSE], [code_row(daily_quota=5)])
        call("POST", self.PATH, db, json={"access_until": "2027-01-31", "daily_quota": 5})
        assert written(db, "insert")["daily_quota"] == 5

    def test_self_key_code_has_no_quota(self, as_user):
        """自備金鑰不限次數，前端多帶了 daily_quota 也不存（資料庫的 CHECK 也不允許）"""
        as_user(TEACHER)
        db = fake_db([COURSE], [code_row(plan="self_key", daily_quota=None)])
        call("POST", self.PATH, db, json={"plan": "self_key", "access_until": "2027-01-31", "daily_quota": 5})

        row = written(db, "insert")
        assert row["plan"] == "self_key"
        assert row["daily_quota"] is None

    def test_access_until_today_is_allowed(self, as_user):
        as_user(TEACHER)
        res = call("POST", self.PATH, fake_db([COURSE], [code_row()]), json={"access_until": "2026-11-11"})
        assert res.status_code == 201

    def test_access_until_in_the_past_is_400(self, as_user):
        as_user(TEACHER)
        db = fake_db()
        res = call("POST", self.PATH, db, json={"access_until": "2026-11-10"})

        assert res.json()["detail"]["code"] == "INVALID_ACCESS_UNTIL"
        db.table.assert_not_called()

    def test_access_until_is_required(self, as_user):
        as_user(TEACHER)
        res = call("POST", self.PATH, fake_db(), json={"plan": "free"})
        assert res.status_code == 422

    @pytest.mark.parametrize("body", [
        {"access_until": "2027-01-31", "plan": "paid"},
        {"access_until": "2027-01-31", "daily_quota": 0},
        {"access_until": "2027-01-31", "daily_quota": 1001},
    ])
    def test_invalid_plan_or_quota_is_422(self, as_user, body):
        as_user(TEACHER)
        res = call("POST", self.PATH, fake_db(), json=body)
        assert res.status_code == 422

    def test_missing_course_is_404(self, as_user):
        as_user(TEACHER)
        res = call("POST", self.PATH, fake_db([]), json={"access_until": "2027-01-31"})
        assert res.json()["detail"]["code"] == "COURSE_NOT_FOUND"

    def test_archived_course_is_403(self, as_user):
        as_user(TEACHER)
        db = fake_db([{**COURSE, "is_archived": True}])
        res = call("POST", self.PATH, db, json={"access_until": "2027-01-31"})

        assert res.json()["detail"]["code"] == "COURSE_ARCHIVED"
        db.table.return_value.insert.assert_not_called()

    def test_duplicate_code_is_retried(self, as_user):
        as_user(TEACHER)
        db = fake_db([COURSE], db_error("23505"), [code_row()])
        res = call("POST", self.PATH, db, json={"access_until": "2027-01-31"})

        assert res.status_code == 201
        assert db.table.return_value.insert.call_count == 2

    def test_gives_up_after_repeated_collisions(self, as_user):
        as_user(TEACHER)
        db = fake_db([COURSE], *[db_error("23505")] * courses.INVITE_CODE_MAX_RETRIES)
        res = call("POST", self.PATH, db, json={"access_until": "2027-01-31"})

        assert res.status_code == 500
        assert res.json()["detail"]["code"] == "INVITE_CODE_GENERATION_FAILED"

    def test_student_cannot_create_code(self, as_user):
        as_user(STUDENT)
        res = call("POST", self.PATH, fake_db(), json={"access_until": "2027-01-31"})
        assert res.status_code == 403


class TestListInviteCodes:

    def test_lists_codes_with_member_count(self, as_user):
        as_user(TEACHER)
        db = fake_db(
            [COURSE],
            [code_row()],
            [{"status": "active", "invite_code_id": CODE1}, {"status": "removed", "invite_code_id": CODE1}],
        )
        res = call("GET", f"/api/courses/{C1}/invite-codes", db)

        assert res.json()["data"][0]["member_count"] == 1

    def test_missing_course_is_404(self, as_user):
        as_user(TEACHER)
        res = call("GET", f"/api/courses/{NOPE}/invite-codes", fake_db([]))
        assert res.status_code == 404


class TestUpdateInviteCode:

    PATH = f"/api/courses/{C1}/invite-codes/{CODE1}"

    def test_disable_code(self, as_user):
        as_user(TEACHER)
        db = fake_db([code_row()], [code_row(is_active=False)], [])
        res = call("PATCH", self.PATH, db, json={"is_active": False})

        assert res.status_code == 200
        assert res.json()["data"]["is_active"] is False
        assert written(db, "update") == {"is_active": False}

    def test_extend_access_until_and_change_quota(self, as_user):
        as_user(TEACHER)
        db = fake_db([code_row()], [code_row(access_until="2027-06-30", daily_quota=20)], [])
        call("PATCH", self.PATH, db, json={"access_until": "2027-06-30", "daily_quota": 20})

        assert written(db, "update") == {"access_until": "2027-06-30", "daily_quota": 20}

    def test_code_of_another_course_is_404(self, as_user):
        """課程 id 與碼 id 要對得上，不能從 A 課改 B 課的碼"""
        as_user(TEACHER)
        db = fake_db([])
        res = call("PATCH", self.PATH, db, json={"is_active": False})

        assert res.json()["detail"]["code"] == "INVITE_CODE_NOT_FOUND"
        db.table.return_value.eq.assert_any_call("course_id", C1)

    def test_quota_on_self_key_code_is_400(self, as_user):
        as_user(TEACHER)
        db = fake_db([code_row(plan="self_key", daily_quota=None)])
        res = call("PATCH", self.PATH, db, json={"daily_quota": 5})

        assert res.json()["detail"]["code"] == "QUOTA_NOT_APPLICABLE"
        db.table.return_value.update.assert_not_called()

    @pytest.mark.parametrize("body", [{"daily_quota": None}, {"is_active": None}, {"access_until": None}])
    def test_clearing_required_fields_is_400(self, as_user, body):
        as_user(TEACHER)
        res = call("PATCH", self.PATH, fake_db([code_row()]), json=body)
        assert res.status_code == 400

    def test_access_until_in_the_past_is_400(self, as_user):
        as_user(TEACHER)
        res = call("PATCH", self.PATH, fake_db([code_row()]), json={"access_until": "2026-11-01"})
        assert res.json()["detail"]["code"] == "INVALID_ACCESS_UNTIL"

    def test_plan_cannot_be_changed(self, as_user):
        """plan 不在可修改的欄位裡：送來也不會寫入，只剩空的修改"""
        as_user(TEACHER)
        res = call("PATCH", self.PATH, fake_db(), json={"plan": "self_key"})
        assert res.json()["detail"]["code"] == "NO_CHANGES"

    def test_student_cannot_update_code(self, as_user):
        as_user(STUDENT)
        res = call("PATCH", self.PATH, fake_db(), json={"is_active": False})
        assert res.status_code == 403


# ──────────────────────────────────────────
# 用邀請碼加入
# ──────────────────────────────────────────

class TestJoinCourse:

    def test_open_course_joins_as_active_under_the_code(self, as_user):
        as_user(STUDENT)
        db = fake_db([invite()], [], [{"status": "active", "joined_at": "2026-11-11T00:00:00Z"}])
        res = call("POST", "/api/courses/join", db, json={"invite_code": " k7mx4qpa "})

        assert res.status_code == 200
        body = res.json()["data"]
        assert body["course_id"] == C1
        assert body["course_name"] == "學前特教實習"
        assert body["status"] == "active"
        assert body["plan"] == "free"
        assert body["daily_quota"] == 10
        assert body["access_until"] == "2027-01-31"
        assert body["needs_api_key"] is False
        # 邀請碼會先轉大寫、去空白，再到邀請碼表查
        db.table.assert_any_call("course_invite_codes")
        db.table.return_value.eq.assert_any_call("code", "K7MX4QPA")
        assert written(db, "insert") == {
            "course_id": C1, "user_id": "student-1", "status": "active", "invite_code_id": CODE1,
        }

    def test_lookup_names_foreign_key(self, as_user):
        as_user(STUDENT)
        db = fake_db([invite()], [], [{"status": "active"}])
        call("POST", "/api/courses/join", db, json={"invite_code": "K7MX4QPA"})
        assert "courses!course_invite_codes_course_id_fkey(" in selected(db)[0]

    def test_approval_course_joins_as_pending(self, as_user):
        as_user(STUDENT)
        db = fake_db([invite(courses={**COURSE, "require_approval": True})], [], [{"status": "pending"}])
        res = call("POST", "/api/courses/join", db, json={"invite_code": "K7MX4QPA"})

        assert res.json()["data"]["status"] == "pending"
        assert written(db, "insert")["status"] == "pending"

    def test_self_key_code_tells_student_to_set_api_key(self, as_user):
        as_user(STUDENT)
        db = fake_db([invite(plan="self_key", daily_quota=None)], [], [{"status": "active"}])
        res = call("POST", "/api/courses/join", db, json={"invite_code": "K7MX4QPA"})

        assert res.json()["data"]["needs_api_key"] is True

    def test_unknown_code_is_404(self, as_user):
        as_user(STUDENT)
        res = call("POST", "/api/courses/join", fake_db([]), json={"invite_code": "NOPE1234"})

        assert res.status_code == 404
        assert res.json()["detail"]["code"] == "INVALID_INVITE_CODE"

    @pytest.mark.parametrize("code", ["", "AB", "有中文的碼", "K7MX 4QPA"])
    def test_malformed_code_is_400(self, as_user, code):
        as_user(STUDENT)
        db = fake_db()
        res = call("POST", "/api/courses/join", db, json={"invite_code": code})

        assert res.status_code == 400
        db.table.assert_not_called()

    def test_disabled_code_is_403(self, as_user):
        as_user(STUDENT)
        db = fake_db([invite(is_active=False)])
        res = call("POST", "/api/courses/join", db, json={"invite_code": "K7MX4QPA"})

        assert res.json()["detail"]["code"] == "CODE_DISABLED"
        db.table.return_value.insert.assert_not_called()

    def test_archived_course_is_403(self, as_user):
        as_user(STUDENT)
        db = fake_db([invite(courses={**COURSE, "is_archived": True})])
        res = call("POST", "/api/courses/join", db, json={"invite_code": "K7MX4QPA"})

        assert res.json()["detail"]["code"] == "COURSE_ARCHIVED"

    def test_code_past_access_until_is_403(self, as_user):
        """碼本身不會失效，但可用日期已過，加入也不能作答"""
        as_user(STUDENT)
        db = fake_db([invite(access_until="2026-11-10")])
        res = call("POST", "/api/courses/join", db, json={"invite_code": "K7MX4QPA"})

        assert res.json()["detail"]["code"] == "CODE_EXPIRED"

    def test_access_until_today_can_still_join(self, as_user):
        as_user(STUDENT)
        db = fake_db([invite(access_until="2026-11-11")], [], [{"status": "active"}])
        res = call("POST", "/api/courses/join", db, json={"invite_code": "K7MX4QPA"})
        assert res.status_code == 200

    @pytest.mark.parametrize("status,code", [("active", "ALREADY_JOINED"), ("pending", "ALREADY_PENDING")])
    def test_already_member_is_409(self, as_user, status, code):
        as_user(STUDENT)
        db = fake_db([invite()], [{"id": "m1", "status": status}])
        res = call("POST", "/api/courses/join", db, json={"invite_code": "K7MX4QPA"})

        assert res.status_code == 409
        assert res.json()["detail"]["code"] == code

    @pytest.mark.parametrize("status", ["rejected", "removed"])
    def test_rejoin_needs_approval_and_moves_to_the_new_code(self, as_user, status):
        """被拒絕或移出的人重新申請：一律回到 pending，方案與可用日期改看這次輸入的碼"""
        as_user(STUDENT)
        db = fake_db([invite(id=CODE2)], [{"id": "m1", "status": status}], [{"status": "pending"}])
        res = call("POST", "/api/courses/join", db, json={"invite_code": "K7MX4QPA"})

        assert res.json()["data"]["status"] == "pending"
        assert written(db, "update") == {"status": "pending", "invite_code_id": CODE2}
        db.table.return_value.insert.assert_not_called()

    def test_double_submit_race_is_409_not_500(self, as_user):
        as_user(STUDENT)
        db = fake_db([invite()], [], db_error("23505"))
        res = call("POST", "/api/courses/join", db, json={"invite_code": "K7MX4QPA"})

        assert res.status_code == 409

    def test_not_logged_in_is_401(self):
        res = call("POST", "/api/courses/join", fake_db(), json={"invite_code": "K7MX4QPA"})
        assert res.status_code == 401


class TestMyCourses:

    def test_lists_own_courses_with_plan_and_access(self, as_user):
        as_user(STUDENT)
        db = fake_db([
            {"status": "active", "joined_at": "t",
             "courses": {"id": C1, "title": "課程", "description": None, "is_archived": False},
             "course_invite_codes": {"plan": "free", "daily_quota": 10, "access_until": "2026-11-10"}},
        ])
        res = call("GET", "/api/courses/mine", db)

        assert res.json()["data"] == [{
            "course_id": C1, "course_name": "課程", "description": None, "is_archived": False,
            "status": "active", "joined_at": "t", "plan": "free", "daily_quota": 10,
            "access_until": "2026-11-10", "access_expired": True, "needs_api_key": False,
        }]
        db.table.return_value.eq.assert_called_once_with("user_id", "student-1")
        assert "course_invite_codes!course_members_invite_code_id_fkey(" in selected(db)[0]

    def test_member_without_code_is_unlimited(self, as_user):
        """舊資料或後台直接加入：沒有邀請碼，不限期、不限次數"""
        as_user(STUDENT)
        db = fake_db([{"status": "active", "joined_at": "t", "course_invite_codes": None,
                       "courses": {"id": C1, "title": "課程", "description": None, "is_archived": False}}])
        item = call("GET", "/api/courses/mine", db).json()["data"][0]

        assert item["plan"] is None
        assert item["access_until"] is None
        assert item["access_expired"] is False


# ──────────────────────────────────────────
# 成員與審核
# ──────────────────────────────────────────

class TestMembers:

    def test_pending_list_includes_name_email_and_code(self, as_user):
        as_user(TEACHER)
        db = fake_db([COURSE], [{"user_id": "s1", "status": "pending", "joined_at": "t",
                                 "profiles": {"full_name": "王小明", "email": "s1@x.com"},
                                 "course_invite_codes": {"code": "K7MX4QPA", "plan": "free",
                                                         "access_until": "2027-01-31"}}])
        res = call("GET", f"/api/courses/{C1}/members?status=pending", db)

        assert res.json()["data"] == [{
            "user_id": "s1", "full_name": "王小明", "email": "s1@x.com",
            "status": "pending", "joined_at": "t",
            "invite_code": "K7MX4QPA", "plan": "free", "access_until": "2027-01-31",
        }]

    def test_invalid_status_filter_is_400(self, as_user):
        as_user(TEACHER)
        res = call("GET", f"/api/courses/{C1}/members?status=whatever", fake_db())
        assert res.status_code == 400

    def test_members_of_missing_course_is_404(self, as_user):
        as_user(TEACHER)
        res = call("GET", f"/api/courses/{NOPE}/members", fake_db([]))
        assert res.status_code == 404

    def test_student_cannot_see_members(self, as_user):
        as_user(STUDENT)
        res = call("GET", f"/api/courses/{C1}/members", fake_db())
        assert res.status_code == 403

    @pytest.mark.parametrize("action,before,after", [
        ("approve", "pending", "active"),
        ("reject", "pending", "rejected"),
        ("remove", "active", "removed"),
        ("approve", "rejected", "active"),
    ])
    def test_member_actions(self, as_user, action, before, after):
        as_user(TEACHER)
        db = fake_db([{"id": "m1", "status": before}], [{"id": "m1", "status": after}])
        res = call("PATCH", f"/api/courses/{C1}/members/{S1}", db, json={"action": action})

        assert res.status_code == 200
        assert written(db, "update") == {"status": after}

    def test_cannot_reject_active_member(self, as_user):
        """已加入的成員要用 remove，不是 reject，以免狀態意義混淆"""
        as_user(TEACHER)
        db = fake_db([{"id": "m1", "status": "active"}])
        res = call("PATCH", f"/api/courses/{C1}/members/{S1}", db, json={"action": "reject"})

        assert res.status_code == 409
        assert res.json()["detail"]["code"] == "INVALID_STATUS_TRANSITION"
        db.table.return_value.update.assert_not_called()

    def test_unknown_member_is_404(self, as_user):
        as_user(TEACHER)
        res = call("PATCH", f"/api/courses/{C1}/members/{S1}", fake_db([]), json={"action": "approve"})
        assert res.status_code == 404

    def test_unknown_action_is_422(self, as_user):
        as_user(TEACHER)
        res = call("PATCH", f"/api/courses/{C1}/members/{S1}", fake_db(), json={"action": "delete"})
        assert res.status_code == 422

    def test_student_cannot_approve(self, as_user):
        as_user(STUDENT)
        res = call("PATCH", f"/api/courses/{C1}/members/{S1}", fake_db(), json={"action": "approve"})
        assert res.status_code == 403


# ──────────────────────────────────────────
# 網址參數格式
# ──────────────────────────────────────────

class TestPathIdFormat:
    """
    回歸：舊版網址裡的 id 是任意字串，亂打的 id 送到資料庫會回 500。
    改成 UUID 型別後，格式錯誤在進資料庫前就回 422。
    """

    @pytest.mark.parametrize("method,path,body", [
        ("PATCH", "/api/courses/not-a-uuid", {"title": "新名稱"}),
        ("GET", "/api/courses/not-a-uuid/members", None),
        ("PATCH", f"/api/courses/not-a-uuid/members/{S1}", {"action": "approve"}),
        ("PATCH", f"/api/courses/{C1}/members/not-a-uuid", {"action": "approve"}),
        ("POST", "/api/courses/not-a-uuid/invite-codes", {"access_until": "2027-01-31"}),
        ("GET", "/api/courses/not-a-uuid/invite-codes", None),
        ("PATCH", f"/api/courses/{C1}/invite-codes/not-a-uuid", {"is_active": False}),
    ])
    def test_non_uuid_id_is_422_and_never_reaches_database(self, as_user, method, path, body):
        as_user(TEACHER)
        db = fake_db()
        res = call(method, path, db, json=body)

        assert res.status_code == 422
        db.table.assert_not_called()
