"""Run the actual staged update.sh against local Git releases and live proxies.

Only OS privilege/package/build/systemd adapters are faked. The updater, its
rollback helpers, compatibility hooks, Git FF, SQLite backups, Uvicorn/Nginx
sockets, HTTP/HA authentication and subsequent LAN rollback are real. This is
not proof of real systemd dependency ordering (Phase 9C).
"""
import json
import os
from pathlib import Path
import shutil
import socketserver
import sqlite3
import subprocess
import sys
import threading
import time


import pytest
from nginx_provision_test import setup, tls, p
import update_safety as safety_module
from ingress_cutover_test import cutover, activate, c

ROOT = p.ROOT
sys.path.insert(0, str(ROOT / 'backend'))


@pytest.fixture
def updater(cutover, monkeypatch):
    tool, evidence = cutover
    host = tool.host
    base = tool.layout.boundary
    database = base / 'work.db'
    url = 'sqlite:///' + str(database)
    subprocess.run([sys.executable, '-m', 'alembic', 'upgrade', 'head'], cwd=ROOT / 'backend',
                   env=os.environ | {'DATABASE_URL': url}, check=True, capture_output=True)
    with sqlite3.connect(database) as db:
        db.execute("INSERT INTO location_timezones (location, timezone, updated_at) VALUES ('office', 'UTC', '2030-01-01T00:00:00+00:00')")
    tool.layout.database_url = url
    p.atomic(tool.layout.env, tool.layout.env.read_bytes().replace(c.Layout.database_url.encode(), url.encode()), 0o640)
    unit = (ROOT / 'deploy/work-tracker.service').read_bytes()
    p.atomic(tool.layout.app_unit, unit, 0o644)
    backend = {'process': None, 'log': None}
    state = {'failure': None, 'calls': [], 'pending': 0, 'systemd_active': True, 'threads': [],
             'backend_delay': .15, 'nginx_delay': .25}
    safety_layout = safety_module.Layout()
    safety_layout.uid = os.getuid()
    safety_layout.boundary = base
    safety_layout.state = tool.layout.update_state
    safety_layout.guard = tool.layout.dropins / safety_module.GUARD_NAME
    safety_layout.backups = base / 'updates'
    safety = safety_module.Safety(safety_layout)
    actual = p.Host()
    real_execute = p.execute
    def execute(args, **kwargs):
        if args[:2] == ['systemctl', 'show']:
            pid = host.process.pid if args[-1] == p.SERVICE and host.active() else (backend['process'].pid if args[-1] != p.SERVICE and backend['process'] else 0)
            return str(pid).encode()
        return real_execute(args, **kwargs)
    monkeypatch.setattr(p, 'execute', execute)
    def app_control(action):
        if backend['process']:
            backend['process'].terminate()
            backend['process'].wait(timeout=5)
            backend['log'].close()
            backend['process'] = None
        if action == 'stop': return
        safety.allow_start()
        env = c.values(tool.layout.env.read_bytes())
        backend['log'] = (base / 'update-uvicorn.log').open('a')
        backend['process'] = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'app.main:app',
            '--host', env['APP_HOST'], '--port', env['APP_PORT'], '--workers', '1', '--no-proxy-headers'],
            cwd=ROOT / 'backend', env=os.environ | env, stdout=backend['log'], stderr=backend['log'])
    monkeypatch.setattr(host, 'app_control', app_control)
    monkeypatch.setattr(actual, 'active', host.active)
    # Loopback Nginx runs as the test UID; real credential checks are covered
    # by host_validation/readiness proc fixtures, not production systemd.
    monkeypatch.setattr(actual, 'ingress_permissions', lambda pid, **kwargs: None)
    monkeypatch.setattr(actual, 'ingress_pid', lambda: str(host.process.pid) if host.active() else '0')
    def readiness(config, **kwargs):
        try: return actual.update_readiness(config | {'lan_ip': '127.0.0.1'}, **kwargs)
        except p.Pending:
            state['pending'] += 1
            raise
    monkeypatch.setattr(host, 'update_readiness', readiness)
    monkeypatch.setattr(host, 'backend_ready', actual.backend_ready)
    monkeypatch.setattr(host, 'backend_health', actual.backend_health)
    monkeypatch.setattr(host, 'app_config_preserved', actual.app_config_preserved)
    monkeypatch.setattr(host, 'private_probes', lambda config: actual.private_probes(config | {'lan_ip': '127.0.0.1'}))
    monkeypatch.setattr(host, 'lan_ready', lambda config: actual.lan_ready(config | {'lan_ip': '127.0.0.1'}))
    tool.wait = c.Cutover.wait.__get__(tool)
    app_control('start')
    tool.wait(lambda: host.lan_ready(host.config))
    activate(cutover)
    app = base / 'checkout'
    source = base / 'releases'
    remote = base / 'remote.git'
    source.mkdir()
    shutil.copytree(ROOT / 'deploy', source / 'deploy', ignore=shutil.ignore_patterns('__pycache__'))
    (source / 'backend/app').mkdir(parents=True)
    shutil.copyfile(ROOT / 'backend/app/origins.py', source / 'backend/app/origins.py')
    shutil.copytree(ROOT / 'backend/alembic', source / 'backend/alembic', ignore=shutil.ignore_patterns('__pycache__'))
    shutil.copyfile(ROOT / 'backend/alembic.ini', source / 'backend/alembic.ini')
    (source / 'frontend').mkdir()
    (source / 'frontend/package-lock.json').write_text('{}\n')
    shutil.copyfile(ROOT / '.gitignore', source / '.gitignore')
    text = (ROOT / 'update.sh').read_text()
    replacements = {'/opt/work-tracker': str(app), '/etc/work-tracker/work-tracker.env': str(tool.layout.env),
                    '/var/lib/work-tracker/work_tracker.db': str(database), '/var/backups/work-tracker': str(base / 'updates'),
                    '/etc/systemd/system/work-tracker.service': str(tool.layout.app_unit),
                    '/etc/work-tracker/ingress/cutover.json': str(tool.journal)}
    for before, after in replacements.items(): text = text.replace(before, after)
    text = text.replace('[[ "${EUID}" -eq 0 ]]', 'true')  # Private OS adapter, no product bypass.
    text = text.replace('readonly SERVICE_USER="work-tracker"', 'readonly SERVICE_USER="' + str(__import__('pwd').getpwuid(os.getuid()).pw_name) + '"')
    (source / 'update.sh').write_text(text)
    (source / 'update.sh').chmod(0o755)
    common = source / 'deploy/common.sh'
    common.write_text(common.read_text().replace('/run/work-tracker-deploy.lock', str(tool.layout.deploy_lock))
                      .replace('/etc/work-tracker/ingress/cutover.json', str(tool.journal))
                      .replace(c.Layout.database_url, url))
    manifest = source / 'deploy/config.manifest'
    manifest.write_text(manifest.read_text().replace(c.Layout.database_url, url))
    (source / 'release-marker').write_text('old\n')
    def git(directory, *args):
        return subprocess.check_output(['git', '-C', str(directory), *args], stderr=subprocess.DEVNULL).decode().strip()
    git(source, 'init', '-q', '-b', 'main')
    git(source, 'config', 'user.name', 'Deployment Test')
    git(source, 'config', 'user.email', 'deployment@example.invalid')
    git(source, 'add', '.')
    git(source, 'commit', '-qm', 'old local release')
    subprocess.run(['git', 'clone', '--bare', '-q', str(source), str(remote)], check=True)
    subprocess.run(['git', 'clone', '-q', str(remote), str(app)], check=True)
    git(source, 'remote', 'add', 'origin', str(remote))
    old_commit = git(app, 'rev-parse', 'HEAD')
    control_socket = base / 'control.sock'
    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            request = json.loads(self.rfile.readline())
            kind, args = request['kind'], request['args']
            state['calls'].append((kind, args))
            rc, output = 0, ''
            try:
                if kind == 'hook':
                    # Hooks execute real validation. Only observation/control of
                    # systemd credentials is simulated by the parent fixture.
                    previous_root = p.ROOT
                    p.ROOT = app
                    try:
                        if args[0] == 'update-check':
                            tool.check_update(args[args.index('--manifest-sha') + 1], '--incoming' in args)
                        elif args[0] == 'update-verify':
                            state['verify_started'] = time.monotonic()
                            if state.get('after_restart'): state['after_restart']()
                            tool.update_verify(int(args[args.index('--readiness-timeout') + 1]))
                        else: raise AssertionError('Unexpected mutating hook')
                    finally: p.ROOT = previous_root
                elif kind == 'safety':
                    operation = args[0]
                    if operation == 'check': safety.clear_required()
                    elif operation == 'install': safety.install()
                    elif operation == 'begin':
                        def arg(key): return args[args.index(key) + 1]
                        safety.begin(arg('--previous'), arg('--candidate'), Path(arg('--directory')), int(arg('--owner-pid')))
                    elif operation == 'hold': safety.hold()
                    elif operation == 'remove-legacy-guard': safety.remove_legacy_guard(app)
                    else: safety.transition(operation)
                elif kind == 'systemctl':
                    if args[0] == 'is-active': rc = 0 if state['systemd_active'] else 3
                    elif args[0] == 'show':
                        output = ('active' if state['systemd_active'] else 'inactive') if args[args.index('-p') + 1] == 'ActiveState' else str(backend['process'].pid if backend['process'] else 0)
                    elif args[0] == 'stop':
                        state['systemd_active'] = False
                        for worker in state['threads']: worker.join(5)
                        state['threads'].clear()
                        host.control('stop')
                        app_control('stop')
                    elif args[0] == 'restart':
                        if state['failure'] == 'restart':
                            state['failure'], rc = None, 1
                        else:
                            # Type=simple: return BEFORE either service is ready.
                            state['systemd_active'] = True
                            state['restart_returned'] = time.monotonic()
                            def start_services():
                                time.sleep(state['backend_delay'])
                                if state['failure'] == 'never-ready': return
                                app_control('restart')
                                state['backend_started'] = time.monotonic()
                                if tool.layout.ingress_link.is_symlink():
                                    time.sleep(state['nginx_delay'])
                                    host.control('start')
                                    state['ingress_started'] = time.monotonic()
                            worker = threading.Thread(target=start_services)
                            state['threads'].append(worker)
                            worker.start()
                elif kind == 'sqlite3':
                    target = __import__('shlex').split(args[1])[1]
                    with sqlite3.connect(args[0]) as db, sqlite3.connect(target) as backup: db.backup(backup)
                    if Path(target).name == 'online.db' and state.get('after_backup'): state['after_backup']()
                elif kind in ('npm', 'alembic'):
                    if kind == 'alembic':
                        result = subprocess.run([sys.executable, '-m', 'alembic', *args], cwd=app / 'backend',
                                                env=os.environ | {'DATABASE_URL': url, 'PYTHONPATH': str(ROOT / 'backend')},
                                                capture_output=True)
                        rc, output = result.returncode, result.stdout.decode()
                    elif args == ['run', 'build'] and state['failure'] == 'build':
                        state['failure'], rc = None, 1
            except (p.Refusal, OSError, ValueError):
                rc, output = 1, 'Compatibility/health refusal (private values withheld)\n'
            self.wfile.write(json.dumps({'rc': rc, 'out': output}).encode() + b'\n')
    server = socketserver.UnixStreamServer(str(control_socket), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    binaries = base / 'bin'
    binaries.mkdir(mode=0o755)
    driver = f'''#!{sys.executable}
import json, os, pathlib, socket, subprocess, sys
kind = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
if kind == 'python3':
    if args and args[0].endswith('/ingress_cutover.py'): kind, args = 'hook', args[1:]
    elif args and args[0].endswith('/update_safety.py'): kind, args = 'safety', args[1:]
    else: os.execv({sys.executable!r}, [{sys.executable!r}, *args])
if kind == 'runuser': os.execvp(args[3], args[3:])
if kind == 'install':
    cleaned=[]
    while args:
        value=args.pop(0)
        if value in ('-o','-g'): args.pop(0)
        else: cleaned.append(value)
    os.execv('/usr/bin/install', ['/usr/bin/install', *cleaned])
if kind == 'chown': sys.exit(0)
with socket.socket(socket.AF_UNIX) as connection:
    connection.connect({str(control_socket)!r})
    connection.sendall(json.dumps(dict(kind=kind,args=args)).encode()+b'\\n')
    response=json.loads(connection.makefile('rb').readline())
    sys.stdout.write(response['out'])
    sys.exit(response['rc'])
'''
    for name in ('python3', 'systemctl', 'sqlite3', 'npm', 'install', 'chown', 'runuser'):
        (binaries / name).write_text(driver)
        (binaries / name).chmod(0o755)
    venv = app / 'backend/.venv/bin'
    venv.mkdir(parents=True)
    for name in ('pip', 'alembic'):
        (venv / name).write_text(driver)
        (venv / name).chmod(0o755)
    def run(change='code', failure=None, after_backup=None, after_restart=None):
        (source / 'release-marker').write_text('new\n')
        if failure == 'migration' or change == 'migration':
            content = '''from alembic import op
import sqlalchemy as sa
revision = '20990101_p1'
down_revision = '20261025_08'
def upgrade():
    op.create_table('interrupted', sa.Column('id', sa.Integer, primary_key=True))
'''
            if failure == 'migration': content += "    raise RuntimeError('test partial migration')\n"
            (source / 'backend/alembic/versions/20990101_p1.py').write_text(content)
        if change == 'unit':
            with (source / 'deploy/work-tracker.service').open('a') as out: out.write('# new incompatible baseline\n')
        if change == 'manifest':
            with (source / 'deploy/config.manifest').open('a') as out: out.write('# changed contract\n')
        git(source, 'add', '.')
        git(source, 'commit', '-qm', 'new local release')
        git(source, 'push', '-q', 'origin', 'main')
        state['failure'] = failure
        state['after_backup'], state['after_restart'] = after_backup, after_restart
        result = subprocess.run(['bash', str(app / 'update.sh'), '--readiness-timeout', '5'], env=os.environ | {'PATH': str(binaries) + ':' + os.environ['PATH']}, capture_output=True, timeout=60)
        return result
    try:
        yield SimpleNamespace(tool=tool, app=app, run=run, state=state, old_commit=old_commit, git=git,
                              database=database, actual=actual, backend=backend, safety=safety)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
        for worker in state['threads']: worker.join(5)
        host.control('stop')
        app_control('stop')


from types import SimpleNamespace


@pytest.mark.parametrize('failure', [None, 'build'])
def test_actual_update_in_active_split_then_full_lan_rollback(updater, failure):
    u = updater
    tool = u.tool
    archive = tool.transaction()[0]
    before = tool.layout.env.read_bytes(), tool.layout.app_unit.read_bytes(), archive['generation'], Path(archive['backup']).read_bytes()
    result = u.run(failure=failure)
    assert result.returncode == (0 if failure is None else 1), result.stdout.decode() + result.stderr.decode()
    assert before == (tool.layout.env.read_bytes(), tool.layout.app_unit.read_bytes(), tool.journal_state()['generation'], Path(archive['backup']).read_bytes())
    assert u.git(u.app, 'rev-parse', 'HEAD') != u.old_commit if failure is None else u.git(u.app, 'rev-parse', 'HEAD') == u.old_commit
    tool.update_verify(5)
    if failure is None:
        assert u.state['restart_returned'] < u.state['verify_started'] < u.state['backend_started'] < u.state['ingress_started']
        assert u.state['pending'] > 0
    # Positive/missing/wrong token authentication on the disposable DB, no user session.
    import http.client
    body = json.dumps(dict(event='entry', source='home_assistant', location='office', timestamp='2030-01-01T08:00:00Z'))
    token = c.values(tool.layout.env.read_bytes())['WEBHOOK_TOKEN']
    for supplied, expected in [(None, 401), ('wrong-test-token', 401), (token, 201)]:
        connection = http.client.HTTPConnection('127.0.0.1', tool.host.config['ha_port'], source_address=('127.0.0.11', 0))
        headers = {'Content-Type': 'application/json'}
        if supplied: headers['X-Webhook-Token'] = supplied
        connection.request('POST', '/api/webhook/home-assistant', body, headers)
        assert connection.getresponse().status == expected
        connection.close()
    with sqlite3.connect(u.database) as db:
        assert db.execute('SELECT count(*) FROM work_events').fetchone()[0] == 1
        assert not db.execute("SELECT name FROM sqlite_master WHERE name='interrupted'").fetchall()
    correction = json.dumps({'raw_event_id': 1, 'time': '07:45'})
    for supplied, expected in [(None, 401), ('wrong-test-token', 401), (token, 200)]:
        connection = http.client.HTTPConnection('127.0.0.1', tool.host.config['ha_port'], source_address=('127.0.0.11', 0))
        headers = {'Content-Type': 'application/json'}
        if supplied: headers['X-Webhook-Token'] = supplied
        connection.request('POST', '/api/webhook/home-assistant/correction', correction, headers)
        assert connection.getresponse().status == expected
        connection.close()
    with sqlite3.connect(u.database) as db:
        assert db.execute('SELECT count(*) FROM work_event_corrections').fetchone()[0] == 1
        raw_before = db.execute('SELECT * FROM work_events').fetchall()
    tool.recover(rollback=True)
    assert not tool.journal.exists()
    assert u.actual.request('127.0.0.1', tool.host.config['ha_port'], '127.0.0.1', '/api/health') == 200
    with sqlite3.connect(u.database) as db:
        assert db.execute('SELECT * FROM work_events').fetchall() == raw_before
        assert db.execute('SELECT count(*) FROM work_event_corrections').fetchone()[0] == 1
    assert list((tool.layout.boundary / 'updates').glob('*/online.db'))


@pytest.mark.parametrize('change', ['unit', 'manifest'])
def test_incompatible_incoming_release_refused_before_merge_or_service_stop(updater, change):
    u = updater
    result = u.run(change=change)
    assert result.returncode == 1
    assert u.git(u.app, 'rev-parse', 'HEAD') == u.old_commit
    assert not any(kind == 'systemctl' and args[0] in ('stop', 'restart') for kind, args in u.state['calls'])
    u.tool.verify()
    u.tool.recover(rollback=True)


def database_rows(path):
    """Compare actual contents, not counts or a file hash as a write oracle."""
    with sqlite3.connect(path) as db:
        tables = ('work_events', 'work_event_corrections', 'users', 'user_sessions', 'login_attempts',
                  'pay_rates', 'location_timezones')
        return {name: db.execute(f'SELECT * FROM {name} ORDER BY rowid').fetchall() for name in tables}


def ha(updater, path, payload):
    import http.client
    connection = http.client.HTTPConnection('127.0.0.1', updater.tool.host.config['ha_port'],
                                            source_address=('127.0.0.11', 0), timeout=3)
    token = c.values(updater.tool.layout.env.read_bytes())['WEBHOOK_TOKEN']
    connection.request('POST', '/api/webhook/home-assistant' + path, json.dumps(payload),
                       {'Content-Type': 'application/json', 'X-Webhook-Token': token})
    response = connection.getresponse()
    value = response.status, json.loads(response.read())
    connection.close()
    return value


def event(updater, kind='entry', timestamp='2030-01-01T08:00:00Z'):
    status, value = ha(updater, '', dict(event=kind, source='home_assistant', location='office', timestamp=timestamp))
    assert status == 201
    return value['id']


def seed_identity(updater):
    with sqlite3.connect(updater.database) as db:
        db.execute("INSERT INTO users VALUES (1,'fixture_owner','public-test-hash','2030-01-01T00:00:00+00:00')")
        db.execute("INSERT INTO user_sessions VALUES (1,1,?,'2030-01-01T00:00:00+00:00','2030-01-01T00:00:00+00:00','2031-01-01T00:00:00+00:00',NULL)", ('b' * 64,))
        db.execute("INSERT INTO login_attempts (login_key,ip_key,attempted_at,failed_at) VALUES (?,'192.0.2.1/32','2030-01-01T00:00:00+00:00',NULL)", ('c' * 64,))


def later_identity_writes(updater):
    with sqlite3.connect(updater.database) as db:
        db.execute("UPDATE user_sessions SET last_seen_at='2030-01-01T01:00:00+00:00', revoked_at='2030-01-01T02:00:00+00:00'")
        db.execute("UPDATE login_attempts SET failed_at='2030-01-01T01:00:00+00:00'")
        db.execute("INSERT INTO users VALUES (2,'second_fixture','public-test-hash','2030-01-01T03:00:00+00:00')")


def wait_for_proxy(updater):
    # Safe, invalid JSON probe; never repeat a valid event POST.
    deadline = time.monotonic() + 4
    while True:
        try:
            if updater.actual.request('127.0.0.1', updater.tool.host.config['ha_port'], '127.0.0.11',
                                      '/api/webhook/home-assistant', method='POST',
                                      headers='Content-Type: application/json\r\n', body='{}') == 422: return
        except OSError: pass
        assert time.monotonic() < deadline, 'test proxy did not start'
        time.sleep(.03)


@pytest.mark.parametrize('failure', ['build', 'migration', 'restart', 'never-ready'])
def test_backup_then_accepted_ha_correction_sessions_and_failure_preserve_every_row(updater, failure):
    u = updater
    first = event(u)
    seed_identity(u)
    before = database_rows(u.database)
    expected = []
    def after_backup():
        event(u, 'exit', '2030-01-01T16:00:00Z')
        assert ha(u, '/correction', {'raw_event_id': first, 'time': '07:45'})[0] == 200
        later_identity_writes(u)
        expected.append(database_rows(u.database))
    result = u.run(failure=failure, after_backup=after_backup)
    assert result.returncode == 1
    assert expected and expected[0] != before
    assert database_rows(u.database) == expected[0]
    assert database_rows(u.database)['work_events'][0] == before['work_events'][0]  # original raw boundary immutable
    archives = list((u.tool.layout.boundary / 'updates').glob('*/online.db'))
    assert len(archives) == 1 and database_rows(archives[0]) == before
    if failure == 'build':
        assert u.git(u.app, 'rev-parse', 'HEAD') == u.old_commit
        assert not u.safety.layout.state.exists()
        u.tool.update_verify(5)
    else:
        assert b'REQUIRES MANUAL RECOVERY' in result.stderr
        assert u.git(u.app, 'rev-parse', 'HEAD') != u.old_commit
        assert u.safety.read()['phase'] == 'manual'
        assert not u.tool.host.active() and u.backend['process'] is None
        with pytest.raises(safety_module.Refusal): u.safety.allow_start()
        with pytest.raises(p.Refusal): u.tool.recover(rollback=True)
        checkpoints = list((u.tool.layout.boundary / 'updates').glob('*/pre-migration.db'))
        assert len(checkpoints) == 1 and database_rows(checkpoints[0]) == expected[0]
        with sqlite3.connect(u.database) as db:
            assert bool(db.execute("SELECT name FROM sqlite_master WHERE name='interrupted'").fetchall()) == (failure == 'migration')
    if failure == 'never-ready':
        assert u.state['pending'] > 1
        assert time.monotonic() - u.state['verify_started'] >= 5


@pytest.mark.parametrize('schema_change', [False, True])
def test_new_post_restart_event_survives_permanent_verification_failure(updater, schema_change):
    u = updater
    event(u)
    expected = []
    def after_restart():
        wait_for_proxy(u)
        event(u, 'exit', '2030-01-01T16:00:00Z')
        expected.append(database_rows(u.database))
        # The services answer HTTP, but immutable configuration is now wrong.
        p.atomic(u.tool.layout.env, u.tool.layout.env.read_bytes() + b'ADMIN_CHANGED=1\n', 0o640)
    result = u.run(change='migration' if schema_change else 'code', after_restart=after_restart)
    assert result.returncode == 1 and b'REQUIRES MANUAL RECOVERY' in result.stderr
    assert expected and database_rows(u.database) == expected[0]
    assert len(expected[0]['work_events']) == 2
    assert u.state['pending'] == 0  # static security failure was not retried
    assert u.safety.read()['phase'] == 'manual'
    assert u.git(u.app, 'rev-parse', 'HEAD') != u.old_commit
    assert not u.tool.host.active() and u.backend['process'] is None
    with pytest.raises(safety_module.Refusal): u.safety.allow_start()
    for path in (u.tool.layout.boundary / 'updates').glob('*/*.db'):
        assert len(database_rows(path)['work_events']) == 1  # both snapshots predate the new write
    with sqlite3.connect(u.database) as db:
        assert db.execute('SELECT version_num FROM alembic_version').fetchone()[0] == ('20990101_p1' if schema_change else '20261025_08')


def test_corrupt_backup_is_never_restored_over_later_writes(updater):
    u = updater
    expected = []
    def after_backup():
        event(u)
        expected.append(database_rows(u.database))
        path = next((u.tool.layout.boundary / 'updates').glob('*/online.db'))
        path.write_bytes(b'corrupt snapshot, never a restore candidate')
    result = u.run(failure='build', after_backup=after_backup)
    assert result.returncode == 1
    assert database_rows(u.database) == expected[0]
    u.tool.update_verify(5)
    checkpoint = next((u.tool.layout.boundary / 'updates').glob('*/online.db'))
    refusal = subprocess.run(['bash', '-c', 'source "$1"; restore_database_backup "$2" "$3" "$4"',
        'test', str(ROOT / 'deploy/update_rollback.sh'), str(u.database), str(checkpoint), 'unused'], capture_output=True)
    assert refusal.returncode == 1
    assert database_rows(u.database) == expected[0]


def test_sigkill_after_new_ha_write_preserves_database_and_blocks_next_start(updater):
    import signal
    u = updater
    event(u)
    expected = []
    def kill_after_write():
        wait_for_proxy(u)
        event(u, 'exit', '2030-01-01T16:00:00Z')
        expected.append(database_rows(u.database))
        os.kill(u.safety.read()['owner']['pid'], signal.SIGKILL)
    result = u.run(after_restart=kill_after_write)
    assert result.returncode == -signal.SIGKILL
    assert expected and database_rows(u.database) == expected[0]
    assert len(expected[0]['work_events']) == 2
    assert u.safety.read()['phase'] == 'verifying'  # No EXIT trap can run after SIGKILL.
    assert not u.safety.alive(u.safety.read())
    with pytest.raises(safety_module.Refusal): u.safety.allow_start()
    assert u.git(u.app, 'rev-parse', 'HEAD') != u.old_commit
    assert not any(kind == 'safety' and args[0] == 'hold' for kind, args in u.state['calls'])
    u.tool.host.control('stop')
    u.tool.host.app_control('stop')
    with pytest.raises(safety_module.Refusal): u.tool.host.app_control('start')
    assert database_rows(u.database) == expected[0]
