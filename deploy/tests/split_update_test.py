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

import pytest
from nginx_provision_test import setup, tls, p
from ingress_cutover_test import cutover, activate, c

ROOT = p.ROOT
sys.path.insert(0, str(ROOT / 'backend'))
from app.database import build_engine
from app.models import Base


@pytest.fixture
def updater(cutover, monkeypatch):
    tool, evidence = cutover
    host = tool.host
    base = tool.layout.boundary
    database = base / 'work.db'
    url = 'sqlite:///' + str(database)
    Base.metadata.create_all(build_engine(url))
    with sqlite3.connect(database) as db:
        db.execute("INSERT INTO location_timezones (location, timezone, updated_at) VALUES ('office', 'UTC', '2030-01-01T00:00:00+00:00')")
    tool.layout.database_url = url
    p.atomic(tool.layout.env, tool.layout.env.read_bytes().replace(c.Layout.database_url.encode(), url.encode()), 0o640)
    unit = (ROOT / 'deploy/work-tracker.service').read_bytes()
    p.atomic(tool.layout.app_unit, unit, 0o644)
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
        if action == 'stop': return
        env = c.values(tool.layout.env.read_bytes())
        backend['log'] = (base / 'update-uvicorn.log').open('a')
        backend['process'] = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'app.main:app',
            '--host', env['APP_HOST'], '--port', env['APP_PORT'], '--workers', '1', '--no-proxy-headers'],
            cwd=ROOT / 'backend', env=os.environ | env, stdout=backend['log'], stderr=backend['log'])
    monkeypatch.setattr(host, 'app_control', app_control)
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
    state = {'failure': None, 'calls': []}
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
                        elif args[0] == 'update-verify': tool.verify()
                        else: raise AssertionError('Unexpected mutating hook')
                    finally: p.ROOT = previous_root
                elif kind == 'systemctl':
                    if args[0] == 'is-active': rc = 0 if backend['process'] and backend['process'].poll() is None else 3
                    elif args[0] == 'stop':
                        host.control('stop')
                        app_control('stop')
                    elif args[0] == 'restart':
                        if state['failure'] == 'restart':
                            state['failure'], rc = None, 1
                        else:
                            app_control('restart')
                            tool.wait(lambda: host.backend_ready(host.config))
                            if tool.layout.ingress_link.is_symlink():
                                host.control('start')
                                tool.tool.wait_for_ingress(host.config, tool.layout.unit.read_bytes())
                elif kind == 'sqlite3':
                    target = __import__('shlex').split(args[1])[1]
                    with sqlite3.connect(args[0]) as db, sqlite3.connect(target) as backup: db.backup(backup)
                elif kind in ('npm', 'alembic'):
                    trigger = 'build' if kind == 'npm' and args == ['run', 'build'] else ('migration' if kind == 'alembic' and args == ['upgrade', 'head'] else None)
                    if trigger and state['failure'] == trigger:
                        state['failure'] = None
                        if trigger == 'migration':
                            with sqlite3.connect(database) as db: db.execute('CREATE TABLE interrupted (id INTEGER)')
                        rc = 1
                    output = '20261025_08 (head)\n' if kind == 'alembic' else ''
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
    def run(change='code', failure=None):
        (source / 'release-marker').write_text('new\n')
        if change == 'unit':
            with (source / 'deploy/work-tracker.service').open('a') as out: out.write('# new incompatible baseline\n')
        if change == 'manifest':
            with (source / 'deploy/config.manifest').open('a') as out: out.write('# changed contract\n')
        git(source, 'add', '.')
        git(source, 'commit', '-qm', 'new local release')
        git(source, 'push', '-q', 'origin', 'main')
        state['failure'] = failure
        result = subprocess.run(['bash', str(app / 'update.sh')], env=os.environ | {'PATH': str(binaries) + ':' + os.environ['PATH']}, capture_output=True, timeout=60)
        return result
    try:
        yield SimpleNamespace(tool=tool, app=app, run=run, state=state, old_commit=old_commit, git=git,
                              database=database, actual=actual, backend=backend)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
        host.control('stop')
        app_control('stop')


from types import SimpleNamespace


@pytest.mark.parametrize('failure', [None, 'build', 'migration', 'restart'])
def test_actual_update_in_active_split_then_full_lan_rollback(updater, failure):
    u = updater
    tool = u.tool
    archive = tool.transaction()[0]
    before = tool.layout.env.read_bytes(), tool.layout.app_unit.read_bytes(), archive['generation'], Path(archive['backup']).read_bytes()
    result = u.run(failure=failure)
    assert result.returncode == (0 if failure is None else 1), result.stdout.decode() + result.stderr.decode()
    assert before == (tool.layout.env.read_bytes(), tool.layout.app_unit.read_bytes(), tool.journal_state()['generation'], Path(archive['backup']).read_bytes())
    assert u.git(u.app, 'rev-parse', 'HEAD') != u.old_commit if failure is None else u.git(u.app, 'rev-parse', 'HEAD') == u.old_commit
    tool.verify()
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
    assert list((tool.layout.boundary / 'updates').glob('*.db'))


@pytest.mark.parametrize('change', ['unit', 'manifest'])
def test_incompatible_incoming_release_refused_before_merge_or_service_stop(updater, change):
    u = updater
    result = u.run(change=change)
    assert result.returncode == 1
    assert u.git(u.app, 'rev-parse', 'HEAD') == u.old_commit
    assert not any(kind == 'systemctl' and args[0] in ('stop', 'restart') for kind, args in u.state['calls'])
    u.tool.verify()
    u.tool.recover(rollback=True)
