from copy import copy
import os

import pytest

os.environ.setdefault("WEBHOOK_TOKEN", "test-secret-that-is-at-least-32-characters")
os.environ.setdefault("BROWSER_ALLOWED_ORIGINS", "http://testserver,https://testserver,http://localhost:5173,http://127.0.0.1:5173")
os.environ.setdefault("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")

BROWSER_HEADERS = {"Origin": "http://testserver", "X-Work-Tracker-Request": "1"}


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
    kwargs.setdefault("headers", BROWSER_HEADERS)
    return AsyncClient(transport=ASGITransport(app=app), cookies={COOKIE_NAME: token}, **kwargs)


# Starlette 0.41's sync TestClient hard-codes the non-IP host "testclient".
# Supply a real transport peer in tests; production never trusts such a name.


@pytest.fixture(autouse=True)
def test_client_peer(monkeypatch):
    from starlette.testclient import _TestClientTransport
    original = _TestClientTransport.handle_request

    def handle_request(self, request):
        original_app = self.app

        async def with_peer(scope, receive, send):
            if scope.get("client", [None])[0] == "testclient":
                scope = {**scope, "client": ("127.0.0.1", 50000)}
            await original_app(scope, receive, send)

        # A transport copy avoids mutating shared apps during concurrent tests.
        transport = copy(self)
        transport.app = with_peer
        return original(transport, request)

    monkeypatch.setattr(_TestClientTransport, "handle_request", handle_request)
