"""Real nginx -> Uvicorn TCP/TLS tests; no Cloudflare connection or tunnel.

Run with backend/.venv/bin/pytest deploy/tests/ingress_security_test.py -q
and WORK_TRACKER_NGINX_BIN pointing to nginx. Missing nginx is an ERROR.
The simulated connector supplies headers here; Access proof is a separate test.
"""
from contextlib import contextmanager
from datetime import date, datetime, timezone
from decimal import Decimal
import http.client
import json
import os
from pathlib import Path
import shutil
import socket
import ssl
import subprocess
import sys
import time

import pytest
from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))
from app.database import build_session_factory
from app.identity import create_user
from app.models import Base, LocationTimezone, PayRate, WorkEvent, WorkEventCorrection

HOST = 'work.example.invalid'
TLS_NAME = 'origin.example.invalid'
TOKEN = 'public-ingress-test-only-token-32-characters'
PASSWORD = 'public-ingress-test-only-password'
WEBHOOK = '/api/webhook/home-assistant'


def port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


@contextmanager
def process(command, directory, env, logfile):
    with logfile.open('w') as log:
        child = subprocess.Popen(command, cwd=directory, env=env, stdout=log, stderr=log)
        try:
            yield child
        finally:
            child.terminate()
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)


def ready(child, target, logfile):
    for _ in range(100):
        if child.poll() is not None:
            pytest.fail(logfile.read_text())
        try:
            with socket.create_connection(('127.0.0.1', target), timeout=.1):
                return
        except OSError:
            time.sleep(.05)
    pytest.fail('Listener failed to start: ' + logfile.read_text())


@pytest.fixture(scope='module')
def ingress(tmp_path_factory):
    nginx = os.environ.get('WORK_TRACKER_NGINX_BIN') or shutil.which('nginx')
    if not nginx:
        pytest.fail('Real nginx required. Set WORK_TRACKER_NGINX_BIN; this check must not silently skip.')
    directory = tmp_path_factory.mktemp('real-ingress')
    browser_port, ha_port, backend_port = port(), port(), port()
    cert, key = directory / 'origin.pem', directory / 'origin.key'
    subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
        '-subj', f'/CN={TLS_NAME}', '-addext', f'subjectAltName=DNS:{TLS_NAME}',
        '-keyout', str(key), '-out', str(cert)], check=True, capture_output=True)
    config = (ROOT / 'deploy/ingress/nginx.conf.example').read_text()
    replacements = {
        'WORK_TRACKER_LAN_IP': '127.0.0.1', 'BROWSER_TLS_PORT': str(browser_port),
        'PUBLIC_HOSTNAME': HOST, 'ORIGIN_CERT_PATH': str(cert), 'ORIGIN_KEY_PATH': str(key),
        'CLOUDFLARED_SOURCE_IP': '127.0.0.10', 'ORIGIN_TLS_NAME': TLS_NAME,
        'HA_PORT': str(ha_port), 'HA_SOURCE_IP': '127.0.0.11', 'BACKEND_PORT': str(backend_port),
    }
    for name, value in replacements.items():
        config = config.replace(f'<{name}>', value)
    assert '<' not in '\n'.join(line for line in config.splitlines() if not line.lstrip().startswith('#'))
    conf = directory / 'nginx.conf'
    conf.write_text(f'pid {directory}/nginx.pid;\nerror_log stderr;\n'
        'daemon off;\nworker_processes 1;\nevents {}\nhttp {\n'
        f'client_body_temp_path {directory}/body;\nproxy_temp_path {directory}/proxy;\n'
        f'fastcgi_temp_path {directory}/fastcgi;\nuwsgi_temp_path {directory}/uwsgi;\nscgi_temp_path {directory}/scgi;\n'
        + config + '\n}\n')
    env = os.environ | dict(WEBHOOK_TOKEN=TOKEN, DATABASE_URL=f'sqlite:///{directory}/db.sqlite',
        BROWSER_ALLOWED_ORIGINS=f'https://{HOST}', CORS_ORIGINS='', INGRESS_PROFILE='split',
        SESSION_COOKIE_SECURE='true', BROWSER_HSTS_SECONDS='300')
    factory = build_session_factory(env['DATABASE_URL'])
    Base.metadata.create_all(factory.kw['bind'])
    now = datetime.now(timezone.utc)
    with factory() as session:
        create_user(session, 'owner', PASSWORD)
        session.add(LocationTimezone(location='office', timezone='UTC', updated_at=now))
        session.add(PayRate(effective_from=date(1970, 1, 1), hourly_rate=Decimal('50'),
                           currency='PLN', created_at=now))
        session.commit()
    subprocess.run([nginx, '-t', '-p', str(directory), '-c', str(conf)], check=True, capture_output=True)
    backend_log, nginx_log = directory / 'backend.log', directory / 'nginx.log'
    with process([sys.executable, '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1',
                  '--port', str(backend_port), '--workers', '1', '--no-proxy-headers'],
                 ROOT / 'backend', env, backend_log) as backend:
        ready(backend, backend_port, backend_log)
        with process([nginx, '-p', str(directory), '-c', str(conf)], directory, env, nginx_log) as proxy:
            ready(proxy, browser_port, nginx_log)
            yield dict(browser=browser_port, ha=ha_port, backend=backend_port, cert=cert, factory=factory)
    factory.kw['bind'].dispose()


def request(ingress, path, *, via='browser', source=None, method='GET', headers=None, payload=None,
            hostname=HOST, sni=TLS_NAME, scheme='https'):
    source = source or ('127.0.0.11' if via == 'ha' else '127.0.0.10')
    sock = socket.create_connection(('127.0.0.1', ingress[via]), timeout=5,
                                    source_address=(source, 0))
    try:
        if via == 'browser':
            sock = ssl.create_default_context(cafile=str(ingress['cert'])).wrap_socket(sock, server_hostname=sni)
        body = json.dumps(payload).encode() if payload is not None else b''
        values = [('Host', hostname), ('Connection', 'close'), ('Content-Length', str(len(body)))]
        if via == 'browser':
            values += [('X-Forwarded-Proto', scheme), ('CF-Connecting-IP', '192.0.2.100')]
        if payload is not None:
            values += [('Content-Type', 'application/json')]
        # A list permits actual duplicate headers on the wire.
        if headers:
            names = {name.lower() for name, _ in headers}
            values = [(name, value) for name, value in values if name.lower() not in names] + headers
        wire = f'{method} {path} HTTP/1.1\r\n' + ''.join(f'{name}: {value}\r\n' for name, value in values)
        sock.sendall(wire.encode() + b'\r\n' + body)
        response = http.client.HTTPResponse(sock, method=method)
        response.begin()
        return response.status, dict((k.lower(), v) for k, v in response.getheaders()), response.read()
    finally:
        sock.close()


def login(ingress, ip='192.0.2.100', password=PASSWORD, extra=None):
    return request(ingress, '/api/auth/login', method='POST', headers=[
        ('Origin', f'https://{HOST}'), ('X-Work-Tracker-Request', '1'), ('CF-Connecting-IP', ip),
        *(extra or [])], payload={'username': 'owner', 'password': password})


def test_real_proxy_source_acl_host_tls_and_direct_backend(ingress):
    assert request(ingress, '/api/health')[0] == 200
    assert request(ingress, '/api/health', source='127.0.0.12')[0] == 403
    assert request(ingress, '/api/health', hostname='alias.example.invalid')[0] == 403
    assert request(ingress, '/api/health', scheme='http')[0] == 403
    assert request(ingress, '/api/auth/login', via='ha', method='POST', payload={})[0] == 403
    assert request(ingress, WEBHOOK, via='ha', source='127.0.0.12', method='POST', payload={})[0] == 403
    assert request(ingress, '/api/health', via='backend', headers=[
        ('CF-Connecting-IP', '192.0.2.1'), ('X-Forwarded-For', '127.0.0.2'),
        ('X-Forwarded-Proto', 'https')])[0] == 403
    with pytest.raises(ssl.SSLCertVerificationError):
        request(ingress, '/api/health', sni='alias.example.invalid')


def test_real_proxy_webhook_variants_and_private_ha_without_cloudflare(ingress):
    status, headers, _ = login(ingress)
    assert status == 200
    cookie = headers['set-cookie'].split(';')[0]
    assert '; Secure' in headers['set-cookie'] and 'Domain=' not in headers['set-cookie']
    assert headers['strict-transport-security'] == 'max-age=300'
    variants = [WEBHOOK, WEBHOOK + '/correction', WEBHOOK + '/', '/api//webhook/home-assistant',
        '//api/webhook/home-assistant', '/api/./webhook/home-assistant',
        '/x/../api/webhook/home-assistant', '/%61pi/webhook/home-assistant',
        '/api/webhook%2fhome-assistant', '/api/%2e%2e/api/webhook/home-assistant',
        '/api/%252e%252e/webhook/home-assistant', '/api\\webhook/home-assistant',
        WEBHOOK + ';alias']
    for path in variants:
        for method in ['GET', 'HEAD', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS', 'TRACE', 'CONNECT']:
            status, _, _ = request(ingress, path, method=method, payload={}, headers=[
                ('Cookie', cookie), ('X-Webhook-Token', TOKEN),
                ('Cf-Access-Jwt-Assertion', 'not-used-as-authentication')])
            assert status in {400, 403, 405}, (method, path, status)
        if path not in {WEBHOOK, WEBHOOK + '/correction'}:
            assert request(ingress, path, via='ha', method='POST', payload={},
                           headers=[('X-Webhook-Token', TOKEN)])[0] in {400, 403}
    for path in ['/', '/api/health', '/api/dashboard', '/api/auth/login', '/api/openapi.json']:
        assert request(ingress, path, via='ha')[0] == 403
    for path in [WEBHOOK, WEBHOOK + '/correction']:
        for method in ['GET', 'HEAD', 'PUT', 'PATCH', 'DELETE', 'OPTIONS', 'TRACE', 'CONNECT']:
            assert request(ingress, path, via='ha', method=method,
                           headers=[('X-Webhook-Token', TOKEN)])[0] in {400, 403, 405}
    ids = []
    for event, hour in [('entry', '08'), ('exit', '16')]:
        payload = dict(event=event, source='home_assistant', location='office', timestamp=f'2030-01-01T{hour}:00:00Z')
        for headers in [[], [('X-Webhook-Token', 'wrong')]]:
            assert request(ingress, WEBHOOK, via='ha', method='POST', payload=payload, headers=headers)[0] == 401
        status, _, body = request(ingress, WEBHOOK, via='ha', method='POST', payload=payload,
                                  headers=[('X-Webhook-Token', TOKEN)])
        assert status == 201
        ids.append(json.loads(body)['id'])
    payload = dict(raw_event_id=ids[0], time='08:15')
    for headers in [[], [('X-Webhook-Token', 'wrong')]]:
        assert request(ingress, WEBHOOK + '/correction', via='ha', method='POST', payload=payload, headers=headers)[0] == 401
    assert request(ingress, WEBHOOK + '/correction', via='ha', method='POST', payload=payload,
                   headers=[('X-Webhook-Token', TOKEN)])[0] == 200
    with ingress['factory']() as session:
        assert session.get(WorkEvent, ids[0]).event_timestamp.minute == 0
        assert session.scalar(select(WorkEventCorrection)).event_timestamp.minute == 15


def test_real_proxy_duplicate_missing_invalid_ip_and_https_heartbeat(ingress):
    for ip in ['', 'invalid', '192.0.2.1, 192.0.2.2', 'fe80::1%eth0']:
        assert login(ingress, ip)[0] == 503
    assert login(ingress, extra=[('CF-Connecting-IP', '192.0.2.1'),
                                 ('CF-Connecting-IP', '192.0.2.2')])[0] == 503
    status, headers, _ = login(ingress, '198.51.100.20', extra=[('X-Forwarded-For', '192.0.2.99'),
            ('Forwarded', 'for=192.0.2.99;proto=http')])
    assert status == 200
    cookie = headers['set-cookie'].split(';')[0]
    for origin, expected in [(f'http://{HOST}', 403), (f'https://{HOST}', 200)]:
        assert request(ingress, '/api/auth/heartbeat', method='POST', payload={}, headers=[
            ('Cookie', cookie), ('Origin', origin), ('X-Work-Tracker-Request', '1')])[0] == expected
    from app.models import LoginAttempt
    with ingress['factory']() as session:
        assert session.scalar(select(LoginAttempt).where(LoginAttempt.ip_key == '198.51.100.20/32'))
        assert not session.scalar(select(LoginAttempt).where(LoginAttempt.ip_key == '192.0.2.99/32'))


@pytest.mark.parametrize('first,second,shared', [
    ('198.51.100.1', '198.51.100.2', False), ('198.51.100.3', '198.51.100.3', True),
    ('2001:db8:1::1', '2001:db8:1::2', True),
    ('2001:db8:2::1', '2001:db8:3::1', False),
    ('198.51.100.4', '::ffff:198.51.100.4', True),
])
def test_real_proxy_ip_budgets_and_private_ha_survives_throttling(ingress, first, second, shared):
    for _ in range(30):
        assert login(ingress, first)[0] == 200
    status, headers, _ = login(ingress, first)
    assert status == 429 and 1 <= int(headers['retry-after']) <= 900
    assert login(ingress, second)[0] == (429 if shared else 200)
    assert request(ingress, WEBHOOK, via='ha', method='POST', headers=[('X-Webhook-Token', TOKEN)],
        payload=dict(event='entry', source='home_assistant', location='ha_independent', timestamp='2030-01-02T08:00:00Z'))[0] == 201
