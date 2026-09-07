from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone, tzinfo
from decimal import Decimal
from enum import Enum
from typing import Iterable

from .pay import (
    SECONDS_PER_HOUR,
    MixedCurrenciesError,
    PayCalculationError,
    PayRateRecord,
    round_money,
    select_rate,
)
from .work_time import (
    MAX_SESSION_DURATION,
    RawWorkEvent,
    SessionStatus,
    WorkTimeItem,
    derive_work_time_items,
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
    completed_today, month_summary = _aggregate_dashboard_items(
        completed_items,
        rates,
        local_now=local_now,
        local_timezone=local_timezone,
    )
    running_today: int | None = None
    if (
        current_session is not None
        and current_session.entry_timestamp_utc.astimezone(local_timezone).date()
        == local_now.date()
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
        month=month_summary,
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


def _aggregate_dashboard_items(
    items: tuple[WorkTimeItem, ...],
    rates: Iterable[PayRateRecord],
    *,
    local_now: datetime,
    local_timezone: tzinfo,
) -> tuple[int, MonthDashboard]:
    ordered_rates = sorted(rates, key=lambda rate: (rate.effective_from, rate.id))
    total_duration_seconds = 0
    completed_today = 0
    work_dates: set[date] = set()
    exact_pay = Decimal(0)
    currencies: set[str] = set()

    for item in items:
        if item.status is not SessionStatus.VALID:
            continue
        if item.duration_seconds is None:
            raise PayCalculationError("Valid work session has no duration")

        entry = next(event for event in item.events if event.event_type == "entry")
        entry_date = entry.event_timestamp_utc.astimezone(local_timezone).date()
        if entry_date.year != local_now.year or entry_date.month != local_now.month:
            continue

        total_duration_seconds += item.duration_seconds
        if item.duration_seconds > 0:
            work_dates.add(entry_date)
        if entry_date == local_now.date():
            completed_today += item.duration_seconds

        rate = select_rate(ordered_rates, entry_date)
        exact_pay += Decimal(item.duration_seconds) * rate.hourly_rate / SECONDS_PER_HOUR
        currencies.add(rate.currency)

    if len(currencies) > 1:
        raise MixedCurrenciesError(
            "Cannot total work sessions using different currencies"
        )
    currency = (
        next(iter(currencies))
        if currencies
        else select_rate(
            ordered_rates, date(local_now.year, local_now.month, 1)
        ).currency
    )

    return completed_today, MonthDashboard(
        year=local_now.year,
        month=local_now.month,
        completed_duration_seconds=total_duration_seconds,
        work_days=len(work_dates),
        pay=round_money(exact_pay),
        currency=currency,
    )
