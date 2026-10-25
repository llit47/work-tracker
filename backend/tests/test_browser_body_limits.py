"""Bounded buffering and atomic rejection at the browser request boundary."""

from datetime import timedelta
import json

from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient
import pytest
from sqlalchemy import select

from app.browser_security import (
    BROWSER_MUTATION_BODY_LIMIT,
    LOGIN_BODY_LIMIT,
    BrowserMutationMiddleware,
)
from app.models import Base, LocationTimezone, UserSession, WorkEvent, WorkEventCorrection
from conftest import BROWSER_HEADERS
from test_auth import WEBHOOK_TOKEN, login, setup


@pytest.fixture
def anyio_backend():
    return 'asyncio'


def database_snapshot(factory):
    with factory() as session:
        return {table.name: list(session.execute(select(table)))
                for table in Base.metadata.sorted_tables}


def json_chunks(payload, size):
    prefix = json.dumps(payload).encode('utf-8')
    yield prefix
    remaining = size - len(prefix)
    while remaining > 0:
        length = min(65536, remaining)
        yield b' ' * length
        remaining -= length


@pytest.mark.parametrize('method,path,limit', [
    ('POST', '/api/auth/login', LOGIN_BODY_LIMIT),
    ('POST', '/api/manual-events', BROWSER_MUTATION_BODY_LIMIT),
    ('PUT', '/api/application-settings', BROWSER_MUTATION_BODY_LIMIT),
    ('PATCH', '/api/future', BROWSER_MUTATION_BODY_LIMIT),
    ('DELETE', '/api/corrections/1', BROWSER_MUTATION_BODY_LIMIT),
    ('POST', '/api/auth/heartbeat', BROWSER_MUTATION_BODY_LIMIT),
    ('POST', '/api/auth/logout', BROWSER_MUTATION_BODY_LIMIT),
    ('CUSTOM', '/api/future', BROWSER_MUTATION_BODY_LIMIT),
])
@pytest.mark.parametrize('declared_length', [None, '0', '1', '999999999'])
@pytest.mark.anyio
async def test_endless_stream_is_rejected_without_unbounded_accumulation(method, path, limit, declared_length):
    # The source never ends. Regressing to unbounded accumulation fails after
    # one excess chunk rather than hanging the test or exhausting process RAM.
    received = 0
    chunk = b' ' * 4096
    messages = []

    async def receive():
        nonlocal received
        received += len(chunk)
        assert received <= limit + len(chunk), 'middleware kept consuming an oversized stream'
        return {'type': 'http.request', 'body': chunk, 'more_body': True}

    async def send(message):
        messages.append(message)

    async def downstream(*_args):
        pytest.fail('oversized request reached a handler')

    headers = {**BROWSER_HEADERS, 'Content-Type': 'application/json'}
    if declared_length is not None:
        headers['Content-Length'] = declared_length
    scope = {'type': 'http', 'method': method, 'path': path,
             'headers': [(key.lower().encode(), value.encode()) for key, value in headers.items()]}
    await BrowserMutationMiddleware(downstream, ['http://testserver'])(scope, receive, send)
    assert messages[0]['status'] == 413
    assert received == limit + len(chunk)


@pytest.mark.parametrize('method,path,payload,success', [
    ('POST', '/api/manual-events', {'event': 'entry', 'location': 'gabinet_zabki',
                                  'timestamp': '2026-10-06T08:00:00+02:00'}, 201),
    ('PUT', '/api/application-settings', {'application_title': 'Praca', 'locations': []}, 200),
    ('PATCH', '/api/future', {'value': True}, 200),
])
@pytest.mark.parametrize('size', [BROWSER_MUTATION_BODY_LIMIT - 1, BROWSER_MUTATION_BODY_LIMIT,
                               BROWSER_MUTATION_BODY_LIMIT + 1, 1024 * BROWSER_MUTATION_BODY_LIMIT])
@pytest.mark.parametrize('declared_length', [None, '0', '1', '999999999'])
@pytest.mark.anyio
async def test_streamed_browser_body_boundaries_and_no_partial_mutations(
    setup, method, path, payload, success, size, declared_length,
):
    client, factory, _ = setup
    calls = []
    def future_patch():
        calls.append(True)
        return {'ok': True}
    client.app.router.routes.insert(0, APIRoute('/api/future', future_patch, methods=['PATCH']))
    assert login(client).status_code == 200
    before = database_snapshot(factory)
    received = 0

    async def stream():
        nonlocal received
        for chunk in json_chunks(payload, size):
            received += len(chunk)
            yield chunk

    headers = {**BROWSER_HEADERS, 'Content-Type': 'application/json'}
    if declared_length is not None:
        headers['Content-Length'] = declared_length
    async with AsyncClient(transport=ASGITransport(app=client.app), base_url='http://testserver',
                           cookies=dict(client.cookies)) as device:
        response = await device.request(method, path, content=stream(), headers=headers)
    if size > BROWSER_MUTATION_BODY_LIMIT:
        assert response.status_code == 413
        assert received <= BROWSER_MUTATION_BODY_LIMIT + 65536
        assert database_snapshot(factory) == before
        assert not calls
        assert 'set-cookie' not in response.headers
    else:
        assert response.status_code == success
        assert received == size
        if method == 'PATCH':
            assert calls == [True]
        else:
            assert database_snapshot(factory) != before


@pytest.mark.parametrize('method,path,content_type,encoding,body,expected', [
    ('POST', '/api/manual-events', 'application/json', None, b'{} trailing', 422),
    ('PUT', '/api/application-settings', 'application/json', None, b'{"title":', 422),
    ('POST', '/api/auth/heartbeat', 'application/json', None, b'{', 422),
    ('POST', '/api/auth/logout', 'application/json', None, b'{', 422),
    ('PUT', '/api/application-settings', 'text/plain', None, b'{}', 415),
    ('POST', '/api/manual-events', 'application/x-www-form-urlencoded', None, b'{}', 415),
    ('POST', '/api/auth/heartbeat', 'application/json', 'gzip', b'{}', 415),
])
def test_rejected_browser_bodies_leave_all_data_unchanged(
    setup, method, path, content_type, encoding, body, expected,
):
    client, factory, clock = setup
    assert login(client).status_code == 200
    clock[0] += timedelta(hours=1)
    before = database_snapshot(factory)
    headers = {'Content-Type': content_type}
    if encoding is not None:
        headers['Content-Encoding'] = encoding
    response = client.request(method, path, content=body, headers=headers)
    assert response.status_code == expected
    assert database_snapshot(factory) == before
    assert 'set-cookie' not in response.headers


@pytest.mark.anyio
async def test_empty_chunked_heartbeat_and_logout_still_work(setup):
    client, factory, clock = setup
    assert login(client).status_code == 200
    clock[0] += timedelta(hours=1)

    async def empty_stream():
        yield b''
        yield b''

    async with AsyncClient(transport=ASGITransport(app=client.app), base_url='http://testserver',
                           cookies=dict(client.cookies), headers=BROWSER_HEADERS) as device:
        assert (await device.post('/api/auth/heartbeat', content=empty_stream())).status_code == 200
        with factory() as session:
            assert session.scalar(select(UserSession.last_seen_at)) == clock[0]
        assert (await device.post('/api/auth/logout', content=empty_stream())).status_code == 204
        with factory() as session:
            assert session.scalar(select(UserSession.revoked_at)) == clock[0]


@pytest.mark.anyio
async def test_both_ha_webhooks_bypass_browser_size_limit_and_keep_token_auth(setup):
    client, factory, clock = setup
    with factory() as session:
        session.add(LocationTimezone(location='gabinet_zabki', timezone='Europe/Warsaw', updated_at=clock[0]))
        session.commit()
    payload = {'event': 'entry', 'location': 'gabinet_zabki',
               'timestamp': '2026-10-06T08:00:17+02:00', 'source': 'home_assistant'}
    async with AsyncClient(transport=ASGITransport(app=client.app), base_url='http://testserver') as device:
        for path in ['/api/webhook/home-assistant', '/api/webhook/home-assistant/correction']:
            for token, expected in [(None, 401), ('wrong', 401), (WEBHOOK_TOKEN, 201 if path.endswith('home-assistant') else 200)]:
                before = database_snapshot(factory)
                async def stream():
                    for chunk in json_chunks(payload, BROWSER_MUTATION_BODY_LIMIT + 1):
                        yield chunk
                headers = {'Content-Type': 'application/json', 'Origin': 'null', 'X-Work-Tracker-Request': 'wrong'}
                if token is not None:
                    headers['X-Webhook-Token'] = token
                response = await device.post(path, content=stream(), headers=headers)
                assert response.status_code == expected
                if expected == 401:
                    assert database_snapshot(factory) == before
            if path.endswith('home-assistant'):
                raw_id = response.json()['id']
                payload = {'raw_event_id': raw_id, 'time': '08:05'}
    with factory() as session:
        assert session.get(WorkEvent, raw_id).event_timestamp.isoformat() == '2026-10-06T08:00:17+02:00'
        assert session.scalar(select(WorkEventCorrection)).raw_event_id == raw_id
