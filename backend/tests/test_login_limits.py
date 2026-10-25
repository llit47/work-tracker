"""B1–B5/X1: public fixtures, deterministic barriers and a real process crash."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import hashlib
from pathlib import Path
import subprocess
import sys
from threading import Barrier, Event, Lock
import time

from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
import pytest
from sqlalchemy import event, func, insert, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app import auth, login_limits as limits
from app.config import Settings
from app.main import create_app
from app.models import LoginAttempt, LocationTimezone, UserSession, WorkEvent, WorkEventCorrection
from conftest import BROWSER_HEADERS
from test_auth import PASSWORD, WEBHOOK_TOKEN, login, setup


@pytest.fixture
def anyio_backend():
    return 'asyncio'


@pytest.fixture
def fast_verify(monkeypatch):
    calls = []
    def verify(password, encoded):
        calls.append(encoded)
        return password == PASSWORD
    monkeypatch.setattr(auth, 'verify_password', verify)
    return calls


def seed(factory, instant, *, count, username='owner', ip='192.0.2.1/32', failed=True):
    with factory() as session:
        session.execute(insert(LoginAttempt), [dict(
            login_key=limits.login_identity(username)[1], ip_key=ip,
            attempted_at=instant, failed_at=instant if failed else None,
        ) for _ in range(count)])
        session.commit()


def rows(factory):
    with factory() as session:
        return list(session.scalars(select(LoginAttempt).order_by(LoginAttempt.id)))


def restart(factory, clock):
    return create_app(Settings(webhook_token=WEBHOOK_TOKEN,
        database_url=str(factory.kw['bind'].url), session_cookie_secure=False),
        now_provider=lambda: clock[0])


@pytest.mark.parametrize('username', [' Owner ', 'unknown', 'invalid name'])
def test_eight_failures_canonical_unknown_and_success_does_not_reset(setup, fast_verify, username):
    client, factory, clock = setup
    for i in range(8):
        assert login(client, username, 'wrong test password').status_code == 401
        if i == 3 and username.strip().lower() == 'owner':
            assert login(client).status_code == 200
    for _ in range(2):
        blocked = login(client, username)
        assert blocked.status_code == 429
        assert blocked.headers['retry-after'] == '900'
        assert 'set-cookie' not in blocked.headers
    attempts = rows(factory)
    assert sum(row.failed_at is not None for row in attempts) == 8
    assert len(attempts) == (11 if username == ' Owner ' else 10)
    before = len(fast_verify)
    clock[0] += timedelta(seconds=900, microseconds=-1)
    assert login(client, username).headers['retry-after'] == '1'
    clock[0] += timedelta(microseconds=1)
    assert login(client, username).status_code == (200 if username == ' Owner ' else 401)
    assert len(fast_verify) == before + 1


def test_ip_thirty_includes_successes_and_overload_without_extending_deadline(setup, fast_verify):
    client, factory, clock = setup
    for _ in range(28):
        assert login(client).status_code == 200
    # Saturated CPU still records IP attempts, never password failures.
    assert limits.VERIFICATION_SLOTS.acquire(False)
    assert limits.VERIFICATION_SLOTS.acquire(False)
    try:
        for _ in range(2):
            assert login(client).status_code == 503
        clock[0] += timedelta(seconds=1, microseconds=1)
        blocked = login(client)
        assert blocked.status_code == 429 and blocked.headers['retry-after'] == '899'
    finally:
        limits.VERIFICATION_SLOTS.release()
        limits.VERIFICATION_SLOTS.release()
    assert len(rows(factory)) == 30
    assert all(row.failed_at is None for row in rows(factory))
    clock[0] += timedelta(seconds=899, microseconds=-1)
    assert login(client).status_code == 200


def test_retry_after_nine_failures_and_both_limits_after_insert(setup, fast_verify):
    client, factory, clock = setup
    seed(factory, clock[0] - timedelta(seconds=800), count=1)
    seed(factory, clock[0] - timedelta(seconds=700), count=1)
    seed(factory, clock[0] - timedelta(seconds=600), count=7)
    blocked = login(client)
    assert blocked.status_code == 429 and blocked.headers['retry-after'] == '200'
    # This account-blocked insertion reaches 30/IP, whose deadline is later.
    seed(factory, clock[0], count=28, username='other', ip='127.0.0.1/32', failed=False)
    blocked = login(client)
    assert blocked.status_code == 429 and blocked.headers['retry-after'] == '900'
    assert len(rows(factory)) == 39


def test_two_verifications_nine_failures_no_sqlite_lock_and_no_wait_queue(setup, monkeypatch):
    client, factory, clock = setup
    seed(factory, clock[0], count=7)
    barrier = Barrier(3)
    release = Event()
    def slow(*_):
        barrier.wait(timeout=10)
        assert release.wait(10)
        return False
    monkeypatch.setattr(auth, 'verify_password', slow)
    with ThreadPoolExecutor(max_workers=2) as pool:
        tasks = [pool.submit(login, client) for _ in range(2)]
        try:
            barrier.wait(timeout=10)
            assert login(client, 'different').status_code == 503
            # Independent write succeeds while both expensive workers are paused.
            with factory() as session:
                session.execute(text('BEGIN IMMEDIATE'))
                session.add(LocationTimezone(location='test', timezone='UTC', updated_at=clock[0]))
                session.commit()
        finally:
            release.set()
        assert [task.result().status_code for task in tasks] == [401, 401]
    assert sum(row.failed_at is not None for row in rows(factory)) == 9
    assert login(client).status_code == 429
    assert limits.VERIFICATION_SLOTS.acquire(False)
    assert limits.VERIFICATION_SLOTS.acquire(False)
    assert not limits.VERIFICATION_SLOTS.acquire(False)
    limits.VERIFICATION_SLOTS.release()
    limits.VERIFICATION_SLOTS.release()


def test_concurrent_ip_admission_exactly_thirty(setup, fast_verify):
    client, factory, clock = setup
    # No Argon slots: all admissions still race in real SQLite transactions.
    assert limits.VERIFICATION_SLOTS.acquire(False)
    assert limits.VERIFICATION_SLOTS.acquire(False)
    try:
        with ThreadPoolExecutor(max_workers=12) as pool:
            statuses = list(pool.map(lambda n: login(client, f'user{n}').status_code, range(45)))
        assert statuses.count(503) == 30
        assert statuses.count(429) == 15
        assert len(rows(factory)) == 30 and not fast_verify
    finally:
        limits.VERIFICATION_SLOTS.release()
        limits.VERIFICATION_SLOTS.release()


@pytest.mark.anyio
async def test_cancelled_http_keeps_actual_worker_slots_and_result_writes(setup, monkeypatch):
    client, factory, _ = setup
    barrier = Barrier(3)
    release = Event()
    finished = Event()
    lock = Lock()
    completions = []
    original_result = auth.record_result
    def slow(*_):
        barrier.wait(timeout=10)
        assert release.wait(10)
        return False
    def saved(*args, **kwargs):
        original_result(*args, **kwargs)
        with lock:
            completions.append(True)
            if len(completions) == 2:
                finished.set()
    monkeypatch.setattr(auth, 'verify_password', slow)
    monkeypatch.setattr(auth, 'record_result', saved)
    async with AsyncClient(transport=ASGITransport(app=client.app), base_url='http://testserver',
                           headers=BROWSER_HEADERS) as device:
        tasks = [asyncio.create_task(device.post('/api/auth/login', json={
            'username': 'owner', 'password': PASSWORD})) for _ in range(2)]
        try:
            await asyncio.to_thread(barrier.wait, 10)
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            response = await device.post('/api/auth/login', json={'username': 'unknown', 'password': PASSWORD})
            assert response.status_code == 503
        finally:
            release.set()
        assert await asyncio.to_thread(finished.wait, 10)
    # Wait for full commit/slot release, not merely entry into record_result.
    for _ in range(100):
        if sum(row.failed_at is not None for row in rows(factory)) == 2:
            break
        await asyncio.sleep(.01)
    assert sum(row.failed_at is not None for row in rows(factory)) == 2


@pytest.mark.parametrize('host,key', [
    ('192.0.2.1', '192.0.2.1/32'), ('::ffff:192.0.2.1', '192.0.2.1/32'),
    ('2001:db8:1:2::1', '2001:db8:1:2::/64'),
    ('2001:0DB8:0001:0002:ffff::9', '2001:db8:1:2::/64'),
])
def test_ip_keys(host, key):
    assert limits.peer_ip_key(host) == key


@pytest.mark.anyio
@pytest.mark.parametrize('host', [None, 'invalid', 'fe80::1%eth0'])
async def test_missing_or_invalid_peer_fails_closed(setup, fast_verify, host):
    client, factory, _ = setup
    async with AsyncClient(transport=ASGITransport(app=client.app, client=(host, 1) if host else None),
                           base_url='http://testserver', headers=BROWSER_HEADERS) as device:
        response = await device.post('/api/auth/login', json={'username': 'owner', 'password': PASSWORD})
    assert response.status_code == 503 and not rows(factory) and not fast_verify


@pytest.mark.anyio
@pytest.mark.parametrize('first,second', [
    ('192.0.2.1', '::ffff:192.0.2.1'), ('2001:db8::1', '2001:db8::ffff'),
])
async def test_shared_ip_bucket_and_spoofed_headers_ignored(setup, fast_verify, first, second):
    client, factory, clock = setup
    seed(factory, clock[0], count=30, ip=limits.peer_ip_key(first), failed=False)
    async with AsyncClient(transport=ASGITransport(app=client.app, client=(second, 1)),
                           base_url='http://testserver', headers=BROWSER_HEADERS) as device:
        response = await device.post('/api/auth/login', json={'username': 'owner', 'password': PASSWORD},
            headers={'X-Forwarded-For': '198.51.100.1', 'Forwarded': 'for=198.51.100.2',
                     'CF-Connecting-IP': '198.51.100.3'})
    assert response.status_code == 429 and len(rows(factory)) == 30 and not fast_verify


def test_username_key_and_unknown_dummy(setup, monkeypatch):
    client, _, _ = setup
    assert limits.login_identity(' Owner ')[1] == hashlib.sha256(b'login:owner').hexdigest()
    assert limits.login_identity('bad username')[1] == limits.login_identity('żółw')[1]
    calls = []
    monkeypatch.setattr(auth, 'verify_password', lambda _p, encoded: calls.append(encoded) or False)
    assert login(client, 'not-a-user').status_code == 401
    assert calls == [auth._DUMMY_HASH]


@pytest.mark.parametrize('stage', ['admission', 'failure', 'success', 'uncertain_admission', 'uncertain_success'])
def test_sqlite_write_errors_and_uncertain_commits_return_503_no_cookie(setup, fast_verify, monkeypatch, stage):
    client, factory, _ = setup
    original_commit = Session.commit
    commits = []
    def commit(session):
        commits.append(True)
        target = 1 if stage in ['admission', 'uncertain_admission'] else 2
        if len(commits) == target:
            if stage.startswith('uncertain'):
                original_commit(session)
            raise OperationalError('redacted test SQL', {}, Exception('test DB failure'))
        return original_commit(session)
    monkeypatch.setattr(Session, 'commit', commit)
    response = login(client, password='wrong test password' if stage == 'failure' else PASSWORD)
    assert response.status_code == 503 and 'set-cookie' not in response.headers
    assert response.json()['detail'] == limits.UNAVAILABLE
    assert response.headers['cache-control'] == 'no-store'
    assert len(fast_verify) == (0 if 'admission' in stage else 1)


def test_sqlite_busy_is_503_without_verification(setup, fast_verify):
    client, factory, _ = setup
    engine = factory.kw['bind']
    def short_timeout(connection, _cursor, statement, *_):
        if statement == 'BEGIN IMMEDIATE':
            connection.connection.execute('PRAGMA busy_timeout=20')
    event.listen(engine, 'before_cursor_execute', short_timeout)
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql('BEGIN IMMEDIATE')
            assert login(client).status_code == 503
            connection.rollback()
    finally:
        event.remove(engine, 'before_cursor_execute', short_timeout)
    assert not fast_verify and not rows(factory)
    assert login(client).status_code == 200


def test_cleanup_uses_both_dates_startup_and_preserves_future_clock_records(setup, fast_verify):
    client, factory, clock = setup
    seed(factory, clock[0] - limits.WINDOW, count=1, failed=False)
    seed(factory, clock[0] - limits.WINDOW, count=1)
    seed(factory, clock[0] + timedelta(hours=1), count=8)
    with factory() as session:
        session.add(LoginAttempt(login_key=limits.login_identity('recent')[1], ip_key='192.0.2.2/32',
            attempted_at=clock[0] - timedelta(hours=1), failed_at=clock[0]))
        session.commit()
    app = restart(factory, clock)
    with TestClient(app, headers=BROWSER_HEADERS) as device:
        assert len(rows(factory)) == 9
        assert login(device).status_code == 429  # future failures count conservatively
    app.state.session_factory.kw['bind'].dispose()
    assert not fast_verify


def test_table_cap_cleanup_and_deleted_slow_attempt_fail_closed(setup, fast_verify, monkeypatch):
    client, factory, clock = setup
    with factory() as session:
        session.execute(insert(LoginAttempt), [dict(login_key=limits.login_identity(f'user{i}')[1],
            ip_key=f'192.0.{i // 256}.{i % 256}/32', attempted_at=clock[0]) for i in range(4095)])
        session.commit()
    assert login(client).status_code == 200
    with factory() as session:
        pages = session.scalar(text('PRAGMA page_count'))
    for _ in range(5):
        assert login(client).status_code == 503
    with factory() as session:
        assert session.scalar(text('PRAGMA page_count')) == pages
    assert len(rows(factory)) == 4096 and len(fast_verify) == 1
    maximum_id = rows(factory)[-1].id
    clock[0] += limits.WINDOW
    assert login(client).status_code == 200
    assert len(rows(factory)) == 1 and rows(factory)[0].id > maximum_id
    def very_slow(*_):
        clock[0] += limits.WINDOW
        with factory() as session:
            limits.cleanup(session, clock[0])
            session.commit()
            # A new row cannot inherit the lost attempt's ID.
        return True
    monkeypatch.setattr(auth, 'verify_password', very_slow)
    assert login(client).status_code == 503
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(UserSession)) == 2


def test_restart_and_real_crash_leave_at_most_two_unknown_results(setup, tmp_path, fast_verify):
    client, factory, clock = setup
    seed(factory, clock[0], count=7)
    ready = tmp_path / 'ready'
    script = '''
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from threading import Event
from fastapi.testclient import TestClient
from app import auth
from app.config import Settings
from app.main import create_app
app = create_app(Settings(database_url=sys.argv[1], session_cookie_secure=False),
                 now_provider=lambda: datetime.fromisoformat(sys.argv[2]))
async def peer(scope, receive, send):
    await app({**scope, 'client': ('127.0.0.1', 1)}, receive, send)
def verify(*args):
    with open(sys.argv[3], 'a') as marker:
        marker.write('running\\n')
    Event().wait(60)
auth.verify_password = verify
with TestClient(peer) as client:
    def login(_):
        client.post('/api/auth/login', headers={'Origin': 'http://testserver', 'X-Work-Tracker-Request': '1'},
                    json={'username': 'owner', 'password': 'public-test-password'})
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(login, range(2)))
'''
    process = subprocess.Popen([sys.executable, '-c', script, str(factory.kw['bind'].url),
                                clock[0].isoformat(), str(ready)], stderr=subprocess.PIPE)
    try:
        for _ in range(1000):
            if ready.exists() and len(ready.read_text().splitlines()) == 2:
                break
            if process.poll() is not None:
                pytest.fail(process.stderr.read().decode())
            time.sleep(.01)
        else:
            pytest.fail('verification processes did not reach barrier')
    finally:
        process.kill()
        process.wait(timeout=10)
    attempts = rows(factory)
    assert len(attempts) == 9 and sum(row.failed_at is None for row in attempts) == 2
    app = restart(factory, clock)
    with TestClient(app, headers=BROWSER_HEADERS) as device:
        assert login(device, password='wrong test password').status_code == 401
        assert login(device).status_code == 429
    app.state.session_factory.kw['bind'].dispose()


@pytest.mark.parametrize('exhaustion', ['account', 'ip', 'table', 'slots'])
def test_exhaustion_preserves_sessions_and_both_ha_webhooks(setup, fast_verify, exhaustion):
    client, factory, clock = setup
    assert login(client).status_code == 200
    if exhaustion == 'account':
        seed(factory, clock[0], count=8)
    elif exhaustion == 'ip':
        seed(factory, clock[0], count=29, ip='127.0.0.1/32', failed=False)
    elif exhaustion == 'table':
        seed(factory, clock[0], count=4095, failed=False)
    else:
        assert limits.VERIFICATION_SLOTS.acquire(False)
        assert limits.VERIFICATION_SLOTS.acquire(False)
    try:
        assert login(client).status_code == (429 if exhaustion in ['account', 'ip'] else 503)
        assert client.get('/api/auth/me').status_code == 200
        assert client.get('/api/dashboard').status_code == 200
        client.cookies.clear()
        client.headers.pop('Origin')
        client.headers.pop('X-Work-Tracker-Request')
        with factory() as session:
            session.add(LocationTimezone(location='gabinet_zabki', timezone='Europe/Warsaw', updated_at=clock[0]))
            session.commit()
        payload = {'event': 'entry', 'location': 'gabinet_zabki',
                   'timestamp': '2026-10-06T08:00:17+02:00', 'source': 'home_assistant'}
        for path in ['/api/webhook/home-assistant', '/api/webhook/home-assistant/correction']:
            for headers in [{}, {'X-Webhook-Token': 'wrong'}]:
                assert client.post(path, json=payload, headers=headers).status_code == 401
            response = client.post(path, json=payload, headers={'X-Webhook-Token': WEBHOOK_TOKEN})
            assert response.status_code == (201 if path.endswith('home-assistant') else 200)
            if path.endswith('home-assistant'):
                raw_id = response.json()['id']
                payload = {'raw_event_id': raw_id, 'time': '08:05'}
        with factory() as session:
            assert session.get(WorkEvent, raw_id).event_timestamp.isoformat() == '2026-10-06T08:00:17+02:00'
            assert session.scalar(select(WorkEventCorrection)).raw_event_id == raw_id
    finally:
        if exhaustion == 'slots':
            limits.VERIFICATION_SLOTS.release()
            limits.VERIFICATION_SLOTS.release()


@pytest.mark.anyio
async def test_uvicorn_lan_configuration_ignores_proxy_headers_even_trusted_env(setup, fast_verify):
    import shlex
    import uvicorn

    client, factory, clock = setup
    unit = (Path(__file__).resolve().parents[2] / 'deploy/work-tracker.service').read_text()
    flags = shlex.split(next(line for line in unit.splitlines() if line.startswith('ExecStart=')))
    assert flags[flags.index('--workers') + 1] == '1'
    assert '--no-proxy-headers' in flags
    # Exercise Uvicorn's actual ASGI stack under the unit's explicit policy,
    # even if an operator environment would otherwise trust every proxy peer.
    config = uvicorn.Config(client.app, workers=1, proxy_headers=False,
                            forwarded_allow_ips='*', log_config=None)
    config.load()
    seed(factory, clock[0], count=30, ip='127.0.0.1/32', failed=False)
    async with AsyncClient(transport=ASGITransport(app=config.loaded_app, client=('127.0.0.1', 1)),
                           base_url='http://testserver', headers=BROWSER_HEADERS) as device:
        response = await device.post('/api/auth/login', json={'username': 'owner', 'password': PASSWORD},
            headers={'X-Forwarded-For': '198.51.100.2', 'X-Forwarded-Proto': 'https'})
    assert response.status_code == 429 and not fast_verify


@pytest.mark.parametrize('success', [True, False])
def test_expired_attempt_cannot_save_either_result(setup, monkeypatch, success):
    client, factory, clock = setup
    def lost(*_):
        clock[0] += limits.WINDOW
        with factory() as session:
            limits.cleanup(session, clock[0])
            session.commit()
        return success
    monkeypatch.setattr(auth, 'verify_password', lost)
    response = login(client)
    assert response.status_code == 503 and 'set-cookie' not in response.headers
    with factory() as session:
        assert not list(session.scalars(select(UserSession)))


def test_failure_window_starts_at_completion_and_invalid_password_skips_argon(setup, monkeypatch):
    from app import identity
    client, factory, clock = setup
    monkeypatch.setattr(type(identity._password_hasher), 'verify', lambda *_: pytest.fail('invalid password reached Argon2'))
    for _ in range(8):
        assert login(client, password='short').status_code == 401
    assert login(client).status_code == 429
    # Move attempted_at to an older time, keeping completed failures in window.
    with factory() as session:
        session.execute(text('UPDATE login_attempts SET attempted_at = :old'),
                        {'old': (clock[0] - timedelta(hours=1)).isoformat()})
        session.commit()
    clock[0] += limits.WINDOW - timedelta(microseconds=1)
    response = login(client)
    assert response.status_code == 429 and response.headers['retry-after'] == '1'
    clock[0] += timedelta(microseconds=1)
    assert login(client, password='short').status_code == 401


def test_slot_covers_rehash_and_is_released_on_worker_exception(setup, monkeypatch, fast_verify):
    client, factory, _ = setup
    barrier = Barrier(3)
    release = Event()
    monkeypatch.setattr(auth, 'password_needs_rehash', lambda _: True)
    def rehash(_):
        barrier.wait(timeout=10)
        assert release.wait(10)
        raise RuntimeError('public test worker exception')
    monkeypatch.setattr(auth, 'hash_password', rehash)
    with ThreadPoolExecutor(max_workers=2) as pool:
        tasks = [pool.submit(login, client) for _ in range(2)]
        try:
            barrier.wait(timeout=10)
            assert login(client).status_code == 503
            with factory() as session:
                session.execute(text('BEGIN IMMEDIATE'))
                session.rollback()
        finally:
            release.set()
        for task in tasks:
            with pytest.raises(RuntimeError, match='public test worker exception'):
                task.result()
    assert limits.VERIFICATION_SLOTS.acquire(False)
    assert limits.VERIFICATION_SLOTS.acquire(False)
    limits.VERIFICATION_SLOTS.release()
    limits.VERIFICATION_SLOTS.release()
