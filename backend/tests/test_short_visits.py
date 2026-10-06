from csv import reader
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from io import StringIO

from conftest import authenticated_client
import pytest

from app.config import Settings
from app.corrections import CorrectionRecord, build_effective_event_stream
from app.dashboard import DashboardStatus, calculate_dashboard
from app.database import build_engine
from app.main import create_app
from app.models import Base, CorrectionType, PayRate, WorkEvent
from app.monthly_report import build_monthly_report
from app.pay import PayRateRecord, calculate_monthly_pay
from app.report_renderers import (
    CSV_HEADERS,
    _anomalies_table,
    _sessions_table,
    render_monthly_report_csv,
    render_monthly_report_pdf,
)
from app.work_time import RawWorkEvent, SessionStatus, calculate_monthly_work_time, derive_work_time_items


START = datetime.fromisoformat("2026-09-07T08:00:17+02:00")
NOW = START + timedelta(hours=2)
RATE = PayRateRecord(1, date(1970, 1, 1), Decimal("50.00"), "PLN")
TOKEN = "test-secret-that-is-at-least-32-characters"


def event(event_id, event_type, timestamp):
    return RawWorkEvent(
        id=event_id,
        event_type=event_type,
        location="gabinet_zabki",
        event_timestamp=timestamp,
        event_timestamp_utc=timestamp.astimezone(timezone.utc),
        received_at=NOW,
        source="home_assistant",
    )


def pair(seconds=300):
    return [event(1, "entry", START), event(2, "exit", START + timedelta(seconds=seconds))]


def correction(correction_type, *, raw_event_id=None, boundary=None):
    return CorrectionRecord(
        id=1,
        correction_type=correction_type,
        raw_event_id=raw_event_id,
        created_at=NOW,
        updated_at=NOW,
        event_type=boundary.event_type if correction_type is CorrectionType.MANUAL_EVENT else None,
        location=boundary.location if correction_type is CorrectionType.MANUAL_EVENT else None,
        event_timestamp=boundary.event_timestamp,
        event_timestamp_utc=boundary.event_timestamp_utc,
    )


@pytest.mark.parametrize("seconds, status", [
    (1, SessionStatus.SUPPRESSED_SHORT_VISIT),
    (37, SessionStatus.SUPPRESSED_SHORT_VISIT),
    (299, SessionStatus.SUPPRESSED_SHORT_VISIT),
    (300, SessionStatus.SUPPRESSED_SHORT_VISIT),
    (301, SessionStatus.VALID),
])
def test_exact_utc_threshold_retains_both_events_and_duration(seconds, status):
    events = pair(seconds)
    before = [raw.__dict__.copy() for raw in events]
    item, = derive_work_time_items(events)

    assert item.status is status
    assert item.duration_seconds == seconds
    assert item.events == tuple(events)
    assert not item.status.is_anomaly
    assert [raw.__dict__ for raw in events] == before


def test_threshold_uses_exact_delta_before_truncating_fractional_seconds():
    item, = derive_work_time_items(pair(300.001))
    assert item.status is SessionStatus.VALID
    assert item.duration_seconds == 300


def test_threshold_uses_instants_rather_than_local_clocks():
    events = pair(299)
    events[1] = replace(events[1], event_timestamp=events[1].event_timestamp.astimezone(timezone.utc))
    item, = derive_work_time_items(events)
    assert item.status is SessionStatus.SUPPRESSED_SHORT_VISIT
    assert item.duration_seconds == 299


@pytest.mark.parametrize("boundary_index", [0, 1])
@pytest.mark.parametrize("kind", [CorrectionType.MANUAL_EVENT, CorrectionType.TIMESTAMP_OVERRIDE])
def test_either_manual_or_timestamp_corrected_boundary_remains_valid(boundary_index, kind):
    events = pair(37)
    boundary = events[boundary_index]
    if kind is CorrectionType.MANUAL_EVENT:
        raw_events = [raw for raw in events if raw is not boundary]
        active_correction = correction(kind, boundary=boundary)
    else:
        raw_events = list(events)
        raw_events[boundary_index] = replace(
            boundary,
            event_timestamp=boundary.event_timestamp + timedelta(seconds=1),
            event_timestamp_utc=boundary.event_timestamp_utc + timedelta(seconds=1),
        )
        active_correction = correction(kind, raw_event_id=boundary.id, boundary=boundary)

    stream = build_effective_event_stream(raw_events, [active_correction])
    item, = derive_work_time_items(stream.events)
    assert item.status is SessionStatus.VALID
    assert item.duration_seconds == 37
    assert calculate_monthly_pay(calculate_monthly_work_time(stream.events, 2026, 9), [RATE]).total_pay == Decimal("0.51")
    report = build_monthly_report(stream.events, [RATE], year=2026, month=9, generated_at=NOW)
    assert len(report.sessions) == 1
    dashboard = calculate_dashboard(stream.events, [RATE], now=NOW, local_timezone=timezone.utc)
    assert dashboard.month.completed_duration_seconds == 37
    assert dashboard.month.pay == Decimal("0.51")
    # Undo removes only the correction, then policy uses the current raw stream.
    if kind is CorrectionType.TIMESTAMP_OVERRIDE:
        undone = build_effective_event_stream(raw_events, [])
        assert derive_work_time_items(undone.events)[0].status is SessionStatus.SUPPRESSED_SHORT_VISIT


def test_active_timestamp_override_exempts_pair_even_if_instant_is_unchanged():
    events = pair(37)
    stream = build_effective_event_stream(events, [
        correction(CorrectionType.TIMESTAMP_OVERRIDE, raw_event_id=1, boundary=events[0]),
    ])
    assert derive_work_time_items(stream.events)[0].status is SessionStatus.VALID


@pytest.mark.parametrize("events, status", [
    ([event(1, "entry", START)], SessionStatus.MISSING_EXIT),
    ([event(1, "exit", START)], SessionStatus.ORPHAN_EXIT),
    ([event(1, "entry", START), event(2, "entry", START + timedelta(seconds=1)), event(3, "exit", START + timedelta(seconds=2))], SessionStatus.DUPLICATE_ENTRY),
    ([event(1, "entry", START), event(2, "exit", START)], SessionStatus.AMBIGUOUS_TIMESTAMP),
    (pair(16 * 3600 + 1), SessionStatus.UNUSUALLY_LONG_SESSION),
])
def test_policy_never_masks_anomalies(events, status):
    item, = derive_work_time_items(events)
    assert item.status is status
    assert item.status.is_anomaly
    summary = calculate_monthly_work_time(events, 2026, 9)
    assert summary.anomaly_count == 1
    assert summary.total_duration_seconds == 0


def test_input_and_receipt_order_do_not_change_canonical_classification():
    entry, exit_event = pair(299)
    delayed_entry = replace(entry, id=2, received_at=NOW + timedelta(minutes=1))
    early_exit = replace(exit_event, id=1)
    expected = derive_work_time_items([delayed_entry, early_exit])
    assert derive_work_time_items([early_exit, delayed_entry]) == expected
    assert expected[0].status is SessionStatus.SUPPRESSED_SHORT_VISIT
    assert [raw.id for raw in expected[0].events] == [2, 1]


@pytest.mark.parametrize("include_work", [False, True])
def test_summary_pay_dashboard_and_reports_share_suppression(include_work):
    events = pair(299)
    if include_work:
        events += [event(3, "entry", START - timedelta(days=1)), event(4, "exit", START - timedelta(days=1) + timedelta(hours=1))]
    expected_duration = 3600 if include_work else 0
    expected_pay = Decimal("50.00") if include_work else Decimal("0.00")
    summary = calculate_monthly_work_time(events, 2026, 9)
    pay = calculate_monthly_pay(summary, [RATE])
    dashboard = calculate_dashboard(events, [RATE], now=NOW, local_timezone=timezone.utc)
    report = build_monthly_report(events, [RATE], year=2026, month=9, generated_at=NOW)

    assert summary.total_duration_seconds == pay.total_duration_seconds == report.total_duration_seconds == dashboard.month.completed_duration_seconds == expected_duration
    assert summary.work_days == pay.work_days == report.work_days == dashboard.month.work_days == int(include_work)
    assert summary.anomaly_count == report.anomaly_count == 0
    assert summary.days[-1].total_duration_seconds == summary.days[-1].anomaly_count == 0
    assert pay.total_pay == report.total_pay == dashboard.month.pay == expected_pay
    assert dashboard.status is DashboardStatus.OUTSIDE
    assert dashboard.current_session is None
    assert dashboard.today.completed_duration_seconds == dashboard.today.effective_duration_seconds == 0
    assert dashboard.today.running_duration_seconds is None
    assert len(report.sessions) == int(include_work)
    assert report.anomalies == ()
    csv_rows = list(reader(StringIO(render_monthly_report_csv(report).decode("utf-8-sig")), delimiter=";"))
    assert csv_rows[0] == list(CSV_HEADERS)
    assert len(csv_rows) == 1 + int(include_work)
    assert len(_sessions_table(report)._cellvalues) == 2 + int(include_work)  # header and total
    assert len(_anomalies_table(report)._cellvalues) == 1  # header only
    assert render_monthly_report_pdf(report).startswith(b"%PDF-")


def test_short_cross_month_visit_keeps_entry_ownership_for_audit():
    entry_time = datetime.fromisoformat("2026-09-30T23:59:37+02:00")
    events = [event(1, "entry", entry_time), event(2, "exit", entry_time + timedelta(seconds=299))]
    september = calculate_monthly_work_time(events, 2026, 9)
    october = calculate_monthly_work_time(events, 2026, 10)
    assert september.days[0].date == date(2026, 9, 30)
    assert september.days[0].items[0].status is SessionStatus.SUPPRESSED_SHORT_VISIT
    assert october.days == ()


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
@pytest.mark.parametrize("delivery", ["entry_first", "exit_first", "existing_history"])
async def test_http_ingestion_preserves_raw_history_and_all_consumers(tmp_path, delivery):
    database_url = f"sqlite:///{tmp_path / 'short-visits.db'}"
    Base.metadata.create_all(build_engine(database_url))
    app = create_app(Settings(webhook_token=TOKEN, database_url=database_url), now_provider=lambda: NOW)
    with app.state.session_factory() as session:
        session.add(PayRate(effective_from=RATE.effective_from, hourly_rate=RATE.hourly_rate, currency=RATE.currency, created_at=NOW))
        if delivery == "existing_history":
            for boundary in pair(299):
                session.add(WorkEvent(
                    event_type=boundary.event_type,
                    location=boundary.location,
                    event_timestamp=boundary.event_timestamp,
                    event_timestamp_utc=boundary.event_timestamp_utc,
                    received_at=boundary.received_at,
                    source=boundary.source,
                ))
        session.commit()

    async with authenticated_client(app, base_url="http://test") as client:
        events = [] if delivery == "existing_history" else pair(299)
        for boundary in reversed(events) if delivery == "exit_first" else events:
            response = await client.post("/api/webhook/home-assistant", headers={"X-Webhook-Token": TOKEN}, json={
                "event": boundary.event_type,
                "location": boundary.location,
                "timestamp": boundary.event_timestamp.isoformat(),
                "source": boundary.source,
            })
            assert response.status_code == 201
            assert response.json()["status"] == "accepted"
            if boundary.event_type == "entry" and delivery == "entry_first":
                dashboard = (await client.get("/api/dashboard?timezone=Europe/Warsaw")).json()
                assert dashboard["status"] == "working"
                assert dashboard["current_session"] is not None

        raw_before = (await client.get("/api/work-events?year=2026&month=9")).json()
        summary = (await client.get("/api/work-summary?year=2026&month=9")).json()
        item, = summary["days"][0]["items"]
        assert item["status"] == "suppressed_short_visit"
        assert item["duration_seconds"] == 299
        assert [raw["event_type"] for raw in item["events"]] == ["entry", "exit"]
        assert all(raw["correction_id"] is None and not raw["is_ignored"] for raw in item["events"])
        assert summary["total_duration_seconds"] == summary["work_days"] == summary["anomaly_count"] == 0
        pay = (await client.get("/api/pay-summary?year=2026&month=9")).json()
        assert pay["total_duration_seconds"] == pay["work_days"] == 0
        assert pay["total_pay"] == "0.00"
        dashboard = (await client.get("/api/dashboard?timezone=Europe/Warsaw")).json()
        assert dashboard["status"] == "outside"
        assert dashboard["current_session"] is None
        assert dashboard["month"]["pay"] == "0.00"
        csv_response = await client.get("/api/export/monthly.csv?year=2026&month=9")
        assert csv_response.status_code == 200
        assert list(reader(StringIO(csv_response.content.decode("utf-8-sig")), delimiter=";")) == [list(CSV_HEADERS)]
        pdf_response = await client.get("/api/export/monthly.pdf?year=2026&month=9")
        assert pdf_response.status_code == 200
        assert pdf_response.content.startswith(b"%PDF-")
        assert (await client.get("/api/corrections")).json() == []
        assert (await client.get("/api/work-events?year=2026&month=9")).json() == raw_before
