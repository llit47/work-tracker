"""9B2 fault tests: real Nginx/OpenSSL; fake systemd, app restart and network evidence.

Actual SIGKILL is injected into a separate process. No test touches host units,
packages, production configuration or database. Real proxy/domain tests are in
 ingress_security_test.py; these tests do not claim a production systemd boot.
"""
import io
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest
from nginx_provision_test import setup, tls, prepare, p

sys.path.insert(0, str(p.ROOT / 'deploy'))
sys.modules['nginx_provision'] = p
import ingress_cutover as c
import ingress_bootstrap as b


@pytest.fixture
def cutover(setup, monkeypatch):
    tool, evidence, _ = prepare(setup)
    layout = tool.layout
    layout.deploy_lock = layout.boundary / 'deploy.lock'
    layout.database_url = c.Layout.database_url
    layout.env = layout.state.parent / 'work-tracker.env'
    layout.app_unit = layout.unit.parent / c.APP
    layout.dropins = layout.unit.parent / (c.APP + '.d')
    layout.recovery_unit = layout.unit.parent / c.RECOVERY
    layout.recovery_link = layout.unit.parent / 'multi-user.target.wants' / c.RECOVERY
    layout.ingress_link = layout.unit.parent / (c.APP + '.wants') / p.SERVICE
    config = tool.host.config
    p.atomic(layout.env, (f'APP_HOST=0.0.0.0\nAPP_PORT={config["ha_port"]}\n'
              'WEBHOOK_TOKEN=test-only-at-least-32-characters-token\n'
              'DATABASE_URL=sqlite:////var/lib/work-tracker/work_tracker.db\n'
              'BROWSER_ALLOWED_ORIGINS=http://10.123.45.10:8000\nSESSION_COOKIE_SECURE=false\n'
              'CUSTOM=retained\n').encode(), 0o640)
    p.atomic(layout.app_unit, b'original app unit\n', 0o644)
    layout.dropins.mkdir(mode=0o755)
    p.atomic(layout.dropins / 'operator.conf', b'[Service]\nNice=1\n', 0o644)
    host = tool.host
    host.lan = True
    host.points = []
    host.crash = None
    host.app_calls = []
    def app_control(action):
        host.app_calls.append(action)
        host.lan = action != 'stop' and c.values(layout.env.read_bytes()).get('INGRESS_PROFILE', 'lan') == 'lan'
    def lan_ready(config):
        p.require(host.lan, 'Test: LAN unavailable')
    def checkpoint(name):
        host.points.append(name)
        if host.crash == name:
            host.crash = None
            raise p.Refusal('Test failure')
    monkeypatch.setattr(host, 'app_control', app_control)
    monkeypatch.setattr(host, 'start_recovery', lambda: None)
    monkeypatch.setattr(host, 'app_layout_ready', lambda _: None)
    monkeypatch.setattr(host, 'app_permissions', lambda _: None)
    monkeypatch.setattr(host, 'lan_ready', lan_ready)
    monkeypatch.setattr(host, 'backend_health', lambda _: None)
    monkeypatch.setattr(host, 'app_config_preserved', lambda _: None)
    monkeypatch.setattr(host, 'private_probes', lambda _: None)
    monkeypatch.setattr(host, 'checkpoint', checkpoint)
    result = c.Cutover(tool)
    result.wait = lambda check: check()
    return result, evidence


def activate(cutover):
    tool, evidence = cutover
    tool.activate(evidence, lambda _: None, lambda _: None)
    return tool


def test_complete_cutover_and_idempotent_full_rollback(cutover):
    tool, _ = cutover
    before = tool.layout.env.read_bytes()
    unit = tool.layout.app_unit.read_bytes()
    database = tool.layout.boundary / 'database-sentinel'
    database.write_bytes(b'events sessions corrections login history remain unchanged')
    activate(cutover)
    assert c.values(tool.layout.env.read_bytes())['INGRESS_PROFILE'] == 'split'
    assert tool.layout.ingress_link.is_symlink() and tool.host.active()
    tool.verify()
    calls = tool.host.calls[:]
    activate(cutover)
    assert tool.host.calls == calls
    tool.recover()  # committed transaction must survive recovery/reboot
    assert tool.host.active()
    tool.recover(rollback=True)
    tool.recover(rollback=True)
    assert tool.layout.env.read_bytes() == before
    assert tool.layout.app_unit.read_bytes() == unit
    assert not tool.dropin.exists() and not tool.layout.ingress_link.is_symlink()
    assert not tool.host.active() and tool.host.lan
    assert database.read_bytes() == b'events sessions corrections login history remain unchanged'


@pytest.mark.parametrize('point', ['journal', 'dropin', 'env', 'backend-restart', 'backend-ready',
                                  'nginx-start', 'probes', 'enabled', 'committed'])
def test_compensation_at_every_cutover_boundary(cutover, point):
    tool, _ = cutover
    original = tool.layout.env.read_bytes()
    tool.host.crash = point
    with pytest.raises(p.Refusal):
        activate(cutover)
    tool.recover(rollback=True)
    assert tool.layout.env.read_bytes() == original
    assert not tool.journal.exists() and tool.host.lan and not tool.host.active()


@pytest.mark.parametrize('point', ['rollback-ingress', 'rollback-files', 'rollback-health'])
def test_rollback_failure_is_retryable(cutover, point):
    tool = activate(cutover)
    tool.host.crash = point
    with pytest.raises(p.Refusal):
        tool.recover(rollback=True)
    assert tool.journal.exists()
    tool.recover(rollback=True)
    assert not tool.journal.exists() and tool.host.lan


class OrphanProcess:
    """Handle real nginx reparented after the coordinator process was killed."""
    def __init__(self, pid):
        self.pid = pid
    def poll(self):
        path = Path(f'/proc/{self.pid}/stat')
        return 0 if not path.exists() or path.read_text().split()[2] == 'Z' else None
    def terminate(self):
        os.kill(self.pid, signal.SIGTERM)
    def wait(self, timeout):
        end = time.monotonic() + timeout
        while self.poll() is None and time.monotonic() < end:
            time.sleep(.02)
        assert self.poll() is not None


@pytest.mark.parametrize('point', ['journal', 'dropin', 'env', 'backend-restart', 'backend-ready', 'nginx-start', 'enabled'])
def test_real_sigkill_then_new_invocation_recovers(cutover, monkeypatch, point):
    tool, _ = cutover
    original = tool.layout.env.read_bytes()
    child = os.fork()
    if child == 0:
        tool.host.checkpoint = lambda name: os.kill(os.getpid(), signal.SIGKILL) if name == point else None
        try:
            activate(cutover)
        finally:
            os._exit(22)
    _, status = os.waitpid(child, 0)
    assert os.WIFSIGNALED(status) and os.WTERMSIG(status) == signal.SIGKILL
    if point in ('nginx-start', 'enabled'):
        tool.host.process = OrphanProcess(int((tool.layout.runtime / 'nginx.pid').read_text()))
        tool.host.log = io.StringIO()
    fresh = c.Cutover(tool.tool)
    fresh.wait = lambda check: check()
    fresh.recover()
    assert tool.layout.env.read_bytes() == original and not fresh.journal.exists()
    assert not tool.host.active()


def test_boot_recovery_restores_files_before_starting_lan(cutover):
    tool, _ = cutover
    # Persist an interrupted transaction using an actual killed coordinator.
    child = os.fork()
    if child == 0:
        tool.host.checkpoint = lambda name: os.kill(os.getpid(), signal.SIGKILL) if name == 'env' else None
        activate(cutover)
        os._exit(22)
    os.waitpid(child, 0)
    tool.host.app_calls.clear()
    tool.recover(boot=True)
    assert p.read_json(tool.journal)['phase'] == 'lan-restored'
    assert not tool.host.app_calls  # no blocking systemctl on jobs ordered after recovery
    assert c.values(tool.layout.env.read_bytes()).get('INGRESS_PROFILE', 'lan') == 'lan'
    tool.recover()  # separate invocation verifies LAN and finishes audit
    assert not tool.journal.exists()


def test_failed_real_ha_confirmation_compensates(cutover):
    tool, evidence = cutover
    with pytest.raises(p.Refusal):
        tool.activate(evidence, lambda _: None, lambda _: p.require(False, 'HA source rejected'))
    assert tool.host.lan and not tool.journal.exists()


def test_window_cancelled_and_missing_evidence_leave_app_untouched(cutover):
    tool, evidence = cutover
    before = tool.layout.env.read_bytes()
    with pytest.raises(p.Refusal):
        tool.activate(evidence, lambda _: p.require(False, 'cancelled'), lambda _: None)
    p.atomic(evidence, p.encoded(p.read_json(evidence) | {'firewall_ipv6_verified': False}))
    with pytest.raises(p.Refusal):
        activate(cutover)
    assert tool.layout.env.read_bytes() == before and not tool.host.app_calls


@pytest.mark.parametrize('field,value', [('ha_port', 1234), ('backend_port', 'busy')])
def test_preserve_ha_port_and_refuse_backend_conflicts(cutover, field, value):
    tool, _ = cutover
    if field == 'ha_port':
        data = tool.layout.env.read_bytes().replace(str(tool.host.config['ha_port']).encode(), b'1234')
        p.atomic(tool.layout.env, data, 0o640)
    else:
        tool.host.extra_listeners = [('::', tool.host.config['backend_port'], 'foreign IPv6 listener')]
    with pytest.raises(p.Refusal):
        tool.plan()


def test_admin_edits_are_never_silently_overwritten(cutover):
    tool = activate(cutover)
    before = tool.layout.env.read_bytes()
    p.atomic(tool.layout.env, before + b'OPERATOR=changed\n', 0o640)
    with pytest.raises(p.Refusal):
        tool.recover(rollback=True)
    assert tool.host.active() and tool.journal.exists()
    p.atomic(tool.layout.env, before, 0o640)
    tool.recover(rollback=True)


def test_reboot_and_update_service_dependency_contract():
    unit = (p.ROOT / 'deploy/ingress/work-tracker-ingress.service.example').read_text()
    assert f'Requires={c.APP}' in unit and f'PartOf={c.APP}' in unit
    assert f'WantedBy={c.APP}' in unit and 'Restart=on-failure' in unit
    assert 'nginx.service' not in unit and 'reload' not in unit
    update = (p.ROOT / 'update.sh').read_text()
    assert 'check_ingress_update_state' in update and 'ingress_cutover.py' not in update


@pytest.fixture
def bootstrap(cutover, monkeypatch):
    tool, _ = cutover
    bootstrap = b.Bootstrap(tool.tool)
    bootstrap.policy = tool.layout.boundary / 'policy-rc.d'
    monkeypatch.setattr(bootstrap, 'installed', lambda: False)
    monkeypatch.setattr(bootstrap, 'available', lambda: True)
    monkeypatch.setattr(tool.host, 'generic_active', lambda: False)
    monkeypatch.setattr(tool.host, 'generic_known', lambda: False)
    bootstrap.calls = []
    monkeypatch.setattr(bootstrap, 'install_packages', lambda: bootstrap.calls.append('apt'))
    # Fresh host has no binary until apt. Fixture nginx is used by real integration tests.
    tool.layout.nginx.unlink()
    def packages():
        bootstrap.calls.append('apt')
        p.atomic(tool.layout.nginx, b'test binary', 0o700)
    monkeypatch.setattr(bootstrap, 'install_packages', packages)
    return bootstrap


def test_fresh_bootstrap_masks_before_apt_and_is_idempotent(bootstrap, monkeypatch):
    original = bootstrap.install_packages
    def install():
        assert bootstrap.mask.is_symlink() and os.readlink(bootstrap.mask) == '/dev/null'
        assert bootstrap.policy.read_bytes() == b.POLICY
        original()
    monkeypatch.setattr(bootstrap, 'install_packages', install)
    assert bootstrap.run() == 'owned'
    monkeypatch.setattr(bootstrap, 'installed', lambda: True)
    assert bootstrap.run() == 'owned'
    assert bootstrap.calls == ['apt']
    for service in ('nginx', 'nginx.service'):
        assert subprocess.run([str(bootstrap.policy), service, 'start']).returncode == 101
    assert subprocess.run([str(bootstrap.policy), 'unrelated.service', 'start']).returncode == 0


def test_existing_nginx_is_read_only_and_no_package_operations(bootstrap, monkeypatch):
    monkeypatch.setattr(bootstrap, 'installed', lambda: True)
    p.atomic(bootstrap.layout.nginx, b'existing admin nginx', 0o700)
    assert bootstrap.run() == 'existing'
    assert not bootstrap.mask.is_symlink() and not bootstrap.policy.exists() and not bootstrap.calls
    monkeypatch.setattr(bootstrap, 'available', lambda: False)
    with pytest.raises(p.Refusal):
        bootstrap.run()
    assert not bootstrap.calls


@pytest.mark.parametrize('foreign', ['policy', 'mask', 'active'])
def test_bootstrap_never_overwrites_foreign_service_or_policy(bootstrap, monkeypatch, foreign):
    if foreign == 'active':
        monkeypatch.setattr(bootstrap.tool.host, 'generic_active', lambda: True)
    elif foreign == 'policy':
        p.atomic(bootstrap.policy, b'admin policy', 0o755)
    else:
        p.atomic(bootstrap.mask, b'admin unit', 0o644)
    with pytest.raises(p.Refusal):
        bootstrap.run()
    assert not bootstrap.calls


def test_interrupted_bootstrap_retains_upgrade_and_boot_guards(bootstrap, monkeypatch):
    monkeypatch.setattr(bootstrap, 'install_packages', lambda: p.require(False, 'apt failed'))
    with pytest.raises(p.Refusal):
        bootstrap.run()
    assert bootstrap.mask.is_symlink() and bootstrap.policy.read_bytes() == b.POLICY
    monkeypatch.setattr(bootstrap, 'installed', lambda: True)
    p.atomic(bootstrap.layout.nginx, b'test binary', 0o700)
    assert bootstrap.run() == 'owned'


def test_lan_install_choice_does_not_call_nginx_and_https_prepares_only(tmp_path):
    script = p.ROOT / 'deploy/common.sh'
    trace = tmp_path / 'trace'
    commands = f'''source "{script}"
python3() {{ printf '%s\\n' "$*" >>"{trace}"; }}
exec 3<<<''
choose_ingress_mode
[[ "$REPLY" == lan ]]
prepare_optional_ingress /test lan
[[ ! -f "{trace}" ]]
exec 3<<<https
choose_ingress_mode
[[ "$REPLY" == https ]]
prepare_optional_ingress /test https
'''
    subprocess.run(['bash', '-e', '-c', commands], check=True, capture_output=True)
    assert trace.read_text().splitlines() == ['/test/deploy/ingress_cutover.py bootstrap', '/test/deploy/ingress_cutover.py configure']


def test_updater_refuses_pending_transaction_but_preserves_committed_split(tmp_path):
    path = tmp_path / 'journal.json'
    for phase, status in [('pending', 1), ('rolling-back', 1), ('lan-restored', 1), ('active', 0)]:
        p.atomic(path, p.encoded({'phase': phase}))
        result = subprocess.run(['bash', '-c', f'source "{p.ROOT}/deploy/common.sh"; check_ingress_update_state "{path}"'], capture_output=True)
        assert result.returncode == status
        assert p.read_json(path) == {'phase': phase}


def test_real_uvicorn_nginx_cutover_and_lan_return_preserve_sqlite(cutover, monkeypatch):
    """Real LAN→split→LAN sockets/HTTP and SQLite; systemd control is simulated."""
    import hashlib
    import sqlite3
    tool, _ = cutover
    host = tool.host
    database = tool.layout.boundary / 'real-test.db'
    with sqlite3.connect(database) as connection:
        connection.execute('CREATE TABLE sentinel (value TEXT)')
        connection.execute("INSERT INTO sentinel VALUES ('preserve events sessions limiter')")
    original_hash = hashlib.sha256(database.read_bytes()).hexdigest()
    tool.layout.database_url = 'sqlite:///' + str(database)
    data = tool.layout.env.read_bytes().replace(c.Layout.database_url.encode(), tool.layout.database_url.encode())
    p.atomic(tool.layout.env, data, 0o640)
    backend = {'process': None, 'log': None}
    actual = p.Host()
    real_execute = p.execute
    def execute(args, **kwargs):
        if args[:2] == ['systemctl', 'show']:
            return str(backend['process'].pid).encode()
        return real_execute(args, **kwargs)
    monkeypatch.setattr(p, 'execute', execute)
    def app_control(action):
        if backend['process']:
            backend['process'].terminate()
            backend['process'].wait(timeout=5)
            backend['log'].close()
            backend['process'] = None
        if action == 'stop':
            return
        env = c.values(tool.layout.env.read_bytes())
        backend['log'] = (tool.layout.boundary / 'uvicorn.log').open('a')
        backend['process'] = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'app.main:app',
            '--host', env['APP_HOST'], '--port', env['APP_PORT'], '--workers', '1', '--no-proxy-headers'],
            cwd=p.ROOT / 'backend', env=os.environ | env, stdout=backend['log'], stderr=backend['log'])
    monkeypatch.setattr(host, 'app_control', app_control)
    monkeypatch.setattr(host, 'backend_ready', actual.backend_ready)
    monkeypatch.setattr(host, 'backend_health', actual.backend_health)
    monkeypatch.setattr(host, 'app_config_preserved', actual.app_config_preserved)
    monkeypatch.setattr(host, 'private_probes', lambda config: actual.private_probes(config | {'lan_ip': '127.0.0.1'}))
    monkeypatch.setattr(host, 'lan_ready', lambda config: actual.lan_ready(config | {'lan_ip': '127.0.0.1'}))
    tool.wait = c.Cutover.wait.__get__(tool)
    try:
        app_control('start')
        tool.wait(lambda: host.lan_ready(host.config))
        activate(cutover)
        tool.verify()
        for path in ('/api/webhook/home-assistant', '/api/webhook/home-assistant/correction'):
            assert actual.request('127.0.0.1', host.config['ha_port'], '127.0.0.11', path, method='POST',
                                  headers='Content-Type: application/json\r\n', body='{}') == 422
        tool.recover(rollback=True)
        assert actual.request('127.0.0.1', host.config['ha_port'], '127.0.0.1', '/api/health') == 200
        assert hashlib.sha256(database.read_bytes()).hexdigest() == original_hash
    finally:
        app_control('stop')



def test_updater_and_cutover_share_nonblocking_lock(cutover):
    tool, _ = cutover
    with tool.deployment_lock():
        command = ['bash', '-c', f'source "{p.ROOT}/deploy/common.sh"; acquire_deployment_lock "{tool.layout.deploy_lock}"']
        assert subprocess.run(command, capture_output=True).returncode == 1
    assert subprocess.run(command, capture_output=True).returncode == 0


def test_failed_application_restart_compensates_and_failed_lan_restart_retries(cutover, monkeypatch):
    tool, _ = cutover
    original = tool.host.app_control
    failures = [True, True]
    def control(action):
        if action == 'restart' and failures:
            failures.pop()
            raise p.Refusal('Test: actual restart command failed')
        original(action)
    monkeypatch.setattr(tool.host, 'app_control', control)
    with pytest.raises(p.Refusal):
        activate(cutover)
    assert tool.journal.exists()
    tool.recover()
    assert not tool.journal.exists() and tool.host.lan


def test_late_ha_port_conflict_never_stops_foreign_listener(cutover, monkeypatch):
    tool, _ = cutover
    original = tool.host.checkpoint
    def checkpoint(name):
        original(name)
        if name == 'backend-ready':
            tool.host.extra_listeners = [('::', tool.host.config['ha_port'], 'foreign service')]
    monkeypatch.setattr(tool.host, 'checkpoint', checkpoint)
    with pytest.raises(p.Refusal):
        activate(cutover)
    assert tool.host.extra_listeners == [('::', tool.host.config['ha_port'], 'foreign service')]
    assert not tool.host.active() and not tool.journal.exists()


def test_real_ipv6_browser_conflict_is_refused_before_app_changes(cutover):
    import socket
    tool, _ = cutover
    with socket.socket(socket.AF_INET6) as foreign:
        foreign.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        foreign.bind(('::1', tool.host.config['browser_port']))
        foreign.listen()
        with pytest.raises(p.Refusal):
            activate(cutover)
        assert not tool.host.app_calls
        assert foreign.getsockname()[1] == tool.host.config['browser_port']


def test_boot_of_committed_split_does_not_call_stop_or_restart(cutover):
    tool = activate(cutover)
    calls = tool.host.calls[:], tool.host.app_calls[:]
    tool.recover(boot=True)
    assert calls == (tool.host.calls, tool.host.app_calls)
    assert tool.host.active() and p.read_json(tool.journal)['phase'] == 'active'
    tool.recover(rollback=True)


def test_prepared_generation_cannot_overwrite_active_cutover(cutover):
    tool = activate(cutover)
    source = tool.layout.boundary / 'new.json'
    p.atomic(source, p.encoded(tool.host.config))
    with pytest.raises(p.Refusal):
        tool.tool.prepare(source)
    tool.recover(rollback=True)


def test_verify_detects_missing_recovery_and_dropin(cutover):
    tool = activate(cutover)
    original = tool.dropin.read_bytes()
    tool.dropin.unlink()
    with pytest.raises(p.Refusal):
        tool.verify()
    p.atomic(tool.dropin, original, 0o644)
    tool.layout.recovery_link.unlink()
    with pytest.raises(p.Refusal):
        tool.verify()
    tool.recover(rollback=True)


def test_bootstrap_can_install_when_iproute2_is_initially_absent(bootstrap, monkeypatch):
    monkeypatch.setattr(bootstrap.tool.host, 'listeners', lambda: p.require(False, 'ss not installed'))
    assert bootstrap.run() == 'owned'
    assert bootstrap.calls == ['apt']


def test_bootstrap_refuses_new_default_listener_after_apt(bootstrap, monkeypatch):
    states = iter([set(), {('tcp6', '0' * 32, 80)}])
    monkeypatch.setattr(bootstrap.tool.host, 'bootstrap_listeners', lambda: next(states))
    with pytest.raises(p.Refusal):
        bootstrap.run()
    assert bootstrap.mask.is_symlink() and bootstrap.policy.read_bytes() == b.POLICY


def test_service_failure_while_waiting_for_ha_confirmation_compensates(cutover):
    tool, evidence = cutover
    def confirmation(_):
        tool.host.control('stop')
    with pytest.raises(p.Refusal):
        tool.activate(evidence, lambda _: None, confirmation)
    assert tool.host.lan and not tool.journal.exists()


def test_real_sigterm_runs_compensation(cutover):
    tool, _ = cutover
    original = tool.layout.env.read_bytes()
    child = os.fork()
    if child == 0:
        signal.signal(signal.SIGTERM, c.terminate)
        tool.host.checkpoint = lambda name: os.kill(os.getpid(), signal.SIGTERM) if name == 'backend-ready' else None
        try:
            activate(cutover)
        except p.Refusal:
            os._exit(0)
        os._exit(22)
    _, status = os.waitpid(child, 0)
    assert os.waitstatus_to_exitcode(status) == 0
    assert not tool.journal.exists() and tool.layout.env.read_bytes() == original


def test_explicit_systemd_not_found_is_safe_even_with_nonzero_exit(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(p.subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(returncode=4, stdout=b'not-found\n'))
    assert not p.Host().generic_known()
    monkeypatch.setattr(p.subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout=b''))
    with pytest.raises(p.Refusal):
        p.Host().generic_known()
