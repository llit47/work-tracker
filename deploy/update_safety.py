#!/usr/bin/env python3
"""Durable update stop gate, stdlib only. NEVER restores or writes SQLite.

A live updater may start the candidate only in verification phase. Unknown
state, a dead/reused PID, a reboot or a failed migration require manual repair.
The small staged copy remains usable after a pre-migration Git rollback.
"""
import argparse
import json
import os
from pathlib import Path
import re
import stat
import sys
import uuid

GUARD_NAME = '90-work-tracker-update-safety.conf'
GUARD = (b'[Service]\nExecStartPre=!/usr/bin/python3 '
         b'/opt/work-tracker/deploy/update_safety.py allow-start\n')
BOOTSTRAP_GUARD = (b'[Service]\nExecStartPre=!/usr/bin/python3 -I '
                   b'/etc/work-tracker/update-safety.py allow-start\n')


class Refusal(ValueError):
    pass


def require(value, message):
    if not value:
        raise Refusal(message)


def checked(path, uid=0, boundary=Path('/'), private=False, directory=False):
    require(path.is_absolute() and path == Path(os.path.abspath(path)), 'Nieprawidłowa ścieżka stanu update.')
    for item in (path, *path.parents):
        info = item.lstat()
        require(not stat.S_ISLNK(info.st_mode) and info.st_uid == uid and not info.st_mode & 0o022,
                'Niezaufane uprawnienia/właściciel stanu update.')
        if item == boundary: break
    info = path.stat()
    require(stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode), 'Nieprawidłowy typ stanu update.')
    require(not private or stat.S_IMODE(info.st_mode) == 0o600, 'Prywatny stan update wymaga 0600.')


def sync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try: os.fsync(fd)
    finally: os.close(fd)


def atomic(path, data, mode=0o600):
    temporary = path.with_name('.' + path.name + '.' + uuid.uuid4().hex)
    try:
        with temporary.open('xb') as stream:
            os.chmod(temporary, mode)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        sync_dir(path.parent)
    finally: temporary.unlink(missing_ok=True)


def stable_owned(path, *, uid=0, boundary=Path('/')):
    checked(path, uid, boundary)
    require(stat.S_IMODE(path.stat().st_mode) == 0o644 and path.read_bytes() == Path(__file__).read_bytes(),
            'Nieznana wersja/treść trwałego checkera; wymagana jawna analiza kompatybilności.')


def guard_owned(path, *, uid=0, boundary=Path('/'), checker=Path('/etc/work-tracker/update-safety.py')):
    if not path.exists() and not path.is_symlink(): return False
    checked(path, uid, boundary)
    require(path.read_bytes() in (GUARD, BOOTSTRAP_GUARD) and stat.S_IMODE(path.stat().st_mode) == 0o644, 'Obcy/zmieniony guard update.')
    if path.read_bytes() == BOOTSTRAP_GUARD: stable_owned(checker, uid=uid, boundary=boundary)
    return True


class Layout:
    uid = 0
    boundary = Path('/')
    state = Path('/etc/work-tracker/update-state.json')
    guard = Path('/etc/systemd/system/work-tracker.service.d') / GUARD_NAME
    backups = Path('/var/backups/work-tracker')
    proc = Path('/proc')


class Safety:
    def __init__(self, layout=None): self.layout = layout or Layout()

    def check(self, path, **kwargs): checked(path, self.layout.uid, self.layout.boundary, **kwargs)

    def install(self):
        path = self.layout.guard
        if guard_owned(path, uid=self.layout.uid, boundary=self.layout.boundary, checker=self.layout.state.parent / 'update-safety.py'):
            if path.read_bytes() == BOOTSTRAP_GUARD: self.stable_checker()
            return
        if not path.parent.exists():
            self.check(path.parent.parent, directory=True)
            path.parent.mkdir(mode=0o755)
            path.parent.chmod(0o755)
            sync_dir(path.parent.parent)
        self.check(path.parent, directory=True)
        atomic(path, GUARD, 0o644)

    def stable_checker(self):
        path = self.layout.state.parent / 'update-safety.py'
        stable_owned(path, uid=self.layout.uid, boundary=self.layout.boundary)

    def install_bootstrap(self):
        # Journal/backup already durable. The checker must survive Git reset,
        # partial merge and power loss; it cannot depend on the old checkout.
        path = self.layout.state.parent / 'update-safety.py'
        if path.exists() or path.is_symlink(): self.stable_checker()
        else: atomic(path, Path(__file__).read_bytes(), 0o644)
        self.install()
        atomic(self.layout.guard, BOOTSTRAP_GUARD, 0o644)

    def read(self):
        self.check(self.layout.state, private=True)
        def unique(pairs):
            result = {}
            for key, value in pairs:
                require(key not in result, 'Powtórzony klucz stanu update.')
                result[key] = value
            return result
        value = json.loads(self.layout.state.read_bytes(), object_pairs_hook=unique)
        require(isinstance(value, dict) and set(value) == {'version', 'phase', 'previous_commit', 'candidate_commit', 'directory', 'owner'}, 'Nieznany format stanu update.')
        require(value['version'] == 1 and value['phase'] in ('preparing', 'migrating', 'verifying', 'manual'), 'Nieznana faza update.')
        require(all(isinstance(value[key], str) and re.fullmatch('[0-9a-f]{40}', value[key]) for key in ('previous_commit', 'candidate_commit')), 'Nieprawidłowy commit stanu update.')
        directory = Path(value['directory'])
        require(directory.parent == self.layout.backups, 'Nieprawidłowy katalog update.')
        self.check(directory, directory=True)
        require(isinstance(value['owner'], dict) and set(value['owner']) == {'pid', 'start', 'boot'}, 'Nieprawidłowy właściciel transakcji.')
        return value

    def identity(self, pid):
        require(isinstance(pid, int) and pid > 1, 'Nieprawidłowy PID updatera.')
        # Field 22, accounting for spaces/parentheses in the comm field.
        fields = (self.layout.proc / str(pid) / 'stat').read_text().rsplit(')', 1)[1].split()
        require(fields[0] != 'Z', 'Updater zakończył pracę.')
        return dict(pid=pid, start=fields[19], boot=(self.layout.proc / 'sys/kernel/random/boot_id').read_text().strip())

    def alive(self, record):
        try: return self.identity(record['owner']['pid']) == record['owner']
        except (OSError, ValueError, KeyError, TypeError, IndexError): return False

    def clear_required(self):
        require(not self.layout.state.exists() and not self.layout.state.is_symlink(),
                'UPDATE REQUIRES MANUAL RECOVERY: zachowaj bieżącą SQLite i backupy; sprawdź runbook przed startem/aktualizacją.')

    def write(self, record):
        data = (json.dumps(record, sort_keys=True, indent=2) + '\n').encode()
        self.check(self.layout.state.parent, directory=True)
        # The archive is diagnostic; only the root-owned state authorizes start.
        atomic(Path(record['directory']) / 'transaction.json', data)
        atomic(self.layout.state, data)

    def begin(self, previous, candidate, directory, owner):
        self.clear_required()
        require(all(re.fullmatch('[0-9a-f]{40}', item) for item in (previous, candidate)), 'Nieprawidłowy commit update.')
        require(directory.parent == self.layout.backups, 'Katalog update poza backupami.')
        self.check(directory, directory=True)
        self.install()
        self.write(dict(version=1, phase='preparing', previous_commit=previous, candidate_commit=candidate,
                        directory=str(directory), owner=self.identity(owner)))

    def transition(self, phase):
        record = self.read()
        require(self.alive(record), 'Brak żywego właściciela update; wymagana interwencja.')
        allowed = {'migrating': 'preparing', 'verifying': 'migrating', 'commit': 'verifying', 'cancel': 'preparing'}
        require(phase in allowed and record['phase'] == allowed[phase], 'Niedozwolone przejście update.')
        if phase in ('commit', 'cancel'):
            atomic(Path(record['directory']) / 'transaction.json', (json.dumps(record | {'phase': phase}, sort_keys=True) + '\n').encode())
            self.layout.state.unlink()
            sync_dir(self.layout.state.parent)
        else: self.write(record | {'phase': phase})

    def hold(self):
        record = self.read()
        self.write(record | {'phase': 'manual'})
        self.install()

    def allow_start(self):
        if not self.layout.state.exists() and not self.layout.state.is_symlink(): return
        record = self.read()
        require(record['phase'] == 'verifying' and self.alive(record),
                'UPDATE REQUIRES MANUAL RECOVERY: start zablokowany; bieżąca SQLite nie została cofnięta.')

    def remove_legacy_guard(self, app_dir):
        # An old release may not contain this checker. Only after a successful
        # pre-migration rollback/cancel may its known guard be removed.
        self.clear_required()
        if not (app_dir / 'deploy/update_safety.py').is_file() and guard_owned(self.layout.guard, uid=self.layout.uid, boundary=self.layout.boundary, checker=self.layout.state.parent / 'update-safety.py') and self.layout.guard.read_bytes() == GUARD:
            self.layout.guard.unlink()
            sync_dir(self.layout.guard.parent)


def main():
    parser = argparse.ArgumentParser(description='Bezpieczny stop gate aktualizacji; nigdy restore SQLite.')
    parser.add_argument('operation', choices=['check', 'install', 'begin', 'migrating', 'verifying', 'commit', 'cancel', 'hold', 'allow-start', 'remove-legacy-guard'])
    parser.add_argument('--previous')
    parser.add_argument('--candidate')
    parser.add_argument('--directory', type=Path)
    parser.add_argument('--owner-pid', type=int)
    args = parser.parse_args()
    try:
        require(os.geteuid() == 0, 'Operacja wymaga roota.')
        safety = Safety()
        if args.operation == 'check': safety.clear_required()
        elif args.operation == 'install': safety.install()
        elif args.operation == 'begin':
            require(args.owner_pid == os.getppid(), 'Właścicielem musi być proces updatera.')
            safety.begin(args.previous, args.candidate, args.directory, args.owner_pid)
        elif args.operation == 'hold': safety.hold()
        elif args.operation == 'allow-start': safety.allow_start()
        elif args.operation == 'remove-legacy-guard': safety.remove_legacy_guard(Path('/opt/work-tracker'))
        else: safety.transition(args.operation)
        return 0
    except (OSError, ValueError, KeyError, TypeError, IndexError):
        print('UPDATE REQUIRES MANUAL RECOVERY: nie można autoryzować operacji/startu. Zachowaj bieżącą SQLite, WAL/SHM i backupy; patrz runbook 9B2.', file=sys.stderr)
        return 1


if __name__ == '__main__': raise SystemExit(main())
