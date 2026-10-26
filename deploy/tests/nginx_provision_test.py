"""9B1 lifecycle tests. All writes/processes stay in pytest's private directory.

Real OpenSSL and Nginx are required, never silently skipped. systemd, firewall,
backend readiness and RFC1918 ownership are simulated, not deployment evidence.
"""
from datetime import datetime, timedelta, timezone
import importlib.util
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

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('nginx_provision', ROOT / 'deploy/nginx_provision.py')
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)


def openssl(*args):
    subprocess.run(['openssl', *map(str, args)], check=True, capture_output=True)


def free_port():
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        return listener.getsockname()[1]


def snapshot(path):
    return {str(f.relative_to(path)): (f.read_bytes(), f.stat().st_mode, f.stat().st_mtime_ns)
            for f in path.rglob('*') if f.is_file()}


@pytest.fixture(scope='module')
def tls(tmp_path_factory):
    directory = tmp_path_factory.mktemp('provision-tls')
    openssl('req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '30', '-subj', '/CN=Test CA',
            '-addext', 'basicConstraints=critical,CA:TRUE', '-addext', 'keyUsage=critical,keyCertSign,cRLSign',
            '-keyout', directory / 'ca.key', '-out', directory / 'ca.pem')
    openssl('req', '-new', '-newkey', 'rsa:2048', '-nodes', '-subj', '/CN=origin.example.invalid',
            '-keyout', directory / 'origin.key', '-out', directory / 'origin.csr')
    (directory / 'extensions').write_text('subjectAltName=DNS:origin.example.invalid\nbasicConstraints=critical,CA:FALSE\nextendedKeyUsage=serverAuth\n')
    openssl('x509', '-req', '-in', directory / 'origin.csr', '-CA', directory / 'ca.pem', '-CAkey', directory / 'ca.key',
            '-CAcreateserial', '-days', '30', '-extfile', directory / 'extensions', '-out', directory / 'origin.pem')
    return directory


class TestHost(p.Host):
    __test__ = False

    def __init__(self, layout):
        self.layout = layout
        self.process = None
        self.extra_listeners = []
        self.fail = None
        self.calls = []
        self.ready = True

    def local_ip(self, address):
        return address == '192.168.0.170'

    def listeners(self):
        values = super().listeners()
        # Network namespace is not available. Only test listener IPs are mapped.
        values = [('192.168.0.170' if ip == '127.0.0.1' else ip, port, line) for ip, port, line in values]
        return values + self.extra_listeners

    def active(self):
        return self.process is not None and self.process.poll() is None

    def ingress_pid(self):
        return str(self.process.pid)

    def backend_ready(self, config):
        p.require(self.ready, 'Test: backend nie jest gotowy.')

    def tls_probe(self, config, ca):
        super().tls_probe(config | {'lan_ip': '127.0.0.1'}, ca)

    def control(self, action):
        self.calls.append(action)
        if self.fail == action:
            raise p.Refusal('Test: awaria operacji systemd.')
        if action == 'start':
            self.layout.runtime.mkdir(exist_ok=True)
            self.log = (self.layout.boundary / 'nginx-test.log').open('a')
            self.process = subprocess.Popen([str(self.layout.nginx), '-e', 'stderr', '-p', str(self.layout.runtime) + '/',
                '-c', str(self.directory / 'nginx.conf')], stdout=self.log, stderr=self.log)
            for _ in range(100):
                if not self.active():
                    raise p.Refusal('Test: Nginx nie wystartował.')
                try:
                    with socket.create_connection(('127.0.0.1', self.config['browser_port']), timeout=.1):
                        return
                except OSError:
                    time.sleep(.03)
            raise p.Refusal('Test: timeout Nginx.')
        elif action == 'stop' and self.active():
            self.process.terminate()
            self.process.wait(timeout=5)
            self.log.close()


class LoopbackProvisioner(p.Provisioner):
    # Exercise the production renderer/configuration, replacing only network IPs
    # and worker uid: unprivileged tests cannot bind operator LAN/drop uid.
    def render(self, config, directory, runtime=None):
        result = super().render(config | {'lan_ip': '127.0.0.1', 'cloudflared_source_ip': '127.0.0.10',
                                         'ha_source_ip': '127.0.0.11'}, directory, runtime)
        return result.replace(b'user www-data;\n', b'')


@pytest.fixture
def setup(tmp_path, tls):
    layout = p.Layout()
    layout.boundary = tmp_path
    layout.uid = os.getuid()
    layout.state = tmp_path / 'config' / 'ingress'
    layout.backups = tmp_path / 'backups' / 'ingress'
    layout.unit = tmp_path / 'systemd' / p.SERVICE
    layout.runtime = tmp_path / 'runtime'
    for parent in (layout.state.parent, layout.backups.parent, layout.unit.parent):
        parent.mkdir(mode=0o700)
    nginx = os.environ.get('WORK_TRACKER_NGINX_BIN') or shutil.which('nginx')
    assert nginx, 'Real nginx required: set WORK_TRACKER_NGINX_BIN. Missing dependency is not PASS.'
    layout.nginx = tmp_path / 'nginx'
    shutil.copyfile(nginx, layout.nginx)
    layout.nginx.chmod(0o700)
    for name in ('origin.pem', 'origin.key', 'ca.pem'):
        shutil.copyfile(tls / name, tmp_path / name)
        (tmp_path / name).chmod(0o600)
    config = dict(lan_ip='192.168.0.170', browser_port=free_port(), ha_port=free_port(), backend_port=free_port(),
                  public_hostname='work.example.invalid', origin_tls_name='origin.example.invalid',
                  cloudflared_source_ip='192.168.0.149', ha_source_ip='192.168.0.86',
                  certificate=str(tmp_path / 'origin.pem'), private_key=str(tmp_path / 'origin.key'),
                  ca_certificate=str(tmp_path / 'ca.pem'))
    source = tmp_path / 'operator.json'
    p.atomic(source, p.encoded(config))
    host = TestHost(layout)
    tool = LoopbackProvisioner(layout, host)
    yield tool, source, config
    host.fail = None
    host.control('stop')


def prepare(setup):
    tool, source, config = setup
    generation = tool.prepare(source)
    tool.host.directory = tool.layout.state / 'generations' / generation
    tool.host.config = config
    evidence = tool.layout.boundary / 'evidence.json'
    report = tool.layout.boundary / 'report.txt'
    p.atomic(report, b'TEST ONLY: simulated network verification, never operator evidence.\n')
    value = dict(generation=generation, verified_at=datetime.now(timezone.utc).isoformat(),
                 evidence_reference=str(report), **{gate: True for gate in p.GATES})
    p.atomic(evidence, p.encoded(value))
    return tool, evidence, generation


def test_read_only_plan_validation_and_ha_busy_prepare(setup):
    tool, source, config = setup
    with socket.socket() as lan:
        lan.bind(('127.0.0.1', config['ha_port']))
        lan.listen()
        before = snapshot(tool.layout.boundary)
        parsed, _, generation = tool.config(source)
        assert tool.conflicts(parsed) is True
        assert snapshot(tool.layout.boundary) == before
        assert tool.prepare(source) == generation
        assert lan.getsockname()[1] == config['ha_port']
        assert not tool.host.calls and not tool.layout.unit.exists()
        before = snapshot(tool.layout.boundary)
        assert tool.prepare(source) == generation
        assert tool.prepared()[0] == generation
        assert snapshot(tool.layout.boundary) == before


@pytest.mark.parametrize('field,value', [
    ('lan_ip', '0.0.0.0'), ('lan_ip', '2001:db8::1'), ('ha_source_ip', '192.168.0.0/24'),
    ('ha_source_ip', '192.168.0.149'), ('cloudflared_source_ip', '8.8.8.8'),
    ('browser_port', True), ('browser_port', 443), ('ha_port', '8000'),
    ('public_hostname', 'foo;include /tmp/evil'), ('public_hostname', '*.example.com'),
    ('origin_tls_name', 'Origin.example.invalid'), ('private_key', '/tmp/key;evil'),
])
def test_invalid_inputs_fail_before_writes(setup, field, value):
    tool, source, config = setup
    p.atomic(source, p.encoded(config | {field: value}))
    before = snapshot(tool.layout.boundary)
    with pytest.raises((p.Refusal, ValueError)):
        tool.prepare(source)
    assert snapshot(tool.layout.boundary) == before


@pytest.mark.parametrize('target,mode', [('operator.json', 0o644), ('origin.key', 0o640), ('origin.pem', 0o666)])
def test_unsafe_permissions_rejected(setup, target, mode):
    tool, source, _ = setup
    (tool.layout.boundary / target).chmod(mode)
    with pytest.raises(p.Refusal):
        tool.prepare(source)
    assert not tool.layout.state.exists()


def test_symlink_untrusted_owner_and_directory_rejected(setup, monkeypatch):
    tool, source, _ = setup
    key = tool.layout.boundary / 'origin.key'
    key.rename(key.with_suffix('.real'))
    key.symlink_to(key.with_suffix('.real'))
    with pytest.raises(p.Refusal):
        tool.prepare(source)
    key.unlink()
    key.with_suffix('.real').rename(key)
    tool.layout.uid = os.getuid() + 1
    with pytest.raises(p.Refusal):
        tool.prepare(source)
    tool.layout.uid = os.getuid()
    tool.layout.boundary.chmod(0o777)
    with pytest.raises(p.Refusal):
        tool.prepare(source)
    tool.layout.boundary.chmod(0o700)


@pytest.mark.parametrize('kind', ['wrong_san', 'wrong_key', 'untrusted_ca', 'expired', 'near_expiry', 'not_yet_valid', 'cn_only'])
def test_bad_certificates(setup, tls, kind):
    tool, source, config = setup
    cert = tool.layout.boundary / 'origin.pem'
    if kind == 'wrong_san':
        p.atomic(source, p.encoded(config | {'origin_tls_name': 'wrong.example.invalid'}))
    elif kind == 'wrong_key':
        p.atomic(tool.layout.boundary / 'origin.key', (tls / 'ca.key').read_bytes())
    elif kind == 'untrusted_ca':
        openssl('req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '30', '-subj', '/CN=Other CA',
                '-keyout', tool.layout.boundary / 'other.key', '-out', tool.layout.boundary / 'ca.pem')
    else:
        # openssl ca allows fixed validity bounds, including future/expired dates.
        directory = tool.layout.boundary
        (directory / 'index').touch()
        (directory / 'serial').write_text('01\n')
        (directory / 'ca.cnf').write_text(f'''[ca]
default_ca=local
[local]
database={directory}/index
serial={directory}/serial
new_certs_dir={directory}
certificate={tls}/ca.pem
private_key={tls}/ca.key
default_md=sha256
policy=policy
x509_extensions=ext
[policy]
commonName=supplied
[ext]
basicConstraints=CA:FALSE
''' + ('' if kind == 'cn_only' else 'subjectAltName=DNS:origin.example.invalid\n'))
        start, end = {'expired': ('20000101000000Z', '20010101000000Z'),
                      'near_expiry': ('20200101000000Z', (datetime.now(timezone.utc) + timedelta(days=1)).strftime('%Y%m%d%H%M%SZ')),
                      'not_yet_valid': ('20900101000000Z', '20910101000000Z'),
                      'cn_only': ('20200101000000Z', '20910101000000Z')}[kind]
        openssl('ca', '-batch', '-config', directory / 'ca.cnf', '-in', tls / 'origin.csr',
                '-startdate', start, '-enddate', end, '-out', cert, '-notext')
    with pytest.raises(p.Refusal):
        tool.prepare(source)
    assert not tool.layout.state.exists()


@pytest.mark.parametrize('address', ['0.0.0.0', '::', '192.168.0.170'])
def test_browser_conflict_including_ipv6(setup, address):
    tool, source, config = setup
    tool.host.extra_listeners = [(address, config['browser_port'], 'another process')]
    with pytest.raises(p.Refusal):
        tool.prepare(source)
    assert not tool.layout.state.exists()


@pytest.mark.parametrize('gate', sorted(p.GATES))
def test_no_activation_without_each_network_gate(setup, gate):
    tool, evidence, _ = prepare(setup)
    p.atomic(evidence, p.encoded(p.read_json(evidence) | {gate: False}))
    with pytest.raises(p.Refusal):
        tool.activate(evidence)
    assert not tool.layout.unit.exists() and tool.host.calls == []


@pytest.mark.parametrize('override', [{'generation': '0' * 64},
    {'verified_at': (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()},
    {'verified_at': '2026-10-26T00:00:00'}, {'verified_at': '2090-01-01T00:00:00+00:00'}])
def test_stale_wrong_or_naive_evidence(setup, override):
    tool, evidence, _ = prepare(setup)
    p.atomic(evidence, p.encoded(p.read_json(evidence) | override))
    with pytest.raises(p.Refusal):
        tool.activate(evidence)
    assert not tool.layout.unit.exists()


def test_activation_requires_free_ha_port_and_ready_backend(setup):
    tool, evidence, _ = prepare(setup)
    tool.host.extra_listeners = [('::', tool.host.config['ha_port'], 'existing uvicorn')]
    with pytest.raises(p.Refusal):
        tool.activate(evidence)
    tool.host.extra_listeners = []
    tool.host.ready = False
    with pytest.raises(p.Refusal):
        tool.activate(evidence)
    assert not tool.layout.unit.exists() and not tool.host.calls


def test_real_activation_tls_acls_idempotence_backup_and_rollback(setup):
    tool, evidence, generation = prepare(setup)
    tool.activate(evidence)
    assert tool.host.active()
    assert p.read_json(tool.layout.state / 'active.json')['phase'] == 'active'
    for source, expected in [('127.0.0.10', 502), ('127.0.0.12', 403)]:
        with socket.create_connection(('127.0.0.1', tool.host.config['browser_port']),
                                      source_address=(source, 0), timeout=2) as raw:
            context = ssl.create_default_context(cafile=str(tool.host.directory / 'ca.pem'))
            with context.wrap_socket(raw, server_hostname='origin.example.invalid') as connection:
                connection.sendall(b'GET /api/health HTTP/1.1\r\nHost: work.example.invalid\r\nX-Forwarded-Proto: https\r\nConnection: close\r\n\r\n')
                assert f' {expected} '.encode() in connection.recv(2048)
    calls = tool.host.calls[:]
    tool.activate(evidence)
    assert tool.host.calls == calls  # no restart/reload
    tool.rollback()
    tool.rollback()
    assert not tool.host.active() and not tool.layout.unit.exists()
    assert not (tool.layout.state / 'active.json').exists()
    backups = list(tool.layout.backups.glob('activate-*.json'))
    assert len(backups) == 1 and p.read_json(backups[0])['rolled_back'] is True
    assert tool.prepared()[0] == generation


@pytest.mark.parametrize('action', ['daemon-reload', 'start', 'postcheck'])
def test_mid_activation_failure_is_recoverable(setup, action, monkeypatch):
    tool, evidence, _ = prepare(setup)
    if action == 'postcheck':
        monkeypatch.setattr(tool, 'postcheck', lambda *args: p.require(False, 'Test: failed postcheck'))
    else:
        tool.host.fail = action
    with pytest.raises(p.Refusal):
        tool.activate(evidence)
    tool.host.fail = None
    tool.rollback()  # retries even when initial rollback's daemon-reload failed
    assert not tool.host.active() and not tool.layout.unit.exists()
    assert not (tool.layout.state / 'active.json').exists()


def test_interrupted_activation_recovered_without_source_config_or_tls(setup):
    tool, evidence, generation = prepare(setup)
    unit = (tool.host.directory / 'service').read_bytes()
    backup = tool.backup('activate', {'generation': generation, 'unit_before': None})
    p.atomic(tool.layout.state / 'active.json', p.encoded(dict(generation=generation, phase='activating',
        unit_sha=p.digest(unit), backup=str(backup))))
    p.atomic(tool.layout.unit, unit, 0o644)  # simulates SIGKILL after unit installation
    (tool.host.directory / 'origin.key').unlink()
    setup[1].unlink()
    tool.rollback()
    assert not tool.layout.unit.exists()


def test_prepare_failure_and_interrupted_publication(setup, monkeypatch):
    tool, source, _ = setup
    real_execute = p.execute
    def fail_nginx(args, **kwargs):
        if '-t' in args:
            raise p.Refusal('Test: nginx -t failure')
        return real_execute(args, **kwargs)
    monkeypatch.setattr(p, 'execute', fail_nginx)
    with pytest.raises(p.Refusal):
        tool.prepare(source)
    assert not list((tool.layout.state / 'generations').iterdir())
    assert not (tool.layout.state / 'prepared.json').exists() and not tool.layout.unit.exists()
    monkeypatch.setattr(p, 'execute', real_execute)
    generation = tool.prepare(source)
    (tool.layout.state / 'prepared.json').unlink()  # crash between publish and pointer
    assert tool.prepare(source) == generation


def test_prepare_new_generation_preserves_prior_and_can_return_to_it(setup):
    tool, source, config = setup
    first = tool.prepare(source)
    p.atomic(source, p.encoded(config | {'ha_port': free_port()}))
    second = tool.prepare(source)
    assert first != second
    p.atomic(source, p.encoded(config))
    assert tool.prepare(source) == first
    assert len(list(tool.layout.backups.glob('prepare-*'))) == 2


def test_refuse_adopting_or_overwriting_unknown_unit(setup):
    tool, evidence, _ = prepare(setup)
    p.atomic(tool.layout.unit, b'operator managed unit\n', 0o644)
    with pytest.raises(p.Refusal):
        tool.activate(evidence)
    with pytest.raises(p.Refusal):
        tool.rollback()
    assert tool.layout.unit.read_bytes() == b'operator managed unit\n'


def test_tampering_and_failed_stop_leave_journal_for_operator(setup):
    tool, evidence, _ = prepare(setup)
    tool.activate(evidence)
    tool.host.fail = 'stop'
    with pytest.raises(p.Refusal):
        tool.rollback()
    assert tool.host.active() and (tool.layout.state / 'active.json').exists()
    tool.host.fail = None
    original = tool.layout.unit.read_bytes()
    p.atomic(tool.layout.unit, b'changed unit\n', 0o644)
    with pytest.raises(p.Refusal):
        tool.rollback()
    assert tool.host.active()
    p.atomic(tool.layout.unit, original, 0o644)
    tool.rollback()


def test_generation_permissions_and_tampering(setup):
    tool, _, _ = prepare(setup)
    directory = tool.host.directory
    assert directory.stat().st_mode & 0o777 == 0o700
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in directory.iterdir())
    (directory / 'nginx.conf').write_text('tampered')
    with pytest.raises(p.Refusal):
        tool.prepared()


def test_default_lan_update_has_no_ingress_lifecycle_hook():
    for name in ('install.sh', 'update.sh', 'deploy/update_rollback.sh'):
        text = (ROOT / name).read_text()
        assert 'nginx_provision' not in text and p.SERVICE not in text
    assert 'INGRESS_PROFILE|optional|plain|lan|' in (ROOT / 'deploy/config.manifest').read_text()


def test_backend_readiness_uses_actual_process_environment_and_socket(setup, monkeypatch):
    tool, _, config = setup
    host = p.Host()
    port = config['backend_port']
    environment = os.environ | dict(INGRESS_PROFILE='split', SESSION_COOKIE_SECURE='true',
        BROWSER_ALLOWED_ORIGINS='https://work.example.invalid', CORS_ORIGINS='', BROWSER_HSTS_SECONDS='0',
        DATABASE_URL='sqlite:///:memory:', WEBHOOK_TOKEN='test-only-at-least-32-characters-token')
    command = [sys.executable, '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', str(port),
               '--workers', '1', '--no-proxy-headers']
    real_execute = p.execute
    with (tool.layout.boundary / 'backend.log').open('w') as log:
        process = subprocess.Popen(command, cwd=ROOT / 'backend', env=environment, stdout=log, stderr=log)
        try:
            for _ in range(100):
                assert process.poll() is None, 'Test backend exited'
                try:
                    with socket.create_connection(('127.0.0.1', port), timeout=.1):
                        break
                except OSError:
                    time.sleep(.05)
            monkeypatch.setattr(p, 'execute', lambda args, **kwargs:
                str(process.pid).encode() if args[:2] == ['systemctl', 'show'] else real_execute(args, **kwargs))
            host.backend_ready(config)
            with pytest.raises(p.Refusal):
                host.backend_ready(config | {'public_hostname': 'wrong.example.invalid'})
            with pytest.raises(p.Refusal):
                host.backend_ready(config | {'backend_port': port + 1})
        finally:
            process.terminate()
            process.wait(timeout=5)


def test_production_renderer_has_only_specific_ipv4_and_preserves_proxy_contract(setup):
    tool, _, config = setup
    rendered = p.Provisioner(tool.layout, tool.host).render(config, tool.layout.boundary)
    assert f"listen 192.168.0.170:{config['ha_port']} default_server;".encode() in rendered
    assert f"listen 192.168.0.170:{config['browser_port']} ssl default_server;".encode() in rendered
    assert rendered.count(b'listen ') == 2
    assert b'proxy_bind 127.0.0.2;' in rendered and b'proxy_bind 127.0.0.3;' in rendered
    assert b'proxy_set_header X-Webhook-Token $http_x_webhook_token;' in rendered
    assert b'include ' not in rendered and b'real_ip_header' not in rendered


def test_dangling_lock_symlink_does_not_create_target(setup):
    tool, source, _ = setup
    tool.layout.mkdir(tool.layout.state)
    target = tool.layout.boundary / 'must-not-create'
    (tool.layout.state / 'lock').symlink_to(target)
    with pytest.raises(p.Refusal):
        tool.prepare(source)
    assert not target.exists()


def test_activation_waits_for_type_simple_listener_readiness(setup, monkeypatch):
    tool, evidence, _ = prepare(setup)
    real_postcheck = tool.postcheck
    attempts = []
    def delayed(*args):
        attempts.append(True)
        if len(attempts) == 1:
            raise p.Refusal('Test: sockets not yet ready')
        real_postcheck(*args)
    monkeypatch.setattr(tool, 'postcheck', delayed)
    tool.activate(evidence)
    assert len(attempts) == 2
    tool.rollback()


def test_encrypted_key_is_rejected_without_prompt_or_writes(setup, tls):
    tool, source, _ = setup
    openssl('pkey', '-in', tls / 'origin.key', '-out', tool.layout.boundary / 'origin.key',
            '-aes256', '-passout', 'pass:test-only-key-password')
    with pytest.raises(p.Refusal, match='niezaszyfrowany'):
        tool.prepare(source)
    assert not tool.layout.state.exists()


def test_duplicate_json_operator_values_rejected(setup):
    tool, source, config = setup
    duplicate = p.encoded(config).decode().replace('"ha_port":', '"ha_port": 8000, "ha_port":')
    p.atomic(source, duplicate.encode())
    with pytest.raises(p.Refusal, match='Powtórzony'):
        tool.prepare(source)
    assert not tool.layout.state.exists()
