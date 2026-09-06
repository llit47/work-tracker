from datetime import datetime

from sqlalchemy import Index, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator


class Base(DeclarativeBase):
    pass


class AwareDateTime(TypeDecorator):
    """Store ISO-8601 timestamps without losing their UTC offset in SQLite."""

    impl = String(40)
    cache_ok = True

    def process_bind_param(self, value: datetime | str | None, dialect):
        if value is None:
            return None
        # Month-range query bounds are already canonical ISO-8601 strings.
        if isinstance(value, str):
            return value
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Timestamp must include a timezone offset")
        return value.isoformat()

    def process_result_value(self, value: str | None, dialect):
        return datetime.fromisoformat(value) if value is not None else None


class WorkEvent(Base):
    __tablename__ = "work_events"
    __table_args__ = (
        Index("ix_work_events_event_timestamp", "event_timestamp"),
        Index("ix_work_events_location", "location"),
        Index("ix_work_events_event_timestamp_utc", "event_timestamp_utc"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_type: Mapped[str] = mapped_column(String(10), nullable=False)
    location: Mapped[str] = mapped_column(String(100), nullable=False)
    event_timestamp: Mapped[datetime] = mapped_column(AwareDateTime(), nullable=False)
    event_timestamp_utc: Mapped[datetime] = mapped_column(AwareDateTime(), nullable=False)
    received_at: Mapped[datetime] = mapped_column(AwareDateTime(), nullable=False)
    source: Mapped[str] = mapped_column(String(100), nullable=False)
