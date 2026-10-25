"""8B1-A browser boundary regressions; all credentials are public test fixtures."""

from datetime import timedelta
import json

from fastapi.testclient import TestClient
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
import pytest
from sqlalchemy import event, select, text, update
from starlette.responses import JSONResponse
from starlette.routing import Route, Router

from app import auth
from app.browser_security import CSP, LOGIN_BODY_LIMIT, SECURITY_HEADERS
from app.config import Settings
from app.identity import hash_password
from app.models import User, UserSession
from app.origins import canonical_origin
from conftest import BROWSER_HEADERS
from test_auth import PASSWORD, WEBHOOK_TOKEN, login, setup


@pytest.fixture
def anyio_backend():
    return 'asyncio'


@pytest.mark.parametrize('elapsed,expected', [(1, 200), (29, 200), (30, 401)])
def test_cross_site_me_is_entirely_read_only(setup, elapsed, expected):
    client, factory, clock = setup
    assert login(client).status_code == 200
    initial = clock[0]
    clock[0] += timedelta(days=elapsed)
    with factory() as session:
        before = list(session.execute(text('SELECT * FROM user_sessions')))
    statements = []
    engine = factory.kw['bind']
    def track(_connection, _cursor, statement, *_args):
        statements.append(statement)
    event.listen(engine, 'before_cursor_execute', track)
    try:
        # Lax cookie can accompany a top-level cross-site GET. Fetch metadata is optional.
        for metadata in [{}, {'Sec-Fetch-Site': 'cross-site', 'Sec-Fetch-Mode': 'navigate'}]:
            response = client.get('/api/auth/me', headers={'Origin': 'https://evil.example', **metadata})
            assert response.status_code == expected
            assert 'set-cookie' not in response.headers
            assert response.headers['cache-control'] == 'no-store'
    finally:
        event.remove(engine, 'before_cursor_execute', track)
    assert all(not sql.lstrip().upper().startswith(('UPDATE', 'INSERT', 'DELETE')) for sql in statements)
    with factory() as session:
        assert list(session.execute(text('SELECT * FROM user_sessions'))) == before
        assert session.scalar(select(UserSession.last_seen_at)) == initial


@pytest.mark.parametrize('endpoint', ['login', 'logout', 'heartbeat'])
@pytest.mark.parametrize('bad_headers', [
    {}, {'Origin': 'http://testserver'}, {'X-Work-Tracker-Request': '1'},
    {'Origin': 'null', 'X-Work-Tracker-Request': '1'},
    {'Origin': 'http://testserver', 'X-Work-Tracker-Request': '0'},
    {'Origin': 'http://testserver', 'X-Work-Tracker-Request': '1, 1'},
    {'Origin': 'http://testserver:8001', 'X-Work-Tracker-Request': '1'},
    {'Origin': 'http://testserver.evil.example', 'X-Work-Tracker-Request': '1'},
    {'Origin': 'http://sub.testserver', 'X-Work-Tracker-Request': '1'},
    {'Origin': 'http://testserver http://testserver', 'X-Work-Tracker-Request': '1'},
    {'Origin': 'http://testserver,http://testserver', 'X-Work-Tracker-Request': '1'},
    [('Origin', 'http://testserver'), ('Origin', 'http://testserver'), ('X-Work-Tracker-Request', '1')],
    [('Origin', 'http://testserver'), ('X-Work-Tracker-Request', '1'), ('X-Work-Tracker-Request', '1')],
])
def test_auth_mutations_require_single_origin_and_marker(setup, endpoint, bad_headers):
    client, factory, clock = setup
    assert login(client).status_code == 200
    clock[0] += timedelta(hours=1)
    with factory() as session:
        before = list(session.execute(text('SELECT * FROM user_sessions')))
    client.headers.clear()
    response = client.post(f'/api/auth/{endpoint}', headers=bad_headers,
                           json={'username': 'owner', 'password': PASSWORD} if endpoint == 'login' else {})
    assert response.status_code == 403
    assert response.headers['cache-control'] == 'no-store'
    assert 'set-cookie' not in response.headers
    with factory() as session:
        assert list(session.execute(text('SELECT * FROM user_sessions'))) == before


@pytest.mark.parametrize('deadline', ['idle', 'absolute'])
@pytest.mark.parametrize('offset,expected', [(-1, 200), (0, 401), (1, 401)])
def test_heartbeat_deadlines_do_not_revive_or_extend_absolute(setup, deadline, offset, expected):
    client, factory, clock = setup
    assert login(client).status_code == 200
    initial = clock[0]
    prior_activity = initial if deadline == 'idle' else initial + timedelta(days=179)
    with factory() as session:
        session.execute(update(UserSession).values(last_seen_at=prior_activity))
        session.commit()
    clock[0] = initial + timedelta(days=30 if deadline == 'idle' else 180, microseconds=offset)
    response = client.post('/api/auth/heartbeat', json={})
    assert response.status_code == expected
    assert 'set-cookie' not in response.headers
    with factory() as session:
        stored = session.scalar(select(UserSession))
        assert stored.last_seen_at == (clock[0] if expected == 200 else prior_activity)
        assert stored.expires_at == initial + auth.ABSOLUTE_LIFETIME


def test_heartbeat_is_not_anonymous_and_revoked_cannot_be_renewed(setup):
    client, factory, clock = setup
    assert client.post('/api/auth/heartbeat').status_code == 401
    assert ('POST', '/api/auth/heartbeat') not in auth.SESSION_INDEPENDENT_ENDPOINTS
    assert login(client).status_code == 200
    initial = clock[0]
    with factory() as session:
        session.execute(update(UserSession).values(revoked_at=initial))
        session.commit()
    clock[0] += timedelta(hours=1)
    assert client.post('/api/auth/heartbeat').status_code == 401
    with factory() as session:
        assert session.scalar(select(UserSession.last_seen_at)) == initial


@pytest.mark.parametrize('content_type', [
    'text/plain', 'application/x-www-form-urlencoded', 'multipart/form-data; boundary=test',
    'application/json; charset=iso-8859-1', 'application/json; unsupported=true',
])
@pytest.mark.parametrize('endpoint', ['login', 'logout', 'heartbeat'])
def test_simple_or_unsupported_body_types_never_mutate(setup, content_type, endpoint, monkeypatch):
    client, _, _ = setup
    assert login(client).status_code == 200
    monkeypatch.setattr(auth, 'verify_password', lambda *_: pytest.fail('must reject before Argon2'))
    response = client.post(f'/api/auth/{endpoint}', content='{}', headers={'Content-Type': content_type})
    assert response.status_code == 415
    assert 'set-cookie' not in response.headers


def test_body_requires_json_but_bodyless_mutations_work_on_lan_http(setup):
    client, _, _ = setup
    assert client.post('/api/auth/login', content='{}').status_code == 415
    assert client.post('/api/auth/login', json={'username': 'owner', 'password': PASSWORD},
                       headers={'Content-Type': 'application/json; charset=UTF-8'}).status_code == 200
    assert client.post('/api/auth/heartbeat').status_code == 200
    assert client.post('/api/auth/logout').status_code == 204
    assert client.post('/api/auth/logout', json={}).status_code == 204


def test_future_routes_mounts_and_unsafe_methods_inherit_policy(setup):
    client, _, _ = setup
    mounts = [r for r in client.app.routes if getattr(r, 'name', '') == 'frontend']
    for route in mounts:
        client.app.routes.remove(route)
    calls = []
    async def handler(_request):
        calls.append(True)
        return JSONResponse({'ok': True})
    methods = ['POST', 'PUT', 'PATCH', 'DELETE', 'TRACE', 'CUSTOM']
    client.app.router.routes.append(Route('/api/future', handler, methods=methods))
    client.app.mount('/api/auth/future', Router(routes=[Route('/data', handler, methods=methods)]))
    client.app.routes.extend(mounts)
    for path in ['/api/future', '/api/auth/future/data']:
        for method in methods:
            assert client.request(method, path, json={}).status_code == 401
    assert login(client).status_code == 200
    for path in ['/api/future', '/api/auth/future/data']:
        for method in methods:
            client.headers.pop('Origin', None)
            assert client.request(method, path, json={}).status_code == 403
            assert not calls
        for method in methods:
            assert client.request(method, path, headers=BROWSER_HEADERS, json={}).status_code == 200
        calls.clear()


@pytest.mark.parametrize('size,status', [(16383, 200), (16384, 200), (16385, 413)])
@pytest.mark.parametrize('declared_length', [None, '1', '999999'])
@pytest.mark.anyio
async def test_login_body_is_counted_while_streaming_before_parsing(setup, size, status, declared_length, monkeypatch):
    client, _, _ = setup
    data = json.dumps({'username': 'owner', 'password': PASSWORD}).encode('utf-8')
    data += b' ' * (size - len(data))
    chunks = [data[:100], data[100:16000], data[16000:]]
    if status == 413:
        monkeypatch.setattr(auth, 'verify_password', lambda *_: pytest.fail('oversized body reached Argon2'))
    async def stream():
        for chunk in chunks:
            yield chunk
    headers = {**BROWSER_HEADERS, 'Content-Type': 'application/json'}
    if declared_length is not None:
        headers['Content-Length'] = declared_length
    async with AsyncClient(transport=ASGITransport(app=client.app), base_url='http://testserver') as device:
        response = await device.post('/api/auth/login', content=stream(), headers=headers)
    assert response.status_code == status
    if status == 413:
        assert 'set-cookie' not in response.headers


@pytest.mark.parametrize('encoding', ['gzip', 'deflate', 'br'])
def test_login_rejects_content_encoding_before_parsing(setup, encoding, monkeypatch):
    client, _, _ = setup
    monkeypatch.setattr(auth, 'verify_password', lambda *_: pytest.fail('encoded body reached Argon2'))
    assert client.post('/api/auth/login', content=b'not compressed JSON', headers={
        'Content-Type': 'application/json', 'Content-Encoding': encoding,
    }).status_code == 415


@pytest.mark.parametrize('password', ['a' * 15, 'ż' * 1024, '😀' * 1024, ' ' + 'ą😀' * 7])
@pytest.mark.parametrize('escaped', [False, True])
def test_unicode_password_length_is_code_points_after_json_unescape(setup, password, escaped):
    client, factory, _ = setup
    with factory() as session:
        session.execute(update(User).values(password_hash=hash_password(password)))
        session.commit()
    body = json.dumps({'username': 'owner', 'password': password}, ensure_ascii=escaped).encode('utf-8')
    assert len(body) <= LOGIN_BODY_LIMIT
    response = client.post('/api/auth/login', content=body, headers={'Content-Type': 'application/json'})
    assert response.status_code == 200
    assert password not in response.text


@pytest.mark.parametrize('password', ['a' * 14, '😀' * 1025, 'abc\x00' * 4, '\ud800' * 15])
def test_invalid_password_has_generic_failure_without_echo(setup, password):
    client, _, _ = setup
    response = client.post('/api/auth/login', content=json.dumps({'username': 'owner', 'password': password}),
                           headers={'Content-Type': 'application/json'})
    assert response.status_code == 401
    assert response.json() == {'detail': 'Nieprawidłowa nazwa użytkownika lub hasło.'}
    assert 'set-cookie' not in response.headers


@pytest.mark.parametrize('body', [
    '{"username":"owner","password": NaN}',
    '{"password":{"secret":"do-not-reflect-input"}}',
    '["do-not-reflect-input"]', '{"password":"do-not-reflect-input"',
])
def test_json_and_schema_errors_do_not_echo_credentials(setup, body, monkeypatch):
    client, _, _ = setup
    monkeypatch.setattr(auth, 'verify_password', lambda *_: pytest.fail('invalid JSON/schema reached Argon2'))
    response = client.post('/api/auth/login', content=body, headers={'Content-Type': 'application/json'})
    assert response.status_code == 422
    assert 'do-not-reflect-input' not in response.text and 'NaN' not in response.text


def test_headers_cache_exports_errors_and_disabled_docs(setup):
    client, _, _ = setup
    responses = [client.get('/api/dashboard'), client.options('/api/auth/login', headers={
        'Origin': 'https://evil.example', 'Access-Control-Request-Method': 'POST',
    })]
    assert login(client).status_code == 200
    for path in ['/api/auth/me', '/api/openapi.json', '/api/dashboard',
                 '/api/export/monthly.csv?year=2026&month=10',
                 '/api/export/monthly.pdf?year=2026&month=10', '/api/no-such-route']:
        responses.append(client.get(path))
    for path in ['/api/docs', '/api/redoc', '/api/docs/oauth2-redirect']:
        response = client.get(path)
        assert response.status_code == 404
        responses.append(response)
    for response in responses:
        for name, value in SECURITY_HEADERS.items():
            assert response.headers[name] == value
        assert response.headers['cache-control'] == 'no-store'
        assert 'strict-transport-security' not in response.headers
    assert "'unsafe-inline'" not in CSP and "'unsafe-eval'" not in CSP


def test_unhandled_errors_are_hardened_without_exposing_exception(setup):
    client, _, _ = setup
    async def fail():
        raise RuntimeError('private error details')
    client.app.router.routes.insert(0, APIRoute('/api/failure', fail))
    assert login(client).status_code == 200
    with TestClient(client.app, raise_server_exceptions=False, headers=BROWSER_HEADERS,
                    cookies=dict(client.cookies)) as device:
        response = device.get('/api/failure')
    assert response.status_code == 500
    assert response.headers['cache-control'] == 'no-store'
    assert response.headers['content-security-policy'] == CSP
    assert 'private error details' not in response.text


@pytest.mark.parametrize('origin', ['', '*', 'null', 'http://host/', 'http://user@host',
    'http://host?query', 'http://host#fragment', 'http://host:0', 'http://host:65536',
    'http://host:', 'https://*.example.com', 'http://host,http://host,',
    'http://host http://other', 'http://127.1', 'http://0177.0.0.1',
    '\x00http://host', 'http://host\x7f', 'http://[fe80::1%eth0]'])
def test_invalid_origin_configuration_rejected(origin):
    with pytest.raises(ValidationError):
        Settings(webhook_token=WEBHOOK_TOKEN, browser_allowed_origins=origin, cors_origins='', _env_file=None)


def test_origin_configuration_is_required_and_cors_is_subset(monkeypatch):
    monkeypatch.delenv('BROWSER_ALLOWED_ORIGINS', raising=False)
    with pytest.raises(ValidationError, match='browser_allowed_origins'):
        Settings(webhook_token=WEBHOOK_TOKEN, _env_file=None)
    with pytest.raises(ValidationError, match='subset'):
        Settings(webhook_token=WEBHOOK_TOKEN, browser_allowed_origins='http://work-tracker.home.arpa:8000',
                 cors_origins='http://localhost:5173', _env_file=None)
    settings = Settings(webhook_token=WEBHOOK_TOKEN, browser_allowed_origins='HTTP://EXAMPLE.COM:80,https://[::1]:443',
                        cors_origins='http://example.com', _env_file=None)
    assert settings.browser_origin_list == ['http://example.com', 'https://[::1]']
    assert canonical_origin('http://[2001:0db8::1]:8000') == 'http://[2001:db8::1]:8000'


def test_preflights_allow_only_explicit_cors_methods_and_headers(setup):
    client, _, _ = setup
    client.headers.clear()
    base = {'Origin': 'http://localhost:5173', 'Access-Control-Request-Method': 'PATCH',
            'Access-Control-Request-Headers': 'Content-Type, X-Work-Tracker-Request'}
    allowed = client.options('/api/future', headers=base)
    assert allowed.status_code == 200
    assert allowed.headers['vary'] == 'Origin'
    assert allowed.headers['access-control-allow-origin'] == base['Origin']
    for changes in [{'Origin': 'null'}, {'Origin': 'https://evil.example'},
                    {'Access-Control-Request-Method': 'TRACE'},
                    {'Access-Control-Request-Headers': 'X-Unapproved'}]:
        assert client.options('/api/future', headers={**base, **changes}).status_code == 400
    # Allowed for same-origin mutations does not automatically mean allowed CORS.
    assert client.options('/api/future', headers={**base, 'Origin': 'http://testserver'}).status_code == 400
    response = client.post('/api/auth/logout', headers={**BROWSER_HEADERS, 'Origin': base['Origin']})
    assert response.status_code == 204
    assert response.headers['access-control-expose-headers'] == 'Retry-After'
