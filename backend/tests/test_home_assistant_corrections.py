from datetime import datetime, time, timezone

import pytest

from app.corrections import (
    TimeOnlyCorrectionError,
    parse_time_only,
    resolve_time_only_timestamp,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("7:45", time(7, 45)),
        ("07:45", time(7, 45)),
        ("7.45", time(7, 45)),
        ("07.45", time(7, 45)),
        ("0:05", time(0, 5)),
        ("00:05", time(0, 5)),
        ("  7:45\n", time(7, 45)),
        ("23:59", time(23, 59)),
    ],
)
def test_parse_time_only_accepts_supported_formats(value: str, expected: time):
    assert parse_time_only(value) == expected


@pytest.mark.parametrize("value", ["7:5", "24:00", "12:72", "abc", "7", ""])
def test_parse_time_only_rejects_invalid_values(value: str):
    with pytest.raises(TimeOnlyCorrectionError) as error:
        parse_time_only(value)
    assert error.value.code == "invalid_time"


@pytest.mark.parametrize(
    ("raw_timestamp", "value", "expected"),
    [
        (
            "2026-09-09T23:50:00+02:00",
            "00:10",
            "2026-09-10T00:10:00+02:00",
        ),
        (
            "2026-09-10T00:10:00+02:00",
            "23:50",
            "2026-09-09T23:50:00+02:00",
        ),
        (
            "2026-09-09T14:00:00+02:00",
            "18:00",
            "2026-09-09T18:00:00+02:00",
        ),
        (
            "2026-09-09T14:00:00+02:00",
            "10:00",
            "2026-09-09T10:00:00+02:00",
        ),
    ],
)
def test_resolver_handles_midnight_and_inclusive_four_hour_window(
    raw_timestamp: str, value: str, expected: str
):
    raw_instant = datetime.fromisoformat(raw_timestamp).astimezone(timezone.utc)
    assert resolve_time_only_timestamp(
        raw_instant, "Europe/Warsaw", value
    ).isoformat() == expected


@pytest.mark.parametrize("value", ["18:01", "09:59"])
def test_resolver_rejects_values_outside_four_hour_window(value: str):
    raw_instant = datetime.fromisoformat("2026-09-09T14:00:00+02:00").astimezone(
        timezone.utc
    )
    with pytest.raises(TimeOnlyCorrectionError) as error:
        resolve_time_only_timestamp(raw_instant, "Europe/Warsaw", value)
    assert error.value.code == "time_out_of_range"


def test_resolver_rejects_nonexistent_warsaw_spring_time():
    raw_instant = datetime.fromisoformat("2026-03-29T01:30:00+01:00").astimezone(
        timezone.utc
    )
    with pytest.raises(TimeOnlyCorrectionError) as error:
        resolve_time_only_timestamp(raw_instant, "Europe/Warsaw", "02:30")
    assert error.value.code == "time_not_resolvable"


def test_resolver_selects_unique_nearest_fold_during_warsaw_fallback():
    raw_instant = datetime.fromisoformat("2026-10-25T00:40:00+00:00")
    result = resolve_time_only_timestamp(raw_instant, "Europe/Warsaw", "02:30")

    assert result.isoformat() == "2026-10-25T02:30:00+02:00"
    assert result.fold == 0


def test_resolver_rejects_tied_warsaw_fallback_folds():
    raw_instant = datetime.fromisoformat("2026-10-25T01:00:00+00:00")
    with pytest.raises(TimeOnlyCorrectionError) as error:
        resolve_time_only_timestamp(raw_instant, "Europe/Warsaw", "02:30")
    assert error.value.code == "time_not_resolvable"


def test_resolver_rejects_unknown_timezone():
    with pytest.raises(TimeOnlyCorrectionError) as error:
        resolve_time_only_timestamp(
            datetime.fromisoformat("2026-09-09T12:00:00+00:00"),
            "Mars/Olympus_Mons",
            "14:00",
        )
    assert error.value.code == "location_timezone_invalid"
