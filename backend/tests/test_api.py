from pathlib import Path

from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
import pytest

from app.config import Settings
from app.database import build_engine
from app.main import create_app
from app.models import Base, WorkEvent

TOKEN = "test-secret-that-is-at-least-32-characters"


def make_app(tmp_path: Path):
    database_url = f"sqlite:///{tmp_path / 'test.db'}"
    Base.metadata.create_all(build_engine(database_url))
    return create_app(Settings(webhook_token=TOKEN, database_url=database_url))


@pytest.fixture
def anyio_backend():
    return "asyncio"


def test_rejects_too_short_webhook_token_in_configuration():
    try:
        Settings(webhook_token="too-short")
    except ValidationError:
        return
    raise AssertionError("A webhook token shorter than 32 characters must be rejected")


def payload(event: str = "entry", timestamp: str = "2026-09-06T08:14:32+02:00") -> dict:
    return {"event": event, "location": "gabinet_zabki", "timestamp": timestamp, "source": "home_assistant"}


@pytest.mark.anyio
async def test_accepts_entry_and_persists_original_timestamp(tmp_path: Path):
    async with AsyncClient(transport=ASGITransport(app=make_app(tmp_path)), base_url="http://testserver") as client:
        response = await client.post("/api/webhook/home-assistant", json=payload(), headers={"X-Webhook-Token": TOKEN})
        assert response.status_code == 201
        events = (await client.get("/api/work-events?year=2026&month=9")).json()
    assert response.json() == {
        "id": 1,
        "status": "accepted",
        "event": "entry",
        "location": "gabinet_zabki",
        "location_display_name": "gabinet_zabki",
        "timestamp": "2026-09-06T08:14:32+02:00",
    }
    assert len(events) == 1
    assert events[0]["event_type"] == "entry"
    assert events[0]["event_timestamp"] == "2026-09-06T08:14:32+02:00"
    assert events[0]["received_at"].endswith("Z") or "+00:00" in events[0]["received_at"]


@pytest.mark.anyio
async def test_ingestion_response_uses_current_location_alias(tmp_path: Path):
    async with AsyncClient(
        transport=ASGITransport(app=make_app(tmp_path)), base_url="http://testserver"
    ) as client:
        settings_response = await client.put(
            "/api/application-settings",
            json={
                "application_title": "Work Tracker",
                "locations": [
                    {
                        "location": "gabinet_zabki",
                        "display_name": "ARTE Stomatologia",
                    }
                ],
            },
        )
        response = await client.post(
            "/api/webhook/home-assistant",
            json=payload(),
            headers={"X-Webhook-Token": TOKEN},
        )

    assert settings_response.status_code == 200
    assert response.status_code == 201
    assert response.json()["location_display_name"] == "ARTE Stomatologia"
    assert "id" in response.json()
    assert response.json()["status"] == "accepted"
    assert "raw_event_id" not in response.json()


@pytest.mark.anyio
async def test_accepts_exit(tmp_path: Path):
    async with AsyncClient(transport=ASGITransport(app=make_app(tmp_path)), base_url="http://testserver") as client:
        response = await client.post("/api/webhook/home-assistant", json=payload("exit"), headers={"X-Webhook-Token": TOKEN})
        assert response.status_code == 201
        assert (await client.get("/api/work-events?year=2026&month=9")).json()[0]["event_type"] == "exit"


@pytest.mark.anyio
async def test_rejects_invalid_event(tmp_path: Path):
    async with AsyncClient(transport=ASGITransport(app=make_app(tmp_path)), base_url="http://testserver") as client:
        response = await client.post("/api/webhook/home-assistant", json=payload("pause"), headers={"X-Webhook-Token": TOKEN})
    assert response.status_code == 422


@pytest.mark.anyio
async def test_rejects_missing_or_wrong_token(tmp_path: Path):
    async with AsyncClient(transport=ASGITransport(app=make_app(tmp_path)), base_url="http://testserver") as client:
        assert (await client.post("/api/webhook/home-assistant", json=payload())).status_code == 401
        assert (await client.post("/api/webhook/home-assistant", json=payload(), headers={"X-Webhook-Token": "wrong"})).status_code == 401


@pytest.mark.anyio
async def test_rejects_timestamp_without_timezone(tmp_path: Path):
    async with AsyncClient(transport=ASGITransport(app=make_app(tmp_path)), base_url="http://testserver") as client:
        response = await client.post(
            "/api/webhook/home-assistant",
            json=payload(timestamp="2026-09-06T08:14:32"),
            headers={"X-Webhook-Token": TOKEN},
        )
    assert response.status_code == 422


@pytest.mark.anyio
async def test_rejects_unexpected_source(tmp_path: Path):
    invalid_payload = payload()
    invalid_payload["source"] = "other_system"
    async with AsyncClient(transport=ASGITransport(app=make_app(tmp_path)), base_url="http://testserver") as client:
        response = await client.post(
            "/api/webhook/home-assistant", json=invalid_payload, headers={"X-Webhook-Token": TOKEN}
        )
    assert response.status_code == 422


@pytest.mark.anyio
async def test_lists_only_requested_month_in_chronological_order(tmp_path: Path):
    async with AsyncClient(transport=ASGITransport(app=make_app(tmp_path)), base_url="http://testserver") as client:
        headers = {"X-Webhook-Token": TOKEN}
        await client.post("/api/webhook/home-assistant", json=payload("exit", "2026-10-01T08:00:00+02:00"), headers=headers)
        await client.post("/api/webhook/home-assistant", json=payload("exit", "2026-09-06T12:00:00+02:00"), headers=headers)
        await client.post("/api/webhook/home-assistant", json=payload("entry", "2026-09-06T08:00:00+02:00"), headers=headers)
        events = (await client.get("/api/work-events?year=2026&month=9")).json()
    assert [event["event_type"] for event in events] == ["entry", "exit"]
    assert all(event["event_timestamp"].startswith("2026-09") for event in events)


@pytest.mark.anyio
async def test_sorts_correctly_across_daylight_saving_fall_back(tmp_path: Path):
    async with AsyncClient(transport=ASGITransport(app=make_app(tmp_path)), base_url="http://testserver") as client:
        headers = {"X-Webhook-Token": TOKEN}
        # 02:50 CEST is 00:50 UTC; 02:10 CET is later, at 01:10 UTC.
        await client.post("/api/webhook/home-assistant", json=payload("entry", "2026-10-25T02:50:00+02:00"), headers=headers)
        await client.post("/api/webhook/home-assistant", json=payload("exit", "2026-10-25T02:10:00+01:00"), headers=headers)
        events = (await client.get("/api/work-events?year=2026&month=10")).json()
    assert [event["event_type"] for event in events] == ["entry", "exit"]
