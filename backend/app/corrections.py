from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

from .models import CorrectionType
from .work_time import RawWorkEvent


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
