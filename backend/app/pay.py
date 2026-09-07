from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable

from .work_time import MonthlyWorkSummary, SessionStatus

MONEY_QUANTUM = Decimal("0.01")
SECONDS_PER_HOUR = Decimal(3600)


class PayCalculationError(ValueError):
    pass


class MissingPayRateError(PayCalculationError):
    pass


class MixedCurrenciesError(PayCalculationError):
    pass


@dataclass(frozen=True)
class PayRateRecord:
    id: int
    effective_from: date
    hourly_rate: Decimal
    currency: str


@dataclass(frozen=True)
class PayRateUsed:
    id: int
    effective_from: date
    hourly_rate: Decimal
    currency: str


@dataclass(frozen=True)
class DailyPaySummary:
    date: date
    duration_seconds: int
    pay: Decimal


@dataclass(frozen=True)
class MonthlyPaySummary:
    year: int
    month: int
    currency: str
    total_duration_seconds: int
    work_days: int
    total_pay: Decimal
    days: tuple[DailyPaySummary, ...]
    rates_used: tuple[PayRateUsed, ...]


def calculate_monthly_pay(
    work_summary: MonthlyWorkSummary,
    rates: Iterable[PayRateRecord],
) -> MonthlyPaySummary:
    """Calculate pay from valid sessions, rounding only completed day/month amounts."""
    ordered_rates = sorted(rates, key=lambda rate: (rate.effective_from, rate.id))
    if not ordered_rates:
        raise MissingPayRateError("No pay rates are configured")

    exact_monthly_pay = Decimal(0)
    currencies_used: set[str] = set()
    used_rates: dict[int, PayRateRecord] = {}
    days: list[DailyPaySummary] = []

    for day in work_summary.days:
        exact_daily_pay = Decimal(0)
        for item in day.items:
            if item.status is not SessionStatus.VALID:
                continue
            if item.duration_seconds is None:
                raise PayCalculationError("Valid work session has no duration")
            rate = select_rate(ordered_rates, item.local_date)
            exact_session_pay = (
                Decimal(item.duration_seconds) * rate.hourly_rate / SECONDS_PER_HOUR
            )
            exact_daily_pay += exact_session_pay
            exact_monthly_pay += exact_session_pay
            currencies_used.add(rate.currency)
            used_rates[rate.id] = rate

        days.append(
            DailyPaySummary(
                date=day.date,
                duration_seconds=day.total_duration_seconds,
                pay=round_money(exact_daily_pay),
            )
        )

    if len(currencies_used) > 1:
        raise MixedCurrenciesError("Cannot total work sessions using different currencies")

    if currencies_used:
        currency = next(iter(currencies_used))
    else:
        currency = select_rate(ordered_rates, date(work_summary.year, work_summary.month, 1)).currency

    return MonthlyPaySummary(
        year=work_summary.year,
        month=work_summary.month,
        currency=currency,
        total_duration_seconds=work_summary.total_duration_seconds,
        work_days=work_summary.work_days,
        total_pay=round_money(exact_monthly_pay),
        days=tuple(days),
        rates_used=tuple(
            PayRateUsed(
                id=rate.id,
                effective_from=rate.effective_from,
                hourly_rate=rate.hourly_rate,
                currency=rate.currency,
            )
            for rate in sorted(used_rates.values(), key=lambda rate: (rate.effective_from, rate.id))
        ),
    )


def select_rate(rates: list[PayRateRecord], work_date: date) -> PayRateRecord:
    matching_rate: PayRateRecord | None = None
    for rate in rates:
        if rate.effective_from > work_date:
            break
        matching_rate = rate
    if matching_rate is None:
        raise MissingPayRateError(f"No pay rate applies on {work_date.isoformat()}")
    return matching_rate


def round_money(value: Decimal) -> Decimal:
    return value.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)
