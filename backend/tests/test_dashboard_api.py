from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from httpx import ASGITransport, AsyncClient
import pytest

from app.config import Settings
from app.database import build_engine
from app.main import create_app
from app.models import Base, PayRate


TOKEN = "test-secret-that-is-at-least-32-characters"
HEADERS = {"X-Webhook-Token": TOKEN}
NOW = datetime.fromisoformat("2026-09-07T12:00:00+00:00")


@pytest.fixture
def anyio_backend():
    return "asyncio"


def make_app(tmp_path: Path):
    database_url = f"sqlite:///{tmp_path / 'dashboard.db'}"
    Base.metadata.create_all(build_engine(database_url))
    app = create_app(
        Settings(webhook_token=TOKEN, database_url=database_url),
        now_provider=lambda: NOW,
    )
    with app.state.session_factory() as session:
        session.add(
            PayRate(
                effective_from=date(1970, 1, 1),
                hourly_rate=Decimal("50.00"),
                currency="PLN",
                created_at=NOW,
            )
        )
        session.commit()
    return app


async def add_raw_event(client: AsyncClient, event_type: str, timestamp: str) -> int:
    response = await client.post(
        "/api/webhook/home-assistant",
        json={
            "event": event_type,
            "location": "gabinet_zabki",
            "timestamp": timestamp,
            "source": "home_assistant",
        },
        headers=HEADERS,
    )
    assert response.status_code == 201
    return response.json()["id"]


@pytest.mark.anyio
async def test_dashboard_api_returns_authoritative_live_and_finalized_totals(tmp_path: Path):
    app = make_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        await add_raw_event(client, "entry", "2026-09-07T08:00:00+02:00")
        await add_raw_event(client, "exit", "2026-09-07T12:00:00+02:00")
        await add_raw_event(client, "entry", "2026-09-07T13:00:00+02:00")
        response = await client.get("/api/dashboard?timezone=Europe%2FWarsaw")

    assert response.status_code == 200
    assert response.json() == {
        "status": "working",
        "generated_at": "2026-09-07T12:00:00Z",
        "current_session": {
            "entry_timestamp": "2026-09-07T13:00:00+02:00",
            "entry_timestamp_utc": "2026-09-07T11:00:00Z",
            "elapsed_seconds": 3600,
        },
        "today": {
            "date": "2026-09-07",
            "completed_duration_seconds": 14400,
            "running_duration_seconds": 3600,
            "effective_duration_seconds": 18000,
        },
        "month": {
            "year": 2026,
            "month": 9,
            "completed_duration_seconds": 14400,
            "work_days": 1,
            "pay": "200.00",
            "currency": "PLN",
        },
    }


@pytest.mark.anyio
async def test_dashboard_api_applies_timestamp_correction_ignore_and_undo(tmp_path: Path):
    app = make_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        entry_id = await add_raw_event(client, "entry", "2026-09-07T08:00:00+02:00")
        correction = await client.put(
            f"/api/work-events/{entry_id}/timestamp-correction",
            json={"timestamp": "2026-09-07T10:00:00+02:00"},
        )
        corrected = await client.get("/api/dashboard?timezone=Europe%2FWarsaw")

        await client.delete(f"/api/corrections/{correction.json()['id']}")
        ignored_correction = await client.put(f"/api/work-events/{entry_id}/ignore")
        ignored = await client.get("/api/dashboard?timezone=Europe%2FWarsaw")

        await client.delete(f"/api/corrections/{ignored_correction.json()['id']}")
        restored = await client.get("/api/dashboard?timezone=Europe%2FWarsaw")

    assert corrected.json()["status"] == "working"
    assert corrected.json()["current_session"]["elapsed_seconds"] == 4 * 3600
    assert ignored.json()["status"] == "outside"
    assert ignored.json()["current_session"] is None
    assert restored.json()["status"] == "working"
    assert restored.json()["current_session"]["elapsed_seconds"] == 6 * 3600


@pytest.mark.anyio
async def test_dashboard_api_rejects_unknown_timezone(tmp_path: Path):
    app = make_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get("/api/dashboard?timezone=Not%2FAZone")

    assert response.status_code == 422
