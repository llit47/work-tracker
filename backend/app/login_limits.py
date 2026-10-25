"""Small, persistent login budgets. No transaction spans password verification."""

from datetime import datetime, timedelta
import hashlib
from ipaddress import ip_address, ip_network
from math import ceil
from threading import BoundedSemaphore

from fastapi import HTTPException
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.orm import Session

from .identity import normalize_username
from .models import LoginAttempt

WINDOW = timedelta(minutes=15)
ACCOUNT_LIMIT = 8
IP_LIMIT = 30
MAX_ATTEMPTS = 4096
# Shared by all routers/apps in this process. Deployment explicitly uses one worker.
VERIFICATION_SLOTS = BoundedSemaphore(2)
UNAVAILABLE = "Logowanie jest chwilowo niedostępne. Spróbuj ponownie później."


def unavailable() -> HTTPException:
    return HTTPException(503, UNAVAILABLE, headers={"Cache-Control": "no-store"})


def login_identity(raw_username: str) -> tuple[str | None, str]:
    try:
        username = normalize_username(raw_username)
    except ValueError:
        username = None
    value = "login:" + username if username is not None else "invalid-username"
    return username, hashlib.sha256(value.encode("ascii")).hexdigest()


def peer_ip_key(host: str | None) -> str:
    try:
        # Scoped/link-local transport addresses must not create arbitrary buckets.
        if not host or "%" in host:
            raise ValueError
        address = ip_address(host)
    except ValueError:
        raise unavailable() from None
    if address.version == 6 and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    prefix = 32 if address.version == 4 else 64
    return str(ip_network(f"{address}/{prefix}", strict=False))


def cleanup(session: Session, instant: datetime) -> None:
    cutoff = instant - WINDOW
    session.execute(delete(LoginAttempt).where(
        LoginAttempt.attempted_at <= cutoff,
        (LoginAttempt.failed_at.is_(None) | (LoginAttempt.failed_at <= cutoff)),
    ))


def admit(session: Session, login_key: str, ip_key: str, instant: datetime) -> int:
    """Serialize cleanup/check/insert; commit even account-blocked IP attempts."""
    session.execute(text("BEGIN IMMEDIATE"))
    cleanup(session, instant)
    if session.scalar(select(func.count()).select_from(LoginAttempt)) >= MAX_ATTEMPTS:
        raise unavailable()
    cutoff = instant - WINDOW
    # No upper bound: clock rollback must not hide future records.
    ip_times = list(session.scalars(select(LoginAttempt.attempted_at).where(
        LoginAttempt.ip_key == ip_key, LoginAttempt.attempted_at > cutoff,
    ).order_by(LoginAttempt.attempted_at, LoginAttempt.id)))
    failures = list(session.scalars(select(LoginAttempt.failed_at).where(
        LoginAttempt.login_key == login_key, LoginAttempt.failed_at > cutoff,
    ).order_by(LoginAttempt.failed_at, LoginAttempt.id)))
    attempt = None
    if len(ip_times) < IP_LIMIT:
        attempt = LoginAttempt(login_key=login_key, ip_key=ip_key, attempted_at=instant)
        session.add(attempt)
        session.flush()
        ip_times.append(instant)
        ip_times.sort()
    deadlines = []
    if attempt is None:
        deadlines.append(ip_times[len(ip_times) - IP_LIMIT] + WINDOW)
    if len(failures) >= ACCOUNT_LIMIT:
        deadlines.append(failures[len(failures) - ACCOUNT_LIMIT] + WINDOW)
        # Retry-After accounts for the IP budget *after* this blocked attempt.
        if len(ip_times) >= IP_LIMIT:
            deadlines.append(ip_times[len(ip_times) - IP_LIMIT] + WINDOW)
    attempt_id = attempt.id if attempt is not None else None
    session.commit()
    if deadlines:
        seconds = max(1, ceil((max(deadlines) - instant).total_seconds()))
        raise HTTPException(429, "Zbyt wiele prób logowania. Spróbuj ponownie później.",
                            headers={"Retry-After": str(seconds), "Cache-Control": "no-store"})
    return attempt_id


def record_result(session: Session, attempt_id: int, instant: datetime, *, failed: bool) -> None:
    # Also check successful results: cleanup may have removed a very slow attempt.
    result = session.execute(update(LoginAttempt).where(LoginAttempt.id == attempt_id).values(
        failed_at=instant if failed else None,
    ))
    if result.rowcount != 1:
        raise unavailable()
    # Caller commits this together with any new session/rehash before issuing cookie.
