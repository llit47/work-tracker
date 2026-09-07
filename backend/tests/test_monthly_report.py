import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from io import StringIO
from csv import reader

from font_roboto import Roboto
from reportlab.pdfbase.ttfonts import TTFont

from app.corrections import CorrectionRecord, build_effective_event_stream
from app.models import CorrectionType
from app.monthly_report import build_monthly_report
from app.pay import PayRateRecord, calculate_monthly_pay
from app.report_renderers import render_monthly_report_csv, render_monthly_report_pdf
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
