from csv import reader
from datetime import date, datetime, timezone
from decimal import Decimal
from io import StringIO
from pathlib import Path

from httpx import ASGITransport, AsyncClient
import pytest

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
    database_url = f"sqlite:///{tmp_path / 'export.db'}"
    Base.metadata.create_all(build_engine(database_url))
    app = create_app(Settings(webhook_token=TOKEN, database_url=database_url))
    with app.state.session_factory() as session:
        session.add(
            PayRate(
                effective_from=date(1970, 1, 1),
                hourly_rate=Decimal("50.00"),
                currency="PLN",
                created_at=datetime.now(timezone.utc),
            )
        )
        session.commit()
    return app


async def add_event(client: AsyncClient, event_type: str, timestamp: str):
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


@pytest.mark.anyio
async def test_csv_and_pdf_export_endpoints_return_downloadable_monthly_reports(tmp_path: Path):
    app = make_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await add_event(client, "entry", "2026-09-06T08:00:00+02:00")
        await add_event(client, "exit", "2026-09-06T16:30:00+02:00")
        csv_response = await client.get("/api/export/monthly.csv?year=2026&month=9")
        pdf_response = await client.get("/api/export/monthly.pdf?year=2026&month=9")

    assert csv_response.status_code == 200
    assert csv_response.headers["content-type"].startswith("text/csv")
    assert csv_response.headers["content-disposition"] == (
        'attachment; filename="work-tracker-2026-09.csv"'
    )
    assert csv_response.content.startswith(b"\xef\xbb\xbf")
    csv_rows = list(reader(StringIO(csv_response.content.decode("utf-8-sig")), delimiter=";"))
    assert csv_rows[0] == [
        "data",
        "wejście",
        "wyjście",
        "czas",
        "lokalizacja",
        "status",
        "stawka_godzinowa",
        "waluta",
        "wynagrodzenie",
    ]
    assert csv_rows[1] == [
        "2026-09-06",
        "2026-09-06T08:00+02:00",
        "2026-09-06T16:30+02:00",
        "08:30",
        "gabinet_zabki",
        "valid",
        "50.00",
        "PLN",
        "425.00",
    ]

    assert pdf_response.status_code == 200
    assert pdf_response.headers["content-type"] == "application/pdf"
    assert pdf_response.headers["content-disposition"] == (
        'attachment; filename="work-tracker-2026-09.pdf"'
    )
    assert pdf_response.content.startswith(b"%PDF-")
    assert len(pdf_response.content) > 1_000


@pytest.mark.anyio
async def test_empty_month_exports_header_only_csv_and_valid_pdf(tmp_path: Path):
    app = make_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        csv_response = await client.get("/api/export/monthly.csv?year=2026&month=8")
        pdf_response = await client.get("/api/export/monthly.pdf?year=2026&month=8")

    rows = list(reader(StringIO(csv_response.content.decode("utf-8-sig")), delimiter=";"))
    assert len(rows) == 1
    assert pdf_response.status_code == 200
    assert pdf_response.content.startswith(b"%PDF-")


@pytest.mark.anyio
async def test_export_endpoints_validate_month_parameters(tmp_path: Path):
    app = make_app(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        invalid_year = await client.get("/api/export/monthly.csv?year=1999&month=9")
        invalid_month = await client.get("/api/export/monthly.pdf?year=2026&month=13")

    assert invalid_year.status_code == 422
    assert invalid_month.status_code == 422
