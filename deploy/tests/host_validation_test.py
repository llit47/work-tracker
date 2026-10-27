"""Execute Host validators against recorded systemd/kernel observations.

These test the validation code, not a production systemd daemon/root credentials.
HTTP fragmentation test uses a real loopback TCP server. Real boot/UIDs are 9C.
"""
import grp
import os
from pathlib import Path
import pwd
import socket
import threading
from types import SimpleNamespace

import pytest
from nginx_provision_test import p


@pytest.fixture
def host(tmp_path):
    result = p.Host()
    result.proc = tmp_path / 'proc'
    result.proc.mkdir()
    return result


def process(host, *, pid='1234', argv=None, env=None, uid='0', children=''):
    directory = host.proc / pid
    directory.mkdir(exist_ok=True)
    (directory / 'cmdline').write_bytes(('\0'.join(argv or []) + '\0').encode())
    (directory / 'environ').write_bytes(('\0'.join(f'{key}={value}' for key, value in (env or {}).items()) + '\0').encode())
    (directory / 'status').write_text('Uid:\t' + '\t'.join([uid] * 4) + '\n')
    task = directory / 'task' / pid
    task.mkdir(parents=True, exist_ok=True)
    (task / 'children').write_text(children)


@pytest.fixture
def systemd_layout(tmp_path, monkeypatch):
    layout = p.Layout()
    layout.uid = os.getuid()
    layout.boundary = tmp_path
    layout.app_unit = tmp_path / 'work-tracker.service'
    layout.env = tmp_path / 'app.env'
    layout.dropins = tmp_path / 'work-tracker.service.d'
    p.atomic(layout.app_unit, (p.ROOT / 'deploy/work-tracker.service').read_bytes(), 0o644)
    p.atomic(layout.env, b'fixture', 0o640)
    properties = dict(FragmentPath=str(layout.app_unit), DropInPaths='', Type='simple', User='work-tracker', Group='work-tracker',
                      WorkingDirectory='/opt/work-tracker/backend', EnvironmentFiles='/etc/work-tracker/work-tracker.env (ignore_errors=no)',
                      Environment='', PassEnvironment='', UnsetEnvironment='', RootDirectory='', RootImage='', DynamicUser='no',
                      NoNewPrivileges='yes', PrivateTmp='yes', ProtectSystem='strict', ProtectHome='yes',
                      ReadWritePaths='/var/lib/work-tracker', ExecStartPre='', ExecStartPost='', ExecCondition='', ExecStop='', ExecStopPost='')
    argv = '/opt/work-tracker/backend/.venv/bin/uvicorn app.main:app --host ${APP_HOST} --port ${APP_PORT} --workers 1 --no-proxy-headers'
    properties['ExecStart'] = '{ path=/opt/work-tracker/backend/.venv/bin/uvicorn ; argv[]=' + argv + ' ; ignore_errors=no ; pid=1234 ; status=0/0 }'
    def execute(args, **kwargs):
        assert args[0] == 'systemctl'
        if args[1] == 'is-enabled':
            return properties.get('enabled', 'enabled').encode()
        return properties[args[args.index('-p') + 1]].encode()
    monkeypatch.setattr(p, 'execute', execute)
    return layout, properties


@pytest.mark.parametrize('resources', [False, True])
def test_app_layout_supported_command_and_resource_overrides(host, systemd_layout, resources):
    layout, props = systemd_layout
    if resources:
        layout.dropins.mkdir(mode=0o755)
        path = layout.dropins / 'resources.conf'
        p.atomic(path, b'[Service]\nNice=2\nCPUWeight=90\n', 0o644)
        props['DropInPaths'] = str(path)
        props['ExecStart'] = props['ExecStart'].replace('pid=1234', 'pid=5678')
    host.app_layout_ready(layout)


@pytest.mark.parametrize('change', ['workers', 'proxy', 'extra', 'binary', 'ignore', 'multiple', 'empty'])
def test_app_layout_refuses_unsupported_effective_execstart(host, systemd_layout, change):
    layout, props = systemd_layout
    value = props['ExecStart']
    changes = {'workers': value.replace('--workers 1', '--workers 2'),
               'proxy': value.replace('--no-proxy-headers', '--proxy-headers'),
               'extra': value.replace(' --host', ' --timeout-keep-alive 90 --host'),
               'binary': value.replace('/opt/work-tracker/backend/.venv/bin/uvicorn', '/usr/bin/custom'),
               'ignore': value.replace('ignore_errors=no', 'ignore_errors=yes'), 'multiple': value + value, 'empty': ''}
    props['ExecStart'] = changes[change]
    with pytest.raises(p.Refusal):
        host.app_layout_ready(layout)


@pytest.mark.parametrize('key,value', [('User', 'root'), ('Group', 'root'), ('Type', 'forking'),
    ('EnvironmentFiles', '/other.env (ignore_errors=no)'), ('Environment', 'WEBHOOK_TOKEN=hidden-fixture'),
    ('UnsetEnvironment', 'INGRESS_PROFILE'), ('WorkingDirectory', '/tmp'), ('NoNewPrivileges', 'no'),
    ('RootDirectory', '/srv'), ('ExecStartPre', '{ path=/usr/bin/custom ; }'), ('enabled', 'enabled-runtime'),
    ('DropInPaths', '/run/systemd/system/work-tracker.service.d/custom.conf')])
def test_app_layout_refuses_unsafe_properties(host, systemd_layout, key, value):
    layout, props = systemd_layout
    props[key] = value
    with pytest.raises(p.Refusal):
        host.app_layout_ready(layout)


def test_app_layout_rejects_disk_edit_not_yet_loaded(host, systemd_layout):
    layout, _ = systemd_layout
    data = layout.app_unit.read_bytes().replace(b'--workers 1', b'--workers 2')
    p.atomic(layout.app_unit, data, 0o644)
    with pytest.raises(p.Refusal):
        host.app_layout_ready(layout)


def test_app_layout_rejects_unloaded_exec_override_in_dropin(host, systemd_layout):
    layout, _ = systemd_layout
    layout.dropins.mkdir(mode=0o755)
    p.atomic(layout.dropins / 'custom.conf', b'[Service]\nExecStart=custom\n', 0o644)
    with pytest.raises(p.Refusal):
        host.app_layout_ready(layout)


def test_app_permissions_owner_group_modes_and_symlink(host, systemd_layout, monkeypatch):
    layout, _ = systemd_layout
    monkeypatch.setattr(grp, 'getgrnam', lambda _: SimpleNamespace(gr_gid=os.getgid()))
    host.app_permissions(layout)
    for mode in (0o600, 0o644, 0o660):
        layout.env.chmod(mode)
        with pytest.raises(p.Refusal):
            host.app_permissions(layout)
    layout.env.chmod(0o640)
    monkeypatch.setattr(grp, 'getgrnam', lambda _: SimpleNamespace(gr_gid=os.getgid() + 1))
    with pytest.raises(p.Refusal):
        host.app_permissions(layout)
    layout.uid += 1
    with pytest.raises(p.Refusal):
        host.app_permissions(layout)
    layout.uid -= 1
    layout.env.unlink()
    layout.env.symlink_to(layout.app_unit)
    with pytest.raises(p.Refusal):
        host.app_permissions(layout)


@pytest.mark.parametrize('changed', [None, 'WEBHOOK_TOKEN', 'DATABASE_URL', 'pid'])
def test_app_config_preserved_compares_real_parser_values_to_proc(host, monkeypatch, changed):
    expected = {'WEBHOOK_TOKEN': 'fixture-token-only', 'DATABASE_URL': 'sqlite:///fixture'}
    environment = expected.copy()
    if changed in environment:
        environment[changed] = 'changed'
    process(host, env=environment)
    monkeypatch.setattr(p, 'execute', lambda *args, **kwargs: b'0' if changed == 'pid' else b'1234')
    if changed:
        with pytest.raises(p.Refusal):
            host.app_config_preserved(expected)
    else:
        host.app_config_preserved(expected)


@pytest.mark.parametrize('invalid', [None, 'master', 'worker', 'children', 'status'])
def test_ingress_permissions_uid_tree_validation(host, monkeypatch, invalid):
    process(host, uid='1000' if invalid == 'master' else '0', children='22 23' if invalid == 'children' else '22')
    process(host, pid='22', uid='99' if invalid == 'worker' else '33')
    monkeypatch.setattr(pwd, 'getpwnam', lambda _: SimpleNamespace(pw_uid=33))
    if invalid == 'status':
        (host.proc / '22/status').write_text('Uid: invalid\n')
    if invalid:
        with pytest.raises(p.Refusal):
            host.ingress_permissions('1234')
    else:
        host.ingress_permissions('1234')


def test_bootstrap_listeners_reads_both_kernel_tables_without_iproute2(host):
    net = host.proc / 'net'
    net.mkdir()
    (net / 'tcp').write_text('header\n0: 00000000:0050 00000000:0000 0A\n1: 00000000:01BB 00000000:0000 01\n')
    (net / 'tcp6').write_text('header\n0: ' + '0' * 32 + ':01BB 00000000:0000 0A\n')
    assert host.bootstrap_listeners() == {('tcp', '00000000', 80), ('tcp6', '0' * 32, 443)}
    (net / 'tcp6').unlink()
    assert host.bootstrap_listeners() == {('tcp', '00000000', 80)}
    (net / 'tcp').write_text('header\nmalformed\n')
    with pytest.raises(p.Refusal):
        host.bootstrap_listeners()


@pytest.mark.parametrize('rc,state,expected', [(0, 'loaded', True), (0, 'masked', True), (4, 'not-found', False),
                                              (1, '', None), (0, 'unknown', None), (5, 'not-found', None)])
def test_generic_known_requires_explicit_observed_state(host, monkeypatch, rc, state, expected):
    monkeypatch.setattr(p.subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(returncode=rc, stdout=state.encode()))
    if expected is None:
        with pytest.raises(p.Refusal):
            host.generic_known()
    else:
        assert host.generic_known() is expected


@pytest.fixture
def lan(host, monkeypatch):
    config = {'lan_ip': '10.123.45.10', 'ha_port': 18000, 'backend_port': 18001, 'public_hostname': 'work.example.invalid'}
    argv = ['python', 'uvicorn', 'app.main:app', '--host', '0.0.0.0', '--port', '18000', '--workers', '1', '--no-proxy-headers']
    process(host, argv=argv, env={'INGRESS_PROFILE': 'lan'})
    monkeypatch.setattr(p, 'execute', lambda *args, **kwargs: b'1234')
    monkeypatch.setattr(host, 'listeners', lambda: [('0.0.0.0', 18000, 'users pid=1234,')])
    monkeypatch.setattr(host, 'request', lambda *args, **kwargs: 200)
    return config, argv


@pytest.mark.parametrize('invalid', [None, 'port', 'workers', 'proxy', 'profile', 'socket', 'pid', 'health', 'truncated'])
def test_lan_ready_process_bind_profile_and_health(host, lan, monkeypatch, invalid):
    config, argv = lan
    if invalid in ('port', 'workers', 'proxy', 'truncated'):
        if invalid == 'port': argv[argv.index('--port') + 1] = '18002'
        if invalid == 'workers': argv[argv.index('--workers') + 1] = '2'
        if invalid == 'proxy': argv[-1] = '--proxy-headers'
        if invalid == 'truncated': argv += ['--port']
        process(host, argv=argv, env={'INGRESS_PROFILE': 'lan'})
    if invalid == 'profile': process(host, argv=argv, env={'INGRESS_PROFILE': 'split'})
    if invalid == 'socket': monkeypatch.setattr(host, 'listeners', lambda: [('::', 18000, 'pid=1234,')])
    if invalid == 'pid': monkeypatch.setattr(p, 'execute', lambda *args, **kwargs: b'0')
    if invalid == 'health': monkeypatch.setattr(host, 'request', lambda *args, **kwargs: 503)
    if invalid:
        with pytest.raises(p.Refusal): host.lan_ready(config)
    else:
        host.lan_ready(config)


@pytest.mark.parametrize('statuses', [(200, 422, 403, 422, 403), (503,), (200, 200), (200, 422, 200)])
def test_backend_health_and_private_probe_status_contract(host, lan, monkeypatch, statuses):
    config, _ = lan
    calls = []
    observed = iter(statuses)
    def request(*args, **kwargs):
        calls.append((args, kwargs))
        return next(observed)
    monkeypatch.setattr(host, 'request', request)
    if statuses == (200, 422, 403, 422, 403):
        host.private_probes(config)
        assert calls[0][0][2] == '127.0.0.2' and calls[0][1]['host'] == config['public_hostname']
        assert calls[1][0][2] == '127.0.0.3' and calls[1][1]['body'] == '{}'
        assert 'X-Webhook-Token' not in str(calls) and 'Cookie' not in str(calls)
    else:
        with pytest.raises(p.Refusal): host.private_probes(config)


@pytest.mark.parametrize('response,expected', [(b'HTTP/1.1 200 OK\r\n', 200), (b'HTTP/1.0 422 Unprocessable\r\n', 422),
                                              (b'HTTP/1.1 20', ConnectionError), (b'garbage\r\n', None), (b'X' * 8193, None)])
def test_http_status_line_fragmentation_over_real_tcp(host, response, expected):
    errors = []
    with socket.socket() as server:
        server.bind(('127.0.0.1', 0))
        server.listen()
        def serve():
            try:
                with server.accept()[0] as connection:
                    connection.recv(4096)
                    for value in response:
                        connection.sendall(bytes([value]))
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception as error:
                errors.append(error)
        thread = threading.Thread(target=serve)
        thread.start()
        try:
            if expected is None or isinstance(expected, type):
                with pytest.raises(expected or p.Refusal): host.request('127.0.0.1', server.getsockname()[1], '127.0.0.1', '/api/health')
            else:
                assert host.request('127.0.0.1', server.getsockname()[1], '127.0.0.1', '/api/health') == expected
        finally:
            thread.join(5)
            assert not thread.is_alive() and not errors


def test_resource_continuation_cannot_hide_launch_override(host, systemd_layout):
    layout, _ = systemd_layout
    layout.dropins.mkdir(mode=0o755)
    p.atomic(layout.dropins / 'custom.conf', b'[Service]\nNice=1\n  ExecStart=custom-command\n', 0o644)
    with pytest.raises(p.Refusal):
        host.app_layout_ready(layout)


def test_validator_contract_matches_the_actual_shipped_systemd_unit():
    """INI contract, not proof of systemd's production interpretation (9C)."""
    import configparser
    import shlex
    parser = configparser.ConfigParser(interpolation=None, strict=True)
    parser.optionxform = str
    parser.read(p.ROOT / 'deploy/work-tracker.service')
    service = parser['Service']
    expected = p.APP_PROPERTIES
    # Any new launch/sandbox directive requires an explicit validator review.
    assert set(service) == {'Type', 'User', 'Group', 'WorkingDirectory', 'EnvironmentFile', 'ExecStart',
        'Restart', 'RestartSec', 'UMask', 'NoNewPrivileges', 'PrivateTmp', 'ProtectSystem', 'ProtectHome', 'ReadWritePaths'}
    for name in ('Type', 'User', 'Group', 'WorkingDirectory', 'ProtectSystem', 'ReadWritePaths'):
        assert service[name] == expected[name]
    for name in ('NoNewPrivileges', 'PrivateTmp', 'ProtectHome'):
        assert {'true':'yes', 'false':'no'}.get(service[name], service[name]) == expected[name]
    assert service['EnvironmentFile'] + ' (ignore_errors=no)' == expected['EnvironmentFiles']
    assert shlex.split(service['ExecStart']) == list(p.APP_EXEC_START)
    for name in ('Environment', 'PassEnvironment', 'UnsetEnvironment', 'RootDirectory', 'RootImage',
                 'ExecStartPre', 'ExecStartPost', 'ExecCondition', 'ExecStop', 'ExecStopPost'):
        assert service.get(name, '') == expected[name] == ''
    assert service.get('DynamicUser', 'no') == expected['DynamicUser']
    assert service['UMask'] == '0027' and service['Restart'] == 'on-failure' and service['RestartSec'] == '5s'


@pytest.mark.parametrize('stable', [False, True])
def test_app_layout_recognizes_only_exact_loaded_update_guard(host, systemd_layout, stable):
    import update_safety as u
    layout, props = systemd_layout
    layout.dropins.mkdir(mode=0o755)
    guard = layout.dropins / u.GUARD_NAME
    content = u.BOOTSTRAP_GUARD if stable else u.GUARD
    p.atomic(guard, content, 0o644)
    if stable: p.atomic(layout.env.parent / 'update-safety.py', Path(u.__file__).read_bytes(), 0o644)
    props['DropInPaths'] = str(guard)
    props['ExecStartPre'] = ('{ path=/usr/bin/python3 ; argv[]=/usr/bin/python3 '
        '/opt/work-tracker/deploy/update_safety.py allow-start ; ignore_errors=no ; pid=1234 ; status=0/0 }')
    if stable: props['ExecStartPre'] = props['ExecStartPre'].replace('/usr/bin/python3 /opt/work-tracker/deploy/update_safety.py', '/usr/bin/python3 -I /etc/work-tracker/update-safety.py')
    host.app_layout_ready(layout)
    props['ExecStartPre'] = ''
    with pytest.raises(p.Refusal): host.app_layout_ready(layout)
    p.atomic(guard, u.GUARD + b'# administrator edit\n', 0o644)
    with pytest.raises(u.Refusal): host.app_layout_ready(layout)
