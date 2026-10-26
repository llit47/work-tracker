#!/usr/bin/env python3
"""Phase 9B2: compose 9B1 generations with a durable, compensating LAN cutover.

No database access, firewall changes, test events or public routing. Fixed CLI
paths; Layout/Host injection is only a Python test seam.
"""
import argparse
import base64
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import time

import nginx_provision as p
from ingress_bootstrap import Bootstrap
from ingress_artifacts import migrate
from env_file import parse as parse_env, EnvError
from update_safety import GUARD_NAME, guard_owned, Layout as SafetyLayout

APP = 'work-tracker.service'
RECOVERY = 'work-tracker-ingress-recovery.service'
CONFIRM = 'AKCEPTUJĘ PRZERWĘ HA'


class Layout(p.Layout):
    deploy_lock = Path('/run/work-tracker-deploy.lock')
    database_url = 'sqlite:////var/lib/work-tracker/work_tracker.db'
    env = Path('/etc/work-tracker/work-tracker.env')
    app_unit = Path('/etc/systemd/system/work-tracker.service')
    dropins = Path('/etc/systemd/system/work-tracker.service.d')
    recovery_unit = Path('/etc/systemd/system') / RECOVERY
    recovery_link = Path('/etc/systemd/system/multi-user.target.wants') / RECOVERY
    ingress_link = Path('/etc/systemd/system/work-tracker.service.wants') / p.SERVICE


def values(data):
    try:
        return parse_env(data)
    except EnvError as error:
        raise p.Refusal(str(error)) from None


def split_env(data, config):
    changes = dict(APP_HOST='127.0.0.1', APP_PORT=str(config['backend_port']),
                   INGRESS_PROFILE='split', BROWSER_ALLOWED_ORIGINS=f"https://{config['public_hostname']}",
                   SESSION_COOKIE_SECURE='true', CORS_ORIGINS='', BROWSER_HSTS_SECONDS='0')
    lines = []
    for line in data.decode().splitlines():
        key = line.split('=', 1)[0]
        lines.append(f'{key}={changes.pop(key)}' if key in changes else line)
    lines.extend(f'{key}={value}' for key, value in changes.items())
    return ('\n'.join(lines) + '\n').encode()


class Cutover:
    def __init__(self, tool=None):
        self.tool = tool or p.Provisioner(Layout())
        self.layout = self.tool.layout
        self.host = self.tool.host
        self.journal = self.layout.state / 'cutover.json'
        self.dropin = self.layout.dropins / '99-work-tracker-ingress.conf'

    def snapshot(self, path):
        self.layout.check(path)
        info = path.stat()
        return dict(data=base64.b64encode(path.read_bytes()).decode(), mode=stat.S_IMODE(info.st_mode), gid=info.st_gid)

    def write(self, path, data, mode=0o644, gid=None):
        self.layout.check(path.parent, directory=True)
        p.atomic(path, data, mode)
        os.chown(path, self.layout.uid, os.getgid() if gid is None else gid)
        with path.open('rb') as stream:
            os.fsync(stream.fileno())

    def recovery_bytes(self, version=2):
        content = (f'[Unit]\nDescription=Recover interrupted Work Tracker ingress transaction\n'
                f'After=local-fs.target\nBefore={APP} {p.SERVICE}\n'
                f'[Service]\nType=oneshot\nRemainAfterExit=yes\nExecStart=/usr/bin/python3 /opt/work-tracker/deploy/ingress_cutover.py recover --boot\n'
                f'UMask=0077\n[Install]\nWantedBy=multi-user.target\n').encode()
        return content if version == 1 else b'# Work Tracker recovery format 2\n' + content

    def install_recovery(self):
        with self.tool.lock():
            unit = self.layout.recovery_unit
            migrate(self.tool, 'recovery-unit', unit, {v: self.recovery_bytes(v) for v in (1, 2)}, 2, 0o644)
            self.link(self.layout.recovery_link, unit)
            self.host.control('daemon-reload')

    def check_recovery(self):
        self.layout.check(self.layout.recovery_unit)
        p.require(self.layout.recovery_unit.read_bytes() in (self.recovery_bytes(1), self.recovery_bytes(2))
                  and self.layout.recovery_link.is_symlink()
                  and os.readlink(self.layout.recovery_link) == str(self.layout.recovery_unit), 'Brak poprawnego boot recovery.')

    def link(self, path, target):
        if not path.parent.exists():
            self.layout.check(path.parent.parent, directory=True)
            path.parent.mkdir(mode=0o755)
            path.parent.chmod(0o755)  # mkdir is filtered by the CLI umask 077.
            p.fsync_dir(path.parent.parent)
        self.layout.check(path.parent, directory=True)
        if path.is_symlink():
            p.require(os.readlink(path) == str(target), 'Obcy link autostartu.')
        else:
            p.require(not path.exists(), 'Obcy plik autostartu.')
            path.symlink_to(target)
            p.fsync_dir(path.parent)

    def unlink(self, path, target):
        if path.is_symlink():
            p.require(os.readlink(path) == str(target), 'Obcy link autostartu: odmowa usunięcia.')
            path.unlink()
            p.fsync_dir(path.parent)
        else:
            p.require(not path.exists(), 'Obcy plik autostartu.')

    def plan(self, evidence=None):
        p.require(not self.journal.exists(), 'Istnieje transakcja: verify, recover albo rollback-lan.')
        generation, directory, config = self.tool.prepared()
        self.tool.conflicts(config)
        p.require(not (self.layout.state / 'active.json').exists() and not self.layout.unit.exists()
                  and not self.layout.unit.is_symlink() and not self.host.active(), 'Najpierw cofnij wcześniejszy ingress 9B1.')
        p.require(not self.dropin.exists() and not self.dropin.is_symlink(), 'Istniejący drop-in ingress.')
        self.layout.check(self.layout.env)
        env = values(self.layout.env.read_bytes())
        p.require(env.get('INGRESS_PROFILE', 'lan') == 'lan', 'Migracja wymaga działającego LAN.')
        p.require(env.get('APP_HOST') in ('0.0.0.0', config['lan_ip'])
                  and env.get('APP_PORT') == str(config['ha_port']), 'Dotychczasowy adres i port HA muszą zostać zachowane.')
        p.require(env.get('DATABASE_URL') == self.layout.database_url, 'Niezgodna ścieżka DB.')
        self.layout.check(self.layout.app_unit)
        # A vendor unit or an external/runtime drop-in would escape our backup.
        self.host.app_layout_ready(self.layout)
        self.host.app_permissions(self.layout)
        self.host.app_config_preserved(env)
        drops = {}
        if self.layout.dropins.exists():
            self.layout.check(self.layout.dropins, directory=True)
            for path in self.layout.dropins.iterdir():
                if path.name == GUARD_NAME:
                    guard_owned(path, uid=self.layout.uid, boundary=self.layout.boundary, checker=self.layout.env.parent / 'update-safety.py')
                    continue
                p.require(path.suffix == '.conf', 'Nieznany plik w katalogu drop-in.')
                drops[path.name] = self.snapshot(path)
        listeners = self.host.listeners()
        p.require(not any(port == config['backend_port'] for _, port, _ in listeners), 'Backend port zajęty, także IPv6.')
        self.host.lan_ready(config)
        if evidence:
            self.tool.evidence(evidence, generation)  # Before any application mutation.
        return generation, directory, config, drops

    def wait(self, check):
        deadline = time.monotonic() + 12
        while True:
            try:
                check()
                return
            except (p.Refusal, OSError):
                if time.monotonic() >= deadline:
                    raise
                time.sleep(.1)

    def require_no_update(self):
        state = getattr(self.layout, 'update_state', SafetyLayout.state)
        p.require(not state.exists() and not state.is_symlink(),
                  'Najpierw rozstrzygnij update-state; nie zmieniaj ingressu przy niepewnej bazie.')

    def activate(self, evidence, confirm, confirm_ha):
        self.require_no_update()
        # Hold both application and 9B1 infrastructure locks across mutations.
        # Recovery is primed first to avoid a systemd dependency waiting on us.
        if self.journal.exists():
            transaction, _ = self.transaction()
            p.require(transaction['phase'] == 'active', 'Istnieje transakcja: recover/rollback-lan.')
            self.verify()
            return
        self.install_recovery()
        # Prime the oneshot BEFORE taking locks and before writing a transaction.
        # App restart requires it; otherwise systemd would wait on our own lock.
        self.host.start_recovery()
        with self.deployment_lock(), self.coordinator_lock(), self.tool.lock():
            self.require_no_update()  # The updater may have held the lock while we waited.
            generation, directory, config, drops = self.plan(evidence)
            confirm(config)
            # Recheck after the operator's window confirmation.
            generation, directory, config, drops = self.plan(evidence)
            self.check_recovery()
            original = self.snapshot(self.layout.env)
            unit_before = self.snapshot(self.layout.app_unit)
            replacement = split_env(self.layout.env.read_bytes(), config)
            dropin = (f'[Unit]\nRequires={RECOVERY}\nAfter={RECOVERY}\n[Service]\nExecStart=\n'
                      f'ExecStart=/opt/work-tracker/backend/.venv/bin/uvicorn app.main:app --host 127.0.0.1 '
                      f'--port {config["backend_port"]} --workers 1 --no-proxy-headers\n').encode()
            backup = self.tool.backup('cutover', dict(env=original, unit=unit_before, dropins=drops))
            transaction = dict(phase='pending', generation=generation, config=config,
                               backup=str(backup), backup_sha=p.digest(backup.read_bytes()),
                               env_sha=p.digest(replacement), dropin_sha=p.digest(dropin))
            p.atomic(self.journal, p.encoded(transaction))  # durable before first mutation
            try:
                self.host.checkpoint('journal')
                if not self.layout.dropins.exists():
                    self.layout.dropins.mkdir(mode=0o755)
                    self.layout.dropins.chmod(0o755)
                    p.fsync_dir(self.layout.dropins.parent)
                self.write(self.dropin, dropin)
                self.host.checkpoint('dropin')
                self.write(self.layout.env, replacement, original['mode'], original['gid'])
                self.host.checkpoint('env')
                self.host.control('daemon-reload')
                self.host.app_control('restart')
                self.host.checkpoint('backend-restart')
                self.wait(lambda: self.host.backend_ready(config))
                self.host.app_config_preserved(values(base64.b64decode(original['data'])))
                self.host.backend_health(config)
                self.host.checkpoint('backend-ready')
                self.tool.activate_locked(evidence)  # All 9B1 TLS/evidence/process/socket gates.
                self.host.checkpoint('nginx-start')
                self.host.private_probes(config)
                self.host.checkpoint('probes')
                confirm_ha(config)  # Real HA peer, invalid tokenless POST: expect 422, never a test event.
                self.tool.postcheck(config, (directory / 'service').read_bytes())
                self.host.private_probes(config)
                self.check_recovery()
                self.verify_unchanged(transaction, dict(env=original, unit=unit_before, dropins=drops))
                self.link(self.layout.ingress_link, self.layout.unit)
                self.host.control('daemon-reload')
                self.host.checkpoint('enabled')
                p.atomic(self.journal, p.encoded(transaction | {'phase': 'active'}))
                self.host.checkpoint('committed')
            except BaseException:
                self.rollback_locked()
                raise

    def deployment_lock(self):
        from contextlib import contextmanager
        import fcntl
        @contextmanager
        def locked():
            path = self.layout.deploy_lock
            self.layout.check(path.parent, directory=True)
            if path.exists() or path.is_symlink():
                self.layout.check(path, private=True)
            with path.open('a') as stream:
                os.chmod(path, 0o600)
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                yield
        return locked()

    def coordinator_lock(self):
        # Provisioner's lock stays authoritative for each Nginx operation.
        # A separate lock is needed to span app restart and those operations.
        from contextlib import contextmanager
        import fcntl
        @contextmanager
        def locked():
            self.layout.mkdir(self.layout.state)
            path = self.layout.state / 'cutover.lock'
            if path.exists() or path.is_symlink():
                self.layout.check(path, private=True)
            with path.open('a') as stream:
                os.chmod(path, 0o600)
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                yield
        return locked()

    def journal_state(self):
        self.layout.check(self.journal, private=True)
        transaction = p.read_json(self.journal)
        p.require(set(transaction) == {'phase', 'generation', 'config', 'backup', 'backup_sha', 'env_sha', 'dropin_sha'},
                  'Nieobsługiwany format journala cutover.')
        p.require(transaction['phase'] in ('pending', 'active', 'rolling-back', 'lan-restored'), 'Nieznana faza cutover.')
        for key in ('generation', 'backup_sha', 'env_sha', 'dropin_sha'):
            p.require(isinstance(transaction[key], str) and re.fullmatch('[0-9a-f]{64}', transaction[key]),
                      'Nieprawidłowy hash w journalu.')
        p.require(isinstance(transaction['config'], dict) and set(transaction['config']) == p.FIELDS,
                  'Nieprawidłowa konfiguracja journala.')
        p.require(isinstance(transaction['backup'], str) and Path(transaction['backup']).parent == self.layout.backups,
                  'Backup poza zarządzanym katalogiem.')
        return transaction

    def transaction(self, transaction=None):
        transaction = self.journal_state() if transaction is None else transaction
        path = Path(transaction['backup'])
        p.require(path.parent == self.layout.backups, 'Backup poza zarządzanym katalogiem.')
        self.layout.check(path, private=True)
        p.require(p.digest(path.read_bytes()) == transaction['backup_sha'], 'Backup transakcji zmieniony.')
        return transaction, p.read_json(path)

    def verify_unchanged(self, transaction, backup):
        # Refuse to overwrite subsequent administrator changes or symlinks.
        self.layout.check(self.layout.app_unit)
        p.require(self.snapshot(self.layout.app_unit) == backup['unit'], 'Unit aplikacji zmieniony od cutoveru.')
        self.layout.check(self.layout.env)
        p.require(p.digest(self.layout.env.read_bytes()) in
                  (transaction['env_sha'], p.digest(base64.b64decode(backup['env']['data']))), 'Env zmieniony: inspekcja operatora.')
        if self.dropin.exists() or self.dropin.is_symlink():
            self.layout.check(self.dropin)
            p.require(p.digest(self.dropin.read_bytes()) == transaction['dropin_sha'], 'Drop-in zmieniony.')
        if self.layout.dropins.exists():
            guard_owned(self.layout.dropins / GUARD_NAME, uid=self.layout.uid, boundary=self.layout.boundary, checker=self.layout.env.parent / 'update-safety.py')
            current = {path.name: self.snapshot(path) for path in self.layout.dropins.iterdir() if path != self.dropin and path.name != GUARD_NAME}
            p.require(current == backup['dropins'], 'Obce drop-in zmienione: odmowa nadpisania.')

    def rollback_locked(self, boot=False):
        self.require_no_update()
        if not self.journal.exists():
            return
        transaction, backup = self.transaction()
        self.verify_unchanged(transaction, backup)
        p.atomic(self.journal, p.encoded(transaction | {'phase': 'rolling-back'}))
        self.unlink(self.layout.ingress_link, self.layout.unit)
        if boot:
            # Runs Before both services. Never wait for their queued start jobs.
            # 9B1 rollback has no active process at boot; refuse unexpected ones.
            p.require(not self.host.active(), 'Recovery boot wymaga nieaktywnego ingressu.')
        self.tool.rollback_locked(boot=boot)
        self.host.checkpoint('rollback-ingress')
        if not boot:
            self.host.app_control('stop')
        original = backup['env']
        self.write(self.layout.env, base64.b64decode(original['data']), original['mode'], original['gid'])
        self.dropin.unlink(missing_ok=True)
        p.fsync_dir(self.layout.dropins if self.layout.dropins.exists() else self.layout.dropins.parent)
        self.host.checkpoint('rollback-files')
        self.host.control('daemon-reload')
        if boot:
            # Keep journal until a later command verifies LAN health. Backend
            # will start with restored files after this ordered oneshot exits.
            p.atomic(self.journal, p.encoded(transaction | {'phase': 'lan-restored'}))
            return
        self.host.app_control('restart')
        self.wait(lambda: self.host.lan_ready(transaction['config']))
        self.host.checkpoint('rollback-health')
        self.tool.backup('cutover-restored', {'backup': transaction['backup'], 'health_verified': True})
        self.journal.unlink()
        p.fsync_dir(self.journal.parent)

    def recover(self, boot=False, rollback=False):
        with self.deployment_lock(), self.coordinator_lock(), self.tool.lock():
            if not self.journal.exists():
                return
            transaction = self.journal_state()
            if transaction['phase'] == 'active' and not rollback:
                # Availability of a committed service does not depend on an
                # archive. Restoration still requires its full validation.
                try:
                    self.transaction(transaction)
                except (p.Refusal, OSError, ValueError, KeyError, TypeError):
                    print('ALARM: backup cutover niedostępny lub uszkodzony; automatyczny rollback NIEDOSTĘPNY. '
                          'Aktywny split pozostaje bez zmian. Sprawdź verify i prywatne backupy.', file=sys.stderr)
                return
            self.rollback_locked(boot)

    def check_update(self, manifest_sha, incoming=False):
        """Read-only hook; updater already owns the shared deployment lock.

        A split update cannot rebase an archived LAN unit or silently migrate a
        manifest/generation. Reject incompatible releases before merge/restart.
        """
        p.require(isinstance(manifest_sha, str) and re.fullmatch('[0-9a-f]{64}', manifest_sha), 'Brak kontraktu manifestu update.')
        transaction, backup = self.transaction()
        p.require(transaction['phase'] == 'active', 'Najpierw recover niedokończonego cutoveru.')
        self.verify_unchanged(transaction, backup)
        self.check_recovery()
        self.tool.prepared()
        p.require(not list(self.layout.state.glob('*-migration.json')), 'Najpierw dokończ migrację własnych artefaktów.')
        def content(name):
            if incoming:
                return p.execute(['git', '-C', str(p.ROOT), 'show', f'origin/main:{name}'])
            return (p.ROOT / name).read_bytes()
        p.require(content('deploy/work-tracker.service') == self.layout.app_unit.read_bytes(),
                  'Update split zmienia bazowy unit: wymagany jawny powrót do LAN przed aktualizacją.')
        p.require(p.digest(content('deploy/config.manifest')) == manifest_sha,
                  'Update split zmienia manifest: wymagany jawny powrót do LAN przed aktualizacją.')
        for name in ('nginx.conf.example', 'work-tracker-ingress.service.example'):
            p.require(content('deploy/ingress/' + name) == (p.ROOT / 'deploy/ingress' / name).read_bytes(),
                      'Update split zmienia wzorce generacji: wymagany jawny powrót do LAN.')

    def verify_configuration(self):
        transaction, _ = self.transaction()
        p.require(transaction['phase'] == 'active', 'Niedokończona transakcja: recover.')
        p.require(self.tool.prepared()[0] == transaction['generation'], 'Wskaźnik generacji zmieniony.')
        p.require(p.digest(self.layout.env.read_bytes()) == transaction['env_sha'] and self.dropin.exists(),
                  'Aktywna transakcja wymaga env/drop-in split.')
        self.check_recovery()
        active = p.read_json(self.layout.state / 'active.json')
        p.require(active['phase'] == 'active' and active['generation'] == transaction['generation'], 'Niezgodny dziennik 9B1.')
        self.verify_unchanged(*self.transaction())
        p.require(self.layout.ingress_link.is_symlink()
                  and os.readlink(self.layout.ingress_link) == str(self.layout.unit), 'Brak autostartu ingress.')
        return transaction

    def verify(self):
        transaction = self.verify_configuration()
        self.tool.postcheck(transaction['config'], self.tool.unit(self.layout.state / 'generations' / transaction['generation']))
        self.host.private_probes(transaction['config'])
        p.require(self.layout.ingress_link.is_symlink()
                  and os.readlink(self.layout.ingress_link) == str(self.layout.unit), 'Brak autostartu ingress.')

    def update_verify(self, timeout=60):
        p.require(type(timeout) is int and 5 <= timeout <= 300, 'Readiness timeout musi wynosić 5..300 sekund.')
        transaction = self.verify_configuration()  # strict static/TLS/ownership checks, once before waiting
        deadline = time.monotonic() + timeout
        while True:
            try:
                self.host.update_readiness(transaction['config'], deadline=deadline)
                break
            except p.Pending:
                if time.monotonic() >= deadline:
                    raise p.Refusal(f'Timeout gotowości split po {timeout} s; aktualizacja wymaga inspekcji.') from None
                time.sleep(min(0.1, max(0, deadline - time.monotonic())))
        self.verify()  # full final security checks, never retried


def terminate(*_):
    raise p.Refusal('Przerwano SIGTERM.')


def confirmation(config):
    print('UWAGA: restart backendu zwolni dotychczasowy port HA. Webhook w tej przerwie może zaginąć.')
    print('HA nie musi ponawiać żądań. Wymagane okno bez wejść/wyjść, konsola i plan cofnięcia.')
    with open('/dev/tty', 'r+') as tty:
        tty.write(f'Wpisz {CONFIRM}, aby rozpocząć: ')
        tty.flush()
        p.require(tty.readline().strip() == CONFIRM, 'Nie potwierdzono okna serwisowego.')


def ha_confirmation(config):
    print('Z rzeczywistego hosta HA sprawdź oba dotychczasowe POST webhooków: JSON {}, BEZ tokenu i cookie.')
    print('Oczekiwane 422 (walidacja danych, nie 403/502). Content-Type: application/json. Nigdy poprawny event testowy.')
    with open('/dev/tty', 'r+') as tty:
        tty.write('Po potwierdzeniu obu odpowiedzi 422 wpisz HA 422 OK: ')
        tty.flush()
        p.require(tty.readline().strip() == 'HA 422 OK', 'Nie potwierdzono rzeczywistej ścieżki HA.')


def configure(tool):
    """Collect explicit operator input, never fabricate evidence or TLS assets."""
    path = Path('/etc/work-tracker/ingress-input')
    tool.layout.mkdir(path)
    target = path / 'provision.json'
    p.require(not target.exists() and not target.is_symlink(), 'Wejście już istnieje: użyj prepare --config.')
    config = {}
    for field in sorted(p.FIELDS):
        with open('/dev/tty', 'r+') as tty:
            tty.write(f'{field} (wartość operatora, bez domyślnej): ')
            tty.flush()
            value = tty.readline().strip()
        p.require(bool(value), 'Wymagane wszystkie parametry. TLS i CA przygotuj osobno.')
        config[field] = int(value) if field.endswith('_port') else value
    # Validate in a private input file; invalid input is never activated.
    p.atomic(target, p.encoded(config))
    tool.prepare(target)


def main():
    parser = argparse.ArgumentParser(description='Prywatny ingress WT: przygotowanie, kontrolowany cutover i pełny LAN rollback.')
    parser.add_argument('operation', choices=['bootstrap', 'configure', 'prepare', 'plan', 'activate', 'verify', 'recover', 'rollback-lan', 'update-check', 'update-verify'])
    parser.add_argument('--config', type=Path)
    parser.add_argument('--evidence', type=Path)
    parser.add_argument('--readiness-timeout', type=int, default=60)
    parser.add_argument('--manifest-sha')
    parser.add_argument('--incoming', action='store_true')
    parser.add_argument('--boot', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        p.require(os.geteuid() == 0, 'Operacja wymaga roota.')
        release = Path('/etc/os-release').read_text()
        p.require(re.search(r'^ID=(?:"?)(debian|ubuntu)(?:"?)$', release, re.M), 'Wymagany Debian/Ubuntu.')
        p.require(Path('/run/systemd/system').is_dir(), 'Wymagany uruchomiony systemd.')
        os.umask(0o077)
        signal.signal(signal.SIGTERM, terminate)
        cutover = Cutover()
        if args.operation == 'bootstrap':
            Bootstrap(cutover.tool).run()
            with cutover.coordinator_lock():
                cutover.install_recovery()
        elif args.operation == 'configure':
            configure(cutover.tool)
            print(f'Generacja: {cutover.tool.prepared()[0]}')
        elif args.operation == 'prepare':
            p.require(args.config is not None, 'Wymagany --config.')
            generation = cutover.tool.prepare(args.config)
            print(f'Przygotowano nieaktywną generację: {generation}')
        elif args.operation == 'plan':
            generation, _, _, _ = cutover.plan(args.evidence)
            print(f'Plan LAN → split; generacja {generation}. HA URL bez zmian; DB nietknięta.')
        elif args.operation == 'activate':
            p.require(args.evidence is not None, 'Wymagany --evidence.')
            cutover.activate(args.evidence, confirmation, ha_confirmation)
        elif args.operation == 'update-check':
            cutover.check_update(args.manifest_sha, args.incoming)
        elif args.operation == 'update-verify':
            cutover.update_verify(args.readiness_timeout)
        elif args.operation == 'verify':
            cutover.verify()
        else:
            cutover.recover(args.boot, args.operation == 'rollback-lan')
        print('Operacja zakończona. Bez publicznego routingu; brak zmian Cloudflare/firewalla/HA/DB.')
        return 0
    except p.Refusal as error:
        print(f'Odmowa: {error}', file=sys.stderr)
        return 1
    except (OSError, ValueError, KeyError, TypeError, IndexError, subprocess.SubprocessError):
        print('ODMOWA/AWARIA: sprawdź runbook, zależności, TLS, bramki i dziennik; ponów recover/rollback-lan. Bez zrzutu konfiguracji.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
