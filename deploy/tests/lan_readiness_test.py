"""Ordinary LAN readiness: real HTTP parser/deadline, controlled systemd observations."""
from contextlib import nullcontext
import io

import pytest
from host_validation_test import host, process, p
from split_update_test import updater, cutover, setup, tls, event, seed_identity, database_rows, later_identity_writes, ha
import update_safety as safety_module


@pytest.fixture
def lan_probe(host, monkeypatch):
    clock, calls = [0.0], []
    monkeypatch.setattr(p.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(p.time, 'sleep', lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    def active(args, **kwargs):
        assert args == ['systemctl', 'is-active', '--quiet', 'work-tracker.service']
        assert 0 < kwargs['timeout'] <= 5
        calls.append('active')
        return b''
    monkeypatch.setattr(p, 'execute', active)
    monkeypatch.setattr(host, 'local_ip', lambda address: address == '10.123.45.10')
    return host, clock, calls


@pytest.mark.parametrize('failure', [ConnectionRefusedError, ConnectionResetError, BrokenPipeError, ConnectionError, TimeoutError])
def test_lan_transport_failure_then_http_200(lan_probe, monkeypatch, failure):
    host, clock, calls = lan_probe
    responses = [failure('starting'), 200]
    def request(*args):
        assert args == ('127.0.0.1', 18000, '127.0.0.1', '/api/health')
        calls.append('http')
        result = responses.pop(0)
        if isinstance(result, Exception): raise result
        return result
    monkeypatch.setattr(host, 'request', request)
    config = host.wait_lan_health(dict(lan_ip='0.0.0.0', ha_port=18000), 5)
    assert config['lan_ip'] == '127.0.0.1' and clock[0] == .1
    assert calls == ['active', 'http', 'active', 'http', 'active']
    assert host._probe_deadline is None


@pytest.mark.parametrize('failure', [ConnectionRefusedError, ConnectionResetError, 503])
def test_lan_permanent_failure_times_out_without_readiness(lan_probe, monkeypatch, failure):
    host, clock, calls = lan_probe
    def request(*args):
        if isinstance(failure, type): raise failure('unavailable')
        return failure
    monkeypatch.setattr(host, 'request', request)
    with pytest.raises(p.Refusal, match='Timeout gotowości HTTP LAN'):
        host.wait_lan_health(dict(lan_ip='127.0.0.1', ha_port=18000), 5)
    assert 5 <= clock[0] <= 5.1 and len(calls) > 1 and host._probe_deadline is None


@pytest.mark.parametrize('after_http', [False, True])
def test_inactive_service_is_not_accepted_or_retried(lan_probe, monkeypatch, after_http):
    host, clock, _ = lan_probe
    calls = []
    def active(*args, **kwargs):
        calls.append('active')
        if not after_http or len(calls) > 1: raise p.Refusal('inactive service')
    monkeypatch.setattr(p, 'execute', active)
    monkeypatch.setattr(host, 'request', lambda *args: 200 if after_http else pytest.fail('inactive service contacted'))
    with pytest.raises(p.Refusal, match='inactive service'):
        host.wait_lan_health(dict(lan_ip='127.0.0.1', ha_port=18000), 5)
    assert clock[0] == 0 and len(calls) == (2 if after_http else 1)


@pytest.mark.parametrize('address', ['work.example.invalid', '203.0.113.10', '10.123.45.99'])
def test_lan_probe_never_resolves_or_contacts_nonlocal_destination(lan_probe, monkeypatch, address):
    host, clock, calls = lan_probe
    monkeypatch.setattr(host, 'request', lambda *args: pytest.fail('unsafe destination contacted'))
    with pytest.raises((p.Refusal, ValueError)):
        host.wait_lan_health(dict(lan_ip=address, ha_port=18000), 5)
    assert clock[0] == 0 and calls == []


@pytest.mark.parametrize('address,expected', [('10.123.45.10', '10.123.45.10'), ('::', '::1')])
def test_configured_private_bind_or_ipv6_wildcard_uses_local_probe(lan_probe, monkeypatch, address, expected):
    host, _, _ = lan_probe
    def request(destination, port, source, target):
        assert (destination, source, port, target) == (expected, expected, 18000, '/api/health')
        return 200
    monkeypatch.setattr(host, 'request', request)
    assert host.wait_lan_health(dict(lan_ip=address, ha_port=18000), 5)['lan_ip'] == expected


@pytest.mark.parametrize('response', [b'HTTP/1.1 20', b'bad status\r\n'])
def test_actual_http_parser_eof_retries_but_complete_malformed_status_refuses(lan_probe, monkeypatch, response):
    host, clock, _ = lan_probe
    replies, calls = [response, b'HTTP/1.1 200 OK\r\n'], []
    def connection(*args, **kwargs):
        calls.append(True)
        data = io.BytesIO(replies.pop(0))
        from types import SimpleNamespace
        return nullcontext(SimpleNamespace(settimeout=lambda _: None, sendall=lambda _: None, recv=data.read))
    monkeypatch.setattr(p.socket, 'create_connection', connection)
    if response.endswith(b'\r\n'):
        with pytest.raises(p.Refusal, match='linia statusu'):
            host.wait_lan_health(dict(lan_ip='127.0.0.1', ha_port=18000), 5)
        assert calls == [True] and clock[0] == 0
    else:
        host.wait_lan_health(dict(lan_ip='127.0.0.1', ha_port=18000), 5)
        assert calls == [True, True] and clock[0] == .1


@pytest.mark.parametrize('failure', [ConnectionResetError, BrokenPipeError])
def test_actual_http_request_transport_error_then_200(lan_probe, monkeypatch, failure):
    host, clock, _ = lan_probe
    from types import SimpleNamespace
    calls = []
    def connection(*args, **kwargs):
        calls.append(True)
        data = io.BytesIO(b'HTTP/1.1 200 OK\r\n')
        def send(_):
            if len(calls) == 1: raise failure('startup transport termination')
        return nullcontext(SimpleNamespace(settimeout=lambda _: None, sendall=send, recv=data.read))
    monkeypatch.setattr(p.socket, 'create_connection', connection)
    host.wait_lan_health(dict(lan_ip='127.0.0.1', ha_port=18000), 5)
    assert calls == [True, True] and clock[0] == .1


@pytest.mark.parametrize('foreign', [False, True])
def test_lan_command_runs_one_strict_final_check_and_refuses_foreign_listener(lan_probe, tmp_path, monkeypatch, foreign):
    import lan_readiness as command
    host, clock, _ = lan_probe
    env = dict(APP_HOST='0.0.0.0', APP_PORT='18000', INGRESS_PROFILE='lan',
               DATABASE_URL='sqlite:////var/lib/work-tracker/work_tracker.db', WEBHOOK_TOKEN='public-test-token')
    path = tmp_path / 'app.env'
    path.write_text(''.join(f'{key}={value}\n' for key, value in env.items()))
    process(host, argv=['uvicorn', 'app.main:app', '--host', '0.0.0.0', '--port', '18000', '--workers', '1', '--no-proxy-headers'], env=env)
    monkeypatch.setattr(p, 'execute', lambda args, **kwargs: b'1234' if args[1] == 'show' else b'')
    monkeypatch.setattr(host, 'listeners', lambda: [('0.0.0.0', 18000, 'pid=99,' if foreign else 'pid=1234,')])
    monkeypatch.setattr(host, 'request', lambda *args: 200)
    final = []
    def strict(config):
        final.append(True)
        p.Host.lan_ready(host, config)
    monkeypatch.setattr(host, 'lan_ready', strict)
    monkeypatch.setattr(command, 'Host', lambda: host)
    monkeypatch.setattr(command, 'Path', lambda _: path)
    monkeypatch.setattr(command.sys, 'argv', ['lan_readiness.py', '--readiness-timeout', '5'])
    assert command.main() == (1 if foreign else 0)
    assert final == [True] and clock[0] == 0


@pytest.mark.parametrize('failure', [None, 'never-ready'])
def test_actual_ordinary_lan_update_waits_before_commit_and_preserves_sqlite(updater, failure):
    u = updater
    first = event(u)
    seed_identity(u)
    assert ha(u, '/correction', {'raw_event_id': first, 'time': '07:45'})[0] == 200
    u.tool.recover(rollback=True)  # Exercise ordinary LAN with the same real updater fixture.
    u.state['backend_delay'] = .3
    before = database_rows(u.database)
    expected = []
    def after_backup():
        event(u, 'exit', '2030-01-01T16:00:00Z')
        later_identity_writes(u)
        expected.append(database_rows(u.database))
    result = u.run(failure=failure, after_backup=after_backup)
    assert database_rows(u.database) == expected[0]
    assert expected[0]['work_events'][0] == before['work_events'][0] and len(expected[0]['work_events']) == 2
    assert expected[0]['work_event_corrections'] == before['work_event_corrections']
    operations = [args[0] for kind, args in u.state['calls'] if kind == 'safety']
    if failure is None:
        assert result.returncode == 0, result.stdout.decode() + result.stderr.decode()
        assert u.state['restart_returned'] < u.state['backend_started'] and operations[-1] == 'commit'
        assert not u.safety.layout.state.exists()
    else:
        assert result.returncode == 1, result.stdout.decode() + result.stderr.decode()
        assert p.time.monotonic() - u.state['restart_returned'] >= 5
        assert 'commit' not in operations and u.safety.read()['phase'] == 'manual'
        assert u.backend['process'] is None and b'REQUIRES MANUAL RECOVERY' in result.stderr
        with pytest.raises(safety_module.Refusal): u.safety.allow_start()
