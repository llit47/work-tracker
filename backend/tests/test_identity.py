from datetime import datetime, timezone

from argon2 import PasswordHasher, Type, extract_parameters
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
import pytest

from app.database import build_engine
from app.identity import (
    create_user, DuplicateUsernameError, hash_password, normalize_username,
    password_needs_rehash, verify_password,
)
from app.models import Base, User


# Public test fixture, never an operator credential.
PASSWORD = "test-only long password żółw"


@pytest.fixture
def engine(tmp_path):
    engine = build_engine(f"sqlite:///{tmp_path / 'identity.db'}")
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.mark.parametrize("value, expected", [
    (" Owner ", "owner"), ("USER.Name_01-X", "user.name_01-x"),
    ("abc", "abc"), ("A" * 64, "a" * 64), ("123", "123"),
])
def test_username_normalization(value, expected):
    assert normalize_username(value) == expected
    assert normalize_username(expected) == expected


@pytest.mark.parametrize("value", [
    None, "", "  ", "ab", "a" * 65, "user name", "user\tname", "name\n",
    "\tname", ".name", "_name", "-name", "żółw", "ÜSER", "Ｎame", "user@host",
    "user\x00", "user\u00a0", "\u00a0user",
])
def test_invalid_username(value):
    with pytest.raises(ValueError):
        normalize_username(value)


def test_password_hash_is_salted_argon2id_and_verifies():
    first = hash_password(PASSWORD)
    second = hash_password(PASSWORD)
    assert first != second
    assert PASSWORD not in first
    parameters = extract_parameters(first)
    assert parameters.type == Type.ID
    assert parameters.memory_cost == 65536
    assert parameters.time_cost == 3
    assert parameters.parallelism == 4
    assert verify_password(PASSWORD, first)
    assert not verify_password(PASSWORD + "!", first)
    assert not password_needs_rehash(first)


def test_password_exact_unicode_and_spaces_and_no_truncation():
    password = "  długie hasło e\u0301 "
    encoded = hash_password(password)
    assert verify_password(password, encoded)
    assert not verify_password(password.strip(), encoded)
    assert not verify_password(password.replace("e\u0301", "é"), encoded)
    long_password = "a" * 1024
    assert verify_password(long_password, hash_password(long_password))


@pytest.mark.parametrize("password", [None, "", "a" * 14, "a" * 1025, "a" * 15 + "\x00", "a" * 15 + "\ud800"])
def test_invalid_password_rejected_without_exposure(password):
    with pytest.raises(ValueError) as error:
        hash_password(password)
    if isinstance(password, str) and password:
        assert password not in str(error.value)
    assert not verify_password(password, "$argon2id$invalid")


@pytest.mark.parametrize("encoded", [None, "", "plain-text", "$bcrypt$bad", "$argon2i$bad", "$argon2id$", "$argon2id$v=99$m=1,t=1,p=1$bad$bad"])
def test_malformed_or_unsupported_hash_fails_closed(encoded):
    assert not verify_password(PASSWORD, encoded)


@pytest.mark.parametrize("encoded", [None, "", "$bcrypt$bad", "$argon2id$"])
def test_rehash_check_handles_unparseable_hash(encoded):
    assert not password_needs_rehash(encoded)


def test_old_argon2id_parameters_can_be_verified_and_upgraded():
    old = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1).hash(PASSWORD)
    assert verify_password(PASSWORD, old)
    assert password_needs_rehash(old)
    assert not password_needs_rehash(hash_password(PASSWORD))


def test_create_user_persists_only_hash_and_duplicate_never_overwrites(engine):
    with Session(engine) as session:
        user = create_user(session, " Owner ", PASSWORD)
        session.commit()
        snapshot = (user.id, user.username, user.password_hash, user.created_at)
        assert user.username == "owner"
        assert user.created_at.utcoffset().total_seconds() == 0
        assert verify_password(PASSWORD, user.password_hash)
        with pytest.raises(DuplicateUsernameError):
            create_user(session, "OWNER", "different test-only password")
        session.commit()
        session.expire_all()
        users = list(session.scalars(select(User)))
        assert len(users) == 1
        assert (users[0].id, users[0].username, users[0].password_hash, users[0].created_at) == snapshot
    with engine.connect() as connection:
        assert set(connection.execute(text("SELECT * FROM users")).keys()) == {
            "id", "username", "password_hash", "created_at",
        }


def test_create_user_requires_commit_and_invalid_input_does_not_write(engine):
    with Session(engine) as session:
        create_user(session, "owner", PASSWORD)
        session.rollback()
    with Session(engine) as session:
        assert list(session.scalars(select(User))) == []
        for username, password in [("bad name", PASSWORD), ("owner", "short")]:
            with pytest.raises(ValueError):
                create_user(session, username, password)
        session.commit()
        assert list(session.scalars(select(User))) == []


def test_database_unique_constraint_protects_duplicate_insert(engine):
    with Session(engine) as session:
        original = create_user(session, "owner", PASSWORD)
        session.commit()
        original_hash = original.password_hash
    with pytest.raises(IntegrityError), Session(engine) as session:
        session.add(User(username="owner", password_hash=hash_password(PASSWORD), created_at=datetime.now(timezone.utc)))
        session.commit()
    with Session(engine) as session:
        assert session.scalar(select(User)).password_hash == original_hash


def test_duplicate_race_is_clear_and_does_not_overwrite(engine, monkeypatch):
    with Session(engine) as session:
        original = create_user(session, "owner", PASSWORD)
        session.commit()
        original_hash = original.password_hash
    with Session(engine) as session:
        scalar = session.scalar
        calls = 0
        def stale_preflight(query):
            nonlocal calls
            calls += 1
            return None if calls == 1 else scalar(query)
        monkeypatch.setattr(session, "scalar", stale_preflight)
        with pytest.raises(DuplicateUsernameError):
            create_user(session, "owner", "different test-only password")
        session.commit()
    with Session(engine) as session:
        users = list(session.scalars(select(User)))
        assert len(users) == 1
        assert users[0].password_hash == original_hash


@pytest.mark.parametrize("username", ["OWNER", " owner", "ab", "a" * 65, "żółw", ".name", "name\x00"])
def test_database_rejects_noncanonical_username(engine, username):
    with pytest.raises(IntegrityError), Session(engine) as session:
        session.add(User(username=username, password_hash=hash_password(PASSWORD), created_at=datetime.now(timezone.utc)))
        session.commit()
