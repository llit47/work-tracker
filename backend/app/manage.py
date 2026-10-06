"""Explicit production-only user creation: python -m app.manage create-user NAME."""

import argparse
import getpass
import os
from pathlib import Path
import pwd
import sqlite3
import stat
import sys
import warnings

from argon2.exceptions import HashingError
from dotenv import dotenv_values
from sqlalchemy import create_engine, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.identity import create_user, DuplicateUsernameError, normalize_username
from app.models import User


CONFIG_FILE = Path("/etc/work-tracker/work-tracker.env")
DATABASE_FILE = Path("/var/lib/work-tracker/work_tracker.db")
SERVICE_USER = "work-tracker"


def production_engine():
    """Never consult Settings/.env/environment defaults or create a SQLite file."""
    service_user = pwd.getpwnam(SERVICE_USER)
    if (
        os.geteuid() == 0
        or os.geteuid() != service_user.pw_uid
        or os.getegid() != service_user.pw_gid
    ):
        raise ValueError("Uruchom polecenie jako użytkownik usługi work-tracker.")
    configured_url = dotenv_values(CONFIG_FILE, interpolate=False).get("DATABASE_URL")
    # Match the existing deployment manifest's supported production URL.
    if configured_url != f"sqlite:///{DATABASE_FILE}":
        raise ValueError("Brak lub nieobsługiwany DATABASE_URL w konfiguracji produkcyjnej.")
    for path in (DATABASE_FILE.parent, DATABASE_FILE):
        info = path.lstat()
        expected_type = stat.S_ISDIR if path == DATABASE_FILE.parent else stat.S_ISREG
        if (
            not expected_type(info.st_mode)
            or info.st_uid != service_user.pw_uid
            or info.st_gid != service_user.pw_gid
            or stat.S_IMODE(info.st_mode) & 0o027
        ):
            raise ValueError("Nieprawidłowy właściciel lub uprawnienia bazy/katalogu danych.")
    os.umask(0o027)
    return create_engine(
        "sqlite://",
        creator=lambda: sqlite3.connect(f"{DATABASE_FILE.as_uri()}?mode=rw", uri=True),
        hide_parameters=True,
    )


def prompt_password() -> str:
    # Opening the controlling terminal is mandatory; never fall back to stdin.
    with open("/dev/tty", "w") as terminal, warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        password = getpass.getpass("Hasło (ukryte): ", stream=terminal)
        confirmation = getpass.getpass("Powtórz hasło (ukryte): ", stream=terminal)
    if password != confirmation:
        raise ValueError("Hasła nie są jednakowe; nic nie zapisano.")
    return password


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Tworzenie użytkownika w bazie produkcyjnej.")
    parser.add_argument("command", choices=["create-user"])
    parser.add_argument("username")
    args = parser.parse_args(argv)
    engine = None
    try:
        username = normalize_username(args.username)
        engine = production_engine()
        with Session(engine) as session:
            if session.scalar(select(User.id).where(User.username == username)) is not None:
                raise DuplicateUsernameError(
                    "Użytkownik o tej nazwie już istnieje; nic nie zmieniono."
                )
            print(f"Baza produkcyjna: {DATABASE_FILE}")
            user = create_user(session, username, prompt_password())
            user_id = user.id
            session.commit()
            print(f"Utworzono użytkownika: {username} (id={user_id}).")
        return 0
    except (ValueError, KeyError, OSError, SQLAlchemyError, HashingError, getpass.GetPassWarning) as error:
        # Never expose SQL parameters, hashes, config contents or credentials.
        # Give validation failures their safe, input-independent messages.
        if isinstance(error, ValueError):
            print(f"Błąd: {error}", file=sys.stderr)
        else:
            print(
                "Błąd: sprawdź konfigurację, użytkownika usługi, istniejącą bazę, "
                "migracje i dostęp do terminala. Nic nie nadpisano.",
                file=sys.stderr,
            )
        return 1
    except (EOFError, KeyboardInterrupt):
        print("Przerwano; nic nie zapisano.", file=sys.stderr)
        return 1
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    sys.exit(main())
