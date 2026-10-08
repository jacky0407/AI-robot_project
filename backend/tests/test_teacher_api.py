"""
單元測試：教師機器人管理 API (api/teacher.py)

用 MagicMock 取代 Supabase，不連線、不寫入。
"""

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
from postgrest.exceptions import APIError

import main
from api import teacher


def fake_db(*, data=None, error: Exception | None = None) -> MagicMock:
    db = MagicMock()
    query = db.table.return_value
    for method in ("select", "delete", "eq"):
        getattr(query, method).return_value = query
    if error is not None:
        query.execute.side_effect = error
    else:
        query.execute.return_value = MagicMock(data=data)
    return db


def call(method: str, path: str, db: MagicMock):
    client = TestClient(main.app, raise_server_exceptions=False)
    with patch.object(teacher, "get_supabase", return_value=db):
        return client.request(method, path)


class TestListTools:

    def test_list_is_wrapped_in_data(self):
        """依 docs/api.md 通用規範，成功回應包成 {"data": ...}"""
        db = fake_db(data=[{"id": "t1", "title": "教練"}])
        res = call("GET", "/api/teacher/tools", db)

        assert res.status_code == 200
        assert res.json() == {"data": [{"id": "t1", "title": "教練"}]}

    def test_empty_list(self):
        res = call("GET", "/api/teacher/tools", fake_db(data=None))
        assert res.json() == {"data": []}


class TestDeleteTool:

    def test_delete_success(self):
        res = call("DELETE", "/api/teacher/tools/t1", fake_db(data=[{"id": "t1"}]))
        assert res.status_code == 200
        assert res.json()["success"] is True

    def test_delete_missing_tool_returns_404(self):
        res = call("DELETE", "/api/teacher/tools/nope", fake_db(data=[]))
        assert res.status_code == 404

    def test_tool_still_used_by_module_step_returns_409(self):
        """
        回歸：module_steps.tool_id 是 ON DELETE RESTRICT，
        舊版沒有處理外鍵錯誤，前端只會拿到 500。
        """
        error = APIError({"code": "23503", "message": "violates foreign key constraint",
                          "details": "", "hint": None})
        res = call("DELETE", "/api/teacher/tools/t1", fake_db(error=error))

        assert res.status_code == 409
        assert res.json()["detail"]["code"] == "TOOL_IN_USE"

    def test_other_database_errors_are_not_disguised_as_409(self):
        error = APIError({"code": "42501", "message": "permission denied",
                          "details": "", "hint": None})
        res = call("DELETE", "/api/teacher/tools/t1", fake_db(error=error))

        assert res.status_code == 500
