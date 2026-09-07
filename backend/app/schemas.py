from datetime import date, datetime
from enum import Enum
import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .models import CorrectionType
from .work_time import SessionStatus

LOCATION_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,99}$")


class EventType(str, Enum):
    ENTRY = "entry"
    EXIT = "exit"


class HomeAssistantWebhook(BaseModel):
    event: EventType
    location: str = Field(min_length=1, max_length=100)
    timestamp: datetime
    source: str = Field(min_length=1, max_length=100)

    @field_validator("location")
    @classmethod
    def validate_location(cls, value: str) -> str:
        if not LOCATION_PATTERN.fullmatch(value):
            raise ValueError("location must use lowercase letters, digits, and underscores")
        return value

    @field_validator("timestamp")
    @classmethod
    def validate_timezone(cls, value: datetime) -> datetime:
        return validate_aware_timestamp(value)

    @field_validator("source")
    @classmethod
    def validate_source(cls, value: str) -> str:
        if value != "home_assistant":
            raise ValueError("source must be home_assistant")
        return value


class WebhookAccepted(BaseModel):
    id: int
    status: str = "accepted"


class WorkEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    event_type: EventType
    location: str
    event_timestamp: datetime
    event_timestamp_utc: datetime
    received_at: datetime
    source: str


class TimestampCorrectionRequest(BaseModel):
    timestamp: datetime

    @field_validator("timestamp")
    @classmethod
    def validate_timezone(cls, value: datetime) -> datetime:
        return validate_correction_timestamp(value)


class ManualEventRequest(BaseModel):
    event: EventType
    location: str = Field(min_length=1, max_length=100)
    timestamp: datetime

    @field_validator("location")
    @classmethod
    def validate_location(cls, value: str) -> str:
        if not LOCATION_PATTERN.fullmatch(value):
            raise ValueError("location must use lowercase letters, digits, and underscores")
        return value

    @field_validator("timestamp")
    @classmethod
    def validate_timezone(cls, value: datetime) -> datetime:
        return validate_correction_timestamp(value)


class CorrectionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    correction_type: CorrectionType
    created_at: datetime
    updated_at: datetime
    raw_event_id: int | None
    event_type: EventType | None
    event_timestamp: datetime | None
    event_timestamp_utc: datetime | None
    location: str | None


class EffectiveWorkEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    event_type: EventType
    location: str
    event_timestamp: datetime
    event_timestamp_utc: datetime
    received_at: datetime
    source: str
    raw_event_id: int | None
    correction_id: int | None
    correction_type: CorrectionType | None
    original_event_timestamp: datetime | None
    is_manual: bool
    is_ignored: bool
    is_timestamp_corrected: bool


class WorkTimeItemResponse(BaseModel):
    status: SessionStatus
    location: str
    local_date: date
    duration_seconds: int | None
    events: list[EffectiveWorkEventResponse]


class WorkDayResponse(BaseModel):
    date: date
    total_duration_seconds: int
    anomaly_count: int
    items: list[WorkTimeItemResponse]
    ignored_events: list[EffectiveWorkEventResponse] = Field(default_factory=list)


class MonthlyWorkSummaryResponse(BaseModel):
    year: int
    month: int
    total_duration_seconds: int
    work_days: int
    anomaly_count: int
    days: list[WorkDayResponse]


def validate_aware_timestamp(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must include a timezone offset")
    return value


def validate_correction_timestamp(value: datetime) -> datetime:
    value = validate_aware_timestamp(value)
    if not 2000 <= value.year <= 2100:
        raise ValueError("correction timestamp year must be between 2000 and 2100")
    return value
