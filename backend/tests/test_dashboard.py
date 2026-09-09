from datetime import date, datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.corrections import CorrectionRecord, build_effective_event_stream
from app.dashboard import DashboardStatus, calculate_dashboard
from app.models import CorrectionType
from app.pay import PayRateRecord
from app.work_time import MAX_SESSION_DURATION, RawWorkEvent


NOW = datetime.fromisoformat("2026-09-07T12:00:00+00:00")
WARSAW = ZoneInfo("Europe/Warsaw")
RATE = PayRateRecord(
    id=1,
    effective_from=date(1970, 1, 1),
    hourly_rate=Decimal("50.00"),
    currency="PLN",
)


def event(event_id: int, event_type: str, timestamp: str) -> RawWorkEvent:
    local_timestamp = datetime.fromisoformat(timestamp)
    return RawWorkEvent(
        id=event_id,
        event_type=event_type,
        location="gabinet_zabki",
        event_timestamp=local_timestamp,
        event_timestamp_utc=local_timestamp.astimezone(timezone.utc),
        received_at=NOW,
        source="home_assistant",
    )


def dashboard(
    events: list[RawWorkEvent],
    *,
    now: datetime = NOW,
    local_timezone=WARSAW,
    rates: list[PayRateRecord] | None = None,
):
    return calculate_dashboard(
        events,
        rates or [RATE],
        now=now,
        local_timezone=local_timezone,
    )


def correction(
    correction_id: int,
    correction_type: CorrectionType,
    *,
    raw_event_id: int | None = None,
    event_type: str | None = None,
    timestamp: str | None = None,
) -> CorrectionRecord:
    event_timestamp = datetime.fromisoformat(timestamp) if timestamp else None
    return CorrectionRecord(
        id=correction_id,
        correction_type=correction_type,
        created_at=NOW,
        updated_at=NOW,
        raw_event_id=raw_event_id,
        event_type=event_type,
        event_timestamp=event_timestamp,
        event_timestamp_utc=(
            event_timestamp.astimezone(timezone.utc) if event_timestamp else None
        ),
        location="gabinet_zabki" if correction_type is CorrectionType.MANUAL_EVENT else None,
    )


def test_dashboard_is_outside_when_no_open_entry_exists():
    result = dashboard(
        [
            event(1, "entry", "2026-09-07T08:00:00+02:00"),
            event(2, "exit", "2026-09-07T12:00:00+02:00"),
        ]
    )

    assert result.status is DashboardStatus.OUTSIDE
    assert result.current_session is None
    assert result.today.completed_duration_seconds == 4 * 3600
    assert result.today.running_duration_seconds is None
    assert result.today.effective_duration_seconds == 4 * 3600
    assert result.month.completed_duration_seconds == 4 * 3600
    assert result.month.work_days == 1
    assert result.month.pay == Decimal("200.00")


def test_future_entry_and_exit_are_ambiguous_and_excluded_from_dashboard_totals():
    result = dashboard(
        [
            event(1, "entry", "2026-09-07T14:00:00+00:00"),
            event(2, "exit", "2026-09-07T16:00:00+00:00"),
        ]
    )

    assert result.status is DashboardStatus.AMBIGUOUS
    assert result.current_session is None
    assert result.today.completed_duration_seconds == 0
    assert result.today.running_duration_seconds is None
    assert result.today.effective_duration_seconds == 0
    assert result.month.completed_duration_seconds == 0
    assert result.month.work_days == 0
    assert result.month.pay == Decimal("0.00")


def test_past_entry_with_future_exit_is_not_finalized_or_paid_by_dashboard():
    result = dashboard(
        [
            event(1, "entry", "2026-09-07T10:00:00+00:00"),
            event(2, "exit", "2026-09-07T14:00:00+00:00"),
        ]
    )

    assert result.status is DashboardStatus.AMBIGUOUS
    assert result.current_session is None
    assert result.today.completed_duration_seconds == 0
    assert result.today.effective_duration_seconds == 0
    assert result.month.completed_duration_seconds == 0
    assert result.month.pay == Decimal("0.00")


def test_future_orphan_exit_remains_ambiguous():
    result = dashboard([event(1, "exit", "2026-09-07T14:00:00+00:00")])

    assert result.status is DashboardStatus.AMBIGUOUS
    assert result.current_session is None
    assert result.today.effective_duration_seconds == 0


def test_single_future_open_entry_is_a_pending_start_while_current_state_is_outside():
    result = dashboard([event(1, "entry", "2026-09-07T14:00:00+00:00")])

    assert result.status is DashboardStatus.OUTSIDE
    assert result.current_session is None
    assert result.today.running_duration_seconds is None
    assert result.today.effective_duration_seconds == 0
    assert result.month.completed_duration_seconds == 0
    assert result.month.pay == Decimal("0.00")


def test_future_effective_entry_starts_exactly_at_its_corrected_instant():
    raw_entry = event(1, "entry", "2026-09-07T07:20:00+02:00")
    corrected_stream = build_effective_event_stream(
        [raw_entry],
        [
            correction(
                1,
                CorrectionType.TIMESTAMP_OVERRIDE,
                raw_event_id=1,
                timestamp="2026-09-07T08:00:00+02:00",
            )
        ],
    )

    before = dashboard(
        list(corrected_stream.events),
        now=datetime.fromisoformat("2026-09-07T05:21:00+00:00"),
    )
    at_start = dashboard(
        list(corrected_stream.events),
        now=datetime.fromisoformat("2026-09-07T06:00:00+00:00"),
    )
    later = dashboard(
        list(corrected_stream.events),
        now=datetime.fromisoformat("2026-09-07T06:30:00+00:00"),
    )

    assert before.status is DashboardStatus.OUTSIDE
    assert before.current_session is None
    assert before.today.running_duration_seconds is None
    assert at_start.status is DashboardStatus.WORKING
    assert at_start.current_session is not None
    assert at_start.current_session.elapsed_seconds == 0
    assert later.status is DashboardStatus.WORKING
    assert later.current_session is not None
    assert later.current_session.elapsed_seconds == 30 * 60


def test_future_entry_does_not_hide_an_existing_open_entry():
    result = dashboard(
        [
            event(1, "entry", "2026-09-07T10:00:00+00:00"),
            event(2, "entry", "2026-09-07T14:00:00+00:00"),
        ]
    )

    assert result.status is DashboardStatus.AMBIGUOUS
    assert result.current_session is None


def test_multiple_future_entries_remain_ambiguous():
    result = dashboard(
        [
            event(1, "entry", "2026-09-07T14:00:00+00:00"),
            event(2, "entry", "2026-09-07T15:00:00+00:00"),
        ]
    )

    assert result.status is DashboardStatus.AMBIGUOUS
    assert result.current_session is None


def test_dashboard_combines_completed_today_with_one_running_shift_without_paying_it():
    result = dashboard(
        [
            event(1, "entry", "2026-09-07T08:00:00+02:00"),
            event(2, "exit", "2026-09-07T12:00:00+02:00"),
            event(3, "entry", "2026-09-07T13:00:00+02:00"),
        ]
    )

    assert result.status is DashboardStatus.WORKING
    assert result.current_session is not None
    assert result.current_session.entry_timestamp.isoformat() == "2026-09-07T13:00:00+02:00"
    assert result.current_session.elapsed_seconds == 3600
    assert result.today.completed_duration_seconds == 4 * 3600
    assert result.today.running_duration_seconds == 3600
    assert result.today.effective_duration_seconds == 5 * 3600
    assert result.month.completed_duration_seconds == 4 * 3600
    assert result.month.pay == Decimal("200.00")


def test_duplicate_entries_produce_ambiguous_status_without_guessed_time():
    result = dashboard(
        [
            event(1, "entry", "2026-09-07T10:00:00+02:00"),
            event(2, "entry", "2026-09-07T10:01:00+02:00"),
        ]
    )

    assert result.status is DashboardStatus.AMBIGUOUS
    assert result.current_session is None
    assert result.today.effective_duration_seconds == 0


def test_identical_entry_and_exit_timestamp_produces_ambiguous_status():
    result = dashboard(
        [
            event(1, "entry", "2026-09-07T10:00:00+02:00"),
            event(2, "exit", "2026-09-07T10:00:00+02:00"),
        ]
    )

    assert result.status is DashboardStatus.AMBIGUOUS
    assert result.current_session is None


def test_open_entry_is_working_at_16_hours_but_ambiguous_after_limit():
    entry = event(1, "entry", "2026-09-06T22:00:00+02:00")
    exactly_at_limit = dashboard(
        [entry], now=datetime.fromisoformat("2026-09-07T12:00:00+00:00")
    )
    over_limit = dashboard(
        [entry], now=datetime.fromisoformat("2026-09-07T12:00:01+00:00")
    )

    assert MAX_SESSION_DURATION.total_seconds() == 16 * 3600
    assert exactly_at_limit.status is DashboardStatus.WORKING
    assert exactly_at_limit.current_session is not None
    assert exactly_at_limit.current_session.elapsed_seconds == 16 * 3600
    assert over_limit.status is DashboardStatus.AMBIGUOUS
    assert over_limit.current_session is None


def test_corrections_and_manual_events_drive_the_effective_live_state():
    raw_entry = event(1, "entry", "2026-09-07T08:00:00+02:00")
    corrected_stream = build_effective_event_stream(
        [raw_entry],
        [
            correction(
                1,
                CorrectionType.TIMESTAMP_OVERRIDE,
                raw_event_id=1,
                timestamp="2026-09-07T10:00:00+02:00",
            )
        ],
    )
    ignored_stream = build_effective_event_stream(
        [raw_entry],
        [correction(2, CorrectionType.IGNORE_EVENT, raw_event_id=1)],
    )
    manual_stream = build_effective_event_stream(
        [],
        [
            correction(
                3,
                CorrectionType.MANUAL_EVENT,
                event_type="entry",
                timestamp="2026-09-07T11:00:00+02:00",
            )
        ],
    )

    corrected = dashboard(list(corrected_stream.events))
    ignored = dashboard(list(ignored_stream.events))
    manual = dashboard(list(manual_stream.events))

    assert corrected.status is DashboardStatus.WORKING
    assert corrected.current_session is not None
    assert corrected.current_session.elapsed_seconds == 4 * 3600
    assert ignored.status is DashboardStatus.OUTSIDE
    assert manual.status is DashboardStatus.WORKING
    assert manual.current_session is not None
    assert manual.current_session.elapsed_seconds == 3 * 3600
    assert raw_entry.event_timestamp.isoformat() == "2026-09-07T08:00:00+02:00"


def test_live_elapsed_duration_uses_utc_across_dst_fallback():
    result = dashboard(
        [event(1, "entry", "2026-10-25T02:30:00+02:00")],
        now=datetime.fromisoformat("2026-10-25T02:30:00+00:00"),
    )

    assert result.status is DashboardStatus.WORKING
    assert result.current_session is not None
    assert result.current_session.elapsed_seconds == 2 * 3600


def test_cross_midnight_running_shift_stays_owned_by_entry_day():
    result = dashboard(
        [event(1, "entry", "2026-09-06T23:00:00+02:00")],
        now=datetime.fromisoformat("2026-09-07T01:00:00+00:00"),
    )

    assert result.status is DashboardStatus.WORKING
    assert result.current_session is not None
    assert result.current_session.elapsed_seconds == 4 * 3600
    assert result.today.date.isoformat() == "2026-09-07"
    assert result.today.running_duration_seconds is None
    assert result.today.effective_duration_seconds == 0
    assert result.month.completed_duration_seconds == 0
    assert result.month.pay == Decimal("0.00")


def test_dashboard_uses_requested_timezone_for_completed_day_month_and_pay():
    los_angeles = ZoneInfo("America/Los_Angeles")
    result = dashboard(
        [
            event(1, "entry", "2026-09-01T00:30:00+02:00"),
            event(2, "exit", "2026-09-01T01:30:00+02:00"),
        ],
        now=datetime.fromisoformat("2026-09-01T00:00:00+00:00"),
        local_timezone=los_angeles,
        rates=[
            RATE,
            PayRateRecord(2, date(2026, 9, 1), Decimal("100.00"), "PLN"),
        ],
    )

    assert result.today.date == date(2026, 8, 31)
    assert result.today.completed_duration_seconds == 3600
    assert result.today.effective_duration_seconds == 3600
    assert result.month.year == 2026
    assert result.month.month == 8
    assert result.month.completed_duration_seconds == 3600
    assert result.month.work_days == 1
    assert result.month.pay == Decimal("50.00")


def test_running_shift_uses_requested_timezone_to_decide_if_it_belongs_to_today():
    result = dashboard(
        [event(1, "entry", "2026-09-01T00:30:00+02:00")],
        now=datetime.fromisoformat("2026-09-01T05:00:00+00:00"),
        local_timezone=ZoneInfo("America/Los_Angeles"),
    )

    assert result.status is DashboardStatus.WORKING
    assert result.current_session is not None
    assert result.current_session.elapsed_seconds == 6 * 3600 + 30 * 60
    assert result.today.date == date(2026, 8, 31)
    assert result.today.running_duration_seconds == 6 * 3600 + 30 * 60
    assert result.today.effective_duration_seconds == 6 * 3600 + 30 * 60


def test_completed_cross_midnight_session_stays_owned_by_requested_timezone_entry_date():
    result = dashboard(
        [
            event(1, "entry", "2026-09-07T06:00:00+00:00"),
            event(2, "exit", "2026-09-07T09:00:00+00:00"),
        ],
        now=datetime.fromisoformat("2026-09-07T10:00:00+00:00"),
        local_timezone=ZoneInfo("America/Los_Angeles"),
    )

    assert result.today.date == date(2026, 9, 7)
    assert result.today.completed_duration_seconds == 0
    assert result.month.completed_duration_seconds == 3 * 3600
    assert result.month.work_days == 1
    assert result.month.pay == Decimal("150.00")


def test_completed_dst_session_uses_utc_duration_and_requested_timezone_date():
    result = dashboard(
        [
            event(1, "entry", "2026-10-25T02:30:00+02:00"),
            event(2, "exit", "2026-10-25T02:30:00+01:00"),
        ],
        now=datetime.fromisoformat("2026-10-25T03:00:00+00:00"),
        local_timezone=WARSAW,
        rates=[
            RATE,
            PayRateRecord(2, date(2026, 10, 25), Decimal("60.00"), "PLN"),
        ],
    )

    assert result.today.date == date(2026, 10, 25)
    assert result.today.completed_duration_seconds == 3600
    assert result.month.completed_duration_seconds == 3600
    assert result.month.pay == Decimal("60.00")
