from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError
import pytest

from app.database import build_engine


def test_correction_migration_preserves_existing_raw_events(tmp_path: Path, monkeypatch):
    backend_dir = Path(__file__).resolve().parents[1]
    database_url = f"sqlite:///{tmp_path / 'migration.db'}"
    config = Config(str(backend_dir / "alembic.ini"))
    config.set_main_option("script_location", str(backend_dir / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    monkeypatch.setenv("DATABASE_URL", database_url)

    command.upgrade(config, "20260906_01")
    engine = build_engine(database_url)
    original_timestamp = "2026-09-07T08:00:00+02:00"
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO work_events "
                "(id, event_type, location, event_timestamp, event_timestamp_utc, received_at, source) "
                "VALUES (1, 'entry', 'gabinet_zabki', :timestamp, "
                "'2026-09-07T06:00:00+00:00', '2026-09-07T06:00:01+00:00', 'home_assistant')"
            ),
            {"timestamp": original_timestamp},
        )

    command.upgrade(config, "head")
    assert "work_event_corrections" in inspect(engine).get_table_names()
    with engine.connect() as connection:
        raw_row = connection.execute(
            text("SELECT event_type, event_timestamp, source FROM work_events WHERE id = 1")
        ).one()
    assert tuple(raw_row) == ("entry", original_timestamp, "home_assistant")

    invalid_shape = text(
        "INSERT INTO work_event_corrections "
        "(correction_type, created_at, updated_at, raw_event_id) "
        "VALUES ('manual_event', '2026-09-07T08:00:00+00:00', "
        "'2026-09-07T08:00:00+00:00', 1)"
    )
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(invalid_shape)

    missing_raw_event = text(
        "INSERT INTO work_event_corrections "
        "(correction_type, created_at, updated_at, raw_event_id) "
        "VALUES ('ignore_event', '2026-09-07T08:00:00+00:00', "
        "'2026-09-07T08:00:00+00:00', 999)"
    )
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(missing_raw_event)

    valid_ignore = text(
        "INSERT INTO work_event_corrections "
        "(correction_type, created_at, updated_at, raw_event_id) "
        "VALUES ('ignore_event', '2026-09-07T08:00:00+00:00', "
        "'2026-09-07T08:00:00+00:00', 1)"
    )
    with engine.begin() as connection:
        connection.execute(valid_ignore)
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(valid_ignore)

    command.downgrade(config, "20260906_01")
    assert "work_event_corrections" not in inspect(engine).get_table_names()
    with engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM work_events")).scalar_one() == 1
