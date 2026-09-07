from datetime import datetime, timezone

from app.corrections import CorrectionRecord, build_effective_event_stream
from app.models import CorrectionType
from app.work_time import RawWorkEvent


def raw_event(event_id: int, event_type: str, timestamp: str) -> RawWorkEvent:
    local_timestamp = datetime.fromisoformat(timestamp)
    return RawWorkEvent(
        id=event_id,
        event_type=event_type,
        location="gabinet_zabki",
        event_timestamp=local_timestamp,
        event_timestamp_utc=local_timestamp.astimezone(timezone.utc),
        received_at=datetime(2026, 9, 7, tzinfo=timezone.utc),
        source="home_assistant",
    )


def correction(correction_id: int, correction_type: CorrectionType, **values) -> CorrectionRecord:
    created_at = datetime(2026, 9, 7, tzinfo=timezone.utc)
    return CorrectionRecord(
        id=correction_id,
        correction_type=correction_type,
        created_at=created_at,
        updated_at=created_at,
        **values,
    )


def test_effective_stream_is_deterministic_and_does_not_mutate_raw_events():
    entry = raw_event(1, "entry", "2026-09-07T08:07:00+02:00")
    exit_event = raw_event(2, "exit", "2026-09-07T16:00:00+02:00")
    raw_events = [entry, exit_event]
    original_values = [event.__dict__.copy() for event in raw_events]
    corrected_timestamp = datetime.fromisoformat("2026-09-07T08:00:00+02:00")

    result = build_effective_event_stream(
        raw_events,
        [
            correction(
                1,
                CorrectionType.TIMESTAMP_OVERRIDE,
                raw_event_id=1,
                event_timestamp=corrected_timestamp,
                event_timestamp_utc=corrected_timestamp.astimezone(timezone.utc),
            )
        ],
    )

    assert result.events[0].event_timestamp == corrected_timestamp
    assert result.metadata_by_id[1].original_event_timestamp == entry.event_timestamp
    assert [event.__dict__ for event in raw_events] == original_values


def test_effective_stream_excludes_ignored_and_includes_manual_event():
    entry = raw_event(1, "entry", "2026-09-07T08:00:00+02:00")
    manual_timestamp = datetime.fromisoformat("2026-09-07T16:00:00+02:00")

    result = build_effective_event_stream(
        [entry],
        [
            correction(1, CorrectionType.IGNORE_EVENT, raw_event_id=1),
            correction(
                2,
                CorrectionType.MANUAL_EVENT,
                event_type="exit",
                event_timestamp=manual_timestamp,
                event_timestamp_utc=manual_timestamp.astimezone(timezone.utc),
                location="gabinet_zabki",
            ),
        ],
    )

    assert [event.id for event in result.events] == [-2]
    assert result.metadata_by_id[-2].is_manual is True
    assert result.ignored_events[0].raw_event_id == 1
    assert result.ignored_events[0].is_ignored is True
