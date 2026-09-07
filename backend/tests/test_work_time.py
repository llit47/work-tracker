from datetime import datetime, timedelta, timezone

from app.work_time import (
    MAX_SESSION_DURATION,
    RawWorkEvent,
    SessionStatus,
    calculate_monthly_work_time,
)


def event(
    event_id: int,
    event_type: str,
    timestamp: str,
    *,
    received_at: str = "2026-09-06T18:00:00+00:00",
    location: str = "gabinet_zabki",
    source: str = "home_assistant",
) -> RawWorkEvent:
    local_timestamp = datetime.fromisoformat(timestamp)
    return RawWorkEvent(
        id=event_id,
        event_type=event_type,
        location=location,
        event_timestamp=local_timestamp,
        event_timestamp_utc=local_timestamp.astimezone(timezone.utc),
        received_at=datetime.fromisoformat(received_at),
        source=source,
    )


def items_for(events: list[RawWorkEvent], year: int = 2026, month: int = 9):
    summary = calculate_monthly_work_time(events, year, month)
    return summary, [item for day in summary.days for item in day.items]


def test_pairs_single_entry_and_exit():
    summary, items = items_for(
        [event(1, "entry", "2026-09-06T08:03:00+02:00"), event(2, "exit", "2026-09-06T16:12:00+02:00")]
    )

    assert len(items) == 1
    assert items[0].status is SessionStatus.VALID
    assert items[0].duration_seconds == 8 * 3600 + 9 * 60
    assert summary.total_duration_seconds == 8 * 3600 + 9 * 60


def test_pairs_two_valid_sessions_on_one_day():
    summary, items = items_for(
        [
            event(1, "entry", "2026-09-06T08:00:00+02:00"),
            event(2, "exit", "2026-09-06T12:00:00+02:00"),
            event(3, "entry", "2026-09-06T13:00:00+02:00"),
            event(4, "exit", "2026-09-06T17:00:00+02:00"),
        ]
    )

    assert [item.status for item in items] == [SessionStatus.VALID, SessionStatus.VALID]
    assert summary.days[0].total_duration_seconds == 8 * 3600
    assert summary.work_days == 1


def test_marks_missing_exit_without_counting_time():
    summary, items = items_for([event(1, "entry", "2026-09-06T08:00:00+02:00")])

    assert items[0].status is SessionStatus.MISSING_EXIT
    assert items[0].duration_seconds is None
    assert summary.total_duration_seconds == 0
    assert summary.work_days == 0


def test_marks_duplicate_entry_and_does_not_choose_one():
    summary, items = items_for(
        [
            event(1, "entry", "2026-09-06T08:00:00+02:00"),
            event(2, "entry", "2026-09-06T08:04:00+02:00"),
            event(3, "exit", "2026-09-06T16:00:00+02:00"),
        ]
    )

    assert len(items) == 1
    assert items[0].status is SessionStatus.DUPLICATE_ENTRY
    assert [raw.id for raw in items[0].events] == [1, 2, 3]
    assert summary.total_duration_seconds == 0


def test_stale_entry_does_not_contaminate_a_later_session():
    summary, items = items_for(
        [
            event(1, "entry", "2026-09-02T10:00:00+02:00", source="manual"),
            event(2, "entry", "2026-09-06T16:42:00+02:00"),
            event(3, "exit", "2026-09-06T16:42:03+02:00"),
        ]
    )

    assert [day.date.isoformat() for day in summary.days] == ["2026-09-02", "2026-09-06"]
    assert [item.status for item in items] == [SessionStatus.MISSING_EXIT, SessionStatus.VALID]
    assert [[raw.id for raw in item.events] for item in items] == [[1], [2, 3]]
    assert items[1].duration_seconds == 3
    assert summary.total_duration_seconds == 3
    assert summary.days[0].anomaly_count == 1
    assert summary.days[1].total_duration_seconds == 3


def test_stale_duplicate_entries_do_not_contaminate_a_later_session():
    summary, items = items_for(
        [
            event(1, "entry", "2026-09-02T08:00:00+02:00"),
            event(2, "entry", "2026-09-02T08:01:00+02:00"),
            event(3, "entry", "2026-09-06T16:00:00+02:00"),
            event(4, "exit", "2026-09-06T18:00:00+02:00"),
        ]
    )

    assert [item.status for item in items] == [SessionStatus.DUPLICATE_ENTRY, SessionStatus.VALID]
    assert [[raw.id for raw in item.events] for item in items] == [[1, 2], [3, 4]]
    assert summary.total_duration_seconds == 2 * 3600


def test_pending_state_is_retained_while_its_newest_entry_is_still_viable():
    summary, items = items_for(
        [
            event(1, "entry", "2026-09-06T08:00:00+02:00"),
            event(2, "entry", "2026-09-06T08:01:00+02:00"),
            event(3, "entry", "2026-09-07T00:00:30+02:00"),
            event(4, "exit", "2026-09-07T00:00:45+02:00"),
        ]
    )

    assert len(items) == 1
    assert items[0].status is SessionStatus.DUPLICATE_ENTRY
    assert [raw.id for raw in items[0].events] == [1, 2, 3, 4]
    assert all(item.status is not SessionStatus.VALID for item in items)
    assert summary.total_duration_seconds == 0


def test_pending_state_is_not_expired_when_newest_entry_is_exactly_16_hours_old():
    summary, items = items_for(
        [
            event(1, "entry", "2026-09-06T08:00:00+02:00"),
            event(2, "entry", "2026-09-06T08:01:00+02:00"),
            event(3, "entry", "2026-09-07T00:01:00+02:00"),
            event(4, "exit", "2026-09-07T00:02:00+02:00"),
        ]
    )

    assert len(items) == 1
    assert items[0].status is SessionStatus.DUPLICATE_ENTRY
    assert [raw.id for raw in items[0].events] == [1, 2, 3, 4]
    assert summary.total_duration_seconds == 0


def test_duplicate_exit_keeps_the_unambiguous_session_valid():
    summary, items = items_for(
        [
            event(1, "entry", "2026-09-06T08:00:00+02:00"),
            event(2, "exit", "2026-09-06T16:00:00+02:00"),
            event(3, "exit", "2026-09-06T16:05:00+02:00"),
        ]
    )

    assert [item.status for item in items] == [SessionStatus.VALID, SessionStatus.ORPHAN_EXIT]
    assert summary.total_duration_seconds == 8 * 3600
    assert summary.anomaly_count == 1


def test_marks_exit_without_entry_as_orphan():
    summary, items = items_for([event(1, "exit", "2026-09-06T16:00:00+02:00")])

    assert items[0].status is SessionStatus.ORPHAN_EXIT
    assert summary.total_duration_seconds == 0


def test_orders_by_event_timestamp_utc_not_input_or_received_order():
    exit_event = event(
        1,
        "exit",
        "2026-09-06T16:00:00+02:00",
        received_at="2026-09-06T14:00:00+00:00",
    )
    entry_event = event(
        2,
        "entry",
        "2026-09-06T08:00:00+02:00",
        received_at="2026-09-06T14:01:00+00:00",
    )

    summary, items = items_for([exit_event, entry_event])

    assert items[0].status is SessionStatus.VALID
    assert [raw.id for raw in items[0].events] == [2, 1]
    assert summary.total_duration_seconds == 8 * 3600


def test_conflicting_events_at_identical_utc_time_are_ambiguous():
    summary, items = items_for(
        [event(2, "exit", "2026-09-06T08:00:00+02:00"), event(1, "entry", "2026-09-06T08:00:00+02:00")]
    )

    assert len(items) == 1
    assert items[0].status is SessionStatus.AMBIGUOUS_TIMESTAMP
    assert [raw.id for raw in items[0].events] == [1, 2]
    assert summary.total_duration_seconds == 0


def test_session_crossing_midnight_belongs_to_entry_day():
    summary, items = items_for(
        [event(1, "entry", "2026-09-07T23:00:00+02:00"), event(2, "exit", "2026-09-08T07:00:00+02:00")]
    )

    assert items[0].status is SessionStatus.VALID
    assert items[0].duration_seconds == 8 * 3600
    assert summary.days[0].date.isoformat() == "2026-09-07"


def test_session_crossing_month_boundary_belongs_to_entry_month():
    events = [
        event(1, "entry", "2026-09-30T22:00:00+02:00"),
        event(2, "exit", "2026-10-01T02:00:00+02:00"),
    ]

    september, items = items_for(events)
    october, october_items = items_for(events, month=10)

    assert items[0].status is SessionStatus.VALID
    assert september.total_duration_seconds == 4 * 3600
    assert september.days[0].date.isoformat() == "2026-09-30"
    assert october_items == []
    assert october.total_duration_seconds == 0


def test_session_longer_than_16_hours_is_not_counted():
    summary, items = items_for(
        [event(1, "entry", "2026-09-06T08:00:00+02:00"), event(2, "exit", "2026-09-07T00:00:01+02:00")]
    )

    assert MAX_SESSION_DURATION == timedelta(hours=16)
    assert items[0].status is SessionStatus.UNUSUALLY_LONG_SESSION
    assert items[0].duration_seconds == 16 * 3600 + 1
    assert summary.total_duration_seconds == 0


def test_session_of_exactly_16_hours_is_valid():
    summary, items = items_for(
        [event(1, "entry", "2026-09-06T08:00:00+02:00"), event(2, "exit", "2026-09-07T00:00:00+02:00")]
    )

    assert items[0].status is SessionStatus.VALID
    assert summary.total_duration_seconds == 16 * 3600


def test_dst_spring_forward_uses_real_utc_duration():
    summary, items = items_for(
        [event(1, "entry", "2026-03-29T01:30:00+01:00"), event(2, "exit", "2026-03-29T03:30:00+02:00")],
        month=3,
    )

    assert items[0].duration_seconds == 3600
    assert summary.total_duration_seconds == 3600


def test_dst_fall_back_uses_real_utc_duration():
    summary, items = items_for(
        [event(1, "entry", "2026-10-25T02:30:00+02:00"), event(2, "exit", "2026-10-25T02:30:00+01:00")],
        month=10,
    )

    assert items[0].duration_seconds == 3600
    assert summary.total_duration_seconds == 3600


def test_monthly_total_counts_only_valid_sessions_and_reports_anomalies():
    events = [
        event(1, "entry", "2026-09-01T08:00:00+02:00"),
        event(2, "exit", "2026-09-01T16:00:00+02:00"),
        event(3, "entry", "2026-09-02T09:00:00+02:00"),
        event(4, "exit", "2026-09-02T14:30:00+02:00"),
        event(5, "entry", "2026-09-03T08:00:00+02:00"),
        event(6, "exit", "2026-09-04T07:00:00+02:00"),
        event(7, "entry", "2026-09-05T08:00:00+02:00"),
        event(8, "entry", "2026-09-05T08:05:00+02:00"),
        event(9, "exit", "2026-09-05T16:00:00+02:00"),
        event(10, "exit", "2026-09-06T16:00:00+02:00"),
        event(11, "entry", "2026-09-07T08:00:00+02:00"),
    ]

    summary, _ = items_for(events)

    assert summary.total_duration_seconds == 13 * 3600 + 30 * 60
    assert summary.anomaly_count == 4
    assert summary.work_days == 2


def test_work_days_requires_positive_valid_time():
    events = [
        event(1, "entry", "2026-09-01T08:00:00+02:00"),
        event(2, "exit", "2026-09-01T09:00:00+02:00"),
        event(3, "entry", "2026-09-02T10:00:00+02:00"),
        event(4, "exit", "2026-09-02T11:00:00+02:00"),
        event(5, "exit", "2026-09-03T12:00:00+02:00"),
    ]

    summary, _ = items_for(events)

    assert summary.work_days == 2
    assert len(summary.days) == 3


def test_calculation_does_not_modify_raw_events():
    events = [
        event(2, "exit", "2026-09-06T16:00:00+02:00"),
        event(1, "entry", "2026-09-06T08:00:00+02:00"),
    ]
    original_order = list(events)
    original_values = [raw.__dict__.copy() for raw in events]

    calculate_monthly_work_time(events, 2026, 9)

    assert events == original_order
    assert [raw.__dict__ for raw in events] == original_values
