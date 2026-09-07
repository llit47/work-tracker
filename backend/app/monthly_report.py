from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Iterable

from .pay import (
    PayRateRecord,
    PayRateUsed,
    calculate_monthly_pay,
    calculate_session_pay,
    round_money,
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


def build_monthly_report(
    events: Iterable[RawWorkEvent],
    rates: Iterable[PayRateRecord],
    *,
    year: int,
    month: int,
    generated_at: datetime,
) -> MonthlyReport:
    """Build one authoritative model shared by CSV and PDF renderers."""
    event_list = tuple(events)
    ordered_rates = sorted(rates, key=lambda rate: (rate.effective_from, rate.id))
    work_summary = calculate_monthly_work_time(event_list, year, month)
    pay_summary = calculate_monthly_pay(work_summary, ordered_rates)

    sessions: list[ReportSession] = []
    anomalies: list[ReportAnomaly] = []
    for day in work_summary.days:
        for item in day.items:
            if item.status is SessionStatus.VALID:
                if item.duration_seconds is None:
                    raise ValueError("Valid work session has no duration")
                entry = next(event for event in item.events if event.event_type == "entry")
                exit_event = next(event for event in item.events if event.event_type == "exit")
                rate = select_rate(ordered_rates, item.local_date)
                sessions.append(
                    ReportSession(
                        date=item.local_date,
                        entry_timestamp=entry.event_timestamp,
                        exit_timestamp=exit_event.event_timestamp,
                        duration_seconds=item.duration_seconds,
                        location=item.location,
                        hourly_rate=rate.hourly_rate,
                        currency=rate.currency,
                        pay=round_money(calculate_session_pay(item.duration_seconds, rate)),
                        entry_source=entry.source,
                        exit_source=exit_event.source,
                    )
                )
                continue

            anomalies.append(
                ReportAnomaly(
                    date=item.local_date,
                    location=item.location,
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
        sessions=tuple(sessions),
        anomalies=tuple(anomalies),
    )
