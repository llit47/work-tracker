from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum

from sqlalchemy import CheckConstraint, Date, ForeignKey, Index, Integer, String, Text, UniqueConstraint
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


class ExactDecimal(TypeDecorator):
    """Store fixed-point decimals as canonical text because SQLite NUMERIC uses REAL."""

    impl = String(20)
    cache_ok = True

    def process_bind_param(self, value: Decimal | int | str | None, dialect):
        if value is None:
            return None
        if isinstance(value, (bool, float)):
            raise ValueError("Exact decimal values cannot use binary floating point")
        decimal_value = Decimal(value)
        if not decimal_value.is_finite() or decimal_value.as_tuple().exponent < -2:
            raise ValueError("Exact decimal values require at most two decimal places")
        return format(decimal_value.quantize(Decimal("0.01")), "f")

    def process_result_value(self, value: str | None, dialect):
        return Decimal(value) if value is not None else None


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


class PayRate(Base):
    __tablename__ = "pay_rates"
    __table_args__ = (
        CheckConstraint(
            "hourly_rate GLOB '[0-9]*.[0-9][0-9]' "
            "AND hourly_rate NOT GLOB '*[^0-9.]*' "
            "AND length(hourly_rate) - length(replace(hourly_rate, '.', '')) = 1 "
            "AND CAST(hourly_rate AS NUMERIC) > 0 "
            "AND CAST(hourly_rate AS NUMERIC) <= 1000000.00",
            name="ck_pay_rates_hourly_rate",
        ),
        CheckConstraint(
            "length(currency) = 3 AND currency GLOB '[A-Z][A-Z][A-Z]'",
            name="ck_pay_rates_currency",
        ),
        UniqueConstraint("effective_from", name="uq_pay_rates_effective_from"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    hourly_rate: Mapped[Decimal] = mapped_column(ExactDecimal(), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="PLN")
    created_at: Mapped[datetime] = mapped_column(AwareDateTime(), nullable=False)


class ApplicationSetting(Base):
    __tablename__ = "application_settings"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_application_settings_singleton"),
        CheckConstraint(
            "length(trim(application_title)) BETWEEN 1 AND 100",
            name="ck_application_settings_title",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    application_title: Mapped[str] = mapped_column(String(100), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(AwareDateTime(), nullable=False)


class LocationDisplayName(Base):
    __tablename__ = "location_display_names"
    __table_args__ = (
        CheckConstraint(
            "length(trim(display_name)) BETWEEN 1 AND 100",
            name="ck_location_display_names_value",
        ),
    )

    location: Mapped[str] = mapped_column(String(100), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(AwareDateTime(), nullable=False)


class LocationTimezone(Base):
    __tablename__ = "location_timezones"
    __table_args__ = (
        CheckConstraint(
            "length(trim(timezone)) BETWEEN 1 AND 100",
            name="ck_location_timezones_value",
        ),
    )

    location: Mapped[str] = mapped_column(String(100), primary_key=True)
    timezone: Mapped[str] = mapped_column(String(100), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(AwareDateTime(), nullable=False)


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "length(username) BETWEEN 3 AND 64 "
            "AND instr(username, char(0)) = 0 "
            "AND username NOT GLOB '*[^a-z0-9._-]*' "
            "AND substr(username, 1, 1) GLOB '[a-z0-9]'",
            name="ck_users_username",
        ),
        CheckConstraint("length(password_hash) > 0", name="ck_users_password_hash"),
        UniqueConstraint("username", name="uq_users_username"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(AwareDateTime(), nullable=False)


class UTCDateTime(AwareDateTime):
    """Fixed-width UTC text for exact SQLite session deadline comparisons."""

    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect):
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Timestamp must include a timezone offset")
        return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


class UserSession(Base):
    __tablename__ = "user_sessions"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_user_sessions_token_hash"),
        Index("ix_user_sessions_user_id", "user_id"),
        CheckConstraint(
            "length(token_hash) = 64 AND token_hash NOT GLOB '*[^0-9a-f]*'",
            name="ck_user_sessions_token_hash",
        ),
        CheckConstraint(
            "created_at <= last_seen_at AND last_seen_at < expires_at "
            "AND (revoked_at IS NULL OR revoked_at >= created_at)",
            name="ck_user_sessions_dates",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
