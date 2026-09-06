from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.database import build_engine
from app.main import create_app
from app.models import Base, WorkEvent

TOKEN = "test-secret"


def make_client(tmp_path: Path) -> TestClient:
    database_url = f"sqlite:///{tmp_path / 'test.db'}"
    Base.metadata.create_all(build_engine(database_url))
    app = create_app(Settings(webhook_token=TOKEN, database_url=database_url))
    return TestClient(app)


def payload(event: str = "entry", timestamp: str = "2026-09-06T08:14:32+02:00") -> dict:
    return {"event": event, "location": "gabinet_zabki", "timestamp": timestamp, "source": "home_assistant"}


def test_accepts_entry_and_persists_original_timestamp(tmp_path: Path):
    client = make_client(tmp_path)
    response = client.post("/api/webhook/home-assistant", json=payload(), headers={"X-Webhook-Token": TOKEN})
    assert response.status_code == 201
    events = client.get("/api/work-events?year=2026&month=9").json()
    assert len(events) == 1
    assert events[0]["event_type"] == "entry"
    assert events[0]["event_timestamp"] == "2026-09-06T08:14:32+02:00"
    assert events[0]["received_at"].endswith("Z") or "+00:00" in events[0]["received_at"]


def test_accepts_exit(tmp_path: Path):
    client = make_client(tmp_path)
    response = client.post("/api/webhook/home-assistant", json=payload("exit"), headers={"X-Webhook-Token": TOKEN})
    assert response.status_code == 201
    assert client.get("/api/work-events?year=2026&month=9").json()[0]["event_type"] == "exit"


def test_rejects_invalid_event(tmp_path: Path):
    response = make_client(tmp_path).post("/api/webhook/home-assistant", json=payload("pause"), headers={"X-Webhook-Token": TOKEN})
    assert response.status_code == 422


def test_rejects_missing_or_wrong_token(tmp_path: Path):
    client = make_client(tmp_path)
    assert client.post("/api/webhook/home-assistant", json=payload()).status_code == 401
    assert client.post("/api/webhook/home-assistant", json=payload(), headers={"X-Webhook-Token": "wrong"}).status_code == 401


def test_rejects_timestamp_without_timezone(tmp_path: Path):
    response = make_client(tmp_path).post(
        "/api/webhook/home-assistant",
        json=payload(timestamp="2026-09-06T08:14:32"),
        headers={"X-Webhook-Token": TOKEN},
    )
    assert response.status_code == 422


def test_lists_only_requested_month_in_chronological_order(tmp_path: Path):
    client = make_client(tmp_path)
    headers = {"X-Webhook-Token": TOKEN}
    client.post("/api/webhook/home-assistant", json=payload("exit", "2026-10-01T08:00:00+02:00"), headers=headers)
    client.post("/api/webhook/home-assistant", json=payload("exit", "2026-09-06T12:00:00+02:00"), headers=headers)
    client.post("/api/webhook/home-assistant", json=payload("entry", "2026-09-06T08:00:00+02:00"), headers=headers)
    events = client.get("/api/work-events?year=2026&month=9").json()
    assert [event["event_type"] for event in events] == ["entry", "exit"]
    assert all(event["event_timestamp"].startswith("2026-09") for event in events)
