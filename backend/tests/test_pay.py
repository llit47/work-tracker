from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from app.pay import (
    MixedCurrenciesError,
    PayRateRecord,
    calculate_monthly_pay,
)
from app.work_time import RawWorkEvent, calculate_monthly_work_time


def event(event_id: int, event_type: str, timestamp: str) -> RawWorkEvent:
    local_timestamp = datetime.fromisoformat(timestamp)
    return RawWorkEvent(
        id=event_id,
        event_type=event_type,
        location="gabinet_zabki",
        event_timestamp=local_timestamp,
        event_timestamp_utc=local_timestamp.astimezone(timezone.utc),
        received_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
        source="home_assistant",
    )


def rate(
    rate_id: int = 1,
    effective_from: date = date(1970, 1, 1),
    hourly_rate: str = "50.00",
    currency: str = "PLN",
) -> PayRateRecord:
    return PayRateRecord(
        id=rate_id,
        effective_from=effective_from,
        hourly_rate=Decimal(hourly_rate),
        currency=currency,
    )


def pay_for(
    events: list[RawWorkEvent],
    *,
    rates: list[PayRateRecord] | None = None,
    year: int = 2026,
    month: int = 9,
):
    work_summary = calculate_monthly_work_time(events, year, month)
    return calculate_monthly_pay(work_summary, rates or [rate()])


def test_simple_and_fractional_work_days_use_exact_valid_session_seconds():
    eight_hours = pay_for(
        [event(1, "entry", "2026-09-06T08:00:00+02:00"), event(2, "exit", "2026-09-06T16:00:00+02:00")]
    )
    eight_and_a_half_hours = pay_for(
        [event(1, "entry", "2026-09-06T08:00:00+02:00"), event(2, "exit", "2026-09-06T16:30:00+02:00")]
    )

    assert eight_hours.total_duration_seconds == 8 * 3600
    assert eight_hours.total_pay == Decimal("400.00")
    assert eight_and_a_half_hours.total_pay == Decimal("425.00")


def test_empty_month_has_zero_pay_and_the_active_currency():
    summary = pay_for([])

    assert summary.currency == "PLN"
    assert summary.total_duration_seconds == 0
    assert summary.work_days == 0
    assert summary.total_pay == Decimal("0.00")
    assert summary.days == ()
    assert summary.rates_used == ()


def test_multiple_sessions_and_days_have_correct_daily_and_monthly_pay():
    summary = pay_for(
        [
            event(1, "entry", "2026-09-06T08:00:00+02:00"),
            event(2, "exit", "2026-09-06T12:00:00+02:00"),
            event(3, "entry", "2026-09-06T13:00:00+02:00"),
            event(4, "exit", "2026-09-06T17:00:00+02:00"),
            event(5, "entry", "2026-09-07T09:00:00+02:00"),
            event(6, "exit", "2026-09-07T14:00:00+02:00"),
        ]
    )

    assert summary.work_days == 2
    assert [day.duration_seconds for day in summary.days] == [8 * 3600, 5 * 3600]
    assert [day.pay for day in summary.days] == [Decimal("400.00"), Decimal("250.00")]
    assert summary.total_pay == Decimal("650.00")


def test_historical_rate_and_inclusive_effective_date_boundary():
    rates = [
        rate(),
        rate(2, date(2026, 9, 15), "60.00"),
        rate(3, date(2027, 1, 1), "55.00"),
    ]
    september = pay_for(
        [
            event(1, "entry", "2026-09-14T08:00:00+02:00"),
            event(2, "exit", "2026-09-14T16:00:00+02:00"),
            event(3, "entry", "2026-09-15T08:00:00+02:00"),
            event(4, "exit", "2026-09-15T16:00:00+02:00"),
        ],
        rates=rates,
    )

    assert [day.pay for day in september.days] == [Decimal("400.00"), Decimal("480.00")]
    assert september.total_pay == Decimal("880.00")
    assert [used.effective_from for used in september.rates_used] == [
        date(1970, 1, 1),
        date(2026, 9, 15),
    ]

    historical = pay_for(
        [event(1, "entry", "2026-12-31T08:00:00+01:00"), event(2, "exit", "2026-12-31T16:00:00+01:00")],
        rates=[rate(), rate(3, date(2027, 1, 1), "55.00")],
        year=2026,
        month=12,
    )
    assert historical.total_pay == Decimal("400.00")


@pytest.mark.parametrize(
    "events",
    [
        [event(1, "entry", "2026-09-06T08:00:00+02:00")],
        [
            event(1, "entry", "2026-09-06T08:00:00+02:00"),
            event(2, "entry", "2026-09-06T08:01:00+02:00"),
            event(3, "exit", "2026-09-06T16:00:00+02:00"),
        ],
        [event(1, "exit", "2026-09-06T16:00:00+02:00")],
        [
            event(1, "entry", "2026-09-06T08:00:00+02:00"),
            event(2, "exit", "2026-09-06T08:00:00+02:00"),
        ],
        [
            event(1, "entry", "2026-09-06T08:00:00+02:00"),
            event(2, "exit", "2026-09-07T00:00:01+02:00"),
        ],
    ],
    ids=[
        "missing-exit",
        "duplicate-entry",
        "orphan-exit",
        "ambiguous-timestamp",
        "unusually-long",
    ],
)
def test_anomalies_generate_no_pay(events: list[RawWorkEvent]):
    summary = pay_for(events)

    assert summary.total_duration_seconds == 0
    assert summary.total_pay == Decimal("0.00")
    assert all(day.pay == Decimal("0.00") for day in summary.days)
    assert summary.rates_used == ()


def test_exactly_16_hours_is_paid_but_direct_session_over_limit_is_not():
    exact = pay_for(
        [event(1, "entry", "2026-09-06T08:00:00+02:00"), event(2, "exit", "2026-09-07T00:00:00+02:00")]
    )
    over = pay_for(
        [event(1, "entry", "2026-09-06T08:00:00+02:00"), event(2, "exit", "2026-09-07T00:00:01+02:00")]
    )

    assert exact.total_pay == Decimal("800.00")
    assert over.total_pay == Decimal("0.00")


def test_cross_midnight_and_cross_month_session_uses_local_entry_date_rate():
    rates = [rate(), rate(2, date(2027, 1, 1), "60.00")]
    events = [
        event(1, "entry", "2026-12-31T22:00:00+01:00"),
        event(2, "exit", "2027-01-01T06:00:00+01:00"),
    ]

    december = pay_for(events, rates=rates, year=2026, month=12)
    january = pay_for(events, rates=rates, year=2027, month=1)

    assert december.total_duration_seconds == 8 * 3600
    assert december.total_pay == Decimal("400.00")
    assert [used.effective_from for used in december.rates_used] == [date(1970, 1, 1)]
    assert january.total_duration_seconds == 0
    assert january.total_pay == Decimal("0.00")


def test_dst_duration_comes_from_utc_while_rate_uses_local_entry_date():
    summary = pay_for(
        [
            event(1, "entry", "2026-10-25T02:30:00+02:00"),
            event(2, "exit", "2026-10-25T02:30:00+01:00"),
        ],
        rates=[rate(), rate(2, date(2026, 10, 25), "60.00")],
        month=10,
    )

    assert summary.total_duration_seconds == 3600
    assert summary.total_pay == Decimal("60.00")
    assert summary.rates_used[0].effective_from == date(2026, 10, 25)


def test_seconds_are_not_truncated_and_rounding_is_half_up_at_output_boundaries():
    seconds_summary = pay_for(
        [event(1, "entry", "2026-09-06T08:00:00+02:00"), event(2, "exit", "2026-09-06T08:00:36+02:00")]
    )
    rounding_summary = pay_for(
        [
            event(1, "entry", "2026-09-06T08:00:00+02:00"),
            event(2, "exit", "2026-09-06T08:00:01+02:00"),
            event(3, "entry", "2026-09-07T08:00:00+02:00"),
            event(4, "exit", "2026-09-07T08:00:01+02:00"),
        ],
        rates=[rate(hourly_rate="18.00")],
    )

    assert seconds_summary.total_pay == Decimal("0.50")
    assert [day.pay for day in rounding_summary.days] == [Decimal("0.01"), Decimal("0.01")]
    assert rounding_summary.total_pay == Decimal("0.01")


def test_mixed_currencies_are_rejected_instead_of_summed():
    with pytest.raises(MixedCurrenciesError):
        pay_for(
            [
                event(1, "entry", "2026-09-14T08:00:00+02:00"),
                event(2, "exit", "2026-09-14T09:00:00+02:00"),
                event(3, "entry", "2026-09-15T08:00:00+02:00"),
                event(4, "exit", "2026-09-15T09:00:00+02:00"),
            ],
            rates=[rate(), rate(2, date(2026, 9, 15), "50.00", "EUR")],
        )
