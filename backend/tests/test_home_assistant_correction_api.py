from datetime import datetime, timezone
from pathlib import Path

from httpx import ASGITransport, AsyncClient
import pytest

from app.config import Settings
from app.database import build_engine
from app.main import create_app
from app.models import Base, LocationTimezone

TOKEN = "test-secret-that-is-at-least-32-characters"
HEADERS = {"X-Webhook-Token": TOKEN}


@pytest.fixture
def anyio_backend():
    return "asyncio"


def make_app(tmp_path: Path):
    database_url = f"sqlite:///{tmp_path / 'ha-correction.db'}"
    Base.metadata.create_all(build_engine(database_url))
    return create_app(Settings(webhook_token=TOKEN, database_url=database_url))


async def configure_location(
    client: AsyncClient,
    *,
    timezone_name: str | None = "Europe/Warsaw",
    display_name: str | None = "ARTE Stomatologia",
):
    response = await client.put(
        "/api/application-settings",
        json={
            "application_title": "Work Tracker",
            "locations": [
                {
                    "location": "gabinet_zabki",
                    "display_name": display_name,
                    "timezone": timezone_name,
                }
            ],
        },
    )
    assert response.status_code == 200


async def add_raw_event(
    client: AsyncClient,
    *,
    event_type: str = "entry",
    timestamp: str = "2026-09-09T07:20:14+02:00",
) -> int:
    response = await client.post(
        "/api/webhook/home-assistant",
        headers=HEADERS,
        json={
            "event": event_type,
            "location": "gabinet_zabki",
            "timestamp": timestamp,
            "source": "home_assistant",
        },
    )
    assert response.status_code == 201
    return response.json()["id"]


async def correct(client: AsyncClient, raw_event_id: int, value: str, headers=HEADERS):
    return await client.post(
        "/api/webhook/home-assistant/correction",
        headers=headers,
        json={"raw_event_id": raw_event_id, "time": value},
    )


@pytest.mark.anyio
async def test_correction_endpoint_requires_the_webhook_token(tmp_path: Path):
    async with AsyncClient(
        transport=ASGITransport(app=make_app(tmp_path)), base_url="http://test"
    ) as client:
        await configure_location(client)
        raw_event_id = await add_raw_event(client)

        missing = await correct(client, raw_event_id, "7:45", headers={})
        wrong = await correct(
            client,
            raw_event_id,
            "7:45",
            headers={"X-Webhook-Token": "wrong"},
        )
        accepted = await correct(client, raw_event_id, "7:45")

    assert missing.status_code == 401
    assert wrong.status_code == 401
    assert accepted.status_code == 200


@pytest.mark.anyio
async def test_exact_raw_instant_is_a_no_op_without_a_correction_record(tmp_path: Path):
    async with AsyncClient(
        transport=ASGITransport(app=make_app(tmp_path)), base_url="http://test"
    ) as client:
        await configure_location(client)
        raw_event_id = await add_raw_event(
            client, timestamp="2026-09-09T07:20:00+02:00"
        )
        raw_before = (await client.get("/api/work-events?year=2026&month=9")).json()

        response = await correct(client, raw_event_id, "07:20")
        corrections = (await client.get("/api/corrections")).json()
        raw_after = (await client.get("/api/work-events?year=2026&month=9")).json()

    assert response.status_code == 200
    assert response.json()["correction_id"] is None
    assert response.json()["effective_timestamp"] == "2026-09-09T07:20:00+02:00"
    assert corrections == []
    assert raw_after == raw_before


@pytest.mark.anyio
async def test_no_op_comparison_uses_utc_instants_not_timestamp_strings(tmp_path: Path):
    async with AsyncClient(
        transport=ASGITransport(app=make_app(tmp_path)), base_url="http://test"
    ) as client:
        await configure_location(client)
        raw_event_id = await add_raw_event(
            client, timestamp="2026-09-09T05:20:00+00:00"
        )

        response = await correct(client, raw_event_id, "07:20")
        corrections = (await client.get("/api/corrections")).json()

    assert response.status_code == 200
    assert response.json()["correction_id"] is None
    assert response.json()["effective_timestamp"] == "2026-09-09T05:20:00Z"
    assert corrections == []


@pytest.mark.anyio
async def test_returning_to_raw_instant_removes_existing_timestamp_override(
    tmp_path: Path,
):
    async with AsyncClient(
        transport=ASGITransport(app=make_app(tmp_path)), base_url="http://test"
    ) as client:
        await configure_location(client)
        raw_event_id = await add_raw_event(
            client, timestamp="2026-09-09T07:20:00+02:00"
        )
        raw_before = (await client.get("/api/work-events?year=2026&month=9")).json()
        created = await correct(client, raw_event_id, "08:00")

        removed = await correct(client, raw_event_id, "07:20")
        corrections = (await client.get("/api/corrections")).json()
        work_summary = (await client.get("/api/work-summary?year=2026&month=9")).json()
        effective_event = work_summary["days"][0]["items"][0]["events"][0]
        raw_after = (await client.get("/api/work-events?year=2026&month=9")).json()

    assert created.status_code == 200
    assert created.json()["correction_id"] is not None
    assert removed.status_code == 200
    assert removed.json()["correction_id"] is None
    assert removed.json()["effective_timestamp"] == "2026-09-09T07:20:00+02:00"
    assert corrections == []
    assert effective_event["event_timestamp"] == "2026-09-09T07:20:00+02:00"
    assert effective_event["correction_id"] is None
    assert effective_event["is_timestamp_corrected"] is False
    assert raw_after == raw_before


@pytest.mark.anyio
async def test_same_minute_with_different_seconds_still_creates_an_override(tmp_path: Path):
    async with AsyncClient(
        transport=ASGITransport(app=make_app(tmp_path)), base_url="http://test"
    ) as client:
        await configure_location(client)
        raw_event_id = await add_raw_event(
            client, timestamp="2026-09-09T07:20:14+02:00"
        )
        raw_before = (await client.get("/api/work-events?year=2026&month=9")).json()

        response = await correct(client, raw_event_id, "07:20")
        corrections = (await client.get("/api/corrections")).json()
        raw_after = (await client.get("/api/work-events?year=2026&month=9")).json()

    assert response.status_code == 200
    assert response.json()["correction_id"] is not None
    assert response.json()["effective_timestamp"] == "2026-09-09T07:20:00+02:00"
    assert len(corrections) == 1
    assert corrections[0]["correction_type"] == "timestamp_override"
    assert raw_after == raw_before


@pytest.mark.anyio
async def test_correction_creates_and_updates_one_override_without_mutating_raw_event(
    tmp_path: Path,
):
    async with AsyncClient(
        transport=ASGITransport(app=make_app(tmp_path)), base_url="http://test"
    ) as client:
        await configure_location(client)
        raw_event_id = await add_raw_event(client)
        raw_before = (await client.get("/api/work-events?year=2026&month=9")).json()[0]

        created = await correct(client, raw_event_id, " 8.00 ")
        created_correction = (await client.get("/api/corrections")).json()[0]
        updated = await correct(client, raw_event_id, "07:45")
        corrections = (await client.get("/api/corrections")).json()
        raw_after = (await client.get("/api/work-events?year=2026&month=9")).json()[0]

    assert created.status_code == 200
    assert created.json() == {
        "raw_event_id": raw_event_id,
        "correction_id": created.json()["correction_id"],
        "event": "entry",
        "location": "gabinet_zabki",
        "location_display_name": "ARTE Stomatologia",
        "original_timestamp": "2026-09-09T07:20:14+02:00",
        "effective_timestamp": "2026-09-09T08:00:00+02:00",
    }
    assert updated.status_code == 200
    assert updated.json()["correction_id"] == created.json()["correction_id"]
    assert updated.json()["effective_timestamp"] == "2026-09-09T07:45:00+02:00"
    assert len(corrections) == 1
    assert corrections[0]["id"] == created_correction["id"]
    assert corrections[0]["created_at"] == created_correction["created_at"]
    assert corrections[0]["updated_at"] > created_correction["updated_at"]
    assert corrections[0]["event_timestamp_utc"] == "2026-09-09T05:45:00Z"
    assert raw_after == raw_before


@pytest.mark.anyio
async def test_correction_uses_canonical_location_fallback_without_alias(tmp_path: Path):
    async with AsyncClient(
        transport=ASGITransport(app=make_app(tmp_path)), base_url="http://test"
    ) as client:
        await configure_location(client, display_name=None)
        raw_event_id = await add_raw_event(client)
        response = await correct(client, raw_event_id, "7:45")

    assert response.status_code == 200
    assert response.json()["location_display_name"] == "gabinet_zabki"


@pytest.mark.anyio
async def test_missing_timezone_and_invalid_time_do_not_create_a_correction(tmp_path: Path):
    async with AsyncClient(
        transport=ASGITransport(app=make_app(tmp_path)), base_url="http://test"
    ) as client:
        raw_event_id = await add_raw_event(client)
        missing_timezone = await correct(client, raw_event_id, "7:45")
        await configure_location(client)
        invalid_time = await correct(client, raw_event_id, "7:5")
        corrections = await client.get("/api/corrections")

    assert missing_timezone.status_code == 422
    assert missing_timezone.json()["detail"]["code"] == "location_timezone_missing"
    assert invalid_time.status_code == 422
    assert invalid_time.json()["detail"]["code"] == "invalid_time"
    assert corrections.json() == []


@pytest.mark.anyio
async def test_unknown_raw_event_returns_stable_not_found_error(tmp_path: Path):
    async with AsyncClient(
        transport=ASGITransport(app=make_app(tmp_path)), base_url="http://test"
    ) as client:
        response = await correct(client, 999, "7:45")

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "raw_event_not_found"


@pytest.mark.anyio
async def test_ignore_conflict_is_preserved_and_returns_stable_conflict_error(tmp_path: Path):
    async with AsyncClient(
        transport=ASGITransport(app=make_app(tmp_path)), base_url="http://test"
    ) as client:
        await configure_location(client)
        raw_event_id = await add_raw_event(
            client, timestamp="2026-09-09T07:20:00+02:00"
        )
        ignored = await client.put(f"/api/work-events/{raw_event_id}/ignore")
        conflict = await correct(client, raw_event_id, "07:20")
        corrections = (await client.get("/api/corrections")).json()

    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "correction_conflict"
    assert corrections == [ignored.json()]


@pytest.mark.anyio
async def test_out_of_range_time_returns_stable_error_without_mutation(tmp_path: Path):
    async with AsyncClient(
        transport=ASGITransport(app=make_app(tmp_path)), base_url="http://test"
    ) as client:
        await configure_location(client)
        raw_event_id = await add_raw_event(client, timestamp="2026-09-09T14:00:00+02:00")
        response = await correct(client, raw_event_id, "18:01")
        corrections = (await client.get("/api/corrections")).json()

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "time_out_of_range"
    assert corrections == []


@pytest.mark.anyio
async def test_invalid_replacement_leaves_existing_override_unchanged(tmp_path: Path):
    async with AsyncClient(
        transport=ASGITransport(app=make_app(tmp_path)), base_url="http://test"
    ) as client:
        await configure_location(client)
        raw_event_id = await add_raw_event(client)
        accepted = await correct(client, raw_event_id, "07:45")
        before = (await client.get("/api/corrections")).json()
        rejected = await correct(client, raw_event_id, "24:00")
        after = (await client.get("/api/corrections")).json()

    assert accepted.status_code == 200
    assert rejected.status_code == 422
    assert rejected.json()["detail"]["code"] == "invalid_time"
    assert after == before


@pytest.mark.anyio
async def test_corrupted_persisted_timezone_fails_safe(tmp_path: Path):
    app = make_app(tmp_path)
    with app.state.session_factory() as session:
        session.add(
            LocationTimezone(
                location="gabinet_zabki",
                timezone="Mars/Olympus_Mons",
                updated_at=datetime.now(timezone.utc),
            )
        )
        session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        raw_event_id = await add_raw_event(client)
        response = await correct(client, raw_event_id, "7:45")
        corrections = (await client.get("/api/corrections")).json()

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "location_timezone_invalid"
    assert corrections == []
