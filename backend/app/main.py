from datetime import date, datetime, timezone
import hmac
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .application_settings import (
    APPLICATION_SETTINGS_ID,
    ApplicationPresentationSettings,
    load_application_settings,
)
from .config import Settings, get_settings
from .database import build_session_factory, get_session
from .dashboard import DashboardSummary, calculate_dashboard
from .corrections import (
    CorrectionConflictError,
    CorrectionRecord,
    EffectiveEventMetadata,
    EffectiveEventStream,
    RawEventNotFoundError,
    TimeOnlyCorrectionError,
    build_effective_event_stream,
    resolve_time_only_timestamp,
    upsert_timestamp_correction,
)
from .models import (
    ApplicationSetting,
    CorrectionType,
    LocationDisplayName,
    LocationTimezone,
    PayRate,
    WorkEvent,
    WorkEventCorrection,
)
from .monthly_report import MonthlyReport, build_monthly_report
from .pay import (
    MixedCurrenciesError,
    MissingPayRateError,
    MonthlyPaySummary,
    PayRateRecord,
    calculate_monthly_pay,
)
from .report_renderers import render_monthly_report_csv, render_monthly_report_pdf
from .schemas import (
    ApplicationSettingsResponse,
    ApplicationSettingsUpdate,
    CorrectionResponse,
    DashboardResponse,
    EffectiveWorkEventResponse,
    HomeAssistantCorrectionRequest,
    HomeAssistantCorrectionResponse,
    HomeAssistantWebhook,
    ManualEventRequest,
    MonthlyPaySummaryResponse,
    MonthlyWorkSummaryResponse,
    LocationPresentationSetting,
    PayRateRequest,
    PayRateResponse,
    TimestampCorrectionRequest,
    WebhookAccepted,
    WorkDayResponse,
    WorkEventResponse,
    WorkTimeItemResponse,
)
from .work_time import RawWorkEvent, calculate_monthly_work_time


def _to_raw_event(event: WorkEvent) -> RawWorkEvent:
    return RawWorkEvent(
        id=event.id,
        event_type=event.event_type,
        location=event.location,
        event_timestamp=event.event_timestamp,
        event_timestamp_utc=event.event_timestamp_utc,
        received_at=event.received_at,
        source=event.source,
    )


def _to_correction_record(correction: WorkEventCorrection) -> CorrectionRecord:
    return CorrectionRecord(
        id=correction.id,
        correction_type=CorrectionType(correction.correction_type),
        created_at=correction.created_at,
        updated_at=correction.updated_at,
        raw_event_id=correction.raw_event_id,
        event_type=correction.event_type,
        event_timestamp=correction.event_timestamp,
        event_timestamp_utc=correction.event_timestamp_utc,
        location=correction.location,
    )


def _to_effective_event_response(event: EffectiveEventMetadata) -> EffectiveWorkEventResponse:
    return EffectiveWorkEventResponse.model_validate(event)


def _to_pay_rate_record(rate: PayRate) -> PayRateRecord:
    return PayRateRecord(
        id=rate.id,
        effective_from=rate.effective_from,
        hourly_rate=rate.hourly_rate,
        currency=rate.currency,
    )


def _to_application_settings_response(
    settings_data: ApplicationPresentationSettings,
) -> ApplicationSettingsResponse:
    return ApplicationSettingsResponse(
        application_title=settings_data.application_title,
        locations=[
            LocationPresentationSetting(
                location=item.location,
                display_name=item.display_name,
                timezone=item.timezone,
            )
            for item in settings_data.locations
        ],
    )


def _load_effective_event_stream(session: Session) -> EffectiveEventStream:
    raw_statement = select(WorkEvent).order_by(WorkEvent.event_timestamp_utc.asc(), WorkEvent.id.asc())
    correction_statement = select(WorkEventCorrection).order_by(WorkEventCorrection.id.asc())
    raw_events = [_to_raw_event(event) for event in session.scalars(raw_statement)]
    corrections = [
        _to_correction_record(correction)
        for correction in session.scalars(correction_statement)
    ]
    return build_effective_event_stream(raw_events, corrections)


def _load_monthly_report(session: Session, year: int, month: int) -> MonthlyReport:
    effective_stream = _load_effective_event_stream(session)
    location_display_names = dict(
        session.execute(
            select(LocationDisplayName.location, LocationDisplayName.display_name)
        ).all()
    )
    rates = [
        _to_pay_rate_record(rate)
        for rate in session.scalars(
            select(PayRate).order_by(PayRate.effective_from.asc(), PayRate.id.asc())
        )
    ]
    return build_monthly_report(
        effective_stream.events,
        rates,
        year=year,
        month=month,
        generated_at=datetime.now(timezone.utc),
        location_display_names=location_display_names,
    )


def create_app(
    settings: Settings | None = None,
    frontend_dist: Path | None = None,
    now_provider: Callable[[], datetime] | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    dashboard_now_provider = now_provider or (lambda: datetime.now(timezone.utc))
    app = FastAPI(title="Work Tracker API", version="0.1.0")
    app.state.session_factory = build_session_factory(settings.database_url)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Content-Type", "X-Webhook-Token"],
    )

    def require_webhook_token(provided_token: str | None) -> None:
        if provided_token is None or not hmac.compare_digest(
            provided_token, settings.webhook_token
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or missing webhook token",
            )

    def integration_error(error: Exception, code: str) -> dict[str, str]:
        return {"code": code, "message": str(error)}

    @app.post("/api/webhook/home-assistant", response_model=WebhookAccepted, status_code=status.HTTP_201_CREATED)
    def receive_home_assistant_webhook(
        payload: HomeAssistantWebhook,
        x_webhook_token: str | None = Header(default=None),
        session: Session = Depends(get_session),
    ) -> WebhookAccepted:
        require_webhook_token(x_webhook_token)

        event = WorkEvent(
            event_type=payload.event.value,
            location=payload.location,
            event_timestamp=payload.timestamp,
            event_timestamp_utc=payload.timestamp.astimezone(timezone.utc),
            received_at=datetime.now(timezone.utc),
            source=payload.source,
        )
        session.add(event)
        session.commit()
        session.refresh(event)
        alias = session.get(LocationDisplayName, event.location)
        return WebhookAccepted(
            id=event.id,
            event=event.event_type,
            location=event.location,
            location_display_name=(alias.display_name if alias is not None else event.location),
            timestamp=event.event_timestamp,
        )

    @app.post(
        "/api/webhook/home-assistant/correction",
        response_model=HomeAssistantCorrectionResponse,
    )
    def receive_home_assistant_correction(
        payload: HomeAssistantCorrectionRequest,
        x_webhook_token: str | None = Header(default=None),
        session: Session = Depends(get_session),
    ) -> HomeAssistantCorrectionResponse:
        require_webhook_token(x_webhook_token)
        raw_event = session.get(WorkEvent, payload.raw_event_id)
        if raw_event is None:
            error = RawEventNotFoundError()
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=integration_error(error, error.code),
            )

        existing_correction = session.scalar(
            select(WorkEventCorrection).where(
                WorkEventCorrection.raw_event_id == raw_event.id
            )
        )
        if (
            existing_correction is not None
            and existing_correction.correction_type
            != CorrectionType.TIMESTAMP_OVERRIDE.value
        ):
            error = CorrectionConflictError()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=integration_error(error, error.code),
            )

        timezone_setting = session.get(LocationTimezone, raw_event.location)
        if timezone_setting is None:
            error = TimeOnlyCorrectionError(
                "location_timezone_missing",
                "Location timezone is not configured",
            )
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=integration_error(error, error.code),
            )
        try:
            effective_timestamp = resolve_time_only_timestamp(
                raw_event.event_timestamp_utc,
                timezone_setting.timezone,
                payload.time,
            )
        except TimeOnlyCorrectionError as error:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=integration_error(error, error.code),
            ) from error

        if (
            effective_timestamp.astimezone(timezone.utc)
            == raw_event.event_timestamp_utc.astimezone(timezone.utc)
        ):
            if existing_correction is not None:
                session.delete(existing_correction)
                session.commit()
            correction_id = None
            response_timestamp = raw_event.event_timestamp
        else:
            try:
                _, correction = upsert_timestamp_correction(
                    session,
                    raw_event.id,
                    effective_timestamp,
                )
            except CorrectionConflictError as error:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=integration_error(error, error.code),
                ) from error
            session.commit()
            session.refresh(correction)
            correction_id = correction.id
            response_timestamp = correction.event_timestamp

        alias = session.get(LocationDisplayName, raw_event.location)
        return HomeAssistantCorrectionResponse(
            raw_event_id=raw_event.id,
            correction_id=correction_id,
            event=raw_event.event_type,
            location=raw_event.location,
            location_display_name=(
                alias.display_name if alias is not None else raw_event.location
            ),
            original_timestamp=raw_event.event_timestamp,
            effective_timestamp=response_timestamp,
        )

    @app.get("/api/work-events", response_model=list[WorkEventResponse])
    def list_work_events(
        year: int = Query(ge=2000, le=2100),
        month: int = Query(ge=1, le=12),
        session: Session = Depends(get_session),
    ) -> list[WorkEvent]:
        next_year, next_month = (year + 1, 1) if month == 12 else (year, month + 1)
        start = f"{year:04d}-{month:02d}-01T00:00:00"
        end = f"{next_year:04d}-{next_month:02d}-01T00:00:00"
        statement = (
            select(WorkEvent)
            .where(WorkEvent.event_timestamp >= start, WorkEvent.event_timestamp < end)
            .order_by(WorkEvent.event_timestamp_utc.asc(), WorkEvent.id.asc())
        )
        return list(session.scalars(statement))

    @app.put(
        "/api/work-events/{raw_event_id}/timestamp-correction",
        response_model=CorrectionResponse,
    )
    def set_timestamp_correction(
        raw_event_id: int,
        payload: TimestampCorrectionRequest,
        session: Session = Depends(get_session),
    ) -> WorkEventCorrection:
        try:
            _, correction = upsert_timestamp_correction(
                session,
                raw_event_id,
                payload.timestamp,
            )
        except RawEventNotFoundError as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Raw event not found",
            ) from error
        except CorrectionConflictError as error:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Raw event already has a conflicting correction",
            ) from error
        session.commit()
        session.refresh(correction)
        return correction

    @app.put(
        "/api/work-events/{raw_event_id}/ignore",
        response_model=CorrectionResponse,
    )
    def ignore_raw_event(
        raw_event_id: int,
        session: Session = Depends(get_session),
    ) -> WorkEventCorrection:
        if session.get(WorkEvent, raw_event_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Raw event not found")

        correction = session.scalar(
            select(WorkEventCorrection).where(WorkEventCorrection.raw_event_id == raw_event_id)
        )
        if correction:
            if correction.correction_type == CorrectionType.IGNORE_EVENT.value:
                return correction
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Raw event already has a conflicting correction",
            )

        now = datetime.now(timezone.utc)
        correction = WorkEventCorrection(
            correction_type=CorrectionType.IGNORE_EVENT.value,
            created_at=now,
            updated_at=now,
            raw_event_id=raw_event_id,
        )
        session.add(correction)
        session.commit()
        session.refresh(correction)
        return correction

    @app.post(
        "/api/manual-events",
        response_model=CorrectionResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def add_manual_event(
        payload: ManualEventRequest,
        session: Session = Depends(get_session),
    ) -> WorkEventCorrection:
        now = datetime.now(timezone.utc)
        correction = WorkEventCorrection(
            correction_type=CorrectionType.MANUAL_EVENT.value,
            created_at=now,
            updated_at=now,
            event_type=payload.event.value,
            event_timestamp=payload.timestamp,
            event_timestamp_utc=payload.timestamp.astimezone(timezone.utc),
            location=payload.location,
        )
        session.add(correction)
        session.commit()
        session.refresh(correction)
        return correction

    @app.get("/api/corrections", response_model=list[CorrectionResponse])
    def list_corrections(session: Session = Depends(get_session)) -> list[WorkEventCorrection]:
        statement = select(WorkEventCorrection).order_by(WorkEventCorrection.created_at, WorkEventCorrection.id)
        return list(session.scalars(statement))

    @app.delete("/api/corrections/{correction_id}", status_code=status.HTTP_204_NO_CONTENT)
    def delete_correction(
        correction_id: int,
        session: Session = Depends(get_session),
    ) -> Response:
        correction = session.get(WorkEventCorrection, correction_id)
        if correction is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Correction not found")
        session.delete(correction)
        session.commit()
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.get("/api/work-summary", response_model=MonthlyWorkSummaryResponse)
    def get_work_summary(
        year: int = Query(ge=2000, le=2100),
        month: int = Query(ge=1, le=12),
        session: Session = Depends(get_session),
    ) -> MonthlyWorkSummaryResponse:
        effective_stream = _load_effective_event_stream(session)
        summary = calculate_monthly_work_time(effective_stream.events, year, month)
        calculated_days = {day.date: day for day in summary.days}
        ignored_by_day: dict[date, list[EffectiveEventMetadata]] = {}
        for ignored_event in effective_stream.ignored_events:
            local_date = ignored_event.event_timestamp.date()
            if local_date.year == year and local_date.month == month:
                ignored_by_day.setdefault(local_date, []).append(ignored_event)

        return MonthlyWorkSummaryResponse(
            year=summary.year,
            month=summary.month,
            total_duration_seconds=summary.total_duration_seconds,
            work_days=summary.work_days,
            anomaly_count=summary.anomaly_count,
            days=[
                WorkDayResponse(
                    date=local_date,
                    total_duration_seconds=(
                        calculated_days[local_date].total_duration_seconds
                        if local_date in calculated_days
                        else 0
                    ),
                    anomaly_count=(
                        calculated_days[local_date].anomaly_count
                        if local_date in calculated_days
                        else 0
                    ),
                    items=[
                        WorkTimeItemResponse(
                            status=item.status,
                            location=item.location,
                            local_date=item.local_date,
                            duration_seconds=item.duration_seconds,
                            events=[
                                _to_effective_event_response(
                                    effective_stream.metadata_by_id[event.id]
                                )
                                for event in item.events
                            ],
                        )
                        for item in (
                            calculated_days[local_date].items
                            if local_date in calculated_days
                            else ()
                        )
                    ],
                    ignored_events=[
                        _to_effective_event_response(event)
                        for event in ignored_by_day.get(local_date, [])
                    ],
                )
                for local_date in sorted(set(calculated_days) | set(ignored_by_day))
            ],
        )

    @app.get("/api/pay-rates", response_model=list[PayRateResponse])
    def list_pay_rates(session: Session = Depends(get_session)) -> list[PayRate]:
        statement = select(PayRate).order_by(PayRate.effective_from.asc(), PayRate.id.asc())
        return list(session.scalars(statement))

    @app.get("/api/application-settings", response_model=ApplicationSettingsResponse)
    def get_application_settings(
        session: Session = Depends(get_session),
    ) -> ApplicationSettingsResponse:
        return _to_application_settings_response(load_application_settings(session))

    @app.put("/api/application-settings", response_model=ApplicationSettingsResponse)
    def update_application_settings(
        payload: ApplicationSettingsUpdate,
        session: Session = Depends(get_session),
    ) -> ApplicationSettingsResponse:
        now = datetime.now(timezone.utc)
        stored_settings = session.get(ApplicationSetting, APPLICATION_SETTINGS_ID)
        if stored_settings is None:
            stored_settings = ApplicationSetting(
                id=APPLICATION_SETTINGS_ID,
                application_title=payload.application_title,
                updated_at=now,
            )
            session.add(stored_settings)
        else:
            stored_settings.application_title = payload.application_title
            stored_settings.updated_at = now

        for location_setting in payload.locations:
            stored_alias = session.get(LocationDisplayName, location_setting.location)
            if location_setting.display_name is None:
                if stored_alias is not None:
                    session.delete(stored_alias)
            elif stored_alias is None:
                session.add(
                    LocationDisplayName(
                        location=location_setting.location,
                        display_name=location_setting.display_name,
                        updated_at=now,
                    )
                )
            else:
                stored_alias.display_name = location_setting.display_name
                stored_alias.updated_at = now

            if "timezone" not in location_setting.model_fields_set:
                continue
            stored_timezone = session.get(LocationTimezone, location_setting.location)
            if location_setting.timezone is None:
                if stored_timezone is not None:
                    session.delete(stored_timezone)
            elif stored_timezone is None:
                session.add(
                    LocationTimezone(
                        location=location_setting.location,
                        timezone=location_setting.timezone,
                        updated_at=now,
                    )
                )
            else:
                stored_timezone.timezone = location_setting.timezone
                stored_timezone.updated_at = now

        session.commit()
        return _to_application_settings_response(load_application_settings(session))

    @app.post(
        "/api/pay-rates",
        response_model=PayRateResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def create_pay_rate(
        payload: PayRateRequest,
        session: Session = Depends(get_session),
    ) -> PayRate:
        rate = PayRate(
            effective_from=payload.effective_from,
            hourly_rate=payload.hourly_rate,
            currency=payload.currency,
            created_at=datetime.now(timezone.utc),
        )
        session.add(rate)
        try:
            session.commit()
        except IntegrityError as error:
            session.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A pay rate already exists for this effective date",
            ) from error
        session.refresh(rate)
        return rate

    @app.get("/api/pay-summary", response_model=MonthlyPaySummaryResponse)
    def get_pay_summary(
        year: int = Query(ge=2000, le=2100),
        month: int = Query(ge=1, le=12),
        session: Session = Depends(get_session),
    ) -> MonthlyPaySummary:
        effective_stream = _load_effective_event_stream(session)
        work_summary = calculate_monthly_work_time(effective_stream.events, year, month)
        rates = [
            _to_pay_rate_record(rate)
            for rate in session.scalars(
                select(PayRate).order_by(PayRate.effective_from.asc(), PayRate.id.asc())
            )
        ]
        try:
            return calculate_monthly_pay(work_summary, rates)
        except (MissingPayRateError, MixedCurrenciesError) as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error

    @app.get("/api/dashboard", response_model=DashboardResponse)
    def get_dashboard(
        timezone_name: str = Query(
            default="UTC",
            alias="timezone",
            min_length=1,
            max_length=100,
            pattern=r"^[A-Za-z0-9_+./-]+$",
        ),
        session: Session = Depends(get_session),
    ) -> DashboardSummary:
        try:
            local_timezone = ZoneInfo(timezone_name)
        except (ValueError, ZoneInfoNotFoundError) as error:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Unknown timezone",
            ) from error

        effective_stream = _load_effective_event_stream(session)
        rates = [
            _to_pay_rate_record(rate)
            for rate in session.scalars(
                select(PayRate).order_by(PayRate.effective_from.asc(), PayRate.id.asc())
            )
        ]
        try:
            return calculate_dashboard(
                effective_stream.events,
                rates,
                now=dashboard_now_provider(),
                local_timezone=local_timezone,
            )
        except (MissingPayRateError, MixedCurrenciesError) as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error

    @app.get("/api/export/monthly.csv")
    def export_monthly_csv(
        year: int = Query(ge=2000, le=2100),
        month: int = Query(ge=1, le=12),
        session: Session = Depends(get_session),
    ) -> Response:
        try:
            report = _load_monthly_report(session, year, month)
        except (MissingPayRateError, MixedCurrenciesError) as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
        filename = f"work-tracker-{year:04d}-{month:02d}.csv"
        return Response(
            content=render_monthly_report_csv(report),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.get("/api/export/monthly.pdf")
    def export_monthly_pdf(
        year: int = Query(ge=2000, le=2100),
        month: int = Query(ge=1, le=12),
        session: Session = Depends(get_session),
    ) -> Response:
        try:
            report = _load_monthly_report(session, year, month)
        except (MissingPayRateError, MixedCurrenciesError) as error:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
        filename = f"work-tracker-{year:04d}-{month:02d}.pdf"
        return Response(
            content=render_monthly_report_pdf(report),
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    frontend_dist = frontend_dist or Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if frontend_dist.is_dir():
        app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="frontend")

    return app


app = create_app()
