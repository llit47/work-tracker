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
    settings = Settings(webhook_token=TOKEN, database_url=f"sqlite:///{tmp_path / 'test.db'}")
    app = create_app(settings, frontend_dist=frontend_dist)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        root_response = await client.get("/")
        health_response = await client.get("/api/health")

    assert root_response.status_code == 200
    assert "Work Tracker production" in root_response.text
    assert health_response.json() == {"status": "ok"}
