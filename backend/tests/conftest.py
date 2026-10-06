import os

os.environ.setdefault("WEBHOOK_TOKEN", "test-secret-that-is-at-least-32-characters")


def authenticated_client(app, **kwargs):
    """Domain tests exercise real enforcement with a valid test-only session.

    Auth/login/HA-boundary tests use anonymous clients explicitly instead.
    """
    import secrets
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import select
    from app.auth import ABSOLUTE_LIFETIME, BrowserSessionMiddleware, COOKIE_NAME, token_hash
    from app.models import User, UserSession

    now = next(m.kwargs["now_provider"] for m in app.user_middleware
               if m.cls is BrowserSessionMiddleware)()
    token = secrets.token_urlsafe(32)
    with app.state.session_factory() as session:
        user = session.scalar(select(User).where(User.username == "domain-test"))
        if user is None:
            user = User(username="domain-test", password_hash="unused-test-only", created_at=now)
            session.add(user)
            session.flush()
        session.add(UserSession(user_id=user.id, token_hash=token_hash(token),
                                created_at=now, last_seen_at=now,
                                expires_at=now + ABSOLUTE_LIFETIME))
        session.commit()
    return AsyncClient(transport=ASGITransport(app=app), cookies={COOKIE_NAME: token}, **kwargs)
