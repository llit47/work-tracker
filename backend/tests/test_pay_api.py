from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from httpx import ASGITransport, AsyncClient
import pytest
from sqlalchemy import select

from app.config import Settings
from app.database import build_engine
from app.main import create_app
from app.models import Base, PayRate

TOKEN = "test-secret-that-is-at-least-32-characters"
HEADERS = {"X-Webhook-Token": TOKEN}


@pytest.fixture
def anyio_backend():
    return "asyncio"


def make_app(tmp_path: Path):
    database_url = f"sqlite:///{tmp_path / 'test.db'}"
    Base.metadata.create_all(build_engine(database_url))
    app = create_app(Settings(webhook_token=TOKEN, database_url=database_url))
    with app.state.session_factory() as session:
        session.add(
            PayRate(
                effective_from=date(1970, 1, 1),
                hourly_rate=Decimal("50.00"),
                currency="PLN",
                created_at=datetime(2026, 9, 7, tzinfo=timezone.utc),
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
async def test_pay_rate_api_lists_chronologically_and_creates_normalized_rate(tmp_path: Path):
    app = make_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        created = await client.post(
            "/api/pay-rates",
            json={"effective_from": "2027-01-01", "hourly_rate": "55.00", "currency": "pln"},
        )
        listed = await client.get("/api/pay-rates")

    assert created.status_code == 201
    assert created.json()["hourly_rate"] == "55.00"
    assert created.json()["currency"] == "PLN"
    assert [item["effective_from"] for item in listed.json()] == ["1970-01-01", "2027-01-01"]
    assert [item["hourly_rate"] for item in listed.json()] == ["50.00", "55.00"]


@pytest.mark.anyio
async def test_pay_rate_api_rejects_duplicate_and_invalid_values(tmp_path: Path):
    app = make_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        duplicate = await client.post(
            "/api/pay-rates",
            json={"effective_from": "1970-01-01", "hourly_rate": "55.00", "currency": "PLN"},
        )
        valid_boundary = await client.post(
            "/api/pay-rates",
            json={
                "effective_from": "2027-01-02",
                "hourly_rate": "1000000.00",
                "currency": "PLN",
            },
        )
        invalid_payloads = [
            {"effective_from": "2027-01-01", "hourly_rate": "0", "currency": "PLN"},
            {"effective_from": "2027-01-01", "hourly_rate": "-1", "currency": "PLN"},
            {"effective_from": "2027-01-01", "hourly_rate": "50.001", "currency": "PLN"},
            {"effective_from": "2027-01-01", "hourly_rate": 50.0, "currency": "PLN"},
            {"effective_from": "2027-01-01", "hourly_rate": "1000000.01", "currency": "PLN"},
            {"effective_from": "2027-01-01", "hourly_rate": "1e100", "currency": "PLN"},
            {
                "effective_from": "2027-01-01",
                "hourly_rate": "999999999999999999999999999999999999999999.99",
                "currency": "PLN",
            },
            {"effective_from": "2027-01-01", "hourly_rate": "50.00", "currency": "PL"},
            {"effective_from": "not-a-date", "hourly_rate": "50.00", "currency": "PLN"},
        ]
        invalid_responses = [
            await client.post("/api/pay-rates", json=payload) for payload in invalid_payloads
        ]

    assert duplicate.status_code == 409
    assert valid_boundary.status_code == 201
    assert valid_boundary.json()["hourly_rate"] == "1000000.00"
    assert all(response.status_code == 422 for response in invalid_responses)


@pytest.mark.anyio
async def test_pay_summary_rejects_mixed_currencies(tmp_path: Path):
    app = make_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        await client.post(
            "/api/pay-rates",
            json={"effective_from": "2026-09-15", "hourly_rate": "50.00", "currency": "EUR"},
        )
        await add_raw_event(client, "entry", "2026-09-14T08:00:00+02:00")
        await add_raw_event(client, "exit", "2026-09-14T09:00:00+02:00")
        await add_raw_event(client, "entry", "2026-09-15T08:00:00+02:00")
        await add_raw_event(client, "exit", "2026-09-15T09:00:00+02:00")
        response = await client.get("/api/pay-summary?year=2026&month=9")

    assert response.status_code == 409
    assert response.json()["detail"] == "Cannot total work sessions using different currencies"


@pytest.mark.anyio
async def test_pay_summary_returns_decimal_strings_and_rate_explanation(tmp_path: Path):
    app = make_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        await add_raw_event(client, "entry", "2026-09-06T08:00:00+02:00")
        await add_raw_event(client, "exit", "2026-09-06T16:30:00+02:00")
        response = await client.get("/api/pay-summary?year=2026&month=9")

    assert response.status_code == 200
    assert response.json() == {
        "year": 2026,
        "month": 9,
        "currency": "PLN",
        "total_duration_seconds": 30600,
        "work_days": 1,
        "total_pay": "425.00",
        "days": [{"date": "2026-09-06", "duration_seconds": 30600, "pay": "425.00"}],
        "rates_used": [
            {
                "id": 1,
                "effective_from": "1970-01-01",
                "hourly_rate": "50.00",
                "currency": "PLN",
            }
        ],
    }


@pytest.mark.anyio
async def test_pay_summary_uses_effective_events_from_manual_corrections(tmp_path: Path):
    app = make_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        await add_raw_event(client, "entry", "2026-09-07T08:00:00+02:00")
        exit_id = await add_raw_event(client, "exit", "2026-09-07T17:00:00+02:00")
        initial = await client.get("/api/pay-summary?year=2026&month=9")

        corrected = await client.put(
            f"/api/work-events/{exit_id}/timestamp-correction",
            json={"timestamp": "2026-09-07T16:00:00+02:00"},
        )
        after_correction = await client.get("/api/pay-summary?year=2026&month=9")

        await client.delete(f"/api/corrections/{corrected.json()['id']}")
        ignored = await client.put(f"/api/work-events/{exit_id}/ignore")
        after_ignore = await client.get("/api/pay-summary?year=2026&month=9")

        manual = await client.post(
            "/api/manual-events",
            json={
                "event": "exit",
                "location": "gabinet_zabki",
                "timestamp": "2026-09-07T16:00:00+02:00",
            },
        )
        after_manual = await client.get("/api/pay-summary?year=2026&month=9")

    assert initial.json()["total_pay"] == "450.00"
    assert corrected.status_code == 200
    assert after_correction.json()["total_pay"] == "400.00"
    assert ignored.status_code == 200
    assert after_ignore.json()["total_pay"] == "0.00"
    assert manual.status_code == 201
    assert after_manual.json()["total_pay"] == "400.00"

    with app.state.session_factory() as session:
        stored_rate = session.scalar(select(PayRate))
        assert isinstance(stored_rate.hourly_rate, Decimal)
