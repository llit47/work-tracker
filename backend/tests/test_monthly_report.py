import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from io import StringIO
from csv import reader

from font_roboto import Roboto
from reportlab.lib.units import mm
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph
import pytest

from app.corrections import CorrectionRecord, build_effective_event_stream
from app.models import CorrectionType
from app.monthly_report import allocate_session_pays, build_monthly_report
from app.pay import PayRateRecord, calculate_monthly_pay
from app.report_renderers import (
    MAX_ANOMALY_EVENT_CELL_HEIGHT,
    MAX_ANOMALY_EVENTS_PER_ROW,
    _anomalies_table,
    _format_anomaly_event,
    _format_utc_offset,
    _register_fonts,
    _sessions_table,
    render_monthly_report_csv,
    render_monthly_report_pdf,
)
from app.work_time import RawWorkEvent, SessionStatus, calculate_monthly_work_time


GENERATED_AT = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
RATE = PayRateRecord(1, date(1970, 1, 1), Decimal("50.00"), "PLN")


def event(
    event_id: int,
    event_type: str,
    timestamp: str,
    *,
    source: str = "home_assistant",
    location: str = "gabinet_zabki",
):
    local_timestamp = datetime.fromisoformat(timestamp)
    return RawWorkEvent(
        id=event_id,
        event_type=event_type,
        location=location,
        event_timestamp=local_timestamp,
        event_timestamp_utc=local_timestamp.astimezone(timezone.utc),
        received_at=GENERATED_AT,
        source=source,
    )


def report(events, *, rates=None, year=2026, month=9):
    return build_monthly_report(
        events,
        rates or [RATE],
        year=year,
        month=month,
        generated_at=GENERATED_AT,
    )


def correction(
    correction_id: int,
    correction_type: CorrectionType,
    *,
    raw_event_id=None,
    event_type=None,
    timestamp=None,
):
    event_timestamp = datetime.fromisoformat(timestamp) if timestamp else None
    return CorrectionRecord(
        id=correction_id,
        correction_type=correction_type,
        created_at=GENERATED_AT,
        updated_at=GENERATED_AT,
        raw_event_id=raw_event_id,
        event_type=event_type,
        event_timestamp=event_timestamp,
        event_timestamp_utc=(
            event_timestamp.astimezone(timezone.utc) if event_timestamp else None
        ),
        location="gabinet_zabki" if correction_type is CorrectionType.MANUAL_EVENT else None,
    )


def duplicate_entry_report(
    event_count: int, *, location: str = "gabinet_zabki"
):
    start = datetime(2026, 9, 6, tzinfo=timezone(timedelta(hours=2)))
    return report(
        [
            event(
                index + 1,
                "entry",
                (start + timedelta(minutes=index)).isoformat(),
                location=location,
            )
            for index in range(event_count)
        ]
    )


def test_report_totals_equal_authoritative_work_and_pay_summaries():
    events = [
        event(1, "entry", "2026-09-06T08:00:00+02:00"),
        event(2, "exit", "2026-09-06T16:30:00+02:00"),
    ]
    monthly_report = report(events)
    work_summary = calculate_monthly_work_time(events, 2026, 9)
    pay_summary = calculate_monthly_pay(work_summary, [RATE])

    assert monthly_report.total_duration_seconds == work_summary.total_duration_seconds
    assert monthly_report.work_days == work_summary.work_days
    assert monthly_report.total_pay == pay_summary.total_pay == Decimal("425.00")
    assert monthly_report.anomaly_count == 0
    assert len(monthly_report.sessions) == 1
    assert monthly_report.sessions[0].pay == Decimal("425.00")
    assert sum((session.pay for session in monthly_report.sessions), Decimal("0.00")) == (
        monthly_report.total_pay
    )


def test_session_cent_allocation_reconciles_two_half_cent_sessions():
    rate = PayRateRecord(1, date(1970, 1, 1), Decimal("18.00"), "PLN")
    monthly_report = report(
        [
            event(1, "entry", "2026-09-01T08:00:00+02:00"),
            event(2, "exit", "2026-09-01T08:00:01+02:00"),
            event(3, "entry", "2026-09-01T09:00:00+02:00"),
            event(4, "exit", "2026-09-01T09:00:01+02:00"),
        ],
        rates=[rate],
    )

    assert monthly_report.total_pay == Decimal("0.01")
    assert [session.pay for session in monthly_report.sessions] == [
        Decimal("0.01"),
        Decimal("0.00"),
    ]
    assert sum((session.pay for session in monthly_report.sessions), Decimal("0.00")) == (
        monthly_report.total_pay
    )


def test_session_cent_allocation_uses_largest_remainder_for_several_sessions():
    rate = PayRateRecord(1, date(1970, 1, 1), Decimal("12.00"), "PLN")
    monthly_report = report(
        [
            event(1, "entry", "2026-09-01T08:00:00+02:00"),
            event(2, "exit", "2026-09-01T08:00:01+02:00"),
            event(3, "entry", "2026-09-01T09:00:00+02:00"),
            event(4, "exit", "2026-09-01T09:00:02+02:00"),
            event(5, "entry", "2026-09-01T10:00:00+02:00"),
            event(6, "exit", "2026-09-01T10:00:04+02:00"),
        ],
        rates=[rate],
    )

    assert monthly_report.total_pay == Decimal("0.02")
    assert [session.pay for session in monthly_report.sessions] == [
        Decimal("0.00"),
        Decimal("0.01"),
        Decimal("0.01"),
    ]
    assert sum((session.pay for session in monthly_report.sessions), Decimal("0.00")) == (
        monthly_report.total_pay
    )


def test_session_cent_allocation_ties_use_stable_input_order():
    exact_pays = (Decimal("0.005"), Decimal("0.005"), Decimal("0.005"))

    first = allocate_session_pays(exact_pays, Decimal("0.02"))
    second = allocate_session_pays(exact_pays, Decimal("0.02"))

    assert first == second == (Decimal("0.01"), Decimal("0.01"), Decimal("0.00"))


def test_report_keeps_multiple_sessions_and_uses_historical_rate_per_session():
    rates = [RATE, PayRateRecord(2, date(2026, 9, 15), Decimal("60.00"), "PLN")]
    monthly_report = report(
        [
            event(1, "entry", "2026-09-10T08:00:00+02:00"),
            event(2, "exit", "2026-09-10T12:00:00+02:00"),
            event(3, "entry", "2026-09-10T13:00:00+02:00"),
            event(4, "exit", "2026-09-10T17:00:00+02:00"),
            event(5, "entry", "2026-09-20T08:00:00+02:00"),
            event(6, "exit", "2026-09-20T16:00:00+02:00"),
        ],
        rates=rates,
    )

    assert len(monthly_report.sessions) == 3
    assert [session.hourly_rate for session in monthly_report.sessions] == [
        Decimal("50.00"),
        Decimal("50.00"),
        Decimal("60.00"),
    ]
    assert monthly_report.total_duration_seconds == 16 * 3600
    assert monthly_report.total_pay == Decimal("880.00")
    assert sum((session.pay for session in monthly_report.sessions), Decimal("0.00")) == (
        monthly_report.total_pay
    )


def test_fractional_session_allocation_preserves_each_historical_rate():
    rates = [
        PayRateRecord(1, date(1970, 1, 1), Decimal("18.00"), "PLN"),
        PayRateRecord(2, date(2026, 9, 15), Decimal("36.00"), "PLN"),
    ]
    monthly_report = report(
        [
            event(1, "entry", "2026-09-10T08:00:00+02:00"),
            event(2, "exit", "2026-09-10T08:00:01+02:00"),
            event(3, "entry", "2026-09-20T08:00:00+02:00"),
            event(4, "exit", "2026-09-20T08:00:01+02:00"),
        ],
        rates=rates,
    )

    assert [session.hourly_rate for session in monthly_report.sessions] == [
        Decimal("18.00"),
        Decimal("36.00"),
    ]
    assert monthly_report.total_pay == Decimal("0.02")
    assert sum((session.pay for session in monthly_report.sessions), Decimal("0.00")) == (
        monthly_report.total_pay
    )


def test_report_preserves_cross_midnight_and_dst_utc_duration_semantics():
    cross_midnight = report(
        [
            event(1, "entry", "2026-09-30T22:00:00+02:00"),
            event(2, "exit", "2026-10-01T02:00:00+02:00"),
        ]
    )
    dst = report(
        [
            event(3, "entry", "2026-10-25T02:30:00+02:00"),
            event(4, "exit", "2026-10-25T02:30:00+01:00"),
        ],
        year=2026,
        month=10,
    )

    assert cross_midnight.sessions[0].date == date(2026, 9, 30)
    assert cross_midnight.sessions[0].duration_seconds == 4 * 3600
    assert dst.sessions[0].date == date(2026, 10, 25)
    assert dst.sessions[0].duration_seconds == 3600


def test_anomalies_are_reported_but_never_counted_or_paid():
    monthly_report = report(
        [
            event(1, "entry", "2026-09-01T08:00:00+02:00"),
            event(2, "exit", "2026-09-02T16:00:00+02:00"),
            event(3, "exit", "2026-09-03T16:00:00+02:00"),
            event(4, "entry", "2026-09-04T08:00:00+02:00"),
            event(5, "entry", "2026-09-04T08:01:00+02:00"),
        ]
    )

    assert monthly_report.sessions == ()
    assert monthly_report.total_duration_seconds == 0
    assert monthly_report.total_pay == Decimal("0.00")
    assert monthly_report.anomaly_count == 3
    assert {anomaly.status for anomaly in monthly_report.anomalies} == {
        SessionStatus.UNUSUALLY_LONG_SESSION,
        SessionStatus.ORPHAN_EXIT,
        SessionStatus.DUPLICATE_ENTRY,
    }


def test_effective_corrections_ignored_and_manual_events_feed_the_report():
    raw_exit = event(2, "exit", "2026-09-06T17:00:00+02:00")
    raw_events = [
        event(1, "entry", "2026-09-06T08:00:00+02:00"),
        raw_exit,
        event(3, "entry", "2026-09-06T08:01:00+02:00"),
    ]
    stream = build_effective_event_stream(
        raw_events,
        [
            correction(
                1,
                CorrectionType.TIMESTAMP_OVERRIDE,
                raw_event_id=2,
                timestamp="2026-09-06T16:00:00+02:00",
            ),
            correction(2, CorrectionType.IGNORE_EVENT, raw_event_id=3),
            correction(
                3,
                CorrectionType.MANUAL_EVENT,
                event_type="entry",
                timestamp="2026-09-06T18:00:00+02:00",
            ),
            correction(
                4,
                CorrectionType.MANUAL_EVENT,
                event_type="exit",
                timestamp="2026-09-06T19:00:00+02:00",
            ),
        ],
    )
    monthly_report = report(stream.events)

    assert [session.duration_seconds for session in monthly_report.sessions] == [
        8 * 3600,
        3600,
    ]
    assert monthly_report.sessions[0].exit_timestamp.isoformat() == "2026-09-06T16:00:00+02:00"
    assert monthly_report.sessions[1].entry_source == "manual"
    assert monthly_report.sessions[1].exit_source == "manual"
    assert raw_exit.event_timestamp.isoformat() == "2026-09-06T17:00:00+02:00"


def test_empty_report_has_zero_totals_and_generates_a_valid_pdf():
    monthly_report = report([])
    pdf = render_monthly_report_pdf(monthly_report)

    assert monthly_report.sessions == ()
    assert monthly_report.anomalies == ()
    assert monthly_report.total_duration_seconds == 0
    assert monthly_report.work_days == 0
    assert monthly_report.total_pay == Decimal("0.00")
    assert pdf.startswith(b"%PDF-")
    assert len(pdf) > 1_000


def test_csv_quotes_delimiter_and_preserves_polish_utf8_text():
    monthly_report = report(
        [
            event(
                1,
                "entry",
                "2026-09-06T08:00:00+02:00",
                location='gabinet; "żółć"',
            ),
            event(
                2,
                "exit",
                "2026-09-06T09:00:00+02:00",
                location='gabinet; "żółć"',
            ),
        ]
    )
    content = render_monthly_report_csv(monthly_report)
    rows = list(reader(StringIO(content.decode("utf-8-sig")), delimiter=";"))

    assert content.startswith(b"\xef\xbb\xbf")
    assert rows[0][1:3] == ["wejście", "wyjście"]
    assert rows[1][5] == 'gabinet; "żółć"'
    assert '"gabinet; ""żółć"""' in content.decode("utf-8-sig")


def test_csv_anomaly_timestamps_keep_dates_offsets_and_deterministic_columns():
    monthly_report = report(
        [
            event(1, "entry", "2026-09-01T08:00:00+02:00"),
            event(2, "exit", "2026-09-02T16:00:00+02:00"),
        ]
    )
    rows = list(
        reader(
            StringIO(render_monthly_report_csv(monthly_report).decode("utf-8-sig")),
            delimiter=";",
        )
    )

    assert rows[1][0:3] == [
        "2026-09-01",
        "2026-09-01T08:00:00+02:00",
        "2026-09-02T16:00:00+02:00",
    ]
    assert rows[1][6] == SessionStatus.UNUSUALLY_LONG_SESSION.value


def test_csv_session_amounts_reconcile_to_monthly_total():
    rate = PayRateRecord(1, date(1970, 1, 1), Decimal("18.00"), "PLN")
    monthly_report = report(
        [
            event(1, "entry", "2026-09-01T08:00:00+02:00"),
            event(2, "exit", "2026-09-01T08:00:01+02:00"),
            event(3, "entry", "2026-09-01T09:00:00+02:00"),
            event(4, "exit", "2026-09-01T09:00:01+02:00"),
        ],
        rates=[rate],
    )
    rows = list(
        reader(
            StringIO(render_monthly_report_csv(monthly_report).decode("utf-8-sig")),
            delimiter=";",
        )
    )

    assert sum((Decimal(row[9]) for row in rows[1:]), Decimal("0.00")) == (
        monthly_report.total_pay
    )


def test_pdf_sessions_table_uses_reconciled_line_item_amounts():
    rate = PayRateRecord(1, date(1970, 1, 1), Decimal("18.00"), "PLN")
    monthly_report = report(
        [
            event(1, "entry", "2026-09-01T08:00:00+02:00"),
            event(2, "exit", "2026-09-01T08:00:01+02:00"),
            event(3, "entry", "2026-09-01T09:00:00+02:00"),
            event(4, "exit", "2026-09-01T09:00:01+02:00"),
        ],
        rates=[rate],
    )
    table = _sessions_table(monthly_report)

    assert [table._cellvalues[1][5], table._cellvalues[2][5]] == [
        "0,01 zł",
        "0,00 zł",
    ]
    assert table._cellvalues[-1][5] == "0,01 zł"


def test_anomaly_table_wraps_and_escapes_dynamic_cells():
    entries = [
        event(
            index,
            "entry",
            f"2026-09-06T{8 + index:02d}:00:00+02:00",
            location="gabinet <test> & archiwum",
        )
        for index in range(1, 8)
    ]
    monthly_report = report(entries)
    _register_fonts()
    table = _anomalies_table(monthly_report)
    events_cell, problem_cell, location_cell = table._cellvalues[1][1:]

    assert len(table._cellvalues) == 2
    assert all(
        isinstance(cell, Paragraph)
        for cell in (events_cell, problem_cell, location_cell)
    )
    assert location_cell.getPlainText() == "gabinet <test> & archiwum"
    _, wrapped_height = events_cell.wrap(52 * mm - 12, 1_000)
    assert wrapped_height > 8.5
    table.wrap(177 * mm, 1_000)


def test_pdf_anomaly_events_show_local_dates_times_and_offsets():
    monthly_report = report(
        [
            event(1, "entry", "2026-09-01T08:00:00+02:00"),
            event(2, "exit", "2026-09-02T16:00:00+02:00"),
        ]
    )
    table = _anomalies_table(monthly_report)

    assert table._cellvalues[1][1].getPlainText() == (
        "01.09 08:00 +02:00 wejście, 02.09 16:00 +02:00 wyjście"
    )


def test_pdf_anomaly_events_distinguish_dst_fallback_offsets():
    monthly_report = report(
        [
            event(1, "entry", "2026-10-25T02:30:00+02:00"),
            event(2, "entry", "2026-10-25T02:30:00+01:00"),
        ],
        year=2026,
        month=10,
    )
    table = _anomalies_table(monthly_report)

    assert table._cellvalues[1][1].getPlainText() == (
        "25.10 02:30 +02:00 wejście, 25.10 02:30 +01:00 wejście"
    )


def test_pdf_ordinary_anomaly_stays_in_one_compact_row():
    monthly_report = report(
        [event(1, "entry", "2026-09-06T08:00:00+02:00")]
    )
    table = _anomalies_table(monthly_report)

    assert len(table._cellvalues) == 2
    assert table._cellvalues[1][1].getPlainText() == (
        "06.09 08:00 +02:00 wejście"
    )


def test_pdf_valid_session_times_are_compact_but_unambiguous():
    ordinary = report(
        [
            event(1, "entry", "2026-09-01T08:00:00+02:00"),
            event(2, "exit", "2026-09-01T16:00:00+02:00"),
        ]
    )
    cross_midnight = report(
        [
            event(3, "entry", "2026-09-01T22:00:00+02:00"),
            event(4, "exit", "2026-09-02T06:00:00+02:00"),
        ]
    )
    dst_fallback = report(
        [
            event(5, "entry", "2026-10-25T02:30:00+02:00"),
            event(6, "exit", "2026-10-25T02:30:00+01:00"),
        ],
        year=2026,
        month=10,
    )
    dst_spring = report(
        [
            event(7, "entry", "2026-03-29T01:30:00+01:00"),
            event(8, "exit", "2026-03-29T03:30:00+02:00"),
        ],
        year=2026,
        month=3,
    )

    ordinary_row = _sessions_table(ordinary)._cellvalues[1]
    cross_midnight_row = _sessions_table(cross_midnight)._cellvalues[1]
    dst_row = _sessions_table(dst_fallback)._cellvalues[1]
    spring_row = _sessions_table(dst_spring)._cellvalues[1]

    assert [ordinary_row[1].getPlainText(), ordinary_row[2].getPlainText()] == [
        "08:00",
        "16:00",
    ]
    assert [
        cross_midnight_row[1].getPlainText(),
        cross_midnight_row[2].getPlainText(),
    ] == ["22:00", "02.09 06:00"]
    assert [dst_row[1].getPlainText(), dst_row[2].getPlainText()] == [
        "02:30 +02:00",
        "02:30 +01:00",
    ]
    assert [spring_row[1].getPlainText(), spring_row[2].getPlainText()] == [
        "01:30 +01:00",
        "03:30 +02:00",
    ]


def test_pdf_short_session_keeps_significant_seconds_visible():
    monthly_report = report(
        [
            event(1, "entry", "2026-09-06T16:42:00+02:00"),
            event(2, "exit", "2026-09-06T16:42:03+02:00"),
        ]
    )
    row = _sessions_table(monthly_report)._cellvalues[1]

    assert [row[1].getPlainText(), row[2].getPlainText(), row[3]] == [
        "16:42:00",
        "16:42:03",
        "0 godz. 00 min 03 s",
    ]


def test_pdf_offset_formatter_uses_colon_and_rejects_naive_timestamps():
    assert _format_utc_offset(datetime.fromisoformat("2026-09-01T08:00:00+02:00")) == "+02:00"
    assert _format_utc_offset(datetime.fromisoformat("2026-10-25T02:30:00+01:00")) == "+01:00"
    assert _format_utc_offset(datetime.fromisoformat("2026-09-01T08:00:00-07:30")) == "-07:30"
    with pytest.raises(ValueError, match="must include a UTC offset"):
        _format_utc_offset(datetime(2026, 9, 1, 8))


def test_large_single_anomaly_pdf_generation_succeeds():
    monthly_report = duplicate_entry_report(250, location="x" * 100)

    pdf = render_monthly_report_pdf(monthly_report)

    assert pdf.startswith(b"%PDF-")


def test_very_large_single_anomaly_pdf_paginates():
    pdf = render_monthly_report_pdf(duplicate_entry_report(1_000))

    assert pdf.startswith(b"%PDF-")
    assert len(re.findall(rb"/Type\s*/Page\b", pdf)) > 1


def test_anomaly_chunks_preserve_every_event_once_and_logical_count():
    monthly_report = duplicate_entry_report(500)
    _register_fonts()
    table = _anomalies_table(monthly_report)
    physical_rows = table._cellvalues[1:]
    rendered_events = [
        value
        for row in physical_rows
        for value in row[1].getPlainText().split(", ")
        if value
    ]
    expected_events = [
        _format_anomaly_event(event) for event in monthly_report.anomalies[0].events
    ]

    assert monthly_report.anomaly_count == 1
    assert len(monthly_report.anomalies) == 1
    assert rendered_events == expected_events
    assert all(
        len(row[1].getPlainText().split(", ")) <= MAX_ANOMALY_EVENTS_PER_ROW
        for row in physical_rows
    )
    assert all(
        row[1].wrap(52 * mm - 12, 10_000)[1] <= MAX_ANOMALY_EVENT_CELL_HEIGHT
        for row in physical_rows
    )
    assert isinstance(physical_rows[0][2], Paragraph)
    assert isinstance(physical_rows[0][3], Paragraph)
    assert all(row[0] == row[2] == row[3] == "" for row in physical_rows[1:])
    table.wrap(177 * mm, 10_000)
    assert max(table._rowHeights[1:]) < 50 * mm


def test_anomaly_only_month_generates_valid_pdf():
    monthly_report = report(
        [event(1, "exit", "2026-09-06T16:00:00+02:00")]
    )

    pdf = render_monthly_report_pdf(monthly_report)

    assert monthly_report.sessions == ()
    assert monthly_report.anomaly_count == 1
    assert pdf.startswith(b"%PDF-")


def test_embedded_pdf_font_covers_polish_characters():
    font = TTFont("RobotoCoverageTest", Roboto)

    assert all(
        ord(character) in font.face.charToGlyph
        for character in "ąćęłńóśźżĄĆĘŁŃÓŚŹŻ"
    )


def test_normal_month_pdf_fits_one_page_and_large_report_paginates():
    normal_events = []
    event_id = 1
    for day in range(1, 24):
        normal_events.extend(
            [
                event(event_id, "entry", f"2026-09-{day:02d}T08:00:00+02:00"),
                event(event_id + 1, "exit", f"2026-09-{day:02d}T16:00:00+02:00"),
            ]
        )
        event_id += 2

    start = datetime(2026, 9, 24, tzinfo=timezone.utc)
    large_events = []
    for index in range(70):
        entry = start + timedelta(minutes=index * 10)
        exit_timestamp = entry + timedelta(minutes=5)
        large_events.extend(
            [
                event(index * 2 + 1, "entry", entry.isoformat()),
                event(index * 2 + 2, "exit", exit_timestamp.isoformat()),
            ]
        )

    normal_pdf = render_monthly_report_pdf(report(normal_events))
    large_pdf = render_monthly_report_pdf(report(large_events))

    assert len(re.findall(rb"/Type\s*/Page\b", normal_pdf)) == 1
    assert len(re.findall(rb"/Type\s*/Page\b", large_pdf)) > 1
