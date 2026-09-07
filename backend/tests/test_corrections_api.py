from pathlib import Path

from httpx import ASGITransport, AsyncClient
import pytest

from app.config import Settings
from app.database import build_engine
from app.main import create_app
from app.models import Base

TOKEN = "test-secret-that-is-at-least-32-characters"
HEADERS = {"X-Webhook-Token": TOKEN}


@pytest.fixture
def anyio_backend():
    return "asyncio"


def make_app(tmp_path: Path):
    database_url = f"sqlite:///{tmp_path / 'test.db'}"
    Base.metadata.create_all(build_engine(database_url))
    return create_app(Settings(webhook_token=TOKEN, database_url=database_url))


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


async def summary(client: AsyncClient, year: int = 2026, month: int = 9) -> dict:
    response = await client.get(f"/api/work-summary?year={year}&month={month}")
    assert response.status_code == 200
    return response.json()


def all_items(month_summary: dict) -> list[dict]:
    return [item for day in month_summary["days"] for item in day["items"]]


@pytest.mark.anyio
async def test_timestamp_correction_changes_duration_and_preserves_raw_event(tmp_path: Path):
    async with AsyncClient(transport=ASGITransport(app=make_app(tmp_path)), base_url="http://testserver") as client:
        await add_raw_event(client, "entry", "2026-09-07T08:00:00+02:00")
        exit_id = await add_raw_event(client, "exit", "2026-09-07T23:59:00+02:00")
        raw_before = (await client.get("/api/work-events?year=2026&month=9")).json()

        correction_response = await client.put(
            f"/api/work-events/{exit_id}/timestamp-correction",
            json={"timestamp": "2026-09-07T16:00:00+02:00"},
        )
        month_summary = await summary(client)
        raw_after = (await client.get("/api/work-events?year=2026&month=9")).json()

        assert correction_response.status_code == 200
        correction_id = correction_response.json()["id"]
        item = all_items(month_summary)[0]
        corrected_exit = item["events"][1]
        assert item["status"] == "valid"
        assert item["duration_seconds"] == 8 * 3600
        assert corrected_exit["raw_event_id"] == exit_id
        assert corrected_exit["correction_id"] == correction_id
        assert corrected_exit["correction_type"] == "timestamp_override"
        assert corrected_exit["is_timestamp_corrected"] is True
        assert corrected_exit["event_timestamp"] == "2026-09-07T16:00:00+02:00"
        assert corrected_exit["original_event_timestamp"] == "2026-09-07T23:59:00+02:00"
        assert raw_after == raw_before

        replacement = await client.put(
            f"/api/work-events/{exit_id}/timestamp-correction",
            json={"timestamp": "2026-09-07T15:00:00+02:00"},
        )
        corrections = (await client.get("/api/corrections")).json()
        assert replacement.status_code == 200
        assert replacement.json()["id"] == correction_id
        assert len(corrections) == 1
        assert (await summary(client))["total_duration_seconds"] == 7 * 3600

        assert (await client.delete(f"/api/corrections/{correction_id}")).status_code == 204
        restored = await summary(client)
        restored_exit = all_items(restored)[0]["events"][1]
        assert restored["total_duration_seconds"] == 15 * 3600 + 59 * 60
        assert restored_exit["event_timestamp"] == "2026-09-07T23:59:00+02:00"
        assert restored_exit["is_timestamp_corrected"] is False


@pytest.mark.anyio
async def test_ignoring_duplicate_entry_resolves_anomaly_and_undo_restores_it(tmp_path: Path):
    async with AsyncClient(transport=ASGITransport(app=make_app(tmp_path)), base_url="http://testserver") as client:
        await add_raw_event(client, "entry", "2026-09-07T08:00:00+02:00")
        duplicate_id = await add_raw_event(client, "entry", "2026-09-07T08:01:00+02:00")
        await add_raw_event(client, "exit", "2026-09-07T16:00:00+02:00")
        raw_before = (await client.get("/api/work-events?year=2026&month=9")).json()
        assert all_items(await summary(client))[0]["status"] == "duplicate_entry"

        ignored = await client.put(f"/api/work-events/{duplicate_id}/ignore")
        corrected_summary = await summary(client)
        day = corrected_summary["days"][0]
        assert ignored.status_code == 200
        assert corrected_summary["total_duration_seconds"] == 8 * 3600
        assert all_items(corrected_summary)[0]["status"] == "valid"
        assert day["ignored_events"][0]["raw_event_id"] == duplicate_id
        assert day["ignored_events"][0]["is_ignored"] is True

        undo = await client.delete(f"/api/corrections/{ignored.json()['id']}")
        restored_summary = await summary(client)
        assert undo.status_code == 204
        assert all_items(restored_summary)[0]["status"] == "duplicate_entry"
        assert restored_summary["total_duration_seconds"] == 0
        assert (await client.get("/api/work-events?year=2026&month=9")).json() == raw_before


@pytest.mark.anyio
async def test_manual_exit_resolves_missing_exit_and_removal_restores_anomaly(tmp_path: Path):
    async with AsyncClient(transport=ASGITransport(app=make_app(tmp_path)), base_url="http://testserver") as client:
        await add_raw_event(client, "entry", "2026-09-07T08:00:00+02:00")
        assert all_items(await summary(client))[0]["status"] == "missing_exit"

        manual = await client.post(
            "/api/manual-events",
            json={
                "event": "exit",
                "location": "gabinet_zabki",
                "timestamp": "2026-09-07T16:00:00+02:00",
            },
        )
        corrected_summary = await summary(client)
        manual_exit = all_items(corrected_summary)[0]["events"][1]
        assert manual.status_code == 201
        assert corrected_summary["total_duration_seconds"] == 8 * 3600
        assert manual_exit["is_manual"] is True
        assert manual_exit["source"] == "manual"
        assert manual_exit["correction_id"] == manual.json()["id"]

        assert (await client.delete(f"/api/corrections/{manual.json()['id']}")).status_code == 204
        restored = await summary(client)
        assert all_items(restored)[0]["status"] == "missing_exit"
        assert restored["total_duration_seconds"] == 0


@pytest.mark.anyio
async def test_manual_entry_resolves_orphan_exit(tmp_path: Path):
    async with AsyncClient(transport=ASGITransport(app=make_app(tmp_path)), base_url="http://testserver") as client:
        await add_raw_event(client, "exit", "2026-09-07T16:00:00+02:00")
        assert all_items(await summary(client))[0]["status"] == "orphan_exit"

        response = await client.post(
            "/api/manual-events",
            json={
                "event": "entry",
                "location": "gabinet_zabki",
                "timestamp": "2026-09-07T08:00:00+02:00",
            },
        )
        corrected = await summary(client)
        assert response.status_code == 201
        assert all_items(corrected)[0]["status"] == "valid"
        assert corrected["total_duration_seconds"] == 8 * 3600


@pytest.mark.anyio
async def test_rejects_invalid_or_conflicting_corrections(tmp_path: Path):
    async with AsyncClient(transport=ASGITransport(app=make_app(tmp_path)), base_url="http://testserver") as client:
        raw_id = await add_raw_event(client, "entry", "2026-09-07T08:00:00+02:00")

        assert (
            await client.put(
                "/api/work-events/999/timestamp-correction",
                json={"timestamp": "2026-09-07T08:00:00+02:00"},
            )
        ).status_code == 404
        assert (await client.put("/api/work-events/999/ignore")).status_code == 404
        assert (
            await client.put(
                f"/api/work-events/{raw_id}/timestamp-correction",
                json={"timestamp": "2026-09-07T08:00:00"},
            )
        ).status_code == 422
        assert (
            await client.post(
                "/api/manual-events",
                json={"event": "pause", "location": "gabinet_zabki", "timestamp": "2026-09-07T08:00:00+02:00"},
            )
        ).status_code == 422
        assert (
            await client.post(
                "/api/manual-events",
                json={"event": "entry", "location": "gabinet_zabki", "timestamp": "1999-12-31T08:00:00+01:00"},
            )
        ).status_code == 422
        assert (
            await client.post(
                "/api/manual-events",
                json={"event": "entry", "location": "Bad Location", "timestamp": "2026-09-07T08:00:00+02:00"},
            )
        ).status_code == 422
        assert (
            await client.post(
                "/api/manual-events",
                json={"event": "entry", "location": "gabinet_zabki", "timestamp": "2026-09-07T08:00:00"},
            )
        ).status_code == 422

        ignored = await client.put(f"/api/work-events/{raw_id}/ignore")
        repeated_ignore = await client.put(f"/api/work-events/{raw_id}/ignore")
        conflict = await client.put(
            f"/api/work-events/{raw_id}/timestamp-correction",
            json={"timestamp": "2026-09-07T08:05:00+02:00"},
        )
        assert ignored.status_code == 200
        assert repeated_ignore.json()["id"] == ignored.json()["id"]
        assert conflict.status_code == 409
        assert len((await client.get("/api/corrections")).json()) == 1
        assert (await client.delete("/api/corrections/999")).status_code == 404


@pytest.mark.anyio
async def test_timestamp_correction_moves_session_between_months_by_effective_entry(tmp_path: Path):
    async with AsyncClient(transport=ASGITransport(app=make_app(tmp_path)), base_url="http://testserver") as client:
        entry_id = await add_raw_event(client, "entry", "2026-09-30T23:00:00+02:00")
        await add_raw_event(client, "exit", "2026-10-01T07:00:00+02:00")
        assert (await summary(client, month=9))["total_duration_seconds"] == 8 * 3600

        response = await client.put(
            f"/api/work-events/{entry_id}/timestamp-correction",
            json={"timestamp": "2026-10-01T00:10:00+02:00"},
        )
        september = await summary(client, month=9)
        october = await summary(client, month=10)
        assert response.status_code == 200
        assert september["total_duration_seconds"] == 0
        assert september["days"] == []
        assert october["total_duration_seconds"] == 6 * 3600 + 50 * 60
        assert october["days"][0]["date"] == "2026-10-01"
        effective_entry = all_items(october)[0]["events"][0]
        assert effective_entry["original_event_timestamp"] == "2026-09-30T23:00:00+02:00"
        assert effective_entry["event_timestamp"] == "2026-10-01T00:10:00+02:00"


@pytest.mark.anyio
async def test_duration_threshold_still_applies_after_timestamp_correction(tmp_path: Path):
    async with AsyncClient(transport=ASGITransport(app=make_app(tmp_path)), base_url="http://testserver") as client:
        await add_raw_event(client, "entry", "2026-09-07T00:00:00+02:00")
        exit_id = await add_raw_event(client, "exit", "2026-09-07T20:00:00+02:00")

        await client.put(
            f"/api/work-events/{exit_id}/timestamp-correction",
            json={"timestamp": "2026-09-07T16:00:00+02:00"},
        )
        exact_limit = await summary(client)
        assert all_items(exact_limit)[0]["status"] == "valid"
        assert exact_limit["total_duration_seconds"] == 16 * 3600

        await client.put(
            f"/api/work-events/{exit_id}/timestamp-correction",
            json={"timestamp": "2026-09-07T16:00:01+02:00"},
        )
        over_limit = await summary(client)
        assert all_items(over_limit)[0]["status"] == "unusually_long_session"
        assert over_limit["total_duration_seconds"] == 0
