from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_DOWN, Decimal
from typing import Iterable, Mapping

from .pay import (
    MONEY_QUANTUM,
    PayRateRecord,
    PayRateUsed,
    calculate_monthly_pay,
    calculate_session_pay,
    select_rate,
)
from .work_time import (
    RawWorkEvent,
    SessionStatus,
    calculate_monthly_work_time,
)


@dataclass(frozen=True)
class ReportSession:
    date: date
    entry_timestamp: datetime
    exit_timestamp: datetime
    duration_seconds: int
    location: str
    display_location: str
    hourly_rate: Decimal
    currency: str
    pay: Decimal
    entry_source: str
    exit_source: str


@dataclass(frozen=True)
class ReportAnomalyEvent:
    event_type: str
    timestamp: datetime
    source: str


@dataclass(frozen=True)
class ReportAnomaly:
    date: date
    location: str
    display_location: str
    status: SessionStatus
    events: tuple[ReportAnomalyEvent, ...]


@dataclass(frozen=True)
class MonthlyReport:
    year: int
    month: int
    generated_at: datetime
    currency: str
    total_duration_seconds: int
    work_days: int
    total_pay: Decimal
    anomaly_count: int
    rates_used: tuple[PayRateUsed, ...]
    sessions: tuple[ReportSession, ...]
    anomalies: tuple[ReportAnomaly, ...]


@dataclass(frozen=True)
class _ReportSessionDraft:
    date: date
    entry_timestamp: datetime
    exit_timestamp: datetime
    duration_seconds: int
    location: str
    display_location: str
    hourly_rate: Decimal
    currency: str
    exact_pay: Decimal
    entry_source: str
    exit_source: str


def allocate_session_pays(
    exact_pays: Iterable[Decimal], authoritative_total: Decimal
) -> tuple[Decimal, ...]:
    """Allocate rounded cents while preserving the authoritative monthly total.

    Each non-negative exact amount is truncated to cents first. Remaining cents
    are assigned by descending fractional-cent remainder, with input order as a
    stable tie-breaker. This is the largest-remainder method using Decimal only.
    """
    amounts = tuple(exact_pays)
    if not amounts:
        if authoritative_total != Decimal("0.00"):
            raise ValueError("A non-zero total cannot be allocated without sessions")
        return ()
    if any(amount < 0 or not amount.is_finite() for amount in amounts):
        raise ValueError("Session pay must be a finite non-negative Decimal")

    base_amounts = [
        amount.quantize(MONEY_QUANTUM, rounding=ROUND_DOWN) for amount in amounts
    ]
    remaining = authoritative_total - sum(base_amounts, Decimal("0.00"))
    remaining_cents = remaining / MONEY_QUANTUM
    integral_cents = remaining_cents.to_integral_value()
    if (
        remaining_cents != integral_cents
        or integral_cents < 0
        or integral_cents > len(amounts)
    ):
        raise ValueError("Authoritative total cannot be reconciled to session cents")

    ranked_indexes = sorted(
        range(len(amounts)),
        key=lambda index: (-(amounts[index] - base_amounts[index]), index),
    )
    for index in ranked_indexes[: int(integral_cents)]:
        base_amounts[index] += MONEY_QUANTUM

    allocated = tuple(base_amounts)
    if sum(allocated, Decimal("0.00")) != authoritative_total:
        raise ValueError("Allocated session pay does not match authoritative total")
    return allocated


def build_monthly_report(
    events: Iterable[RawWorkEvent],
    rates: Iterable[PayRateRecord],
    *,
    year: int,
    month: int,
    generated_at: datetime,
    location_display_names: Mapping[str, str] | None = None,
) -> MonthlyReport:
    """Build one authoritative model shared by CSV and PDF renderers."""
    event_list = tuple(events)
    ordered_rates = sorted(rates, key=lambda rate: (rate.effective_from, rate.id))
    work_summary = calculate_monthly_work_time(event_list, year, month)
    pay_summary = calculate_monthly_pay(work_summary, ordered_rates)
    display_names = location_display_names or {}

    session_drafts: list[_ReportSessionDraft] = []
    anomalies: list[ReportAnomaly] = []
    for day in work_summary.days:
        for item in day.items:
            if item.status is SessionStatus.VALID:
                if item.duration_seconds is None:
                    raise ValueError("Valid work session has no duration")
                entry = next(event for event in item.events if event.event_type == "entry")
                exit_event = next(event for event in item.events if event.event_type == "exit")
                rate = select_rate(ordered_rates, item.local_date)
                session_drafts.append(
                    _ReportSessionDraft(
                        date=item.local_date,
                        entry_timestamp=entry.event_timestamp,
                        exit_timestamp=exit_event.event_timestamp,
                        duration_seconds=item.duration_seconds,
                        location=item.location,
                        display_location=display_names.get(item.location, item.location),
                        hourly_rate=rate.hourly_rate,
                        currency=rate.currency,
                        exact_pay=calculate_session_pay(item.duration_seconds, rate),
                        entry_source=entry.source,
                        exit_source=exit_event.source,
                    )
                )
                continue

            anomalies.append(
                ReportAnomaly(
                    date=item.local_date,
                    location=item.location,
                    display_location=display_names.get(item.location, item.location),
                    status=item.status,
                    events=tuple(
                        ReportAnomalyEvent(
                            event_type=event.event_type,
                            timestamp=event.event_timestamp,
                            source=event.source,
                        )
                        for event in item.events
                    ),
                )
            )

    allocated_pays = allocate_session_pays(
        (draft.exact_pay for draft in session_drafts), pay_summary.total_pay
    )
    sessions = tuple(
        ReportSession(
            date=draft.date,
            entry_timestamp=draft.entry_timestamp,
            exit_timestamp=draft.exit_timestamp,
            duration_seconds=draft.duration_seconds,
            location=draft.location,
            display_location=draft.display_location,
            hourly_rate=draft.hourly_rate,
            currency=draft.currency,
            pay=allocated_pay,
            entry_source=draft.entry_source,
            exit_source=draft.exit_source,
        )
        for draft, allocated_pay in zip(session_drafts, allocated_pays, strict=True)
    )

    return MonthlyReport(
        year=year,
        month=month,
        generated_at=generated_at,
        currency=pay_summary.currency,
        total_duration_seconds=work_summary.total_duration_seconds,
        work_days=work_summary.work_days,
        total_pay=pay_summary.total_pay,
        anomaly_count=work_summary.anomaly_count,
        rates_used=pay_summary.rates_used,
        sessions=sessions,
        anomalies=tuple(anomalies),
    )
