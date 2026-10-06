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
from app.models import ApplicationSetting, LocationDisplayName, LocationTimezone, PayRate, User


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


def test_location_timezone_migration_is_additive_and_preserves_existing_data(
    tmp_path: Path, monkeypatch
):
    backend_dir = Path(__file__).resolve().parents[1]
    database_url = f"sqlite:///{tmp_path / 'timezone-migration.db'}"
    config = alembic_config(backend_dir, database_url)
    monkeypatch.setenv("DATABASE_URL", database_url)

    command.upgrade(config, "20260908_04")
    engine = build_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO work_events "
                "(id, event_type, location, event_timestamp, event_timestamp_utc, received_at, source) "
                "VALUES (1, 'entry', 'gabinet_zabki', '2026-09-09T07:20:14+02:00', "
                "'2026-09-09T05:20:14+00:00', '2026-09-09T05:20:15+00:00', 'home_assistant')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO work_event_corrections "
                "(id, correction_type, created_at, updated_at, raw_event_id) "
                "VALUES (1, 'ignore_event', '2026-09-09T05:21:00+00:00', "
                "'2026-09-09T05:21:00+00:00', 1)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO location_display_names (location, display_name, updated_at) "
                "VALUES ('gabinet_zabki', 'ARTE Stomatologia', '2026-09-09T05:00:00+00:00')"
            )
        )

    command.upgrade(config, "head")

    assert "location_timezones" in inspect(engine).get_table_names()
    with Session(engine) as session:
        assert list(session.scalars(select(LocationTimezone))) == []
        assert session.get(ApplicationSetting, 1).application_title == "Work Tracker"
        assert session.get(LocationDisplayName, "gabinet_zabki").display_name == "ARTE Stomatologia"
        assert len(list(session.scalars(select(PayRate)))) == 1
    with engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM work_events")).scalar_one() == 1
        assert connection.execute(
            text("SELECT count(*) FROM work_event_corrections")
        ).scalar_one() == 1

    command.downgrade(config, "20260908_04")
    assert "location_timezones" not in inspect(engine).get_table_names()
    assert connection_event_count(engine) == 1
    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT count(*) FROM work_event_corrections")
        ).scalar_one() == 1
        assert connection.execute(
            text("SELECT display_name FROM location_display_names")
        ).scalar_one() == "ARTE Stomatologia"


def connection_event_count(engine) -> int:
    with engine.connect() as connection:
        return connection.execute(text("SELECT count(*) FROM work_events")).scalar_one()


def test_identity_upgrade_preserves_all_preexisting_tables(tmp_path, monkeypatch):
    backend_dir = Path(__file__).resolve().parents[1]
    database_url = f"sqlite:///{tmp_path / 'identity-upgrade.db'}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    config = alembic_config(backend_dir, database_url)
    command.upgrade(config, "20260909_05")
    engine = build_engine(database_url)
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO work_events VALUES (1, 'entry', 'gabinet_zabki', "
            "'2026-10-06T08:00:17+02:00', '2026-10-06T06:00:17+00:00', "
            "'2026-10-06T06:00:18+00:00', 'home_assistant')"
        ))
        connection.execute(text(
            "INSERT INTO work_event_corrections VALUES (1, 'timestamp_override', "
            "'2026-10-06T06:01:00+00:00', '2026-10-06T06:01:00+00:00', 1, NULL, "
            "'2026-10-06T08:02:00+02:00', '2026-10-06T06:02:00+00:00', NULL)"
        ))
        connection.execute(text(
            "INSERT INTO pay_rates VALUES (2, '2026-10-01', '73.29', 'PLN', '2026-10-01T00:00:00+00:00')"
        ))
        connection.execute(text("UPDATE application_settings SET application_title = 'Praca'"))
        connection.execute(text(
            "INSERT INTO location_display_names VALUES ('gabinet_zabki', 'ARTE', '2026-10-06T00:00:00+00:00')"
        ))
        connection.execute(text(
            "INSERT INTO location_timezones VALUES ('gabinet_zabki', 'Europe/Warsaw', '2026-10-06T00:00:00+00:00')"
        ))
    tables = set(inspect(engine).get_table_names()) - {"alembic_version"}
    def snapshot():
        with engine.connect() as connection:
            return {table: list(connection.execute(text(f"SELECT * FROM {table} ORDER BY 1"))) for table in tables}
    before = snapshot()
    command.upgrade(config, "20261006_06")
    assert snapshot() == before
    assert set(inspect(engine).get_table_names()) == tables | {"alembic_version", "users"}
    with Session(engine) as session:
        assert list(session.scalars(select(User))) == []
    with engine.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "20261006_06"
    command.upgrade(config, "20261006_06")
    assert snapshot() == before
    command.downgrade(config, "20260909_05")
    assert snapshot() == before
    assert "users" not in inspect(engine).get_table_names()
    engine.dispose()


def test_fresh_identity_schema_matches_model_and_has_no_seed_users(tmp_path, monkeypatch):
    backend_dir = Path(__file__).resolve().parents[1]
    database_url = f"sqlite:///{tmp_path / 'fresh-identity.db'}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    command.upgrade(alembic_config(backend_dir, database_url), "head")
    engine = build_engine(database_url)
    schema = inspect(engine)
    assert {column["name"] for column in schema.get_columns("users")} == set(User.__table__.columns.keys())
    assert {c["name"] for c in schema.get_check_constraints("users")} == {"ck_users_username", "ck_users_password_hash"}
    assert schema.get_unique_constraints("users")[0]["column_names"] == ["username"]
    with Session(engine) as session:
        assert list(session.scalars(select(User))) == []
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(text("INSERT INTO users VALUES (1, 'UPPER', 'hash', '2026-10-06T00:00:00+00:00')"))
    engine.dispose()


def test_sessions_upgrade_preserves_existing_user_and_domain_data(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    from app.identity import create_user
    from app.models import UserSession

    backend_dir = Path(__file__).resolve().parents[1]
    database_url = f"sqlite:///{tmp_path / 'sessions-upgrade.db'}"
    monkeypatch.setenv('DATABASE_URL', database_url)
    config = alembic_config(backend_dir, database_url)
    command.upgrade(config, '20261006_06')
    engine = build_engine(database_url)
    with Session(engine) as session:
        create_user(session, 'przemek', 'public-migration-test-only-password')
        session.commit()
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO work_events VALUES (1, 'entry', 'gabinet_zabki', "
            "'2026-10-06T08:00:17+02:00', '2026-10-06T06:00:17+00:00', "
            "'2026-10-06T06:00:18+00:00', 'home_assistant')"
        ))
        connection.execute(text(
            "INSERT INTO work_event_corrections VALUES (1, 'timestamp_override', "
            "'2026-10-06T06:01:00+00:00', '2026-10-06T06:01:00+00:00', 1, NULL, "
            "'2026-10-06T08:02:00+02:00', '2026-10-06T06:02:00+00:00', NULL)"
        ))
        connection.execute(text("UPDATE application_settings SET application_title = 'Praca'"))
        connection.execute(text(
            "INSERT INTO pay_rates VALUES (2, '2026-10-01', '73.29', 'PLN', '2026-10-01T00:00:00+00:00')"
        ))
        connection.execute(text(
            "INSERT INTO location_display_names VALUES ('gabinet_zabki', 'ARTE', '2026-10-06T00:00:00+00:00')"
        ))
        connection.execute(text(
            "INSERT INTO location_timezones VALUES ('gabinet_zabki', 'Europe/Warsaw', '2026-10-06T00:00:00+00:00')"
        ))
    tables = set(inspect(engine).get_table_names()) - {'alembic_version'}
    def snapshot():
        with engine.connect() as connection:
            return {table: list(connection.execute(text(f'SELECT * FROM {table} ORDER BY 1'))) for table in tables}
    before = snapshot()
    command.upgrade(config, 'head')
    schema = inspect(engine)
    assert set(schema.get_table_names()) == tables | {'alembic_version', 'user_sessions'}
    assert snapshot() == before
    assert {column['name'] for column in schema.get_columns('user_sessions')} == set(UserSession.__table__.columns.keys())
    assert {c['name'] for c in schema.get_check_constraints('user_sessions')} == {
        'ck_user_sessions_token_hash', 'ck_user_sessions_dates'}
    assert schema.get_unique_constraints('user_sessions')[0]['column_names'] == ['token_hash']
    assert schema.get_indexes('user_sessions')[0]['column_names'] == ['user_id']
    assert schema.get_foreign_keys('user_sessions')[0]['referred_table'] == 'users'
    with Session(engine) as session:
        assert list(session.scalars(select(UserSession))) == []
        assert session.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == '20261006_07'
        session.add(UserSession(user_id=1, token_hash='a' * 64,
                    created_at=datetime(2030, 1, 1, tzinfo=timezone.utc),
                    last_seen_at=datetime(2030, 1, 1, tzinfo=timezone.utc),
                    expires_at=datetime(2030, 6, 30, tzinfo=timezone.utc)))
        session.commit()
    command.upgrade(config, 'head')
    assert snapshot() == before
    for invalid in [
        {'user_id': 999}, {'token_hash': 'raw-token'}, {'token_hash': 'g' * 64},
        {'expires_at': '2029-01-01T00:00:00.000000+00:00'},
        {'token_hash': 'a' * 64},  # Unique constraint.
    ]:
        params = dict(user_id=1, token_hash='b' * 64, created_at='2030-01-01T00:00:00.000000+00:00',
                      last_seen_at='2030-01-01T00:00:00.000000+00:00', expires_at='2030-06-30T00:00:00.000000+00:00')
        params.update(invalid)
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(text('INSERT INTO user_sessions (user_id, token_hash, created_at, last_seen_at, expires_at) '
                                    'VALUES (:user_id, :token_hash, :created_at, :last_seen_at, :expires_at)'), params)
    command.downgrade(config, '20261006_06')
    assert snapshot() == before
    assert 'user_sessions' not in inspect(engine).get_table_names()
    engine.dispose()
