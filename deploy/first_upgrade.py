#!/usr/bin/env python3
"""Pinned first LAN upgrade. No migrations here; exec the new staged updater.

The historical updater cannot acquire the new protection retroactively. This
entry is extracted from an operator-reviewed commit, then verifies itself and
all handoff code against that commit. All production paths/identities are fixed.
"""
import argparse
import fcntl
import grp
import importlib.util
import json
import os
from pathlib import Path
import pwd
import re
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time

BASE = '5d974b100a92f0b7aed6aefa9939f0d5383c9cac'
ORIGIN = 'https://github.com/llit47/work-tracker.git'
REFS = {'refs/heads/main', 'refs/heads/feat/phase-9b2-safe-cutover'}
FILES = ('first_upgrade.py', 'update_safety.py', 'update_rollback.sh', 'common.sh', 'env_file.py', 'nginx_provision.py')
SAFE_PATH = '/usr/sbin:/usr/bin:/sbin:/bin'


class Refusal(ValueError): pass


def require(value):
    if not value: raise Refusal('Pierwszy upgrade: odmowa; sprawdź zaufany SHA/ref, layout, guard i runbook. Wartości ukryte.')


def run(args):
    # No inherited Git redirects, Python paths, shell startup files or secrets.
    return subprocess.run(args, env={'PATH': SAFE_PATH, 'LANG': 'C.UTF-8'},
                          check=True, capture_output=True, timeout=120).stdout


def checked(path, directory=False):
    for item in (path, *path.parents):
        info = item.lstat()
        sticky_tmp = item == Path('/tmp') and stat.S_ISDIR(info.st_mode) and info.st_mode & stat.S_ISVTX
        require(info.st_uid == 0 and not stat.S_ISLNK(info.st_mode) and (not info.st_mode & 0o022 or sticky_tmp))
    require(path.is_dir() if directory else path.is_file())


def atomic(path, data, mode=0o600):
    fd, name = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        sync(path.parent)
    finally:
        Path(name).unlink(missing_ok=True)


def sync(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try: os.fsync(fd)
    finally: os.close(fd)


class Layout:
    app = Path('/opt/work-tracker')
    env = Path('/etc/work-tracker/work-tracker.env')
    database = Path('/var/lib/work-tracker/work_tracker.db')
    unit = Path('/etc/systemd/system/work-tracker.service')
    lock = Path('/run/work-tracker-deploy.lock')
    backups = Path('/var/backups/work-tracker')
    state = env.parent / 'update-state.json'
    guard = unit.with_name('work-tracker.service.d') / '90-work-tracker-update-safety.conf'


class FirstUpgrade:
    def __init__(self): self.layout = Layout()

    def git(self, *args):
        return run(['git', '-c', 'core.hooksPath=/dev/null', '-c', 'core.fsmonitor=false',
                    '-c', 'protocol.ext.allow=never', '-C', str(self.layout.app), *args])

    def blob(self, sha, name): return self.git('show', f'{sha}:{name}')

    def repository(self, expected_head):
        l = self.layout
        checked(l.app, True)
        checked(l.app / '.git', True)
        for name in ('config', 'HEAD', 'index'): checked(l.app / '.git' / name)
        # Later shell Git commands must not run administrator hooks/filters or
        # rewrite the verified origin. Refuse rather than modify Git config.
        config = run(['git', '-C', str(l.app), 'config', '--list']).decode().splitlines()
        require(not any(re.match(r'(?:url\.|filter\.|include|core\.(?:hookspath|fsmonitor)|remote\.origin\.(?:pushurl|uploadpack))', key, re.I)
                        for key in config))
        require(not any(path.is_file() and not path.name.endswith('.sample') for path in (l.app / '.git/hooks').glob('*')))

        require(self.git('rev-parse', '--show-toplevel').decode().strip() == str(l.app))
        require(self.git('remote', 'get-url', 'origin').decode().strip() == ORIGIN)
        require(self.git('rev-parse', 'HEAD').decode().strip() == expected_head)
        require(not self.git('status', '--porcelain', '--untracked-files=all'))
        require(all(line.startswith(b'H ') for line in self.git('ls-files', '-v').splitlines()))
        require(not (l.app / '.git/objects/info/alternates').exists())

    def no_legacy_updater(self):
        # Legacy 5d974b1 does not take our lock. Require an exclusive operator
        # maintenance window and refuse an already running legacy/staged job.
        for path in Path('/proc').glob('[0-9]*/cmdline'):
            if int(path.parent.name) == os.getpid(): continue
            try: argv = path.read_bytes().split(b'\0')
            except FileNotFoundError: continue
            require(not any(value.startswith(b'/tmp/work-tracker-update.') or value == str(self.layout.app / 'update.sh').encode() for value in argv))

    def lock(self):
        checked(self.layout.lock.parent, True)
        fd = os.open(self.layout.lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        require(os.fstat(fd).st_uid == 0 and stat.S_ISREG(os.fstat(fd).st_mode)
                and stat.S_IMODE(os.fstat(fd).st_mode) == 0o600 and os.fstat(fd).st_size == 0)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if fd != 9:
            os.dup2(fd, 9, inheritable=True)
            os.close(fd)
        os.set_inheritable(9, True)

    def inherited_lock(self):
        info = os.fstat(9)
        checked(self.layout.lock)
        require((info.st_dev, info.st_ino) == (self.layout.lock.stat().st_dev, self.layout.lock.stat().st_ino))
        # A fresh descriptor must conflict; FD 9 itself must own that lock.
        with self.layout.lock.open('rb') as other:
            try: fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError: pass
            else: raise Refusal('Brak odziedziczonej blokady update.')
        fcntl.flock(9, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def safety(self, directory):
        spec = importlib.util.spec_from_file_location('first_update_safety', directory / 'deploy/update_safety.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.Safety()

    def database_layout(self):
        # Config/code belong to root; live SQLite and its directory belong to
        # the service identity. Never chmod/chown/delete the DB or sidecars.
        path = self.layout.database
        uid, gid = pwd.getpwnam('work-tracker').pw_uid, grp.getgrnam('work-tracker').gr_gid
        checked(path.parent.parent, True)
        info = path.parent.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == uid and info.st_gid == gid
                and stat.S_IMODE(info.st_mode) in (0o700, 0o750))
        for item in (path, *(path.with_name(path.name + suffix) for suffix in ('-wal', '-shm', '-journal'))):
            if item != path and not item.exists() and not item.is_symlink(): continue
            info = item.lstat()
            require(stat.S_ISREG(info.st_mode) and info.st_uid == uid and info.st_gid == gid
                    and stat.S_IMODE(info.st_mode) in (0o600, 0o640))

    def effective_guard(self, safety):
        safety.install()  # exact content, owner, mode and stable checker
        shown = run(['systemctl', 'show', '-p', 'ExecStartPre', '--value', 'work-tracker.service']).decode().strip()
        require(re.fullmatch(r'\{ path=/usr/bin/python3 ; argv\[\]=/usr/bin/python3 -I /etc/work-tracker/update-safety.py allow-start ; ignore_errors=no ; [^{}]*\}', shown))
        paths = run(['systemctl', 'show', '-p', 'DropInPaths', '--value', 'work-tracker.service']).decode().split()
        require(paths == [str(self.layout.guard)])
        require(run(['systemctl', 'show', '-p', 'NeedDaemonReload', '--value', 'work-tracker.service']).strip() == b'no')

    def prepare(self, sha, ref, timeout):
        l = self.layout
        require(re.fullmatch('[0-9a-f]{40}', sha) and ref in REFS and 5 <= timeout <= 300)
        self.repository(BASE)
        self.lock()
        self.repository(BASE)
        self.no_legacy_updater()
        require(re.search(r'^ID="?(?:debian|ubuntu)"?$', Path('/etc/os-release').read_text(), re.M))
        require(run(['systemctl', 'show', '-p', 'FragmentPath', '--value', 'work-tracker.service']).decode().strip() == str(l.unit))
        require(not l.state.exists() and not l.state.is_symlink())
        require(not l.guard.parent.exists() or not list(l.guard.parent.iterdir()))
        if l.guard.parent.exists(): checked(l.guard.parent, True)
        require(not (l.env.parent / 'update-safety.py').exists() and not (l.env.parent / 'update-safety.py').is_symlink())
        for path in (l.env, l.unit): checked(path)
        self.database_layout()
        require(l.unit.read_bytes() == self.blob(BASE, 'deploy/work-tracker.service'))
        require(run(['systemctl', 'is-active', 'work-tracker.service']).strip() == b'active')
        self.git('fetch', '--no-tags', 'origin', ref)
        require(self.git('rev-parse', 'FETCH_HEAD').decode().strip() == sha and sha != BASE)
        self.git('merge-base', '--is-ancestor', BASE, sha)
        require(self.blob(sha, 'deploy/first_upgrade.py') == Path(__file__).read_bytes())
        # Stage guard/checker support first. No checkout/dependency mutation yet.
        if not l.backups.exists():
            checked(l.backups.parent, True)
            l.backups.mkdir(mode=0o700)
            sync(l.backups.parent)
        checked(l.backups, True)
        directory = Path(tempfile.mkdtemp(prefix='update-first-', dir=l.backups))
        sync(l.backups)
        (directory / 'deploy').mkdir(mode=0o700)
        sync(directory)
        for name in FILES: atomic(directory / 'deploy' / name, self.blob(sha, 'deploy/' + name), 0o700 if name == 'first_upgrade.py' else 0o600)
        safety = self.safety(directory)
        require(self.blob(sha, 'deploy/work-tracker.service') == l.unit.read_bytes())
        # Validate the actual old LAN contract using verified target validators.
        sys.path.insert(0, str(directory / 'deploy'))
        import nginx_provision as p
        from env_file import parse
        p.ROOT = l.app
        layout = p.Layout()
        layout.app_unit, layout.dropins, layout.env = l.unit, l.guard.parent, l.env
        host = p.Host()
        try:
            host.app_layout_ready(layout)
            host.app_permissions(layout)
            env = parse(l.env.read_bytes())
            require(env.get('INGRESS_PROFILE', 'lan') == 'lan' and env.get('DATABASE_URL') == 'sqlite:////var/lib/work-tracker/work_tracker.db')
            host.app_config_preserved(env)
            host.lan_ready(dict(lan_ip='127.0.0.1' if env['APP_HOST'] == '0.0.0.0' else env['APP_HOST'], ha_port=int(env['APP_PORT'])))
        except p.Refusal:
            raise Refusal('Preflight istniejącego LAN odmówił; brak zmian aplikacji/DB.') from None
        # Preserve original bytes privately, never print config or tokens.
        atomic(directory / 'env.before', l.env.read_bytes())
        atomic(directory / 'unit.before', l.unit.read_bytes())
        with sqlite3.connect(l.database.as_uri() + '?mode=ro', uri=True) as source, sqlite3.connect(directory / 'online.db') as backup:
            source.backup(backup)
        os.chmod(directory / 'online.db', 0o600)
        with (directory / 'online.db').open('rb') as stream: os.fsync(stream.fileno())
        sync(directory)
        record = dict(version=1, phase='preparing', previous_commit=BASE, candidate_commit=sha,
                      directory=str(directory), owner=safety.identity(os.getpid()))
        safety.write(record)  # durable BEFORE first guard installation
        safety.install_bootstrap()
        run(['systemctl', 'daemon-reload'])
        self.effective_guard(safety)
        # Only now stage the executable updater. Keep PID + lock across exec.
        fd, name = tempfile.mkstemp(prefix='work-tracker-update.', dir='/tmp')
        os.close(fd)
        updater = Path(name)
        atomic(updater, self.blob(sha, 'update.sh'), 0o700)
        atomic(directory / 'handoff.json', (json.dumps(dict(version=1, target=sha, ref=ref, updater=str(updater), timeout=timeout)) + '\n').encode())
        # Retire the old entry before handoff. No unprotected target checkout:
        # state and effective stable guard are already durable and verified.
        self.git('merge', '--ff-only', sha)
        self.no_legacy_updater()
        return directory, updater

    def accept(self, directory, updater, owner):
        l = self.layout
        require(directory.parent == l.backups)
        checked(directory, True)
        self.inherited_lock()
        for path in (directory / 'handoff.json', directory / 'deploy/first_upgrade.py'): checked(path)
        context = json.loads((directory / 'handoff.json').read_bytes())
        require(set(context) == {'version', 'target', 'ref', 'updater', 'timeout'} and context['version'] == 1)
        require(isinstance(context['target'], str) and re.fullmatch('[0-9a-f]{40}', context['target']))
        require(context['ref'] in REFS and str(updater) == context['updater'] and updater.parent == Path('/tmp') and updater.name.startswith('work-tracker-update.'))
        self.repository(context['target'])
        for name in FILES:
            path = directory / 'deploy' / name
            checked(path)
            require(path.read_bytes() == self.blob(context['target'], 'deploy/' + name))
        checked(updater)
        require(updater.read_bytes() == self.blob(context['target'], 'update.sh'))
        safety = self.safety(directory)
        record = safety.read()
        require(record['phase'] == 'preparing' and record['previous_commit'] == BASE and record['candidate_commit'] == context['target']
                and record['directory'] == str(directory) and record['owner'] == safety.identity(owner))
        self.effective_guard(safety)
        require(l.env.read_bytes() == (directory / 'env.before').read_bytes() and l.unit.read_bytes() == (directory / 'unit.before').read_bytes())
        return context['target']

    def verify_lan(self, directory, timeout):
        # Runs only after FF and Alembic. Exact config/unit preservation and
        # runtime checks once; only refused/timed-out HTTP is retried.
        sys.path.insert(0, str(self.layout.app / 'deploy'))
        import nginx_provision as p
        from env_file import parse
        l = self.layout
        require(l.env.read_bytes() == (directory / 'env.before').read_bytes() and l.unit.read_bytes() == (directory / 'unit.before').read_bytes())
        env = parse(l.env.read_bytes())
        require(env.get('INGRESS_PROFILE', 'lan') == 'lan')
        host = p.Host()
        layout = p.Layout()
        layout.app_unit, layout.dropins, layout.env = l.unit, l.guard.parent, l.env
        try:
            host.app_layout_ready(layout)
            host.app_permissions(layout)
            config = dict(lan_ip='127.0.0.1' if env['APP_HOST'] == '0.0.0.0' else env['APP_HOST'], ha_port=int(env['APP_PORT']))
            deadline = time.monotonic() + timeout
            host._probe_deadline = deadline
            try:
                while True:
                    try:
                        require(host.request(config['lan_ip'], config['ha_port'], config['lan_ip'], '/api/health') == 200)
                        break
                    except (ConnectionRefusedError, TimeoutError):
                        require(time.monotonic() < deadline)
                        time.sleep(min(.1, max(0, deadline - time.monotonic())))
            finally: host._probe_deadline = None
            host.lan_ready(config)
            host.app_config_preserved(env)
        except p.Refusal:
            raise Refusal('Końcowa walidacja LAN odmówiła; brak commit transakcji.') from None


def main():
    parser = argparse.ArgumentParser(description='Pierwszy upgrade LAN z przypiętego commita; nigdy stary staged updater.')
    parser.add_argument('operation', choices=('start', 'accept', 'verify'))
    parser.add_argument('--expected-sha')
    parser.add_argument('--ref', default='refs/heads/main')
    parser.add_argument('--directory', type=Path)
    parser.add_argument('--updater', type=Path)
    parser.add_argument('--owner', type=int)
    parser.add_argument('--readiness-timeout', type=int, default=60)
    args = parser.parse_args()
    try:
        require(os.geteuid() == 0 and 5 <= args.readiness_timeout <= 300)
        os.umask(0o077)
        tool = FirstUpgrade()
        if args.operation == 'start':
            directory, updater = tool.prepare(args.expected_sha, args.ref, args.readiness_timeout)
            os.execve('/bin/bash', ['bash', str(updater), '--first-upgrade', str(directory), '--readiness-timeout', str(args.readiness_timeout)],
                      {'PATH': SAFE_PATH, 'LANG': 'C.UTF-8', 'WORK_TRACKER_STAGED_UPDATER': str(updater)})
        elif args.operation == 'accept': print(BASE, tool.accept(args.directory, args.updater, args.owner))
        else: tool.verify_lan(args.directory, args.readiness_timeout)
        return 0
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError):
        print('FIRST UPGRADE REFUSED / RECOVERY REQUIRED: zachowaj SQLite/WAL/SHM i backupy. Nie uruchamiaj starego updatera; patrz runbook 9B2. Wartości ukryte.', file=sys.stderr)
        return 1


if __name__ == '__main__': raise SystemExit(main())
