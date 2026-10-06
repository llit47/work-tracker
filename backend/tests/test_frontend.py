from pathlib import Path

from httpx import ASGITransport, AsyncClient
import pytest

from app.config import Settings
from app.main import create_app

TOKEN = "test-secret-that-is-at-least-32-characters"


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_serves_built_frontend_without_shadowing_api(tmp_path: Path):
    frontend_dist = tmp_path / "dist"
    frontend_dist.mkdir()
    (frontend_dist / "index.html").write_text("<h1>Work Tracker production</h1>", encoding="utf-8")
    (frontend_dist / "assets").mkdir()
    (frontend_dist / "assets" / "login.js").write_text("// login shell", encoding="utf-8")
    (frontend_dist / "assets" / "login.css").write_text("body {}", encoding="utf-8")
    # The reserved API namespace cannot bypass the guard via static fallback.
    (frontend_dist / "api").mkdir()
    (frontend_dist / "api" / "private.json").write_text('{"private":true}', encoding="utf-8")
    settings = Settings(webhook_token=TOKEN, database_url=f"sqlite:///{tmp_path / 'test.db'}")
    app = create_app(settings, frontend_dist=frontend_dist)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        root_response = await client.get("/")
        assert (await client.get("/assets/login.js")).status_code == 200
        assert (await client.get("/assets/login.css")).status_code == 200
        assert (await client.get("/api/private.json")).status_code == 401
        assert (await client.get("/api/dashboard")).status_code == 401
        assert (await client.get("/api/auth/me")).status_code == 401
        assert (await client.post("/api/auth/logout")).status_code == 204
        health_response = await client.get("/api/health")

    assert root_response.status_code == 200
    assert "Work Tracker production" in root_response.text
    assert health_response.json() == {"status": "ok"}
