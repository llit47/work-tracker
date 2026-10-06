"""Identity primitives; independent of browser sessions and machine auth."""

from datetime import datetime, timezone
import re

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from argon2.profiles import RFC_9106_LOW_MEMORY
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import User


# Explicit library profile: Argon2id, 64 MiB, 3 iterations, 4 lanes.
# Encoded hashes carry their algorithm/version/parameters/salt; no pepper needed.
_password_hasher = PasswordHasher.from_parameters(RFC_9106_LOW_MEMORY)


class DuplicateUsernameError(ValueError):
    pass


def normalize_username(username: str) -> str:
    """Strip surrounding ASCII spaces, accept ASCII only, then lowercase."""
    if not isinstance(username, str):
        raise ValueError("Nazwa użytkownika musi być tekstem.")
    username = username.strip(" ")
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{2,63}", username) is None:
        raise ValueError(
            "Nazwa użytkownika: 3–64 znaki ASCII (litery, cyfry, '.', '_', '-'); "
            "pierwszy znak musi być literą lub cyfrą."
        )
    return username.lower()


def validate_password(password: str) -> None:
    # No trimming, normalization, composition rules or silent truncation.
    if not isinstance(password, str) or not 15 <= len(password) <= 1024:
        raise ValueError("Hasło musi mieć od 15 do 1024 znaków.")
    if "\x00" in password:
        raise ValueError("Hasło nie może zawierać znaku NUL.")
    try:
        password.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("Hasło musi być poprawnym tekstem Unicode.") from None


def hash_password(password: str) -> str:
    validate_password(password)
    return _password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Fail closed on invalid credentials or malformed/unsupported hashes."""
    if not isinstance(password_hash, str) or not password_hash.startswith("$argon2id$"):
        return False
    try:
        validate_password(password)
        return _password_hasher.verify(password_hash, password)
    except (ValueError, InvalidHashError, VerificationError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    """Compare parameters only, after successful verification; not a validator."""
    if not isinstance(password_hash, str) or not password_hash.startswith("$argon2id$"):
        return False
    try:
        return _password_hasher.check_needs_rehash(password_hash)
    except (InvalidHashError, VerificationError, ValueError):
        return False


def create_user(session: Session, username: str, password: str) -> User:
    """Use a dedicated session; caller commits. Failure rolls back, never updates."""
    username = normalize_username(username)
    validate_password(password)
    if session.scalar(select(User.id).where(User.username == username)) is not None:
        raise DuplicateUsernameError("Użytkownik o tej nazwie już istnieje; nic nie zmieniono.")
    user = User(
        username=username,
        password_hash=hash_password(password),
        created_at=datetime.now(timezone.utc),
    )
    try:
        session.add(user)
        session.flush()
    except IntegrityError:
        # The unique constraint also protects concurrent operator invocations.
        session.rollback()
        if session.scalar(select(User.id).where(User.username == username)) is not None:
            raise DuplicateUsernameError(
                "Użytkownik o tej nazwie już istnieje; nic nie zmieniono."
            ) from None
        raise
    return user
