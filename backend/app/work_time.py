from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import Enum
from itertools import groupby
from typing import Iterable

MAX_SESSION_DURATION = timedelta(hours=16)


class SessionStatus(str, Enum):
    VALID = "valid"
    MISSING_EXIT = "missing_exit"
    DUPLICATE_ENTRY = "duplicate_entry"
    ORPHAN_EXIT = "orphan_exit"
    UNUSUALLY_LONG_SESSION = "unusually_long_session"
    AMBIGUOUS_TIMESTAMP = "ambiguous_timestamp"


@dataclass(frozen=True)
class RawWorkEvent:
    id: int
    event_type: str
    location: str
    event_timestamp: datetime
    event_timestamp_utc: datetime
    received_at: datetime
    source: str


@dataclass(frozen=True)
class WorkTimeItem:
    status: SessionStatus
    location: str
    local_date: date
    events: tuple[RawWorkEvent, ...]
    duration_seconds: int | None = None


@dataclass(frozen=True)
class WorkDay:
    date: date
    items: tuple[WorkTimeItem, ...]
    total_duration_seconds: int
    anomaly_count: int


@dataclass(frozen=True)
class MonthlyWorkSummary:
    year: int
    month: int
    days: tuple[WorkDay, ...]
    total_duration_seconds: int
    work_days: int
    anomaly_count: int


def calculate_monthly_work_time(
    events: Iterable[RawWorkEvent], year: int, month: int
) -> MonthlyWorkSummary:
    """Derive sessions from immutable raw events without changing the input."""
    all_items = derive_work_time_items(events)
    return summarize_work_time_items(all_items, year, month)


def summarize_work_time_items(
    items: Iterable[WorkTimeItem], year: int, month: int
) -> MonthlyWorkSummary:
    """Build a monthly summary from items produced by the canonical pairing rules."""

    month_items = [
        item for item in items if item.local_date.year == year and item.local_date.month == month
    ]

    items_by_day: dict[date, list[WorkTimeItem]] = defaultdict(list)
    for item in month_items:
        items_by_day[item.local_date].append(item)

    days: list[WorkDay] = []
    for local_date in sorted(items_by_day):
        items = tuple(items_by_day[local_date])
        total = sum(
            item.duration_seconds or 0 for item in items if item.status is SessionStatus.VALID
        )
        anomaly_count = sum(item.status is not SessionStatus.VALID for item in items)
        days.append(
            WorkDay(
                date=local_date,
                items=items,
                total_duration_seconds=total,
                anomaly_count=anomaly_count,
            )
        )

    return MonthlyWorkSummary(
        year=year,
        month=month,
        days=tuple(days),
        total_duration_seconds=sum(day.total_duration_seconds for day in days),
        work_days=sum(day.total_duration_seconds > 0 for day in days),
        anomaly_count=sum(day.anomaly_count for day in days),
    )


def derive_work_time_items(events: Iterable[RawWorkEvent]) -> tuple[WorkTimeItem, ...]:
    """Apply the canonical pairing rules and return deterministic, unfiltered items."""
    events_by_location: dict[str, list[RawWorkEvent]] = defaultdict(list)
    for event in events:
        _validate_event(event)
        events_by_location[event.location].append(event)

    all_items: list[WorkTimeItem] = []
    for location_events in events_by_location.values():
        all_items.extend(_pair_location_events(location_events))
    return tuple(sorted(all_items, key=_item_sort_key))


def _pair_location_events(events: list[RawWorkEvent]) -> list[WorkTimeItem]:
    ordered = sorted(events, key=lambda event: (event.event_timestamp_utc, event.id))
    items: list[WorkTimeItem] = []
    pending_entries: list[RawWorkEvent] = []

    for timestamp, grouped_events in groupby(ordered, key=lambda event: event.event_timestamp_utc):
        timestamp_events = list(grouped_events)
        event_types = {event.event_type for event in timestamp_events}

        # Finalize the whole pending state only when even its newest entry can
        # no longer form a valid session. Exits still close pending entries so
        # a direct pair over the limit retains unusually_long_session semantics.
        if (
            "entry" in event_types
            and pending_entries
            and timestamp - pending_entries[-1].event_timestamp_utc > MAX_SESSION_DURATION
        ):
            items.append(_pending_entries_anomaly(pending_entries))
            pending_entries = []

        if len(event_types) > 1:
            related_events = tuple(pending_entries + timestamp_events)
            anchor = pending_entries[0] if pending_entries else timestamp_events[0]
            items.append(
                WorkTimeItem(
                    status=SessionStatus.AMBIGUOUS_TIMESTAMP,
                    location=anchor.location,
                    local_date=anchor.event_timestamp.date(),
                    events=related_events,
                )
            )
            pending_entries = []
            continue

        for event in timestamp_events:
            if event.event_type == "entry":
                pending_entries.append(event)
                continue

            if not pending_entries:
                items.append(_anomaly(SessionStatus.ORPHAN_EXIT, (event,)))
            elif len(pending_entries) > 1:
                items.append(_anomaly(SessionStatus.DUPLICATE_ENTRY, tuple(pending_entries + [event])))
                pending_entries = []
            else:
                entry = pending_entries.pop()
                duration = event.event_timestamp_utc - entry.event_timestamp_utc
                duration_seconds = duration // timedelta(seconds=1)
                status = (
                    SessionStatus.UNUSUALLY_LONG_SESSION
                    if duration > MAX_SESSION_DURATION
                    else SessionStatus.VALID
                )
                items.append(
                    WorkTimeItem(
                        status=status,
                        location=entry.location,
                        local_date=entry.event_timestamp.date(),
                        events=(entry, event),
                        duration_seconds=duration_seconds,
                    )
                )

    if pending_entries:
        items.append(_pending_entries_anomaly(pending_entries))

    return items


def _pending_entries_anomaly(pending_entries: list[RawWorkEvent]) -> WorkTimeItem:
    status = (
        SessionStatus.MISSING_EXIT
        if len(pending_entries) == 1
        else SessionStatus.DUPLICATE_ENTRY
    )
    return _anomaly(status, tuple(pending_entries))


def _anomaly(status: SessionStatus, events: tuple[RawWorkEvent, ...]) -> WorkTimeItem:
    anchor = events[0]
    return WorkTimeItem(
        status=status,
        location=anchor.location,
        local_date=anchor.event_timestamp.date(),
        events=events,
    )


def _item_sort_key(item: WorkTimeItem) -> tuple[datetime, int, str]:
    first_event = min(item.events, key=lambda event: (event.event_timestamp_utc, event.id))
    return first_event.event_timestamp_utc, first_event.id, item.location


def _validate_event(event: RawWorkEvent) -> None:
    if event.event_type not in {"entry", "exit"}:
        raise ValueError(f"Unsupported event type: {event.event_type}")
    for timestamp in (event.event_timestamp, event.event_timestamp_utc, event.received_at):
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError("Work event timestamps must be timezone-aware")
