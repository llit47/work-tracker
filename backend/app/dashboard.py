from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone, tzinfo
from decimal import Decimal
from enum import Enum
from typing import Iterable

from .pay import MonthlyPaySummary, PayRateRecord, calculate_monthly_pay
from .work_time import (
    MAX_SESSION_DURATION,
    MonthlyWorkSummary,
    RawWorkEvent,
    SessionStatus,
    WorkTimeItem,
    derive_work_time_items,
    summarize_work_time_items,
)


class DashboardStatus(str, Enum):
    WORKING = "working"
    OUTSIDE = "outside"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True)
class CurrentSession:
    entry_timestamp: datetime
    entry_timestamp_utc: datetime
    elapsed_seconds: int


@dataclass(frozen=True)
class TodayDashboard:
    date: date
    completed_duration_seconds: int
    running_duration_seconds: int | None
    effective_duration_seconds: int


@dataclass(frozen=True)
class MonthDashboard:
    year: int
    month: int
    completed_duration_seconds: int
    work_days: int
    pay: Decimal
    currency: str


@dataclass(frozen=True)
class DashboardSummary:
    status: DashboardStatus
    generated_at: datetime
    current_session: CurrentSession | None
    today: TodayDashboard
    month: MonthDashboard


def calculate_dashboard(
    events: Iterable[RawWorkEvent],
    rates: Iterable[PayRateRecord],
    *,
    now: datetime,
    local_timezone: tzinfo,
) -> DashboardSummary:
    """Build live state from canonical work items without persisting a synthetic exit."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Dashboard time must be timezone-aware")

    now_utc = now.astimezone(timezone.utc)
    local_now = now_utc.astimezone(local_timezone)
    items = derive_work_time_items(events)
    status, current_session = _derive_live_state(items, now_utc)
    completed_items = tuple(
        item for item in items if _item_has_no_future_events(item, now_utc)
    )
    month_summary = summarize_work_time_items(
        completed_items, local_now.year, local_now.month
    )
    pay_summary = calculate_monthly_pay(month_summary, rates)

    completed_today = next(
        (
            day.total_duration_seconds
            for day in month_summary.days
            if day.date == local_now.date()
        ),
        0,
    )
    running_today: int | None = None
    if (
        current_session is not None
        and current_session.entry_timestamp.date() == local_now.date()
    ):
        running_today = current_session.elapsed_seconds

    return DashboardSummary(
        status=status,
        generated_at=now_utc,
        current_session=current_session,
        today=TodayDashboard(
            date=local_now.date(),
            completed_duration_seconds=completed_today,
            running_duration_seconds=running_today,
            effective_duration_seconds=completed_today + (running_today or 0),
        ),
        month=_month_dashboard(month_summary, pay_summary),
    )


def _derive_live_state(
    items: tuple[WorkTimeItem, ...], now_utc: datetime
) -> tuple[DashboardStatus, CurrentSession | None]:
    if any(not _item_has_no_future_events(item, now_utc) for item in items):
        return DashboardStatus.AMBIGUOUS, None

    terminal_items_by_location: dict[str, WorkTimeItem] = {}
    items_by_location: dict[str, list[WorkTimeItem]] = defaultdict(list)
    for item in items:
        items_by_location[item.location].append(item)

    for location, location_items in items_by_location.items():
        terminal_items_by_location[location] = max(location_items, key=_terminal_item_key)

    running_entries: list[RawWorkEvent] = []
    for item in terminal_items_by_location.values():
        if item.status in {
            SessionStatus.DUPLICATE_ENTRY,
            SessionStatus.AMBIGUOUS_TIMESTAMP,
        }:
            return DashboardStatus.AMBIGUOUS, None

        if item.status is not SessionStatus.MISSING_EXIT:
            continue

        if len(item.events) != 1 or item.events[0].event_type != "entry":
            return DashboardStatus.AMBIGUOUS, None
        entry = item.events[0]
        elapsed = now_utc - entry.event_timestamp_utc
        if elapsed < timedelta(0) or elapsed > MAX_SESSION_DURATION:
            return DashboardStatus.AMBIGUOUS, None
        running_entries.append(entry)

    if len(running_entries) > 1:
        return DashboardStatus.AMBIGUOUS, None
    if not running_entries:
        return DashboardStatus.OUTSIDE, None

    entry = running_entries[0]
    elapsed_seconds = (now_utc - entry.event_timestamp_utc) // timedelta(seconds=1)
    return DashboardStatus.WORKING, CurrentSession(
        entry_timestamp=entry.event_timestamp,
        entry_timestamp_utc=entry.event_timestamp_utc,
        elapsed_seconds=elapsed_seconds,
    )


def _item_has_no_future_events(item: WorkTimeItem, now_utc: datetime) -> bool:
    return all(event.event_timestamp_utc <= now_utc for event in item.events)


def _terminal_item_key(item: WorkTimeItem) -> tuple[datetime, int]:
    terminal_event = max(
        item.events,
        key=lambda event: (event.event_timestamp_utc, event.id),
    )
    return terminal_event.event_timestamp_utc, terminal_event.id


def _month_dashboard(
    work_summary: MonthlyWorkSummary, pay_summary: MonthlyPaySummary
) -> MonthDashboard:
    return MonthDashboard(
        year=work_summary.year,
        month=work_summary.month,
        completed_duration_seconds=work_summary.total_duration_seconds,
        work_days=work_summary.work_days,
        pay=pay_summary.total_pay,
        currency=pay_summary.currency,
    )
