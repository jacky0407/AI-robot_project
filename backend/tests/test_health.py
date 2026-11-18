"""
單元測試：/api/health（部署除錯資訊）
"""

from fastapi.testclient import TestClient

import main


def test_health_reports_status_and_cors_origins(monkeypatch):
    monkeypatch.setattr(main.settings, "frontend_url", "https://demo.vercel.app/,http://localhost:3000")
    res = TestClient(main.app).get("/api/health")

    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    assert body["cors_origins"] == ["https://demo.vercel.app", "http://localhost:3000"]


def test_health_reports_render_branch_and_short_commit(monkeypatch):
    monkeypatch.setenv("RENDER_GIT_BRANCH", "main")
    monkeypatch.setenv("RENDER_GIT_COMMIT", "bf5c601abcdef0123")
    body = TestClient(main.app).get("/api/health").json()

    assert body["branch"] == "main"
    assert body["commit"] == "bf5c601"


def test_health_without_render_env_returns_null(monkeypatch):
    monkeypatch.delenv("RENDER_GIT_BRANCH", raising=False)
    monkeypatch.delenv("RENDER_GIT_COMMIT", raising=False)
    body = TestClient(main.app).get("/api/health").json()

    assert body["branch"] is None and body["commit"] is None
