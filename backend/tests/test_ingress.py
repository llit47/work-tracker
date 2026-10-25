"""C1/C2 policy tests. Synthetic ASGI peers do NOT prove tunnel/Access security.

Real TCP proxy tests live in deploy/tests/ingress_security_test.py; the actual
cloudflared JWT verifier and Phase 9 checks are separate evidence levels.
"""
from datetime import timedelta

from fastapi.testclient import TestClient
from pydantic import ValidationError
import pytest
from sqlalchemy import select

from app import auth
from app.config import Settings
from app.ingress import BROWSER_PEER, HA_PEER
from app.main import create_app
from app.models import LocationTimezone, WorkEvent, WorkEventCorrection
from test_auth import PASSWORD, WEBHOOK_TOKEN, setup
from test_login_limits import fast_verify, rows, seed

ORIGIN = 'https://work.example.invalid'
HEADERS = {'Origin': ORIGIN, 'X-Work-Tracker-Request': '1',
           'X-Forwarded-Proto': 'https', 'CF-Connecting-IP': '192.0.2.1'}
WEBHOOK = '/api/webhook/home-assistant'


class TransportPeer:
    """Test-only transport fixture, never installed in the application."""
    def __init__(self, app, peer):
        self.app, self.peer = app, peer
        self.target = None

    async def __call__(self, scope, receive, send):
        if scope['type'] == 'http':
            scope = dict(scope, client=(self.peer, 1234))
            if self.target is not None:
                scope.update(raw_path=self.target.encode('ascii'), path=self.target)
        await self.app(scope, receive, send)


def split_app(factory, clock, **settings):
    return create_app(Settings(_env_file=None, webhook_token=WEBHOOK_TOKEN,
        database_url=str(factory.kw['bind'].url), ingress_profile='split',
        browser_allowed_origins=ORIGIN, cors_origins='', session_cookie_secure=True,
        **settings), now_provider=lambda: clock[0])


@pytest.fixture
def split(setup):
    _, factory, clock = setup
    app = split_app(factory, clock, browser_hsts_seconds=300)
    transport = TransportPeer(app, BROWSER_PEER)
    with TestClient(transport, base_url=ORIGIN, headers=HEADERS) as browser:
        yield browser, transport, factory, clock
    app.state.session_factory.kw['bind'].dispose()


def login(client, **kwargs):
    return client.post('/api/auth/login', json={'username': kwargs.pop('username', 'owner'),
                       'password': kwargs.pop('password', PASSWORD)}, **kwargs)


@pytest.mark.parametrize('overrides', [
    {'session_cookie_secure': False}, {'browser_allowed_origins': 'http://work.example.invalid'},
    {'browser_allowed_origins': ORIGIN + ',https://alias.example.invalid'},
    {'cors_origins': ORIGIN}, {'browser_allowed_origins': ORIGIN + ':444'},
    {'ingress_profile': 'other'}, {'browser_hsts_seconds': -1},
    {'browser_hsts_seconds': 31536001},
])
def test_split_invalid_configuration_fails_startup(overrides):
    values = dict(_env_file=None, webhook_token=WEBHOOK_TOKEN, browser_allowed_origins=ORIGIN,
                  cors_origins='', ingress_profile='split')
    with pytest.raises(ValidationError):
        Settings(**(values | overrides))


@pytest.mark.parametrize('value', [None, '', 'unknown', '192.0.2.1,192.0.2.2',
    ' 192.0.2.1', '192.0.2.1 ', 'fe80::1%eth0', '240.0.0.1', '2a06:98c0:3600::103'])
def test_missing_or_invalid_trusted_client_ip_fails_closed(split, fast_verify, value):
    browser, _, factory, _ = split
    del browser.headers['cf-connecting-ip']
    headers = {} if value is None else {'CF-Connecting-IP': value}
    response = login(browser, headers=headers)
    assert response.status_code == 503
    assert 'set-cookie' not in response.headers
    assert not rows(factory) and not fast_verify


def test_duplicate_ip_header_and_spoofed_forwarding_headers(split, fast_verify):
    browser, _, factory, _ = split
    response = login(browser, headers=[('CF-Connecting-IP', '192.0.2.1'),
                                     ('CF-Connecting-IP', '192.0.2.2')])
    assert response.status_code == 503 and not rows(factory)
    assert login(browser, headers={'X-Forwarded-For': '198.51.100.5',
        'Forwarded': 'for=198.51.100.6;proto=http;host=evil.invalid',
        'X-Real-IP': '198.51.100.7'}).status_code == 200
    assert rows(factory)[0].ip_key == '192.0.2.1/32'


@pytest.mark.parametrize('peer', ['192.0.2.1', '127.0.0.1', '::1', '127.0.0.4'])
def test_direct_peer_cannot_spoof_ingress_or_ip(split, fast_verify, peer):
    browser, transport, factory, _ = split
    transport.peer = peer
    assert login(browser, headers={'CF-Connecting-IP': '198.51.100.5',
        'X-Forwarded-For': BROWSER_PEER, 'Forwarded': f'for={BROWSER_PEER}',
        'Cf-Access-Jwt-Assertion': 'header-presence-is-not-proof'}).status_code == 403
    assert browser.get('/api/health').status_code == 403
    assert not rows(factory) and not fast_verify


@pytest.mark.parametrize('headers', [
    {'Host': 'alias.example.invalid'}, {'Host': 'work.example.invalid:443'},
    {'X-Forwarded-Proto': 'http'}, {'X-Forwarded-Proto': 'https,http'},
    [('X-Forwarded-Proto', 'https'), ('X-Forwarded-Proto', 'https')],
    [('Host', 'work.example.invalid'), ('Host', 'alias.example.invalid')],
    {'CF-Worker': 'example.invalid'},
])
def test_host_scheme_and_workers_fail_closed(split, headers):
    browser, _, _, _ = split
    assert browser.get('/api/health', headers=headers).status_code == 403


@pytest.mark.parametrize('method', ['GET', 'HEAD', 'PUT', 'PATCH', 'DELETE', 'OPTIONS', 'TRACE', 'CONNECT'])
@pytest.mark.parametrize('path', [WEBHOOK, WEBHOOK + '/correction'])
def test_private_ha_has_no_alternative_methods(split, method, path):
    browser, transport, _, _ = split
    transport.peer = HA_PEER
    assert browser.request(method, path, headers={'X-Webhook-Token': WEBHOOK_TOKEN},
                           follow_redirects=False).status_code == 403


@pytest.mark.parametrize('method', ['GET', 'HEAD', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS', 'TRACE', 'CONNECT'])
@pytest.mark.parametrize('path', [WEBHOOK, WEBHOOK + '/correction', WEBHOOK + '/',
    '/api//webhook/home-assistant', '/api/x/../webhook/home-assistant',
    '/api/./webhook/home-assistant', '/api/%77ebhook/home-assistant',
    '/api/webhook%2fhome-assistant', '/api/%252e%252e/webhook/home-assistant',
    '/api\\webhook/home-assistant', '//api/webhook/home-assistant',
    '/API/WEBHOOK/home-assistant', WEBHOOK + ';alias'])
def test_every_public_webhook_target_rejected_even_with_application_session(split, fast_verify, method, path):
    browser, transport, _, _ = split
    assert login(browser).status_code == 200
    transport.target = path  # Preserve targets HTTP clients ordinarily normalize.
    response = browser.request(method, '/', headers={
        'X-Webhook-Token': WEBHOOK_TOKEN, 'Cf-Access-Jwt-Assertion': 'test-only'},
        json={'event': 'entry', 'location': 'office', 'timestamp': '2030-01-01T08:00:00Z'},
        follow_redirects=False)
    assert response.status_code == 403


@pytest.mark.parametrize('ip,other,shared', [
    ('192.0.2.1', '192.0.2.2', False), ('192.0.2.1', '192.0.2.1', True),
    ('2001:db8:1::1', '2001:db8:1::ffff', True),
    ('2001:db8:1::1', '2001:db8:2::1', False),
    ('192.0.2.1', '::ffff:192.0.2.1', True),
])
def test_persistent_thirty_per_ip_nat_ipv6_and_mapped_budgets(split, fast_verify, ip, other, shared):
    browser, _, factory, _ = split
    browser.headers['CF-Connecting-IP'] = ip
    for _ in range(30):
        assert login(browser).status_code == 200
    response = login(browser)
    assert response.status_code == 429 and response.headers['retry-after'] == '900'
    browser.headers['CF-Connecting-IP'] = other
    assert login(browser).status_code == (429 if shared else 200)
    assert len(rows(factory)) == (30 if shared else 31)


def test_https_cutover_does_not_reset_account_or_ip_history(split, fast_verify):
    browser, _, factory, clock = split
    # Existing LAN-era rows use exactly the same keys, database and window.
    seed(factory, clock[0], count=30, username='different-account', failed=False)
    assert login(browser).status_code == 429
    browser.headers['CF-Connecting-IP'] = '198.51.100.1'
    seed(factory, clock[0], count=8, ip='198.51.100.2/32')
    assert login(browser).status_code == 429
    app = split_app(factory, clock)
    with TestClient(TransportPeer(app, BROWSER_PEER), base_url=ORIGIN, headers=HEADERS) as restarted:
        assert login(restarted).status_code == 429
    app.state.session_factory.kw['bind'].dispose()


def test_secure_host_only_cookie_csrf_and_heartbeat(split, fast_verify):
    browser, _, factory, clock = split
    response = login(browser)
    assert response.status_code == 200
    cookie = response.headers['set-cookie'].lower()
    assert 'secure' in cookie and 'httponly' in cookie and 'samesite=lax' in cookie
    assert 'domain=' not in cookie and 'path=/' in cookie
    assert response.headers['strict-transport-security'] == 'max-age=300'
    assert browser.get('/api/auth/me').status_code == 200
    clock[0] += timedelta(hours=2)
    for origin in ['http://work.example.invalid', 'https://alias.example.invalid', 'null']:
        assert browser.post('/api/auth/heartbeat', headers={'Origin': origin}, json={}).status_code == 403
    assert browser.post('/api/auth/heartbeat', json={}).status_code == 200
    with factory() as session:
        stored = session.scalar(select(auth.UserSession))
        assert stored.last_seen_at == clock[0]
        assert stored.expires_at - stored.created_at == timedelta(days=180)
    # Access/transport trust does not replace the WT application session.
    browser.cookies.clear()
    assert browser.get('/api/dashboard').status_code == 401


def test_private_ha_independent_of_access_sessions_and_exhausted_login(split, fast_verify):
    browser, transport, factory, clock = split
    assert login(browser).status_code == 200
    for _ in range(8):
        assert login(browser, password='wrong-test-password').status_code == 401
    assert login(browser).status_code == 429
    with factory() as session:
        session.add(LocationTimezone(location='office', timezone='UTC', updated_at=clock[0]))
        session.commit()
    transport.peer = HA_PEER
    browser.headers.clear()  # No Access, Origin, CSRF marker or browser session.
    browser.cookies.clear()
    raw_ids = []
    for event, hour in [('entry', '08'), ('exit', '16')]:
        payload = {'event': event, 'source': 'home_assistant', 'location': 'office', 'timestamp': f'2030-01-01T{hour}:00:00Z'}
        for headers in [{}, {'X-Webhook-Token': 'wrong'}]:
            assert browser.post(WEBHOOK, headers=headers, json=payload).status_code == 401
        response = browser.post(WEBHOOK, headers={'X-Webhook-Token': WEBHOOK_TOKEN}, json=payload)
        assert response.status_code == 201
        raw_ids.append(response.json()['id'])
    for headers in [{}, {'X-Webhook-Token': 'wrong'}]:
        assert browser.post(WEBHOOK + '/correction', headers=headers,
            json={'raw_event_id': raw_ids[0], 'time': '08:15'}).status_code == 401
    assert browser.post(WEBHOOK + '/correction', headers={'X-Webhook-Token': WEBHOOK_TOKEN},
        json={'raw_event_id': raw_ids[0], 'time': '08:15'}).status_code == 200
    with factory() as session:
        assert session.get(WorkEvent, raw_ids[0]).event_timestamp.hour == 8
        assert session.get(WorkEvent, raw_ids[0]).event_timestamp.minute == 0
        assert session.scalar(select(WorkEventCorrection)).event_timestamp.minute == 15
    for path in ['/', '/api/auth/login', '/api/health', '/api/dashboard', '/api/work-events']:
        assert browser.get(path).status_code == 403
    transport.target = WEBHOOK + '/'
    assert browser.post(WEBHOOK, headers={'X-Webhook-Token': WEBHOOK_TOKEN}, json={}).status_code == 403
    transport.target = None
    transport.peer = BROWSER_PEER
    browser.headers.update(HEADERS)
    clock[0] += timedelta(minutes=15)
    assert login(browser).status_code == 200
    summary = browser.get('/api/work-summary?year=2030&month=1').json()
    assert summary['total_duration_seconds'] == 7 * 3600 + 45 * 60
    pay = browser.get('/api/pay-summary?year=2030&month=1').json()
    assert pay['total_pay'] == '387.50'
    dashboard = browser.get('/api/dashboard?timezone=UTC')
    assert dashboard.status_code == 200
