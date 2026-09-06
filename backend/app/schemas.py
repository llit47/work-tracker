from datetime import datetime
from enum import Enum
import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

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
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must include a timezone offset")
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
    received_at: datetime
    source: str
