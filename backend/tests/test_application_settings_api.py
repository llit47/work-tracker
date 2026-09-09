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


def make_app(database_url: str):
    Base.metadata.create_all(build_engine(database_url))
    return create_app(Settings(webhook_token=TOKEN, database_url=database_url))


@pytest.mark.anyio
async def test_default_settings_and_unconfigured_location_fallback(tmp_path: Path):
    database_url = f"sqlite:///{tmp_path / 'settings.db'}"
    app = make_app(database_url)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/application-settings")
        await client.post(
            "/api/webhook/home-assistant",
            headers=HEADERS,
            json={
                "event": "entry",
                "location": "unknown_room",
                "timestamp": "2026-09-08T08:00:00+02:00",
                "source": "home_assistant",
            },
        )
        with_unknown = await client.get("/api/application-settings")

    assert response.status_code == 200
    assert response.json() == {
        "application_title": "Work Tracker",
        "locations": [
            {
                "location": "gabinet_zabki",
                "display_name": None,
                "timezone": None,
            }
        ],
    }
    assert {item["location"]: item for item in with_unknown.json()["locations"]} == {
        "gabinet_zabki": {
            "location": "gabinet_zabki",
            "display_name": None,
            "timezone": None,
        },
        "unknown_room": {
            "location": "unknown_room",
            "display_name": None,
            "timezone": None,
        },
    }


@pytest.mark.anyio
async def test_global_title_and_location_alias_persist_without_changing_raw_key(tmp_path: Path):
    database_url = f"sqlite:///{tmp_path / 'settings.db'}"
    app = make_app(database_url)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post(
            "/api/webhook/home-assistant",
            headers=HEADERS,
            json={
                "event": "entry",
                "location": "gabinet_zabki",
                "timestamp": "2026-09-08T08:00:00+02:00",
                "source": "home_assistant",
            },
        )
        updated = await client.put(
            "/api/application-settings",
            json={
                "application_title": "  Czas pracy Przemek  ",
                "locations": [
                    {
                        "location": "gabinet_zabki",
                        "display_name": "  Gabinet Ząbki  ",
                        "timezone": " Europe/Warsaw ",
                    }
                ],
            },
        )

    reloaded_app = create_app(Settings(webhook_token=TOKEN, database_url=database_url))
    async with AsyncClient(
        transport=ASGITransport(app=reloaded_app), base_url="http://test"
    ) as client:
        persisted = await client.get("/api/application-settings")
        raw_events = await client.get("/api/work-events?year=2026&month=9")

    assert updated.status_code == 200
    assert persisted.json() == {
        "application_title": "Czas pracy Przemek",
        "locations": [
            {
                "location": "gabinet_zabki",
                "display_name": "Gabinet Ząbki",
                "timezone": "Europe/Warsaw",
            }
        ],
    }
    assert raw_events.json()[0]["location"] == "gabinet_zabki"


@pytest.mark.anyio
async def test_settings_validation_and_alias_removal(tmp_path: Path):
    database_url = f"sqlite:///{tmp_path / 'settings.db'}"
    app = make_app(database_url)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        empty_title = await client.put(
            "/api/application-settings",
            json={"application_title": "   ", "locations": []},
        )
        invalid_location = await client.put(
            "/api/application-settings",
            json={
                "application_title": "Work Tracker",
                "locations": [{"location": "Bad Location", "display_name": "Gabinet"}],
            },
        )
        duplicate_location = await client.put(
            "/api/application-settings",
            json={
                "application_title": "Work Tracker",
                "locations": [
                    {"location": "gabinet_zabki", "display_name": "Gabinet"},
                    {
                        "location": "gabinet_zabki",
                        "display_name": "Gabinet drugi",
                    },
                ],
            },
        )
        await client.put(
            "/api/application-settings",
            json={
                "application_title": "Work Tracker",
                "locations": [
                    {"location": "gabinet_zabki", "display_name": "Gabinet Ząbki"}
                ],
            },
        )
        removed = await client.put(
            "/api/application-settings",
            json={
                "application_title": "Work Tracker",
                "locations": [{"location": "gabinet_zabki", "display_name": "   "}],
            },
        )

    assert empty_title.status_code == 422
    assert invalid_location.status_code == 422
    assert duplicate_location.status_code == 422
    assert removed.json()["locations"] == [
        {"location": "gabinet_zabki", "display_name": None, "timezone": None}
    ]


@pytest.mark.anyio
async def test_timezone_update_removal_and_legacy_payload_preservation(tmp_path: Path):
    database_url = f"sqlite:///{tmp_path / 'timezone-settings.db'}"
    app = make_app(database_url)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        configured = await client.put(
            "/api/application-settings",
            json={
                "application_title": "Work Tracker",
                "locations": [
                    {
                        "location": "gabinet_zabki",
                        "display_name": "Gabinet",
                        "timezone": "Europe/Warsaw",
                    }
                ],
            },
        )
        legacy_alias_update = await client.put(
            "/api/application-settings",
            json={
                "application_title": "Nowy tytuł",
                "locations": [
                    {"location": "gabinet_zabki", "display_name": "Nowy alias"}
                ],
            },
        )
        title_only_update = await client.put(
            "/api/application-settings",
            json={"application_title": "Tylko tytuł", "locations": []},
        )
        removed = await client.put(
            "/api/application-settings",
            json={
                "application_title": "Tylko tytuł",
                "locations": [
                    {
                        "location": "gabinet_zabki",
                        "display_name": "Nowy alias",
                        "timezone": None,
                    }
                ],
            },
        )

    assert configured.json()["locations"][0]["timezone"] == "Europe/Warsaw"
    assert legacy_alias_update.json()["locations"][0] == {
        "location": "gabinet_zabki",
        "display_name": "Nowy alias",
        "timezone": "Europe/Warsaw",
    }
    assert title_only_update.json()["locations"][0]["timezone"] == "Europe/Warsaw"
    assert removed.json()["locations"][0]["timezone"] is None


@pytest.mark.anyio
async def test_invalid_timezone_rejects_the_whole_settings_transaction(tmp_path: Path):
    database_url = f"sqlite:///{tmp_path / 'invalid-timezone.db'}"
    app = make_app(database_url)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.put(
            "/api/application-settings",
            json={
                "application_title": "Stan początkowy",
                "locations": [
                    {
                        "location": "gabinet_zabki",
                        "display_name": "Poprawny alias",
                        "timezone": "Europe/Warsaw",
                    }
                ],
            },
        )
        invalid = await client.put(
            "/api/application-settings",
            json={
                "application_title": "Nie może się zapisać",
                "locations": [
                    {
                        "location": "gabinet_zabki",
                        "display_name": "Też nie może się zapisać",
                        "timezone": "Mars/Olympus_Mons",
                    }
                ],
            },
        )
        persisted = await client.get("/api/application-settings")

    assert invalid.status_code == 422
    assert persisted.json() == {
        "application_title": "Stan początkowy",
        "locations": [
            {
                "location": "gabinet_zabki",
                "display_name": "Poprawny alias",
                "timezone": "Europe/Warsaw",
            }
        ],
    }
