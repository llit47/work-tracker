from csv import writer
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

from .monthly_report import MonthlyReport, ReportAnomaly
from .work_time import SessionStatus

CSV_HEADERS = (
    "data",
    "wejście",
    "wyjście",
    "czas",
    "czas_sekundy",
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


def render_monthly_report_csv(report: MonthlyReport) -> bytes:
    output = StringIO(newline="")
    csv_writer = writer(output, delimiter=";", lineterminator="\r\n")
    csv_writer.writerow(CSV_HEADERS)

    for session in report.sessions:
        csv_writer.writerow(
            (
                session.date.isoformat(),
                session.entry_timestamp.isoformat(),
                session.exit_timestamp.isoformat(),
                _format_clock_duration(session.duration_seconds),
                session.duration_seconds,
                session.location,
                SessionStatus.VALID.value,
                f"{session.hourly_rate:.2f}",
                session.currency,
                f"{session.pay:.2f}",
            )
        )

    for anomaly in report.anomalies:
        entries = [event.timestamp.isoformat() for event in anomaly.events if event.event_type == "entry"]
        exits = [event.timestamp.isoformat() for event in anomaly.events if event.event_type == "exit"]
        csv_writer.writerow(
            (
                anomaly.date.isoformat(),
                " | ".join(entries),
                " | ".join(exits),
                "",
                "",
                anomaly.location,
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
    rows = [("Data", "Wejście", "Wyjście", "Czas", "Stawka", "Kwota")]
    rows.extend(
        (
            session.date.strftime("%d.%m.%Y"),
            session.entry_timestamp.strftime("%H:%M"),
            session.exit_timestamp.strftime("%H:%M"),
            _format_duration(session.duration_seconds),
            _format_hourly_rate(session.hourly_rate, session.currency),
            _format_money(session.pay, session.currency),
        )
        for session in report.sessions
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
    rows = [("Data", "Zdarzenia", "Problem", "Lokalizacja")]
    rows.extend(
        (
            anomaly.date.strftime("%d.%m.%Y"),
            _format_anomaly_events(anomaly),
            ANOMALY_LABELS[anomaly.status],
            anomaly.location,
        )
        for anomaly in report.anomalies
    )
    table = Table(
        rows,
        colWidths=(31 * mm, 52 * mm, 55 * mm, 39 * mm),
        repeatRows=1,
    )
    table.setStyle(_table_style(len(rows)))
    return table


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


def _format_anomaly_events(anomaly: ReportAnomaly) -> str:
    labels = {"entry": "wejście", "exit": "wyjście"}
    return ", ".join(
        f"{event.timestamp.strftime('%H:%M')} {labels[event.event_type]}"
        for event in anomaly.events
    )


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
        {session.location for session in report.sessions}
        | {anomaly.location for anomaly in report.anomalies}
    )
    return ", ".join(locations) if locations else "brak"


def _format_duration(seconds: int) -> str:
    hours, remainder = divmod(seconds, 3600)
    minutes = remainder // 60
    return f"{hours} godz. {minutes:02d} min"


def _format_clock_duration(seconds: int) -> str:
    hours, remainder = divmod(seconds, 3600)
    minutes, remaining_seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{remaining_seconds:02d}"


def _format_decimal(value) -> str:
    return f"{value:.2f}".replace(".", ",")


def _format_money(value, currency: str) -> str:
    suffix = "zł" if currency == "PLN" else currency
    return f"{_format_decimal(value)} {suffix}"


def _format_hourly_rate(value, currency: str) -> str:
    return f"{_format_money(value, currency)}/h"
