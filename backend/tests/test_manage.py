import getpass
import os
from pathlib import Path
import pwd
from types import SimpleNamespace

from argon2.exceptions import HashingError
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
import pytest

from app import manage
from app.identity import verify_password
from app.models import Base, User
from test_identity import PASSWORD


@pytest.fixture
def production(tmp_path, monkeypatch):
    previous_umask = os.umask(0o027)
    database = tmp_path / "data" / "work_tracker.db"
    database.parent.mkdir(mode=0o750)
    database.touch(mode=0o640)
    config = tmp_path / "work-tracker.env"
    config.write_text(f"DATABASE_URL=sqlite:///{database}\n")
    monkeypatch.setattr(manage, "CONFIG_FILE", config)
    monkeypatch.setattr(manage, "DATABASE_FILE", database)
    service = SimpleNamespace(pw_uid=os.geteuid(), pw_gid=os.getegid())
    monkeypatch.setattr(pwd, "getpwnam", lambda name: service)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'wrong.db'}")
    engine = manage.production_engine()
    Base.metadata.create_all(engine)
    engine.dispose()
    yield database, config
    os.umask(previous_umask)


def users():
    engine = manage.production_engine()
    try:
        with Session(engine) as session:
            return [(u.id, u.username, u.password_hash, u.created_at) for u in session.scalars(select(User))]
    finally:
        engine.dispose()


def test_create_and_rerun_never_overwrites(production, monkeypatch, capsys):
    database, _ = production
    before = database.stat()
    monkeypatch.setattr(manage, "prompt_password", lambda: PASSWORD)
    assert manage.main(["create-user", " Owner "]) == 0
    original = users()
    assert len(original) == 1
    assert original[0][1] == "owner"
    assert verify_password(PASSWORD, original[0][2])
    monkeypatch.setattr(manage, "prompt_password", lambda: pytest.fail("duplicate must not prompt"))
    assert manage.main(["create-user", "OWNER"]) == 1
    assert users() == original
    after = database.stat()
    assert (after.st_uid, after.st_gid, after.st_mode) == (before.st_uid, before.st_gid, before.st_mode)
    output = capsys.readouterr()
    assert "już istnieje" in output.err
    assert str(database) in output.out
    assert PASSWORD not in output.out + output.err
    assert original[0][2] not in output.out + output.err
    assert not (database.parent.parent / "wrong.db").exists()


@pytest.mark.parametrize("username,password", [("bad name", PASSWORD), ("owner", "too-short")])
def test_invalid_input_does_not_create(production, monkeypatch, username, password):
    monkeypatch.setattr(manage, "prompt_password", lambda: password)
    assert manage.main(["create-user", username]) == 1
    assert users() == []


@pytest.mark.parametrize("url", ["", "sqlite:///./work_tracker.db", "sqlite:///:memory:", "sqlite:////tmp/unwanted.db"])
def test_bad_config_never_uses_environment_or_default(production, url):
    _, config = production
    config.write_text(f"DATABASE_URL={url}\n")
    with pytest.raises(ValueError, match="DATABASE_URL"):
        manage.production_engine()


def test_missing_config_or_key_fails_closed(production):
    _, config = production
    config.write_text("OTHER=value\n")
    with pytest.raises(ValueError):
        manage.production_engine()
    config.unlink()
    with pytest.raises(ValueError):
        manage.production_engine()


def test_missing_database_never_created(production):
    database, _ = production
    database.unlink()
    with pytest.raises(FileNotFoundError):
        manage.production_engine()
    assert not database.exists()


def test_database_disappearing_after_validation_never_recreated(production):
    database, _ = production
    engine = manage.production_engine()
    database.unlink()
    try:
        with pytest.raises(OperationalError):
            engine.connect()
        assert not database.exists()
    finally:
        engine.dispose()


@pytest.mark.parametrize("target", ["database", "directory"])
def test_unsafe_permissions_rejected(production, target):
    database, _ = production
    path = database if target == "database" else database.parent
    path.chmod(0o666 if target == "database" else 0o777)
    with pytest.raises(ValueError, match="uprawnienia"):
        manage.production_engine()


def test_symlink_rejected(production):
    database, _ = production
    moved = database.with_name("moved.db")
    database.rename(moved)
    database.symlink_to(moved)
    with pytest.raises(ValueError):
        manage.production_engine()


@pytest.mark.parametrize("uid,gid", [(0, 0), (100001, 100001)])
def test_root_or_wrong_service_user_rejected(production, monkeypatch, uid, gid):
    monkeypatch.setattr(os, "geteuid", lambda: uid)
    monkeypatch.setattr(os, "getegid", lambda: gid)
    with pytest.raises(ValueError, match="użytkownik usługi"):
        manage.production_engine()


def test_wrong_owner_rejected(production, monkeypatch):
    monkeypatch.setattr(pwd, "getpwnam", lambda name: SimpleNamespace(pw_uid=100001, pw_gid=100001))
    monkeypatch.setattr(os, "geteuid", lambda: 100001)
    monkeypatch.setattr(os, "getegid", lambda: 100001)
    with pytest.raises(ValueError, match="właściciel"):
        manage.production_engine()


@pytest.mark.parametrize("failure", [EOFError(), KeyboardInterrupt(), OSError(), getpass.GetPassWarning()])
def test_password_entry_failure_does_not_write(production, monkeypatch, failure):
    def fail():
        raise failure
    monkeypatch.setattr(manage, "prompt_password", fail)
    assert manage.main(["create-user", "owner"]) == 1
    assert users() == []


def test_hidden_password_prompt_and_confirmation(monkeypatch):
    # /dev/tty is substituted only in this unit test; no stdin password path exists.
    from io import StringIO
    terminal = StringIO()
    monkeypatch.setattr("builtins.open", lambda path, mode: terminal)
    calls = []
    def hidden(prompt, stream):
        calls.append((prompt, stream))
        return PASSWORD
    monkeypatch.setattr(getpass, "getpass", hidden)
    assert manage.prompt_password() == PASSWORD
    assert len(calls) == 2
    assert all(stream is terminal for _, stream in calls)


def test_confirmation_mismatch_does_not_write(production, monkeypatch):
    from io import StringIO
    with monkeypatch.context() as patch:
        patch.setattr("builtins.open", lambda path, mode: StringIO())
        answers = iter([PASSWORD, PASSWORD + "different"])
        patch.setattr(getpass, "getpass", lambda *args, **kwargs: next(answers))
        with pytest.raises(ValueError, match="jednakowe"):
            manage.prompt_password()
    assert users() == []


def test_no_password_argument(production):
    with pytest.raises(SystemExit) as error:
        manage.main(["create-user", "owner", "--password"])
    assert error.value.code == 2
    assert users() == []


def test_hashing_failure_does_not_write_or_expose_details(production, monkeypatch, capsys):
    monkeypatch.setattr(manage, "prompt_password", lambda: PASSWORD)
    def fail(*args):
        raise HashingError("test diagnostic that must not be printed")
    monkeypatch.setattr(manage, "create_user", fail)
    assert manage.main(["create-user", "owner"]) == 1
    assert users() == []
    output = capsys.readouterr()
    assert "test diagnostic" not in output.out + output.err
