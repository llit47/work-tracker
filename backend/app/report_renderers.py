from csv import writer
from datetime import datetime
from html import escape
from io import BytesIO, StringIO

from font_roboto import Roboto, RobotoBold
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from .monthly_report import MonthlyReport, ReportAnomalyEvent
from .work_time import SessionStatus

CSV_HEADERS = (
    "data",
    "wejście",
    "wyjście",
    "czas",
    "lokalizacja",
    "status",
    "stawka_godzinowa",
    "waluta",
    "wynagrodzenie",
)

MONTH_NAMES = (
    "styczeń",
    "luty",
    "marzec",
    "kwiecień",
    "maj",
    "czerwiec",
    "lipiec",
    "sierpień",
    "wrzesień",
    "październik",
    "listopad",
    "grudzień",
)

ANOMALY_LABELS = {
    SessionStatus.MISSING_EXIT: "Brak wyjścia",
    SessionStatus.DUPLICATE_ENTRY: "Niejednoznaczne wejście",
    SessionStatus.ORPHAN_EXIT: "Wyjście bez wejścia",
    SessionStatus.UNUSUALLY_LONG_SESSION: "Podejrzanie długa sesja",
    SessionStatus.AMBIGUOUS_TIMESTAMP: "Sprzeczne zdarzenia o tej samej godzinie",
}

# A Table can split between rows but not inside one row. Each formatted event
# has bounded text (date, time, offset, and a fixed label). The count cap and
# measured Paragraph height keep every physical row well below the A4 frame.
MAX_ANOMALY_EVENTS_PER_ROW = 12
MAX_ANOMALY_EVENT_CELL_HEIGHT = 45 * mm
ANOMALY_EVENT_COLUMN_WIDTH = 52 * mm
TABLE_HORIZONTAL_PADDING = 12


def render_monthly_report_csv(report: MonthlyReport) -> bytes:
    output = StringIO(newline="")
    csv_writer = writer(output, delimiter=";", lineterminator="\r\n")
    csv_writer.writerow(CSV_HEADERS)

    for session in report.sessions:
        csv_writer.writerow(
            (
                session.date.isoformat(),
                _format_minute_timestamp(session.entry_timestamp),
                _format_minute_timestamp(session.exit_timestamp),
                _format_clock_duration(session.duration_seconds),
                session.display_location,
                SessionStatus.VALID.value,
                f"{session.hourly_rate:.2f}",
                session.currency,
                f"{session.pay:.2f}",
            )
        )

    for anomaly in report.anomalies:
        entries = [
            _format_minute_timestamp(event.timestamp)
            for event in anomaly.events
            if event.event_type == "entry"
        ]
        exits = [
            _format_minute_timestamp(event.timestamp)
            for event in anomaly.events
            if event.event_type == "exit"
        ]
        csv_writer.writerow(
            (
                anomaly.date.isoformat(),
                " | ".join(entries),
                " | ".join(exits),
                "",
                anomaly.display_location,
                anomaly.status.value,
                "",
                "",
                "",
            )
        )

    return b"\xef\xbb\xbf" + output.getvalue().encode("utf-8")


def render_monthly_report_pdf(report: MonthlyReport) -> bytes:
    _register_fonts()
    output = BytesIO()
    document = SimpleDocTemplate(
        output,
        pagesize=A4,
        leftMargin=12 * mm,
        rightMargin=12 * mm,
        topMargin=12 * mm,
        bottomMargin=13 * mm,
        title=f"Work Tracker - {report.year}-{report.month:02d}",
        author="Work Tracker",
    )
    styles = _report_styles()
    story = [
        Paragraph("Work Tracker", styles["Title"]),
        Paragraph(
            f"Raport miesięczny — {MONTH_NAMES[report.month - 1]} {report.year}",
            styles["Heading"],
        ),
        Paragraph(
            f"Wygenerowano: {report.generated_at.strftime('%d.%m.%Y %H:%M %Z')}",
            styles["Meta"],
        ),
        Spacer(1, 4 * mm),
        _summary_table(report),
        Paragraph(
            f"Lokalizacja: {escape(_format_locations(report))}",
            styles["Location"],
        ),
        Spacer(1, 4 * mm),
    ]

    if report.sessions:
        story.extend(
            [
                Paragraph("Sesje pracy", styles["Section"]),
                Spacer(1, 1.5 * mm),
                _sessions_table(report),
            ]
        )
    else:
        story.append(
            Paragraph("Brak zarejestrowanych sesji pracy.", styles["Empty"])
        )

    if report.anomalies:
        story.extend(
            [
                Spacer(1, 4 * mm),
                Paragraph("Problemy / korekty", styles["Section"]),
                Spacer(1, 1.5 * mm),
                _anomalies_table(report),
            ]
        )

    document.build(story, onFirstPage=_draw_page_number, onLaterPages=_draw_page_number)
    return output.getvalue()


def _register_fonts() -> None:
    registered = set(pdfmetrics.getRegisteredFontNames())
    if "Roboto" not in registered:
        pdfmetrics.registerFont(TTFont("Roboto", Roboto))
    if "Roboto-Bold" not in registered:
        pdfmetrics.registerFont(TTFont("Roboto-Bold", RobotoBold))


def _report_styles() -> dict[str, ParagraphStyle]:
    sample = getSampleStyleSheet()
    return {
        "Title": ParagraphStyle(
            "ReportTitle", parent=sample["Title"], fontName="Roboto-Bold", fontSize=15, leading=17
        ),
        "Heading": ParagraphStyle(
            "ReportHeading",
            parent=sample["Heading2"],
            fontName="Roboto",
            fontSize=11,
            leading=13,
            alignment=TA_CENTER,
        ),
        "Meta": ParagraphStyle(
            "ReportMeta",
            parent=sample["Normal"],
            fontName="Roboto",
            fontSize=7,
            leading=9,
            textColor=colors.HexColor("#66717A"),
            alignment=TA_CENTER,
        ),
        "Section": ParagraphStyle(
            "ReportSection", parent=sample["Heading3"], fontName="Roboto-Bold", fontSize=9, leading=11
        ),
        "Empty": ParagraphStyle(
            "ReportEmpty",
            parent=sample["Normal"],
            fontName="Roboto",
            fontSize=9,
            leading=12,
            textColor=colors.HexColor("#52616B"),
        ),
        "Location": ParagraphStyle(
            "ReportLocation",
            parent=sample["Normal"],
            fontName="Roboto",
            fontSize=7,
            leading=9,
            textColor=colors.HexColor("#66717A"),
            alignment=TA_RIGHT,
        ),
    }


def _summary_table(report: MonthlyReport) -> Table:
    rates = _format_rates(report)
    data = [
        (
            "Dni pracy",
            "Łączny czas",
            "Wynagrodzenie",
            "Stawka / stawki",
            "Problemy",
        ),
        (
            str(report.work_days),
            _format_duration(report.total_duration_seconds),
            _format_money(report.total_pay, report.currency),
            rates,
            str(report.anomaly_count),
        ),
    ]
    table = Table(
        data,
        colWidths=(25 * mm, 38 * mm, 44 * mm, 50 * mm, 20 * mm),
    )
    table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, 0), "Roboto-Bold"),
                ("FONTNAME", (0, 1), (-1, 1), "Roboto"),
                ("FONTSIZE", (0, 0), (-1, 0), 7),
                ("FONTSIZE", (0, 1), (-1, 1), 9),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#52616B")),
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F5F7F8")),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#D6DDE1")),
                ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#D6DDE1")),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return table


def _sessions_table(report: MonthlyReport) -> Table:
    time_style = ParagraphStyle(
        "SessionTimeCell",
        fontName="Roboto",
        fontSize=7.3,
        leading=8.5,
        alignment=TA_RIGHT,
        splitLongWords=1,
    )
    rows = [("Data", "Wejście", "Wyjście", "Czas", "Stawka", "Kwota")]
    for session in report.sessions:
        entry_text, exit_text = _format_session_times(
            session.entry_timestamp, session.exit_timestamp
        )
        rows.append(
            (
                session.date.strftime("%d.%m.%Y"),
                Paragraph(escape(entry_text), time_style),
                Paragraph(escape(exit_text), time_style),
                _format_duration(session.duration_seconds),
                _format_hourly_rate(session.hourly_rate, session.currency),
                _format_money(session.pay, session.currency),
            )
        )
    rows.append(
        (
            "RAZEM",
            "",
            "",
            _format_duration(report.total_duration_seconds),
            "",
            _format_money(report.total_pay, report.currency),
        )
    )
    table = Table(
        rows,
        colWidths=(31 * mm, 25 * mm, 25 * mm, 33 * mm, 31 * mm, 32 * mm),
        repeatRows=1,
    )
    table.setStyle(_table_style(len(rows)))
    table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, -1), (-1, -1), "Roboto-Bold"),
                ("LINEABOVE", (0, -1), (-1, -1), 0.8, colors.HexColor("#56636B")),
            ]
        )
    )
    return table


def _anomalies_table(report: MonthlyReport) -> Table:
    cell_style = ParagraphStyle(
        "AnomalyTableCell",
        fontName="Roboto",
        fontSize=7.3,
        leading=8.5,
        splitLongWords=1,
    )
    rows = [("Data", "Zdarzenia", "Problem", "Lokalizacja")]
    rows.extend(_anomaly_rows(report, cell_style))
    table = Table(
        rows,
        colWidths=(31 * mm, 52 * mm, 55 * mm, 39 * mm),
        repeatRows=1,
    )
    table.setStyle(_table_style(len(rows)))
    return table


def _anomaly_rows(
    report: MonthlyReport, cell_style: ParagraphStyle
) -> list[tuple[str, Paragraph, Paragraph | str, Paragraph | str]]:
    rows: list[tuple[str, Paragraph, Paragraph | str, Paragraph | str]] = []
    for anomaly in report.anomalies:
        event_chunks = _chunk_anomaly_events(anomaly.events, cell_style)
        for chunk_index, event_chunk in enumerate(event_chunks):
            first_row = chunk_index == 0
            rows.append(
                (
                    anomaly.date.strftime("%d.%m.%Y") if first_row else "",
                    Paragraph(escape(_format_anomaly_events(event_chunk)), cell_style),
                    (
                        Paragraph(escape(ANOMALY_LABELS[anomaly.status]), cell_style)
                        if first_row
                        else ""
                    ),
                    (
                        Paragraph(escape(anomaly.display_location), cell_style)
                        if first_row
                        else ""
                    ),
                )
            )
    return rows


def _chunk_anomaly_events(
    events: tuple[ReportAnomalyEvent, ...], cell_style: ParagraphStyle
) -> list[tuple[ReportAnomalyEvent, ...]]:
    if not events:
        return [()]

    chunks: list[tuple[ReportAnomalyEvent, ...]] = []
    current: list[ReportAnomalyEvent] = []
    available_width = ANOMALY_EVENT_COLUMN_WIDTH - TABLE_HORIZONTAL_PADDING
    for event in events:
        candidate = (*current, event)
        candidate_paragraph = Paragraph(
            escape(_format_anomaly_events(candidate)), cell_style
        )
        _, candidate_height = candidate_paragraph.wrap(
            available_width, MAX_ANOMALY_EVENT_CELL_HEIGHT
        )
        if current and (
            len(candidate) > MAX_ANOMALY_EVENTS_PER_ROW
            or candidate_height > MAX_ANOMALY_EVENT_CELL_HEIGHT
        ):
            chunks.append(tuple(current))
            current = [event]
        else:
            current.append(event)
    chunks.append(tuple(current))
    return chunks


def _table_style(row_count: int) -> TableStyle:
    commands = [
        ("FONTNAME", (0, 0), (-1, 0), "Roboto-Bold"),
        ("FONTNAME", (0, 1), (-1, -1), "Roboto"),
        ("FONTSIZE", (0, 0), (-1, -1), 7.3),
        ("LEADING", (0, 0), (-1, -1), 8.5),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E8EEF1")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#263238")),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#C8D1D7")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 2.2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.2),
        ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
    ]
    for row in range(1, row_count, 2):
        commands.append(("BACKGROUND", (0, row), (-1, row), colors.HexColor("#FAFBFC")))
    return TableStyle(commands)


def _draw_page_number(canvas, document) -> None:
    canvas.saveState()
    canvas.setFont("Roboto", 7)
    canvas.setFillColor(colors.HexColor("#66717A"))
    canvas.drawRightString(A4[0] - 12 * mm, 7 * mm, f"Strona {document.page}")
    canvas.restoreState()


def _format_anomaly_events(events: tuple[ReportAnomalyEvent, ...]) -> str:
    return ", ".join(_format_anomaly_event(event) for event in events)


def _format_anomaly_event(event: ReportAnomalyEvent) -> str:
    labels = {"entry": "wejście", "exit": "wyjście"}
    return (
        f"{event.timestamp.strftime('%d.%m %H:%M')} "
        f"{_format_utc_offset(event.timestamp)} {labels[event.event_type]}"
    )


def _format_session_times(
    entry_timestamp: datetime, exit_timestamp: datetime
) -> tuple[str, str]:
    entry_offset = _format_utc_offset(entry_timestamp)
    exit_offset = _format_utc_offset(exit_timestamp)
    crosses_date = entry_timestamp.date() != exit_timestamp.date()
    changes_offset = entry_offset != exit_offset
    clock_format = "%H:%M"

    entry_text = entry_timestamp.strftime(clock_format)
    exit_text = exit_timestamp.strftime(clock_format)
    if crosses_date:
        exit_format = (
            f"%d.%m.%Y {clock_format}"
            if entry_timestamp.year != exit_timestamp.year
            else f"%d.%m {clock_format}"
        )
        exit_text = exit_timestamp.strftime(exit_format)
    if changes_offset:
        entry_text = f"{entry_text} {entry_offset}"
        exit_text = f"{exit_text} {exit_offset}"
    return entry_text, exit_text


def _format_utc_offset(timestamp: datetime) -> str:
    offset = timestamp.utcoffset()
    if offset is None:
        raise ValueError("PDF timestamps must include a UTC offset")

    total_microseconds = (
        (offset.days * 86_400 + offset.seconds) * 1_000_000 + offset.microseconds
    )
    sign = "+" if total_microseconds >= 0 else "-"
    absolute_microseconds = abs(total_microseconds)
    total_minutes = absolute_microseconds // 60_000_000
    hours, minutes = divmod(total_minutes, 60)
    return f"{sign}{hours:02d}:{minutes:02d}"


def _format_minute_timestamp(timestamp: datetime) -> str:
    return f"{timestamp.strftime('%Y-%m-%dT%H:%M')}{_format_utc_offset(timestamp)}"


def _format_rates(report: MonthlyReport) -> str:
    if not report.rates_used:
        return "Brak wykorzystanej stawki"
    values = [rate.hourly_rate for rate in report.rates_used]
    if len(values) == 1:
        return _format_hourly_rate(values[0], report.rates_used[0].currency)
    return (
        f"{_format_decimal(min(values))}–{_format_decimal(max(values))} "
        f"{report.currency}/h"
    )


def _format_locations(report: MonthlyReport) -> str:
    locations = sorted(
        {session.display_location for session in report.sessions}
        | {anomaly.display_location for anomaly in report.anomalies}
    )
    return ", ".join(locations) if locations else "brak"


def _format_duration(seconds: int) -> str:
    hours, remainder = divmod(seconds, 3600)
    minutes = remainder // 60
    return f"{hours} godz. {minutes:02d} min"


def _format_clock_duration(seconds: int) -> str:
    hours, remainder = divmod(seconds, 3600)
    minutes = remainder // 60
    return f"{hours:02d}:{minutes:02d}"


def _format_decimal(value) -> str:
    return f"{value:.2f}".replace(".", ",")


def _format_money(value, currency: str) -> str:
    suffix = "zł" if currency == "PLN" else currency
    return f"{_format_decimal(value)} {suffix}"


def _format_hourly_rate(value, currency: str) -> str:
    return f"{_format_money(value, currency)}/h"
