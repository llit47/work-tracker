from datetime import timedelta

import pytest
from sqlalchemy import select
from starlette.responses import JSONResponse
from starlette.routing import Route, Router

from app import auth
from app.models import ApplicationSetting, PayRate, UserSession, WorkEvent, WorkEventCorrection
from test_auth import WEBHOOK_TOKEN, login, setup


READ_PATHS = [
    '/api/openapi.json', '/api/docs', '/api/redoc',
    '/api/dashboard', '/api/work-events?year=2026&month=10',
    '/api/work-summary?year=2026&month=10', '/api/pay-rates',
    '/api/pay-summary?year=2026&month=10', '/api/application-settings',
    '/api/corrections', '/api/export/monthly.csv?year=2026&month=10',
    '/api/export/monthly.pdf?year=2026&month=10',
]
WRITE_PATHS = [
    ('POST', '/api/pay-rates'), ('POST', '/api/manual-events'),
    ('PUT', '/api/application-settings'), ('PUT', '/api/work-events/1/timestamp-correction'),
    ('PUT', '/api/work-events/1/ignore'), ('DELETE', '/api/corrections/1'),
]


@pytest.mark.parametrize('method,path', [('GET', path) for path in READ_PATHS] + WRITE_PATHS)
def test_anonymous_reads_and_writes_fail_before_payload_validation(setup, method, path):
    client, factory, _ = setup
    response = client.request(method, path, content='{', headers={'Content-Type': 'application/json'})
    assert response.status_code == 401
    assert response.json() == {'detail': auth.SESSION_ERROR}
    assert response.headers['cache-control'] == 'no-store'
    assert 'location' not in response.headers and 'set-cookie' not in response.headers
    with factory() as session:
        assert list(session.scalars(select(WorkEvent))) == []
        assert list(session.scalars(select(WorkEventCorrection))) == []
        assert session.get(ApplicationSetting, 1) is None
        assert len(list(session.scalars(select(PayRate)))) == 1


def test_every_registered_domain_api_is_default_denied(setup):
    client, _, _ = setup
    for route in client.app.routes:
        if not getattr(route, 'path', '').startswith('/api/'):
            continue
        for method in route.methods:
            if (method, route.path) in auth.SESSION_INDEPENDENT_ENDPOINTS:
                continue
            path = route.path.replace('{raw_event_id}', '1').replace('{correction_id}', '1')
            assert client.request(method, path).status_code == 401, (method, path)


def test_authenticated_reads_mutations_logout_and_relogin(setup):
    client, _, _ = setup
    assert login(client).status_code == 200
    for path in READ_PATHS:
        assert client.get(path).status_code == 200, path
    assert client.put('/api/application-settings', json={
        'application_title': 'Praca', 'locations': [],
    }).status_code == 200
    assert client.post('/api/pay-rates', json={'effective_from': '2026-11-01',
                       'hourly_rate': '60.00', 'currency': 'PLN'}).status_code == 201
    raw = client.post('/api/webhook/home-assistant', headers={'X-Webhook-Token': WEBHOOK_TOKEN},
                      json={'event': 'entry', 'location': 'gabinet_zabki',
                            'timestamp': '2026-10-06T08:00:00+02:00', 'source': 'home_assistant'})
    raw_id = raw.json()['id']
    corrected = client.put(f'/api/work-events/{raw_id}/timestamp-correction',
                           json={'timestamp': '2026-10-06T08:06:00+02:00'})
    assert corrected.status_code == 200
    assert client.delete(f"/api/corrections/{corrected.json()['id']}").status_code == 204
    assert client.put(f'/api/work-events/{raw_id}/ignore').status_code == 200
    assert client.post('/api/manual-events', json={'event': 'exit', 'location': 'gabinet_zabki',
                       'timestamp': '2026-10-06T16:00:00+02:00'}).status_code == 201
    assert client.post('/api/auth/logout').status_code == 204
    assert client.get('/api/dashboard').status_code == 401
    assert login(client).status_code == 200
    assert client.get('/api/dashboard').status_code == 200


def test_exact_exceptions_and_future_routes_and_mounts(setup):
    client, _, _ = setup
    assert auth.SESSION_INDEPENDENT_ENDPOINTS == frozenset({
        ('POST', '/api/auth/login'), ('GET', '/api/auth/me'), ('POST', '/api/auth/logout'),
        ('GET', '/api/health'), ('POST', '/api/webhook/home-assistant'),
        ('POST', '/api/webhook/home-assistant/correction'),
    })
    # Production may have a catch-all static mount; insert test routes before it.
    static_mounts = [route for route in client.app.routes if getattr(route, 'name', '') == 'frontend']
    for route in static_mounts:
        client.app.routes.remove(route)
    paths = ['/api/future', '/api/auth/future', '/api/webhook/home-assistant/future']
    for path in paths:
        client.app.add_api_route(path, lambda: {'protected': True}, methods=['GET', 'POST'])
    client.app.mount('/api/mounted', Router(routes=[
        Route('/data', lambda request: JSONResponse({'protected': True})),
    ]))
    client.app.routes.extend(static_mounts)
    for path in paths + ['/api/mounted/data', '/api/health/extra', '/api/health/',
                         '/api/auth/login/', '/api/unknown', '/api']:
        assert client.get(path).status_code == 401
        assert client.post(path).status_code == 401
    assert client.post('/api/health').status_code == 401
    assert client.get('/api/auth/login').status_code == 401
    assert client.get('/api/webhook/home-assistant').status_code == 401
    assert client.options('/api/dashboard').status_code == 401  # Not a CORS preflight.
    assert client.get('/api/health').json() == {'status': 'ok'}
    assert client.get('/api/auth/me').status_code == 401
    assert client.post('/api/auth/logout').status_code == 204
    assert login(client).status_code == 200
    for path in paths + ['/api/mounted/data']:
        assert client.get(path).status_code == 200


def test_cors_wraps_guard_and_preflights_are_session_independent(setup):
    client, _, _ = setup
    origin = 'http://localhost:5173'
    for method, path in [('GET', '/api/dashboard'), ('PUT', '/api/application-settings'),
                         ('POST', '/api/manual-events'), ('DELETE', '/api/corrections/1')]:
        response = client.options(path, headers={
            'Origin': origin, 'Access-Control-Request-Method': method,
            'Access-Control-Request-Headers': 'Content-Type',
        })
        assert response.status_code == 200
        assert response.headers['access-control-allow-origin'] == origin
        assert response.headers['access-control-allow-credentials'] == 'true'
    denied = client.get('/api/dashboard', headers={'Origin': origin})
    assert denied.status_code == 401
    assert denied.headers['access-control-allow-origin'] == origin
    assert denied.headers['access-control-allow-credentials'] == 'true'
    assert login(client).status_code == 200
    allowed = client.get('/api/dashboard', headers={'Origin': origin})
    assert allowed.status_code == 200
    assert allowed.headers['access-control-allow-origin'] == origin


def test_domain_and_ha_requests_validate_without_renewing_idle(setup):
    client, factory, clock = setup
    assert login(client).status_code == 200
    initial = clock[0]
    clock[0] += timedelta(hours=1)
    assert client.post('/api/webhook/home-assistant', headers={'X-Webhook-Token': WEBHOOK_TOKEN},
                       json={'event': 'entry', 'location': 'gabinet_zabki',
                             'timestamp': '2026-10-06T08:00:00+02:00',
                             'source': 'home_assistant'}).status_code == 201
    with factory() as session:
        assert session.scalar(select(UserSession.last_seen_at)) == initial
    assert client.get('/api/pay-rates').status_code == 200
    assert client.put('/api/application-settings', json={
        'application_title': 'Praca', 'locations': [],
    }).status_code == 200
    with factory() as session:
        assert session.scalar(select(UserSession.last_seen_at)) == initial
    assert client.get('/api/auth/me').status_code == 200
    with factory() as session:
        assert session.scalar(select(UserSession.last_seen_at)) == clock[0]
        assert session.scalar(select(UserSession.expires_at)) == initial + auth.ABSOLUTE_LIFETIME


def test_repeated_domain_polling_cannot_postpone_exact_idle_expiry(setup):
    client, factory, clock = setup
    assert login(client).status_code == 200
    initial = clock[0]
    token = client.cookies.get(auth.COOKIE_NAME)
    for elapsed in [timedelta(hours=1), timedelta(hours=2), timedelta(days=7),
                    timedelta(days=29), auth.IDLE_TIMEOUT - timedelta(microseconds=1),
                    auth.IDLE_TIMEOUT, auth.IDLE_TIMEOUT + timedelta(microseconds=1)]:
        clock[0] = initial + elapsed
        expected = 200 if elapsed < auth.IDLE_TIMEOUT else 401
        # Dashboard, monthly refreshes, settings, exports and future API traffic
        # all pass through the same validation-only guard.
        for path in READ_PATHS:
            assert client.get(path).status_code == expected, (elapsed, path)
        with factory() as session:
            stored = session.scalar(select(UserSession))
            assert stored.last_seen_at == initial
            assert stored.expires_at == initial + auth.ABSOLUTE_LIFETIME
        assert client.cookies.get(auth.COOKIE_NAME) == token
    # An expired heartbeat must not revive the session either.
    assert client.get('/api/auth/me').status_code == 401
    with factory() as session:
        assert session.scalar(select(UserSession.last_seen_at)) == initial


def test_deliberate_heartbeat_renews_idle_without_extending_absolute_expiry(setup):
    client, factory, clock = setup
    assert login(client).status_code == 200
    initial = clock[0]
    heartbeat_at = initial + timedelta(days=29)
    clock[0] = heartbeat_at
    assert client.get('/api/dashboard').status_code == 200
    with factory() as session:
        assert session.scalar(select(UserSession.last_seen_at)) == initial
    assert client.get('/api/auth/me').status_code == 200
    # The heartbeat keeps the session valid beyond its original idle deadline.
    for instant in [initial + auth.IDLE_TIMEOUT,
                    heartbeat_at + auth.IDLE_TIMEOUT - timedelta(microseconds=1)]:
        clock[0] = instant
        assert client.get('/api/dashboard').status_code == 200
        with factory() as session:
            stored = session.scalar(select(UserSession))
            assert stored.last_seen_at == heartbeat_at
            assert stored.expires_at == initial + auth.ABSOLUTE_LIFETIME
    clock[0] = heartbeat_at + auth.IDLE_TIMEOUT
    assert client.get('/api/dashboard').status_code == 401
    assert client.get('/api/auth/me').status_code == 401
    with factory() as session:
        assert session.scalar(select(UserSession.last_seen_at)) == heartbeat_at
