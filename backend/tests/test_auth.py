"""Public test credentials/tokens only; never production secrets."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import secrets

from argon2 import PasswordHasher
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import event, select, text, update
import pytest

from app import auth
from app.config import Settings
from app.identity import create_user, password_needs_rehash, verify_password
from app.main import create_app
from app.models import Base, PayRate, User, UserSession

PASSWORD = 'public-test-only long password żółw'
WEBHOOK_TOKEN = 'public-test-only-webhook-token-32-characters'


@pytest.fixture
def setup(tmp_path):
    clock = [datetime(2030, 1, 1, tzinfo=timezone.utc)]
    app = create_app(Settings(
        webhook_token=WEBHOOK_TOKEN, database_url=f"sqlite:///{tmp_path / 'auth.db'}",
        session_cookie_secure=False,
    ), now_provider=lambda: clock[0])
    factory = app.state.session_factory
    Base.metadata.create_all(factory.kw['bind'])
    with factory() as session:
        create_user(session, 'Owner', PASSWORD)
        session.add(PayRate(effective_from=date(1970, 1, 1), hourly_rate=Decimal('50.00'),
                            currency='PLN', created_at=clock[0]))
        session.commit()
    with TestClient(app) as client:
        yield client, factory, clock
    factory.kw['bind'].dispose()


def login(client, username='owner', password=PASSWORD):
    return client.post('/api/auth/login', json={'username': username, 'password': password})


def test_login_normalization_and_me(setup, caplog):
    client, factory, clock = setup
    response = login(client, ' Owner ')
    assert response.status_code == 200
    assert response.json() == {'username': 'owner', 'expires_at': (clock[0] + timedelta(days=180)).isoformat()}
    assert (client.get('/api/auth/me')).json() == response.json()
    cookie = client.cookies.get(auth.COOKIE_NAME)
    assert cookie not in response.text
    with factory() as session:
        stored = session.scalar(select(UserSession))
        assert stored.token_hash == hashlib.sha256(cookie.encode('ascii')).hexdigest()
        assert stored.created_at == stored.last_seen_at == clock[0]
        assert stored.expires_at == clock[0] + timedelta(days=180)
        assert stored.revoked_at is None
        raw = session.execute(text('SELECT * FROM user_sessions')).one()
        assert cookie not in str(raw)
    assert cookie not in caplog.text and PASSWORD not in caplog.text
    assert response.headers['cache-control'] == 'no-store'


@pytest.mark.parametrize('username,password', [
    ('owner', 'wrong public-test-only password'), ('unknown', PASSWORD),
    ('invalid username', PASSWORD), ('owner', 'short'),
])
def test_generic_failure_creates_no_session(setup, username, password):
    client, factory, _ = setup
    response = login(client, username, password)
    assert response.status_code == 401
    assert response.json() == {'detail': 'Nieprawidłowa nazwa użytkownika lub hasło.'}
    assert 'set-cookie' not in response.headers
    with factory() as session:
        assert list(session.scalars(select(UserSession))) == []


def test_nonexistent_account_performs_argon2_verification(setup, monkeypatch):
    client, _, _ = setup
    calls = []
    original = auth.verify_password
    def observed(password, encoded):
        calls.append(encoded)
        return original(password, encoded)
    monkeypatch.setattr(auth, 'verify_password', observed)
    assert login(client, 'unknown').status_code == 401
    assert calls == [auth._DUMMY_HASH]
    assert calls[0].startswith('$argon2id$')


def test_successful_login_rehashes_only_after_verification(setup):
    client, factory, _ = setup
    old = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1).hash(PASSWORD)
    with factory() as session:
        session.execute(update(User).values(password_hash=old))
        session.commit()
    assert login(client, password='wrong public-test-only password').status_code == 401
    with factory() as session:
        assert session.scalar(select(User.password_hash)) == old
    assert login(client).status_code == 200
    with factory() as session:
        encoded = session.scalar(select(User.password_hash))
        assert encoded != old and verify_password(PASSWORD, encoded)
        assert not password_needs_rehash(encoded)


def test_new_random_token_each_login_prevents_fixation(setup, monkeypatch):
    client, factory, _ = setup
    original = secrets.token_urlsafe
    lengths = []
    def observed(length):
        lengths.append(length)
        return original(length)
    monkeypatch.setattr(auth.secrets, 'token_urlsafe', observed)
    client.cookies.set(auth.COOKIE_NAME, 'prelogin-public-test-only-identifier')
    assert login(client).status_code == 200
    first = client.cookies.get(auth.COOKIE_NAME, domain='testserver.local')
    assert login(client).status_code == 200
    second = client.cookies.get(auth.COOKIE_NAME, domain='testserver.local')
    assert first != second and len(first) == len(second) == 43
    assert lengths == [32, 32]
    with factory() as session:
        assert len(list(session.scalars(select(UserSession)))) == 2


@pytest.mark.parametrize('cookie', [None, '', 'garbage', 'a' * 42, 'a' * 44, '!' * 43, 'a' * 43])
def test_invalid_cookie_fails_closed_without_changing_cookie(setup, cookie):
    client, _, _ = setup
    if cookie is not None:
        client.cookies.set(auth.COOKIE_NAME, cookie)
    response = client.get('/api/auth/me')
    assert response.status_code == 401
    assert client.get('/api/dashboard').status_code == 401
    assert 'set-cookie' not in response.headers
    assert response.headers['cache-control'] == 'no-store'


def test_revoked_session(setup):
    client, factory, clock = setup
    login(client)
    with factory() as session:
        session.execute(update(UserSession).values(revoked_at=clock[0]))
        session.commit()
    assert client.get('/api/auth/me').status_code == 401
    assert client.get('/api/dashboard').status_code == 401


@pytest.mark.parametrize('offset,expected', [(-1, 200), (0, 401), (1, 401)])
@pytest.mark.parametrize('deadline', ['idle', 'absolute'])
def test_exact_expiration_boundaries(setup, offset, expected, deadline):
    client, factory, clock = setup
    login(client)
    initial = clock[0]
    clock[0] = initial + timedelta(days=30 if deadline == 'idle' else 180, microseconds=offset)
    if deadline == 'absolute':
        with factory() as session:
            session.execute(update(UserSession).values(last_seen_at=initial + timedelta(days=179)))
            session.commit()
    response = client.get('/api/auth/me')
    assert response.status_code == expected
    assert client.get('/api/dashboard').status_code == expected
    with factory() as session:
        assert session.scalar(select(UserSession.expires_at)) == initial + timedelta(days=180)
        if expected == 401:
            assert session.scalar(select(UserSession.last_seen_at)) == (
                initial if deadline == 'idle' else initial + timedelta(days=179))


def test_logout_revokes_and_clears_cookie_idempotently(setup):
    client, factory, clock = setup
    login(client)
    token = client.cookies.get(auth.COOKIE_NAME)
    response = client.post('/api/auth/logout')
    assert response.status_code == 204 and response.content == b''
    assert 'Max-Age=0' in response.headers['set-cookie']
    assert 'HttpOnly' in response.headers['set-cookie']
    assert 'SameSite=lax' in response.headers['set-cookie']
    assert 'Path=/' in response.headers['set-cookie']
    assert auth.COOKIE_NAME not in client.cookies
    with factory() as session:
        assert session.scalar(select(UserSession.revoked_at)) == clock[0]
    client.cookies.set(auth.COOKIE_NAME, token)
    assert client.get('/api/auth/me').status_code == 401
    assert client.post('/api/auth/logout').status_code == 204
    assert client.post('/api/auth/logout').status_code == 204


@pytest.mark.parametrize('secure', [False, True])
def test_cookie_attributes_and_no_forwarded_header_inference(setup, secure):
    _, factory, clock = setup
    app = create_app(Settings(webhook_token=WEBHOOK_TOKEN,
                     database_url=str(factory.kw['bind'].url), session_cookie_secure=secure),
                     now_provider=lambda: clock[0])
    with TestClient(app, base_url='https://testserver' if secure else 'http://testserver') as client:
        response = client.post('/api/auth/login', json={'username': 'owner', 'password': PASSWORD},
                               headers={'X-Forwarded-Proto': 'https' if not secure else 'http'})
        cookie = response.headers['set-cookie']
        for attribute in ['HttpOnly', 'SameSite=lax', 'Path=/', 'Max-Age=15552000', 'expires=']:
            assert attribute in cookie
        assert ('Secure' in cookie) == secure
        assert 'Domain=' not in cookie
        assert client.get('/api/auth/me').status_code == 200
    app.state.session_factory.kw['bind'].dispose()


def test_secure_default_and_wildcard_cors_rejected():
    assert Settings(webhook_token=WEBHOOK_TOKEN).session_cookie_secure is True
    for origins in ['*', 'http://localhost:5173,*', 'null']:
        with pytest.raises(ValidationError):
            Settings(webhook_token=WEBHOOK_TOKEN, cors_origins=origins)


def test_credentialed_cors_is_explicit(setup):
    client, _, _ = setup
    allowed = client.options('/api/auth/login', headers={
        'Origin': 'http://localhost:5173', 'Access-Control-Request-Method': 'POST',
        'Access-Control-Request-Headers': 'Content-Type',
    })
    assert allowed.status_code == 200
    assert allowed.headers['access-control-allow-origin'] == 'http://localhost:5173'
    assert allowed.headers['access-control-allow-credentials'] == 'true'
    denied = client.options('/api/auth/login', headers={
        'Origin': 'http://untrusted.example', 'Access-Control-Request-Method': 'POST',
    })
    assert denied.status_code == 400
    assert 'access-control-allow-origin' not in denied.headers


def test_activity_writes_are_coalesced_and_token_absolute_expiry_stable(setup):
    client, factory, clock = setup
    login(client)
    cookie = client.cookies.get(auth.COOKIE_NAME)
    initial = clock[0]
    writes = []
    def track(_connection, _cursor, statement, *_args):
        if statement.startswith('UPDATE user_sessions'):
            writes.append(statement)
    engine = factory.kw['bind']
    event.listen(engine, 'before_cursor_execute', track)
    try:
        clock[0] += timedelta(minutes=59, seconds=59)
        for _ in range(10):
            response = client.get('/api/auth/me')
            assert response.status_code == 200 and 'set-cookie' not in response.headers
        assert not writes
        clock[0] = initial + timedelta(hours=1)
        assert client.get('/api/auth/me').status_code == 200
        assert len(writes) == 1
        assert client.get('/api/auth/me').status_code == 200
        assert len(writes) == 1
    finally:
        event.remove(engine, 'before_cursor_execute', track)
    assert client.cookies.get(auth.COOKIE_NAME) == cookie
    with factory() as session:
        stored = session.scalar(select(UserSession))
        assert stored.last_seen_at == clock[0]
        assert stored.expires_at == initial + timedelta(days=180)


def test_concurrent_logins_create_independently_revocable_sessions(setup):
    client, factory, clock = setup
    app = client.app
    def independent_login(_):
        with TestClient(app) as device:
            assert login(device).status_code == 200
            return device.cookies.get(auth.COOKIE_NAME)
    with ThreadPoolExecutor(max_workers=2) as pool:
        tokens = list(pool.map(independent_login, range(2)))
    assert len(set(tokens)) == 2
    clock[0] += timedelta(hours=1)
    def touch(token):
        with TestClient(app) as device:
            device.cookies.set(auth.COOKIE_NAME, token)
            return device.get('/api/auth/me').status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(touch, [tokens[0], tokens[0]])) == [200, 200]
    with TestClient(app) as device:
        device.cookies.set(auth.COOKIE_NAME, tokens[0])
        assert device.post('/api/auth/logout').status_code == 204
    assert touch(tokens[0]) == 401 and touch(tokens[1]) == 200
    with factory() as session:
        # Indexed user_id permits future bulk revocation without token knowledge.
        session.execute(update(UserSession).where(UserSession.user_id == 1).values(revoked_at=clock[0]))
        session.commit()
    assert touch(tokens[1]) == 401


@pytest.mark.parametrize('browser_session', ['none', 'valid', 'malformed', 'revoked'])
def test_ha_auth_independent(setup, browser_session):
    client, factory, clock = setup
    if browser_session in ['valid', 'revoked']:
        login(client)
    if browser_session == 'malformed':
        client.cookies.set(auth.COOKIE_NAME, 'bad-cookie')
    if browser_session == 'revoked':
        with factory() as session:
            session.execute(update(UserSession).values(revoked_at=clock[0]))
            session.commit()
    from app.models import LocationTimezone
    with factory() as session:
        session.add(LocationTimezone(location='gabinet_zabki', timezone='Europe/Warsaw',
                                     updated_at=datetime.now(timezone.utc)))
        session.commit()
    payload = {'event': 'entry', 'location': 'gabinet_zabki',
               'timestamp': '2026-10-06T08:00:17+02:00', 'source': 'home_assistant'}
    assert client.post('/api/webhook/home-assistant', json=payload).status_code == 401
    assert client.post('/api/webhook/home-assistant', json=payload,
                       headers={'X-Webhook-Token': 'wrong'}).status_code == 401
    accepted = client.post('/api/webhook/home-assistant', json=payload, headers={'X-Webhook-Token': WEBHOOK_TOKEN})
    assert accepted.status_code == 201
    correction = {'raw_event_id': accepted.json()['id'], 'time': '08:05'}
    assert client.post('/api/webhook/home-assistant/correction', json=correction).status_code == 401
    assert client.post('/api/webhook/home-assistant/correction', json=correction,
                       headers={'X-Webhook-Token': 'wrong'}).status_code == 401
    assert client.post('/api/webhook/home-assistant/correction', json=correction,
                       headers={'X-Webhook-Token': WEBHOOK_TOKEN}).status_code == 200
    with factory() as session:
        assert session.execute(text('SELECT event_timestamp FROM work_events')).scalar_one() == payload['timestamp']


def test_activity_touch_cannot_revive_session_revoked_concurrently(setup):
    client, factory, clock = setup
    login(client)
    clock[0] += timedelta(hours=1)
    engine = factory.kw['bind']
    revoked = False
    def revoke_before_touch(_connection, _cursor, statement, *_args):
        nonlocal revoked
        if statement.startswith('UPDATE user_sessions') and not revoked:
            revoked = True
            with factory() as session:
                session.execute(update(UserSession).values(revoked_at=clock[0]))
                session.commit()
    event.listen(engine, 'before_cursor_execute', revoke_before_touch)
    try:
        assert client.get('/api/auth/me').status_code == 401
    finally:
        event.remove(engine, 'before_cursor_execute', revoke_before_touch)
    with factory() as session:
        stored = session.scalar(select(UserSession))
        assert stored.revoked_at == clock[0]
        assert stored.last_seen_at == stored.created_at


@pytest.mark.parametrize('same_password', [True, False])
def test_rehash_never_overwrites_concurrent_password_hash_change(setup, same_password):
    client, factory, _ = setup
    old = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1).hash(PASSWORD)
    replacement = auth.hash_password(PASSWORD if same_password else 'different-public-test-only-password')
    with factory() as session:
        session.execute(update(User).values(password_hash=old))
        session.commit()
    engine = factory.kw['bind']
    replaced = False
    def replace_before_rehash(_connection, _cursor, statement, *_args):
        nonlocal replaced
        if statement.startswith('UPDATE users') and not replaced:
            replaced = True
            with factory() as session:
                session.execute(update(User).values(password_hash=replacement))
                session.commit()
    event.listen(engine, 'before_cursor_execute', replace_before_rehash)
    try:
        assert login(client).status_code == (200 if same_password else 401)
    finally:
        event.remove(engine, 'before_cursor_execute', replace_before_rehash)
    with factory() as session:
        assert session.scalar(select(User.password_hash)) == replacement
        assert len(list(session.scalars(select(UserSession)))) == (1 if same_password else 0)


def test_sql_exceptions_hide_credential_parameters(setup):
    from sqlalchemy.exc import IntegrityError
    _, factory, _ = setup
    encoded = auth.hash_password(PASSWORD)
    with factory() as session:
        session.add(User(username='owner', password_hash=encoded, created_at=datetime.now(timezone.utc)))
        with pytest.raises(IntegrityError) as caught:
            session.commit()
        assert encoded not in str(caught.value)
        assert 'parameters hidden' in str(caught.value)


def test_session_survives_application_restart(setup):
    client, factory, clock = setup
    assert login(client).status_code == 200
    token = client.cookies.get(auth.COOKIE_NAME)
    restarted = create_app(Settings(webhook_token=WEBHOOK_TOKEN,
                           database_url=str(factory.kw['bind'].url), session_cookie_secure=False),
                           now_provider=lambda: clock[0])
    with TestClient(restarted) as device:
        device.cookies.set(auth.COOKIE_NAME, token)
        assert device.get('/api/auth/me').json()['username'] == 'owner'
    restarted.state.session_factory.kw['bind'].dispose()
