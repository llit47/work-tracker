from datetime import datetime, timedelta
from pathlib import Path

from httpx import ASGITransport, AsyncClient
import pytest

from app.config import Settings
from app.database import build_engine
from app.main import create_app
from app.models import Base

TOKEN = "test-secret-that-is-at-least-32-characters"


@pytest.fixture
def anyio_backend():
    return "asyncio"


def make_app(tmp_path: Path):
    database_url = f"sqlite:///{tmp_path / 'test.db'}"
    Base.metadata.create_all(build_engine(database_url))
    return create_app(Settings(webhook_token=TOKEN, database_url=database_url))


def payload(event_type: str, timestamp: str) -> dict[str, str]:
    return {
        "event": event_type,
        "location": "gabinet_zabki",
        "timestamp": timestamp,
        "source": "home_assistant",
    }


@pytest.mark.anyio
async def test_work_summary_handles_out_of_order_cross_month_session_without_changing_raw_events(tmp_path: Path):
    async with AsyncClient(transport=ASGITransport(app=make_app(tmp_path)), base_url="http://testserver") as client:
        headers = {"X-Webhook-Token": TOKEN}
        exit_response = await client.post(
            "/api/webhook/home-assistant",
            json=payload("exit", "2026-10-01T02:00:00+02:00"),
            headers=headers,
        )
        entry_response = await client.post(
            "/api/webhook/home-assistant",
            json=payload("entry", "2026-09-30T22:00:00+02:00"),
            headers=headers,
        )
        assert exit_response.status_code == 201
        assert entry_response.status_code == 201

        september_raw_before = (await client.get("/api/work-events?year=2026&month=9")).json()
        october_raw_before = (await client.get("/api/work-events?year=2026&month=10")).json()
        summary_response = await client.get("/api/work-summary?year=2026&month=9")
        september_raw_after = (await client.get("/api/work-events?year=2026&month=9")).json()
        october_raw_after = (await client.get("/api/work-events?year=2026&month=10")).json()

    assert summary_response.status_code == 200
    summary = summary_response.json()
    assert summary["total_duration_seconds"] == 4 * 3600
    assert summary["work_days"] == 1
    assert summary["anomaly_count"] == 0
    assert summary["days"][0]["date"] == "2026-09-30"
    assert summary["days"][0]["items"][0]["status"] == "valid"
    assert [event["event_type"] for event in summary["days"][0]["items"][0]["events"]] == ["entry", "exit"]
    utc_value = summary["days"][0]["items"][0]["events"][0]["event_timestamp_utc"]
    assert datetime.fromisoformat(utc_value.replace("Z", "+00:00")).utcoffset() == timedelta(0)
    assert september_raw_after == september_raw_before
    assert october_raw_after == october_raw_before


@pytest.mark.anyio
async def test_work_summary_validates_year_and_month(tmp_path: Path):
    async with AsyncClient(transport=ASGITransport(app=make_app(tmp_path)), base_url="http://testserver") as client:
        assert (await client.get("/api/work-summary?year=1999&month=9")).status_code == 422
        assert (await client.get("/api/work-summary?year=2026&month=13")).status_code == 422
