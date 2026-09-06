from datetime import datetime, timezone
import hmac
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import Settings, get_settings
from .database import build_session_factory, get_session
from .models import WorkEvent
from .schemas import HomeAssistantWebhook, WebhookAccepted, WorkEventResponse


def create_app(settings: Settings | None = None, frontend_dist: Path | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(title="Work Tracker API", version="0.1.0")
    app.state.session_factory = build_session_factory(settings.database_url)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "X-Webhook-Token"],
    )

    @app.post("/api/webhook/home-assistant", response_model=WebhookAccepted, status_code=status.HTTP_201_CREATED)
    def receive_home_assistant_webhook(
        payload: HomeAssistantWebhook,
        x_webhook_token: str | None = Header(default=None),
        session: Session = Depends(get_session),
    ) -> WebhookAccepted:
        if x_webhook_token is None or not hmac.compare_digest(x_webhook_token, settings.webhook_token):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or missing webhook token")

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
        return WebhookAccepted(id=event.id)

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

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    frontend_dist = frontend_dist or Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if frontend_dist.is_dir():
        app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="frontend")

    return app


app = create_app()
