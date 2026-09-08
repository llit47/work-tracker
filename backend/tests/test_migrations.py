from pathlib import Path

from alembic import command
from alembic.config import Config
from datetime import date
from decimal import Decimal

from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
import pytest

from app.database import build_engine
from app.models import ApplicationSetting, LocationDisplayName, PayRate


def alembic_config(backend_dir: Path, database_url: str) -> Config:
    config = Config(str(backend_dir / "alembic.ini"))
    config.set_main_option("script_location", str(backend_dir / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def test_fresh_database_has_exactly_one_default_pay_rate(tmp_path: Path, monkeypatch):
    backend_dir = Path(__file__).resolve().parents[1]
    database_url = f"sqlite:///{tmp_path / 'fresh.db'}"
    config = alembic_config(backend_dir, database_url)
    monkeypatch.setenv("DATABASE_URL", database_url)

    command.upgrade(config, "head")

    engine = build_engine(database_url)
    with Session(engine) as session:
        rates = list(session.scalars(select(PayRate)))
        assert len(rates) == 1
        assert rates[0].effective_from == date(1970, 1, 1)
        assert rates[0].hourly_rate == Decimal("50.00")
        assert rates[0].currency == "PLN"
    with engine.connect() as connection:
        stored_rate = connection.execute(
            text("SELECT hourly_rate, typeof(hourly_rate) FROM pay_rates")
        ).one()
        assert tuple(stored_rate) == ("50.00", "text")


def test_correction_migration_preserves_existing_raw_events(tmp_path: Path, monkeypatch):
    backend_dir = Path(__file__).resolve().parents[1]
    database_url = f"sqlite:///{tmp_path / 'migration.db'}"
    config = alembic_config(backend_dir, database_url)
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


def test_pay_rate_migration_seeds_default_and_preserves_existing_data(tmp_path: Path, monkeypatch):
    backend_dir = Path(__file__).resolve().parents[1]
    database_url = f"sqlite:///{tmp_path / 'pay-migration.db'}"
    config = alembic_config(backend_dir, database_url)
    monkeypatch.setenv("DATABASE_URL", database_url)

    command.upgrade(config, "20260907_02")
    engine = build_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO work_events "
                "(id, event_type, location, event_timestamp, event_timestamp_utc, received_at, source) "
                "VALUES (1, 'entry', 'gabinet_zabki', '2026-09-07T08:00:00+02:00', "
                "'2026-09-07T06:00:00+00:00', '2026-09-07T06:00:01+00:00', 'home_assistant')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO work_event_corrections "
                "(id, correction_type, created_at, updated_at, raw_event_id) "
                "VALUES (1, 'ignore_event', '2026-09-07T08:00:00+00:00', "
                "'2026-09-07T08:00:00+00:00', 1)"
            )
        )

    command.upgrade(config, "head")
    assert "pay_rates" in inspect(engine).get_table_names()
    with Session(engine) as session:
        rates = list(session.scalars(select(PayRate)))
        assert len(rates) == 1
        assert rates[0].effective_from == date(1970, 1, 1)
        assert rates[0].hourly_rate == Decimal("50.00")
        assert rates[0].currency == "PLN"
    with engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM work_events")).scalar_one() == 1
        assert connection.execute(text("SELECT count(*) FROM work_event_corrections")).scalar_one() == 1

    command.downgrade(config, "20260907_02")
    table_names = inspect(engine).get_table_names()
    assert "pay_rates" not in table_names
    assert "work_events" in table_names
    assert "work_event_corrections" in table_names
    with engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM work_events")).scalar_one() == 1
        assert connection.execute(text("SELECT count(*) FROM work_event_corrections")).scalar_one() == 1


def test_application_settings_migration_preserves_domain_data(tmp_path: Path, monkeypatch):
    backend_dir = Path(__file__).resolve().parents[1]
    database_url = f"sqlite:///{tmp_path / 'settings-migration.db'}"
    config = alembic_config(backend_dir, database_url)
    monkeypatch.setenv("DATABASE_URL", database_url)

    command.upgrade(config, "20260907_03")
    engine = build_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO work_events "
                "(id, event_type, location, event_timestamp, event_timestamp_utc, received_at, source) "
                "VALUES (1, 'entry', 'gabinet_zabki', '2026-09-08T08:00:00+02:00', "
                "'2026-09-08T06:00:00+00:00', '2026-09-08T06:00:01+00:00', 'home_assistant')"
            )
        )

    command.upgrade(config, "head")
    with Session(engine) as session:
        application_settings = session.get(ApplicationSetting, 1)
        assert application_settings is not None
        assert application_settings.application_title == "Work Tracker"
        assert list(session.scalars(select(LocationDisplayName))) == []
    with engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM work_events")).scalar_one() == 1

    command.downgrade(config, "20260907_03")
    table_names = inspect(engine).get_table_names()
    assert "application_settings" not in table_names
    assert "location_display_names" not in table_names
    assert "work_events" in table_names
    assert "pay_rates" in table_names
    assert connection_event_count(engine) == 1
    with Session(engine) as session:
        default_rate = session.scalar(
            select(PayRate).where(PayRate.effective_from == date(1970, 1, 1))
        )
        assert default_rate is not None


def connection_event_count(engine) -> int:
    with engine.connect() as connection:
        return connection.execute(text("SELECT count(*) FROM work_events")).scalar_one()
