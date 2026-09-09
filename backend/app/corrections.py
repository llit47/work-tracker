from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
import re
from typing import Iterable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import CorrectionType, WorkEvent, WorkEventCorrection
from .work_time import RawWorkEvent

TIME_ONLY_PATTERN = re.compile(r"^(?P<hour>[0-9]{1,2})[:.](?P<minute>[0-9]{2})$")
HA_CORRECTION_WINDOW = timedelta(hours=4)


class CorrectionServiceError(Exception):
    code: str

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class RawEventNotFoundError(CorrectionServiceError):
    def __init__(self):
        super().__init__("raw_event_not_found", "Raw event not found")


class CorrectionConflictError(CorrectionServiceError):
    def __init__(self):
        super().__init__(
            "correction_conflict",
            "Raw event already has a conflicting correction",
        )


class TimeOnlyCorrectionError(CorrectionServiceError):
    pass


@dataclass(frozen=True)
class CorrectionRecord:
    id: int
    correction_type: CorrectionType
    created_at: datetime
    updated_at: datetime
    raw_event_id: int | None = None
    event_type: str | None = None
    event_timestamp: datetime | None = None
    event_timestamp_utc: datetime | None = None
    location: str | None = None


@dataclass(frozen=True)
class EffectiveEventMetadata:
    id: int
    event_type: str
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


@dataclass(frozen=True)
class EffectiveEventStream:
    events: tuple[RawWorkEvent, ...]
    metadata_by_id: dict[int, EffectiveEventMetadata]
    ignored_events: tuple[EffectiveEventMetadata, ...]


def parse_time_only(value: str) -> time:
    match = TIME_ONLY_PATTERN.fullmatch(value.strip())
    if match is None:
        raise TimeOnlyCorrectionError(
            "invalid_time",
            "Time must use H:MM, HH:MM, H.MM, or HH.MM format",
        )
    hour = int(match.group("hour"))
    minute = int(match.group("minute"))
    if hour > 23 or minute > 59:
        raise TimeOnlyCorrectionError("invalid_time", "Time is outside the 00:00-23:59 range")
    return time(hour=hour, minute=minute)


def resolve_time_only_timestamp(
    raw_instant: datetime,
    timezone_name: str,
    value: str,
) -> datetime:
    """Resolve a wall-clock time near one raw instant without guessing a DST fold."""
    requested_time = parse_time_only(value)
    if raw_instant.tzinfo is None or raw_instant.utcoffset() is None:
        raise ValueError("Raw event timestamp must be timezone-aware")
    try:
        location_timezone = ZoneInfo(timezone_name)
    except (ValueError, ZoneInfoNotFoundError) as error:
        raise TimeOnlyCorrectionError(
            "location_timezone_invalid",
            "Location timezone is not a valid IANA timezone",
        ) from error

    raw_utc = raw_instant.astimezone(timezone.utc)
    raw_date = raw_utc.astimezone(location_timezone).date()
    candidates: dict[datetime, datetime] = {}
    valid_on_raw_date = False
    for day_offset in (-1, 0, 1):
        candidate_date = raw_date + timedelta(days=day_offset)
        naive_wall_time = datetime.combine(candidate_date, requested_time)
        for fold in (0, 1):
            tentative = naive_wall_time.replace(tzinfo=location_timezone, fold=fold)
            candidate_utc = tentative.astimezone(timezone.utc)
            round_trip = candidate_utc.astimezone(location_timezone)
            if round_trip.replace(tzinfo=None) != naive_wall_time:
                continue
            candidates[candidate_utc] = round_trip
            if day_offset == 0:
                valid_on_raw_date = True

    if not candidates:
        raise TimeOnlyCorrectionError(
            "time_not_resolvable",
            "Time does not identify a real local instant",
        )

    eligible = {
        candidate_utc: candidate_local
        for candidate_utc, candidate_local in candidates.items()
        if abs(candidate_utc - raw_utc) <= HA_CORRECTION_WINDOW
    }
    if not eligible:
        code = "time_out_of_range" if valid_on_raw_date else "time_not_resolvable"
        message = (
            "Time is more than four hours from the raw event"
            if code == "time_out_of_range"
            else "Time does not identify a real local instant near the raw event"
        )
        raise TimeOnlyCorrectionError(code, message)

    nearest_distance = min(abs(candidate_utc - raw_utc) for candidate_utc in eligible)
    nearest = [
        candidate_local
        for candidate_utc, candidate_local in eligible.items()
        if abs(candidate_utc - raw_utc) == nearest_distance
    ]
    if len(nearest) != 1:
        raise TimeOnlyCorrectionError(
            "time_not_resolvable",
            "Time is ambiguous near the raw event",
        )
    return nearest[0].replace(second=0, microsecond=0)


def upsert_timestamp_correction(
    session: Session,
    raw_event_id: int,
    effective_timestamp: datetime,
    *,
    now: datetime | None = None,
) -> tuple[WorkEvent, WorkEventCorrection]:
    """Set the one auditable timestamp override without mutating its raw event."""
    if effective_timestamp.tzinfo is None or effective_timestamp.utcoffset() is None:
        raise ValueError("Correction timestamp must be timezone-aware")
    raw_event = session.get(WorkEvent, raw_event_id)
    if raw_event is None:
        raise RawEventNotFoundError()

    correction = session.scalar(
        select(WorkEventCorrection).where(WorkEventCorrection.raw_event_id == raw_event_id)
    )
    if correction and correction.correction_type != CorrectionType.TIMESTAMP_OVERRIDE.value:
        raise CorrectionConflictError()

    changed_at = now or datetime.now(timezone.utc)
    if correction is None:
        correction = WorkEventCorrection(
            correction_type=CorrectionType.TIMESTAMP_OVERRIDE.value,
            created_at=changed_at,
            updated_at=changed_at,
            raw_event_id=raw_event_id,
            event_timestamp=effective_timestamp,
            event_timestamp_utc=effective_timestamp.astimezone(timezone.utc),
        )
        session.add(correction)
    else:
        correction.event_timestamp = effective_timestamp
        correction.event_timestamp_utc = effective_timestamp.astimezone(timezone.utc)
        correction.updated_at = changed_at
    return raw_event, correction


def build_effective_event_stream(
    raw_events: Iterable[RawWorkEvent], corrections: Iterable[CorrectionRecord]
) -> EffectiveEventStream:
    """Apply corrections without mutating the raw event objects."""
    corrections_by_raw_event: dict[int, CorrectionRecord] = {}
    manual_corrections: list[CorrectionRecord] = []

    for correction in corrections:
        _validate_correction(correction)
        if correction.correction_type is CorrectionType.MANUAL_EVENT:
            manual_corrections.append(correction)
            continue
        assert correction.raw_event_id is not None
        if correction.raw_event_id in corrections_by_raw_event:
            raise ValueError(f"Multiple corrections for raw event {correction.raw_event_id}")
        corrections_by_raw_event[correction.raw_event_id] = correction

    effective_events: list[RawWorkEvent] = []
    metadata_by_id: dict[int, EffectiveEventMetadata] = {}
    ignored_events: list[EffectiveEventMetadata] = []
    raw_event_ids: set[int] = set()

    for raw_event in raw_events:
        raw_event_ids.add(raw_event.id)
        correction = corrections_by_raw_event.get(raw_event.id)

        if correction and correction.correction_type is CorrectionType.IGNORE_EVENT:
            ignored_events.append(
                _raw_metadata(
                    raw_event,
                    correction=correction,
                    event_timestamp=raw_event.event_timestamp,
                    event_timestamp_utc=raw_event.event_timestamp_utc,
                    is_ignored=True,
                )
            )
            continue

        effective_timestamp = raw_event.event_timestamp
        effective_timestamp_utc = raw_event.event_timestamp_utc
        if correction:
            assert correction.correction_type is CorrectionType.TIMESTAMP_OVERRIDE
            assert correction.event_timestamp is not None
            assert correction.event_timestamp_utc is not None
            effective_timestamp = correction.event_timestamp
            effective_timestamp_utc = correction.event_timestamp_utc

        effective_event = RawWorkEvent(
            id=raw_event.id,
            event_type=raw_event.event_type,
            location=raw_event.location,
            event_timestamp=effective_timestamp,
            event_timestamp_utc=effective_timestamp_utc,
            received_at=raw_event.received_at,
            source=raw_event.source,
        )
        effective_events.append(effective_event)
        metadata_by_id[effective_event.id] = _raw_metadata(
            raw_event,
            correction=correction,
            event_timestamp=effective_timestamp,
            event_timestamp_utc=effective_timestamp_utc,
        )

    missing_raw_ids = set(corrections_by_raw_event) - raw_event_ids
    if missing_raw_ids:
        raise ValueError(f"Corrections reference missing raw events: {sorted(missing_raw_ids)}")

    for correction in manual_corrections:
        assert correction.event_type is not None
        assert correction.location is not None
        assert correction.event_timestamp is not None
        assert correction.event_timestamp_utc is not None
        effective_id = -correction.id
        manual_event = RawWorkEvent(
            id=effective_id,
            event_type=correction.event_type,
            location=correction.location,
            event_timestamp=correction.event_timestamp,
            event_timestamp_utc=correction.event_timestamp_utc,
            received_at=correction.created_at,
            source="manual",
        )
        effective_events.append(manual_event)
        metadata_by_id[effective_id] = EffectiveEventMetadata(
            id=effective_id,
            event_type=manual_event.event_type,
            location=manual_event.location,
            event_timestamp=manual_event.event_timestamp,
            event_timestamp_utc=manual_event.event_timestamp_utc,
            received_at=manual_event.received_at,
            source=manual_event.source,
            raw_event_id=None,
            correction_id=correction.id,
            correction_type=correction.correction_type,
            original_event_timestamp=None,
            is_manual=True,
            is_ignored=False,
            is_timestamp_corrected=False,
        )

    return EffectiveEventStream(
        events=tuple(effective_events),
        metadata_by_id=metadata_by_id,
        ignored_events=tuple(
            sorted(ignored_events, key=lambda event: (event.event_timestamp_utc, event.id))
        ),
    )


def _raw_metadata(
    raw_event: RawWorkEvent,
    *,
    correction: CorrectionRecord | None,
    event_timestamp: datetime,
    event_timestamp_utc: datetime,
    is_ignored: bool = False,
) -> EffectiveEventMetadata:
    return EffectiveEventMetadata(
        id=raw_event.id,
        event_type=raw_event.event_type,
        location=raw_event.location,
        event_timestamp=event_timestamp,
        event_timestamp_utc=event_timestamp_utc,
        received_at=raw_event.received_at,
        source=raw_event.source,
        raw_event_id=raw_event.id,
        correction_id=correction.id if correction else None,
        correction_type=correction.correction_type if correction else None,
        original_event_timestamp=raw_event.event_timestamp,
        is_manual=False,
        is_ignored=is_ignored,
        is_timestamp_corrected=(
            correction is not None
            and correction.correction_type is CorrectionType.TIMESTAMP_OVERRIDE
        ),
    )


def _validate_correction(correction: CorrectionRecord) -> None:
    timestamps = (correction.created_at, correction.updated_at)
    if correction.event_timestamp is not None:
        timestamps += (correction.event_timestamp,)
    if correction.event_timestamp_utc is not None:
        timestamps += (correction.event_timestamp_utc,)
    if any(timestamp.tzinfo is None or timestamp.utcoffset() is None for timestamp in timestamps):
        raise ValueError("Correction timestamps must be timezone-aware")

    if correction.correction_type is CorrectionType.TIMESTAMP_OVERRIDE:
        valid = (
            correction.raw_event_id is not None
            and correction.event_type is None
            and correction.event_timestamp is not None
            and correction.event_timestamp_utc is not None
            and correction.location is None
        )
    elif correction.correction_type is CorrectionType.IGNORE_EVENT:
        valid = (
            correction.raw_event_id is not None
            and correction.event_type is None
            and correction.event_timestamp is None
            and correction.event_timestamp_utc is None
            and correction.location is None
        )
    else:
        valid = (
            correction.raw_event_id is None
            and correction.event_type in {"entry", "exit"}
            and correction.event_timestamp is not None
            and correction.event_timestamp_utc is not None
            and correction.location is not None
        )
    if not valid:
        raise ValueError(f"Invalid shape for correction type {correction.correction_type.value}")
