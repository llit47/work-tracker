from datetime import datetime
from enum import Enum

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String, UniqueConstraint
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
        CheckConstraint("event_type IN ('entry', 'exit')", name="ck_work_events_event_type"),
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


class CorrectionType(str, Enum):
    TIMESTAMP_OVERRIDE = "timestamp_override"
    IGNORE_EVENT = "ignore_event"
    MANUAL_EVENT = "manual_event"


class WorkEventCorrection(Base):
    __tablename__ = "work_event_corrections"
    __table_args__ = (
        CheckConstraint(
            "correction_type IN ('timestamp_override', 'ignore_event', 'manual_event')",
            name="ck_work_event_corrections_type",
        ),
        CheckConstraint(
            "event_type IS NULL OR event_type IN ('entry', 'exit')",
            name="ck_work_event_corrections_event_type",
        ),
        CheckConstraint(
            "(correction_type = 'timestamp_override' AND raw_event_id IS NOT NULL "
            "AND event_type IS NULL AND event_timestamp IS NOT NULL "
            "AND event_timestamp_utc IS NOT NULL AND location IS NULL) OR "
            "(correction_type = 'ignore_event' AND raw_event_id IS NOT NULL "
            "AND event_type IS NULL AND event_timestamp IS NULL "
            "AND event_timestamp_utc IS NULL AND location IS NULL) OR "
            "(correction_type = 'manual_event' AND raw_event_id IS NULL "
            "AND event_type IS NOT NULL AND event_timestamp IS NOT NULL "
            "AND event_timestamp_utc IS NOT NULL AND location IS NOT NULL)",
            name="ck_work_event_corrections_shape",
        ),
        UniqueConstraint("raw_event_id", name="uq_work_event_corrections_raw_event_id"),
        Index("ix_work_event_corrections_event_timestamp_utc", "event_timestamp_utc"),
        Index("ix_work_event_corrections_location", "location"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    correction_type: Mapped[str] = mapped_column(String(30), nullable=False)
    created_at: Mapped[datetime] = mapped_column(AwareDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(AwareDateTime(), nullable=False)
    raw_event_id: Mapped[int | None] = mapped_column(
        ForeignKey("work_events.id", ondelete="RESTRICT"), nullable=True
    )
    event_type: Mapped[str | None] = mapped_column(String(10), nullable=True)
    event_timestamp: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)
    event_timestamp_utc: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)
    location: Mapped[str | None] = mapped_column(String(100), nullable=True)
