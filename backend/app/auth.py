"""Optional browser authentication. Never participates in Home Assistant auth."""

from datetime import datetime, timedelta, timezone
import hashlib
import re
import secrets
from typing import Callable

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, SecretStr
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .config import Settings
from .database import get_session
from .identity import hash_password, normalize_username, password_needs_rehash, verify_password
from .models import User, UserSession

COOKIE_NAME = "work_tracker_session"
IDLE_TIMEOUT = timedelta(days=30)
ABSOLUTE_LIFETIME = timedelta(days=180)
ACTIVITY_WRITE_INTERVAL = timedelta(hours=1)
# Random, process-local dummy hash: nonexistent accounts still do Argon2 work.
_DUMMY_HASH = hash_password(secrets.token_urlsafe(32))


class LoginRequest(BaseModel):
    username: str
    password: SecretStr


def token_hash(token: str | None) -> str | None:
    if token is None or re.fullmatch(r"[A-Za-z0-9_-]{43}", token) is None:
        return None
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def valid_session_statement(digest: str, now: datetime):
    return select(UserSession, User.username).join(User).where(
        UserSession.token_hash == digest,
        UserSession.revoked_at.is_(None),
        UserSession.created_at <= now,
        UserSession.expires_at > now,
        UserSession.last_seen_at > now - IDLE_TIMEOUT,
    )


def session_response(username: str, expires_at: datetime) -> dict:
    return {"username": username, "expires_at": expires_at.isoformat()}


def build_auth_router(settings: Settings, now_provider: Callable[[], datetime]) -> APIRouter:
    router = APIRouter(prefix="/api/auth")

    def now() -> datetime:
        return now_provider().astimezone(timezone.utc)

    def clear_cookie(response: Response) -> None:
        response.delete_cookie(
            COOKIE_NAME, path="/", httponly=True,
            secure=settings.session_cookie_secure, samesite="lax",
        )
        response.headers["Cache-Control"] = "no-store"

    def login_failure() -> JSONResponse:
        return JSONResponse(
            status_code=401,
            content={"detail": "Nieprawidłowa nazwa użytkownika lub hasło."},
            headers={"Cache-Control": "no-store"},
        )

    @router.post("/login")
    def login(payload: LoginRequest, response: Response, session: Session = Depends(get_session)):
        try:
            username = normalize_username(payload.username)
        except ValueError:
            username = None
        credentials = session.execute(
            select(User.id, User.username, User.password_hash).where(User.username == username)
        ).first()
        # Do not hold a SQLite read transaction during expensive Argon2 work.
        session.rollback()
        password = payload.password.get_secret_value()
        verified = verify_password(password, credentials.password_hash if credentials else _DUMMY_HASH)
        if not verified or credentials is None:
            return login_failure()
        new_hash = hash_password(password) if password_needs_rehash(credentials.password_hash) else None
        if new_hash is not None:
            changed = session.execute(update(User).where(
                User.id == credentials.id, User.password_hash == credentials.password_hash,
            ).values(password_hash=new_hash))
            if changed.rowcount != 1:
                session.rollback()
                # Another login may have rehashed the same password. Verify its
                # result without overwriting it or holding a read transaction.
                current_hash = session.scalar(select(User.password_hash).where(User.id == credentials.id))
                session.rollback()
                if not verify_password(password, current_hash):
                    return login_failure()
        instant = now()
        raw_token = secrets.token_urlsafe(32)
        expires_at = instant + ABSOLUTE_LIFETIME
        session.add(UserSession(
            user_id=credentials.id, token_hash=token_hash(raw_token),
            created_at=instant, last_seen_at=instant, expires_at=expires_at,
        ))
        session.commit()
        response.set_cookie(
            COOKIE_NAME, raw_token, max_age=int(ABSOLUTE_LIFETIME.total_seconds()),
            expires=expires_at, path="/", httponly=True,
            secure=settings.session_cookie_secure, samesite="lax",
        )
        response.headers["Cache-Control"] = "no-store"
        return session_response(credentials.username, expires_at)

    @router.get("/me")
    def me(request: Request, response: Response, session: Session = Depends(get_session)):
        digest = token_hash(request.cookies.get(COOKIE_NAME))
        instant = now()
        found = session.execute(valid_session_statement(digest, instant)).first() if digest else None
        if found is not None:
            stored, username = found
            expires_at = stored.expires_at
            last_seen_at = stored.last_seen_at
            session.rollback()
            if last_seen_at <= instant - ACTIVITY_WRITE_INTERVAL:
                # One conditional update coalesces concurrent touches, never revives
                # expired/revoked sessions, and never moves last_seen backwards.
                session.execute(update(UserSession).where(
                    UserSession.token_hash == digest,
                    UserSession.revoked_at.is_(None),
                    UserSession.expires_at > instant,
                    UserSession.last_seen_at > instant - IDLE_TIMEOUT,
                    UserSession.last_seen_at <= instant - ACTIVITY_WRITE_INTERVAL,
                ).values(last_seen_at=instant))
                session.commit()
                if session.execute(valid_session_statement(digest, instant)).first() is None:
                    found = None
            if found is not None:
                response.headers["Cache-Control"] = "no-store"
                return session_response(username, expires_at)
        invalid = JSONResponse(status_code=401, content={"detail": "Sesja wygasła lub jest nieprawidłowa."})
        # Never clear cookies on a read: a delayed anonymous /me response
        # must not erase a cookie issued by a concurrent successful login.
        invalid.headers["Cache-Control"] = "no-store"
        return invalid

    @router.post("/logout", status_code=204)
    def logout(request: Request, session: Session = Depends(get_session)):
        digest = token_hash(request.cookies.get(COOKIE_NAME))
        if digest:
            session.execute(update(UserSession).where(
                UserSession.token_hash == digest, UserSession.revoked_at.is_(None),
            ).values(revoked_at=now()))
            session.commit()
        response = Response(status_code=204)
        clear_cookie(response)
        return response

    return router
