#!/usr/bin/env python3
"""Offline Nginx preparation and explicit private-ingress lifecycle, stdlib only.

No package installation, application changes, generic nginx reloads or public
routing. CLI paths are fixed; tests inject a Layout/Host without a CLI bypass.
"""
import argparse
import configparser
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import socket
import ssl
import stat
import subprocess
import sys
import tempfile
import time
import uuid

from update_safety import GUARD_NAME, BOOTSTRAP_GUARD, guard_owned

ROOT = Path(__file__).resolve().parents[1]
SERVICE = 'work-tracker-ingress.service'
FIELDS = {'lan_ip', 'browser_port', 'ha_port', 'backend_port', 'public_hostname',
          'origin_tls_name', 'cloudflared_source_ip', 'ha_source_ip',
          'certificate', 'private_key', 'ca_certificate'}
GATES = {'firewall_ipv4_verified', 'firewall_ipv6_verified',
         'source_isolation_verified', 'negative_origin_probes_passed',
         'public_route_disabled', 'console_and_lan_rollback_ready'}


APP_PROPERTIES = {'Type': 'simple', 'User': 'work-tracker', 'Group': 'work-tracker',
              'WorkingDirectory': '/opt/work-tracker/backend',
              'EnvironmentFiles': '/etc/work-tracker/work-tracker.env (ignore_errors=no)',
              'Environment': '', 'PassEnvironment': '', 'UnsetEnvironment': '',
              'RootDirectory': '', 'RootImage': '', 'DynamicUser': 'no',
              'NoNewPrivileges': 'yes', 'PrivateTmp': 'yes', 'ProtectSystem': 'strict',
              'ProtectHome': 'yes', 'ReadWritePaths': '/var/lib/work-tracker',
              'ExecStartPre': '', 'ExecStartPost': '', 'ExecCondition': '', 'ExecStop': '', 'ExecStopPost': ''}


APP_EXEC_START = ['/opt/work-tracker/backend/.venv/bin/uvicorn', 'app.main:app', '--host', '${APP_HOST}',
            '--port', '${APP_PORT}', '--workers', '1', '--no-proxy-headers']

class Refusal(Exception):
    pass


class Pending(Refusal):
    """An absent starting process/socket or incomplete initialization."""


class RuntimeMismatch(Pending):
    """Dynamic argv/env/worker snapshot has not reached the required contract.

    May be inherited pre-exec state. If it persists to the deadline it is an
    invalid runtime, not a successful/merely slow start. Never used for files,
    TLS, policy, master UID or a wrong/foreign listening socket.
    """



def require(condition, message):
    if not condition:
        raise Refusal(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def encoded(value):
    return (json.dumps(value, sort_keys=True, indent=2) + '\n').encode()


def execute(args, *, timeout=30, **kwargs):
    # Never forward subprocess output: OpenSSL/nginx/systemctl may print inputs.
    try:
        return subprocess.run(args, check=True, capture_output=True, timeout=timeout, **kwargs).stdout
    except (OSError, subprocess.SubprocessError):
        raise Refusal(f'Nie powiodła się kontrola/operacja: {Path(args[0]).name}.') from None


def secure_path(path, *, uid=0, private=False, boundary=Path('/'), directory=False):
    require(path.is_absolute() and path == Path(os.path.abspath(path)), 'Wymagana bezwzględna ścieżka bez .. .')
    require(path.is_relative_to(boundary), 'Ścieżka poza dozwolonym katalogiem.')
    for item in [path, *path.parents]:
        info = item.lstat()
        require(not stat.S_ISLNK(info.st_mode), 'Symlinki infrastruktury są niedozwolone.')
        require(info.st_uid == uid and not info.st_mode & 0o022,
                'Infrastruktura wymaga zaufanego właściciela i braku zapisu grupy/innych.')
        if item == boundary:
            break
    info = path.stat()
    require(stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode), 'Nieprawidłowy typ pliku.')
    if private:
        require(stat.S_IMODE(info.st_mode) == 0o600, 'Plik prywatny musi mieć tryb 0600.')
    require(not path.is_relative_to(ROOT), 'Konfiguracja i TLS muszą być poza checkoutem.')


def read_json(path):
    # Reject duplicate keys instead of silently accepting an overridden gate.
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, 'Powtórzony klucz JSON.')
            result[key] = value
        return result
    value = json.loads(path.read_bytes(), object_pairs_hook=unique)
    require(isinstance(value, dict), 'Wymagany obiekt JSON.')
    return value


def atomic(path, data, mode=0o600):
    temporary = path.with_name(f'.{path.name}.{uuid.uuid4().hex}')
    try:
        with temporary.open('xb') as stream:
            os.chmod(temporary, mode)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        fsync_dir(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def fsync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class Layout:
    state = Path('/etc/work-tracker/ingress')
    backups = Path('/var/backups/work-tracker/ingress')
    unit = Path('/etc/systemd/system') / SERVICE
    runtime = Path('/run/work-tracker-ingress')
    nginx = Path('/usr/sbin/nginx')
    uid = 0
    boundary = Path('/')

    def check(self, path, **kwargs):
        secure_path(path, uid=self.uid, boundary=self.boundary, **kwargs)

    def mkdir(self, path):
        # Do not chmod/chown existing objects to paper over unsafe ownership.
        if not path.exists():
            self.check(path.parent, directory=True)
            path.mkdir(mode=0o700)
            fsync_dir(path.parent)
        self.check(path, directory=True)
        require(stat.S_IMODE(path.stat().st_mode) == 0o700, 'Katalog zarządzany wymaga 0700.')


class Host:
    proc = Path('/proc')  # Test kernels may be represented by private fixture files.
    _probe_deadline = None

    def io_timeout(self, maximum):
        if self._probe_deadline is None: return maximum
        remaining = self._probe_deadline - time.monotonic()
        if remaining <= 0: raise Pending('Upłynął czas gotowości usług.')
        return min(maximum, remaining)

    def ingress_pid(self):
        return execute(['systemctl', 'show', '-p', 'MainPID', '--value', SERVICE], timeout=self.io_timeout(30)).decode().strip()

    def local_ip(self, address):
        interfaces = json.loads(execute(['ip', '-j', 'address', 'show']))
        return any(a.get('local') == address for i in interfaces for a in i.get('addr_info', []))

    def bootstrap_listeners(self):
        # iproute2 may not exist yet on a minimal Debian. Read the kernel table
        # before apt, so dependency installation never depends on missing ss.
        result = set()
        for name in ('tcp', 'tcp6'):
            path = self.proc / 'net' / name
            if name == 'tcp6' and not path.exists():
                continue
            for line in path.read_text().splitlines()[1:]:
                parts = line.split()
                require(len(parts) >= 4, 'Nie można odczytać tabeli TCP jądra.')
                if parts[3] == '0A':
                    address, port = parts[1].split(':')
                    if int(port, 16) in (80, 443):
                        result.add((name, address, int(port, 16)))
        return result

    def listeners(self):
        result = []
        for line in execute(['ss', '-H', '-ltnp'], timeout=self.io_timeout(30)).decode().splitlines():
            parts = line.split()
            require(len(parts) >= 5, 'Nie można odczytać listenerów ss.')
            address, port = parts[3].rsplit(':', 1)
            result.append((address.strip('[]'), int(port), line))
        return result

    def active(self):
        result = subprocess.run(['systemctl', 'is-active', SERVICE], capture_output=True, timeout=self.io_timeout(15))
        require(result.returncode in (0, 3, 4), 'Nie można odczytać stanu usługi ingress.')
        return result.returncode == 0

    def control(self, action):
        execute(['systemctl', action, *([] if action == 'daemon-reload' else [SERVICE])])

    def generic_known(self):
        result = subprocess.run(['systemctl', 'show', '-p', 'LoadState', '--value', 'nginx.service'],
                                capture_output=True, timeout=15)
        value = result.stdout.decode().strip()
        require(result.returncode in (0, 1, 4) and value in
                ('not-found', 'loaded', 'masked', 'error', 'bad-setting', 'merged', 'stub'),
                'Nie można potwierdzić braku obcej jednostki Nginx.')
        return value != 'not-found'

    def generic_active(self):
        result = subprocess.run(['systemctl', 'is-active', 'nginx.service'], capture_output=True, timeout=15)
        require(result.returncode in (0, 3, 4), 'Nie można odczytać stanu generic Nginx.')
        return result.returncode == 0

    def start_recovery(self):
        execute(['systemctl', 'start', 'work-tracker-ingress-recovery.service'])

    def app_control(self, action):
        execute(['systemctl', action, 'work-tracker.service'])

    def checkpoint(self, name):
        pass  # Fault injection only through the test Host, never through env/CLI.

    def app_permissions(self, layout):
        import grp
        layout.check(layout.env)
        info = layout.env.stat()
        require(info.st_uid == layout.uid and info.st_gid == grp.getgrnam('work-tracker').gr_gid
                and stat.S_IMODE(info.st_mode) == 0o640, 'Env wymaga root:work-tracker 0640.')

    def app_layout_ready(self, layout):
        fragment = execute(['systemctl', 'show', '-p', 'FragmentPath', '--value', 'work-tracker.service']).decode().strip()
        require(fragment == str(layout.app_unit), 'Unit aplikacji poza obsługiwanym layoutem.')
        paths = execute(['systemctl', 'show', '-p', 'DropInPaths', '--value', 'work-tracker.service']).decode().split()
        require(all(Path(path).parent == layout.dropins for path in paths), 'Drop-in poza backupem; odmowa migracji.')
        # The on-disk launch contract must agree with the loaded unit. Only
        # resource scheduling drop-ins are supported before a first cutover.
        layout.check(layout.app_unit)
        require(layout.app_unit.read_bytes() == (ROOT / 'deploy/work-tracker.service').read_bytes(),
                'Niestandardowy bazowy unit: wymagany osobny plan migracji.')
        if layout.dropins.exists():
            for path in layout.dropins.iterdir():
                layout.check(path)
                if path.name == GUARD_NAME:
                    guard_owned(path, uid=layout.uid, boundary=layout.boundary, checker=layout.env.parent / 'update-safety.py')
                    continue
                parser = configparser.ConfigParser(interpolation=None, strict=True)
                parser.optionxform = str
                try:
                    parser.read_string(path.read_text())
                except configparser.Error:
                    raise Refusal('Nieobsługiwany drop-in systemd.') from None
                require(parser.sections() == ['Service'] and not parser.defaults() and
                        set(parser['Service']) <= {'Nice', 'CPUWeight', 'IOWeight', 'MemoryHigh', 'MemoryMax', 'TasksMax'} and
                        all('\n' not in value and '\\' not in value for value in parser['Service'].values()),
                        'Drop-in zmienia kontrakt uruchomienia; odmowa utraty ustawień administratora.')
        properties = APP_PROPERTIES.copy()
        guarded = guard_owned(layout.dropins / GUARD_NAME, uid=layout.uid, boundary=layout.boundary, checker=layout.env.parent / 'update-safety.py') if layout.dropins.exists() else False
        for key, expected in properties.items():
            actual = execute(['systemctl', 'show', '-p', key, '--value', 'work-tracker.service']).decode().strip()
            if key == 'ExecStartPre' and guarded:
                stable = (layout.dropins / GUARD_NAME).read_bytes() == BOOTSTRAP_GUARD
                checker = '-I /etc/work-tracker/update-safety.py' if stable else '/opt/work-tracker/deploy/update_safety.py'
                launch = re.findall(r'\{ path=([^;]+?) ; argv\[\]=([^;]+?) ; ignore_errors=(yes|no) ;', actual)
                require(len(launch) == 1 and actual.count('{ path=') == 1 and launch[0] ==
                        ('/usr/bin/python3', f'/usr/bin/python3 {checker} allow-start', 'no'),
                        'Nieprawidłowy efektywny guard update.')
                continue
            require(actual == expected, 'Efektywne właściwości systemd poza wspieranym kontraktem.')
        command = execute(['systemctl', 'show', '-p', 'ExecStart', '--value', 'work-tracker.service']).decode().strip()
        launches = re.findall(r'\{ path=([^;]+?) ; argv\[\]=([^;]+?) ; ignore_errors=(yes|no) ;', command)
        expected = APP_EXEC_START
        require(len(launches) == 1 and command.count('{ path=') == 1 and command.endswith('}') and
                launches[0][0] == expected[0] and launches[0][1].split() == expected and launches[0][2] == 'no',
                'Niestandardowy efektywny ExecStart: odmowa usunięcia parametrów administratora.')
        enabled = execute(['systemctl', 'is-enabled', 'work-tracker.service']).decode().strip()
        require(enabled == 'enabled', 'Backend wymaga trwałego autostartu przed migracją.')

    def request(self, address, port, source, target, *, method='GET', headers='', host='localhost', body=''):
        # No token/cookie and no valid event body. Never creates production data.
        end = time.monotonic() + self.io_timeout(3)
        with socket.create_connection((address, port), timeout=self.io_timeout(3), source_address=(source, 0)) as connection:
            request = (f'{method} {target} HTTP/1.1\r\nHost: {host}\r\n{headers}'
                       f'Connection: close\r\nContent-Length: {len(body.encode())}\r\n\r\n{body}')
            remaining = end - time.monotonic()
            if remaining <= 0: raise TimeoutError('HTTP connect timeout')
            connection.settimeout(remaining)
            connection.sendall(request.encode())
            line = bytearray()
            while not line.endswith(b'\r\n'):
                remaining = end - time.monotonic()
                if remaining <= 0: raise TimeoutError('HTTP status timeout')
                connection.settimeout(remaining)
                chunk = connection.recv(1)
                require(chunk and len(line) < 8192, 'Niepełna lub zbyt długa linia statusu HTTP.')
                line.extend(chunk)
            match = re.fullmatch(rb'HTTP/1\.[01] ([1-5][0-9]{2}) [^\r\n]*\r\n', line)
            require(match is not None, 'Nieprawidłowa linia statusu HTTP.')
            return int(match[1])

    def app_config_preserved(self, expected):
        pid = execute(['systemctl', 'show', '-p', 'MainPID', '--value', 'work-tracker.service']).decode().strip()
        require(pid.isdigit() and int(pid) > 1, 'Brak PID aplikacji.')
        environment = dict(v.split('=', 1) for v in (self.proc / pid / 'environ').read_bytes().decode().split('\0') if '=' in v)
        require(all(environment.get(key) == expected.get(key) for key in ('DATABASE_URL', 'WEBHOOK_TOKEN')),
                'Runtime musi zachować DB i token HA.')

    def backend_health(self, config):
        require(self.request('127.0.0.1', config['backend_port'], '127.0.0.2', '/api/health',
                             headers='X-Forwarded-Proto: https\r\n', host=config['public_hostname']) == 200, 'Backend health split nie działa.')

    def private_probes(self, config):
        self.backend_health(config)
        for path in ('/api/webhook/home-assistant', '/api/webhook/home-assistant/correction'):
            require(self.request('127.0.0.1', config['backend_port'], '127.0.0.3', path, method='POST',
                                 headers='Content-Type: application/json\r\n', body='{}') == 422,
                    'Prywatny backend musi odrzucać niepełne dane bez tokenu HA.')
            require(self.request(config['lan_ip'], config['ha_port'], config['lan_ip'], path, method='POST') == 403,
                    'Nginx HA nie odrzuca niezaufanego lokalnego źródła.')

    def lan_ready(self, config):
        pid = execute(['systemctl', 'show', '-p', 'MainPID', '--value', 'work-tracker.service']).decode().strip()
        require(pid.isdigit() and int(pid) > 1, 'Brak PID działającego LAN.')
        process = self.proc / pid
        argv = process.joinpath('cmdline').read_bytes().decode().split('\0')
        for flag, expected in (('--host', None), ('--port', str(config['ha_port'])), ('--workers', '1')):
            require(argv.count(flag) == 1 and argv.index(flag) + 1 < len(argv), 'Proces LAN ma niezgodne argumenty.')
            actual = argv[argv.index(flag) + 1]
            require(actual in ('0.0.0.0', config['lan_ip']) if expected is None else actual == expected,
                    'Proces LAN ma niezgodne parametry.')
        require(argv.count('app.main:app') == 1 and argv.count('--no-proxy-headers') == 1 and '--proxy-headers' not in argv,
                'LAN wymaga jednego Uvicorn bez proxy headers.')
        environment = dict(v.split('=', 1) for v in process.joinpath('environ').read_bytes().decode().split('\0') if '=' in v)
        require(environment.get('INGRESS_PROFILE', 'lan') == 'lan', 'Proces nie działa w profilu LAN.')
        sockets = [s for s in self.listeners() if s[1] == config['ha_port']]
        require(len(sockets) == 1 and sockets[0][0] in ('0.0.0.0', config['lan_ip'])
                and f'pid={pid},' in sockets[0][2], 'Port HA nie należy wyłącznie do backendu LAN.')
        require(self.request(config['lan_ip'], config['ha_port'], config['lan_ip'], '/api/health') == 200,
                'LAN /api/health nie działa.')

    def ingress_permissions(self, pid, *, starting=False):
        import pwd
        process = self.proc / pid
        master = re.search(r'^Uid:\s+(.*)$', (process / 'status').read_text(), re.M)
        require(master is not None and set(master[1].split()) == {'0'}, 'Nginx master wymaga UID root.')
        children = (process / 'task' / pid / 'children').read_text().split()
        worker_uid = str(pwd.getpwnam('www-data').pw_uid)
        if starting and not children: raise Pending('Worker Nginx jeszcze nie istnieje.')
        if starting and len(children) != 1: raise RuntimeMismatch('Liczba workerów Nginx jeszcze niezgodna.')
        require(len(children) == 1, 'Wymagany jeden worker Nginx.')
        worker = re.search(r'^Uid:\s+(.*)$', (self.proc / children[0] / 'status').read_text(), re.M)
        if starting and worker is not None and set(worker[1].split()) <= {'0', worker_uid} and set(worker[1].split()) != {worker_uid}:
            raise RuntimeMismatch('Worker Nginx jeszcze nie zakończył zmiany UID.')
        require(worker is not None and set(worker[1].split()) == {worker_uid}, 'Worker Nginx wymaga www-data.')
        return children[0]

    def tls_probe(self, config, ca):
        context = ssl.create_default_context(cafile=str(ca))
        with socket.create_connection((config['lan_ip'], config['browser_port']), timeout=3) as connection:
            with context.wrap_socket(connection, server_hostname=config['origin_tls_name']):
                pass

    def backend_ready(self, config, *, starting=False):
        pid = execute(['systemctl', 'show', '-p', 'MainPID', '--value', 'work-tracker.service'], timeout=self.io_timeout(30)).decode().strip()
        if starting and pid in ('', '0'): raise Pending('Backend jeszcze nie wystartował.')
        require(pid.isdigit() and int(pid) > 1, 'Backend systemd nie działa.')
        listeners = [s for s in self.listeners() if s[1] == config['backend_port']]
        # A wrong bind/owner is a confirmed violation even before exec settles.
        if listeners:
            require(len(listeners) == 1 and listeners[0][0] == '127.0.0.1' and f'pid={pid},' in listeners[0][2],
                    'Nieprawidłowy lub obcy listener backendu.')
        process = self.proc / pid
        def runtime_require(valid, message):
            if starting and not valid: raise RuntimeMismatch(message)
            require(valid, message)
        def runtime_text(name):
            try: return process.joinpath(name).read_bytes().decode()
            except (UnicodeDecodeError, PermissionError, ProcessLookupError):
                runtime_require(False, 'Niekompletny obraz argv/env procesu.')
        argv = runtime_text('cmdline').split('\0')
        for flag, value in [('--host', '127.0.0.1'), ('--port', str(config['backend_port'])), ('--workers', '1')]:
            runtime_require(argv.count(flag) == 1 and argv.index(flag) + 1 < len(argv) and argv[argv.index(flag) + 1] == value, 'Backend ma niezgodny bind/worker.')
        runtime_require(argv.count('app.main:app') == 1 and argv.count('--no-proxy-headers') == 1 and '--proxy-headers' not in argv,
                'Backend wymaga pojedynczego Uvicorn bez proxy headers.')
        tokens = argv[:-1] if argv[-1:] == [''] else argv
        prefix = tokens[:tokens.index('app.main:app')]
        runtime_require((len(prefix) in (1, 2) and Path(prefix[-1]).name == 'uvicorn') or
                        (len(prefix) == 3 and prefix[-2:] == ['-m', 'uvicorn']), 'Nieprawidłowy launcher Uvicorna.')
        runtime_require(tokens[len(prefix):] == ['app.main:app', '--host', '127.0.0.1', '--port', str(config['backend_port']),
                                               '--workers', '1', '--no-proxy-headers'], 'Nieobsługiwane argumenty Uvicorna.')
        environment = dict(v.split('=', 1) for v in runtime_text('environ').split('\0') if '=' in v)
        defaults = {'CORS_ORIGINS': '', 'BROWSER_HSTS_SECONDS': '0'}
        for key, expected in {'INGRESS_PROFILE': 'split', 'SESSION_COOKIE_SECURE': 'true',
                              'BROWSER_ALLOWED_ORIGINS': f"https://{config['public_hostname']}",
                              'CORS_ORIGINS': '', 'BROWSER_HSTS_SECONDS': '0'}.items():
            runtime_require(environment.get(key, defaults.get(key)) == expected,
                    'Backend nie ma wymaganej konfiguracji split/Secure/origin/HSTS=0.')
        if starting and not listeners: raise Pending('Brak socketu startującego backendu.')
        require(len(listeners) == 1 and listeners[0][0] == '127.0.0.1' and f'pid={pid},' in listeners[0][2],
                'Backend musi słuchać wyłącznie na 127.0.0.1 i należeć do usługi WT.')
        return pid

    def update_readiness(self, config, *, deadline=None):
        """No TLS/ACL/file checks are retried here; final verify handles them."""
        self._probe_deadline = deadline
        try:
            backend = self.backend_ready(config, starting=True)
            self.backend_health(config)
            pid = self.ingress_pid()
            listeners = [s for s in self.listeners() if s[1] in (config['ha_port'], config['browser_port'])]
            require(all(s[0] == config['lan_ip'] and pid not in ('', '0') and f'pid={pid},' in s[2] for s in listeners),
                    'Obcy/nieprawidłowy listener podczas startu ingressu.')
            if not self.active() or pid in ('', '0') or len(listeners) < 2:
                raise Pending('Ingress jeszcze nie otworzył obu listenerów.')
            require(len(listeners) == 2, 'Nadmiarowe listenery ingressu.')
            worker = self.ingress_permissions(pid, starting=True)
            for port in (config['ha_port'], config['browser_port']):
                with socket.create_connection((config['lan_ip'], port), timeout=self.io_timeout(1)): pass
            return backend, pid, worker
        except (FileNotFoundError, ConnectionRefusedError, TimeoutError) as error:
            raise Pending('Usługi jeszcze nie odpowiadają.') from error
        finally:
            self._probe_deadline = None


class Provisioner:
    def __init__(self, layout=None, host=None):
        self.layout = layout or Layout()
        self.host = host or Host()

    def config(self, source):
        self.layout.check(source, private=True)
        config = read_json(source)
        require(set(config) == FIELDS, 'Podaj dokładnie wszystkie klucze provision.json.example.')
        for field in ('lan_ip', 'cloudflared_source_ip', 'ha_source_ip'):
            value = config[field]
            require(isinstance(value, str), 'IP musi być tekstem.')
            address = ipaddress.ip_address(value)
            require(address.version == 4 and any(address in ipaddress.ip_network(n)
                    for n in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16')),
                    '9B1 wymaga konkretnych prywatnych IPv4 RFC1918; IPv6 pozostaje bez listenera.')
            require(str(address) == value, 'Wymagany kanoniczny IP.')
        require(len({config[k] for k in ('lan_ip', 'cloudflared_source_ip', 'ha_source_ip')}) == 3,
                'WT, HA i connector wymagają odrębnych adresów bez wspólnego SNAT.')
        ports = [config[k] for k in ('browser_port', 'ha_port', 'backend_port')]
        require(all(type(p) is int and 1024 <= p <= 65535 for p in ports) and len(set(ports)) == 3,
                'Wymagane trzy różne porty 1024–65535.')
        for field in ('public_hostname', 'origin_tls_name'):
            name = config[field]
            require(isinstance(name, str) and len(name) <= 253 and '.' in name
                    and all(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label) for label in name.split('.'))
                    and not re.fullmatch(r'[0-9.]+', name), 'Wymagany dokładny lowercase DNS hostname bez wildcard/portu.')
        assets = {}
        for field in ('certificate', 'private_key', 'ca_certificate'):
            require(isinstance(config[field], str) and re.fullmatch(r'/[a-zA-Z0-9_./-]+', config[field]),
                    'Niebezpieczna ścieżka TLS.')
            path = Path(config[field])
            self.layout.check(path, private=field == 'private_key')
            assets[field] = path.read_bytes()
            if field != 'private_key':
                require(b'PRIVATE KEY' not in assets[field], 'Certyfikat/CA nie może zawierać klucza prywatnego.')
        self.tls(config)
        self.layout.check(self.layout.nginx)
        require(os.access(self.layout.nginx, os.X_OK), 'Brak wykonywalnego Nginx; zainstaluj zależność w kontrolowany sposób.')
        require(self.host.local_ip(config['lan_ip']), 'LAN IP nie należy do tego hosta.')
        generation = digest(encoded({'config': config, 'assets': {k: digest(v) for k, v in assets.items()},
            'templates': {p: digest((ROOT / 'deploy/ingress' / p).read_bytes()) for p in
                          ('nginx.conf.example', 'work-tracker-ingress.service.example')},
            'tool': digest(Path(__file__).read_bytes())}))
        return config, assets, generation

    def tls(self, config):
        cert, key, ca = (config[f] for f in ('certificate', 'private_key', 'ca_certificate'))
        require(b'ENCRYPTED' not in Path(key).read_bytes(), 'Wymagany niezaszyfrowany klucz; brak interaktywnego pytania o hasło.')
        require('CA:TRUE' in execute(['openssl', 'x509', '-in', ca, '-noout', '-ext', 'basicConstraints']).decode(),
                'Wymagany certyfikat CA z CA:TRUE.')
        sans = execute(['openssl', 'x509', '-in', cert, '-noout', '-ext', 'subjectAltName']).decode()
        require(config['origin_tls_name'] in re.findall(r'DNS:([^,\s]+)', sans), 'Certyfikat wymaga dokładnego DNS SAN originu.')
        execute(['openssl', 'x509', '-in', cert, '-noout', '-checkend', '604800'])
        execute(['openssl', 'verify', '-x509_strict', '-CAfile', ca, '-no-CApath', '-no-CAstore', '-untrusted', cert,
                 '-purpose', 'sslserver', '-verify_hostname', config['origin_tls_name'], cert])
        try:
            ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER).load_cert_chain(cert, key, password=lambda: b'')
        except (ssl.SSLError, OSError):
            raise Refusal('Certyfikat i niezaszyfrowany klucz TLS nie są zgodne/akceptowane.') from None

    def conflicts(self, config, *, activation=False):
        listeners = self.host.listeners()
        occupied = {port for _, port, _ in listeners}
        require(config['browser_port'] not in occupied, 'Port browser jest zajęty (IPv4 lub IPv6).')
        if activation:
            require(config['ha_port'] not in occupied, 'Port HA nadal zajęty; narzędzie nie zatrzyma Uvicorna.')
            self.host.backend_ready(config)
        else:
            backend = [s for s in listeners if s[1] == config['backend_port']]
            require(not backend or all(s[0] == '127.0.0.1' for s in backend), 'Niebezpieczny listener portu backendu.')
        return config['ha_port'] in occupied

    def render(self, config, generation_dir, runtime=None):
        runtime = runtime or self.layout.runtime
        replacements = dict(WORK_TRACKER_LAN_IP=config['lan_ip'], BROWSER_TLS_PORT=config['browser_port'],
            PUBLIC_HOSTNAME=config['public_hostname'], ORIGIN_CERT_PATH=generation_dir / 'origin.pem',
            ORIGIN_KEY_PATH=generation_dir / 'origin.key', CLOUDFLARED_SOURCE_IP=config['cloudflared_source_ip'],
            ORIGIN_TLS_NAME=config['origin_tls_name'], HA_PORT=config['ha_port'], HA_SOURCE_IP=config['ha_source_ip'],
            BACKEND_PORT=config['backend_port'])
        servers = (ROOT / 'deploy/ingress/nginx.conf.example').read_text()
        for key, value in replacements.items():
            servers = servers.replace(f'<{key}>', str(value))
        require(not re.search(r'<[A-Z_]+>', servers), 'Nierozwiązany placeholder Nginx.')
        temporary_paths = ''.join(f'{kind}_temp_path {runtime}/{kind};\n' for kind in
                                  ('client_body', 'proxy', 'fastcgi', 'uwsgi', 'scgi'))
        return (f'user www-data;\ndaemon off;\nworker_processes 1;\npid {runtime}/nginx.pid;\n'
                'error_log stderr crit;\nevents { worker_connections 256; }\nhttp {\n'
                'access_log off;\nserver_tokens off;\n' + temporary_paths + servers + '\n}\n').encode()

    def unit(self, generation_dir):
        text = (ROOT / 'deploy/ingress/work-tracker-ingress.service.example').read_text()
        for key, value in {'NGINX_BIN': self.layout.nginx, 'RUNTIME_DIR': self.layout.runtime,
                           'GENERATION_DIR': generation_dir, 'STATE_DIR': self.layout.state}.items():
            text = text.replace(f'<{key}>', str(value))
        return text.encode()

    @contextmanager
    def lock(self):
        self.layout.mkdir(self.layout.state)
        lock = self.layout.state / 'lock'
        if lock.exists() or lock.is_symlink():
            self.layout.check(lock, private=True)
        with lock.open('a') as stream:
            os.chmod(lock, 0o600)
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield

    def prepared(self, generation=None):
        if generation is None:
            pointer = self.layout.state / 'prepared.json'
            self.layout.check(pointer, private=True)
            generation = read_json(pointer)['generation']
        require(isinstance(generation, str) and re.fullmatch('[0-9a-f]{64}', generation), 'Nieprawidłowa generacja.')
        directory = self.layout.state / 'generations' / generation
        self.layout.check(directory, directory=True)
        manifest = directory / 'manifest.json'
        self.layout.check(manifest, private=True)
        manifest = read_json(manifest)
        require(set(manifest) == {'nginx.conf', 'service', 'config.json', 'origin.pem', 'origin.key', 'ca.pem'},
                'Nieprawidłowy manifest generacji.')
        for filename, expected in manifest.items():
            path = directory / filename
            self.layout.check(path, private=True)
            require(digest(path.read_bytes()) == expected, 'Generacja zmieniona po przygotowaniu.')
        config = read_json(directory / 'config.json')
        tls_config = config | {k: str(directory / v) for k, v in
                              {'certificate': 'origin.pem', 'private_key': 'origin.key', 'ca_certificate': 'ca.pem'}.items()}
        self.tls(tls_config)
        require((directory / 'nginx.conf').read_bytes() == self.render(config, directory)
                and (directory / 'service').read_bytes() == self.unit(directory),
                'Wzorce/narzędzie zmienione: wykonaj prepare ponownie.')
        return generation, directory, config

    def prepare(self, source):
        config, assets, generation = self.config(source)
        self.conflicts(config)
        with self.lock():
            require(not (self.layout.state / 'cutover.json').exists(), 'Najpierw recover/rollback-lan transakcji 9B2.')
            journal = self.layout.state / 'active.json'
            require(not journal.exists() and not journal.is_symlink(), 'Najpierw rollback aktywacji.')
            generations = self.layout.state / 'generations'
            self.layout.mkdir(generations)
            directory = generations / generation
            pointer = self.layout.state / 'prepared.json'
            if pointer.exists() or pointer.is_symlink():
                self.layout.check(pointer, private=True)
                if read_json(pointer)['generation'] == generation:
                    self.prepared()
                    return generation
            if directory.exists():
                # Recovery after interruption between directory publication and pointer update.
                self.prepared(generation)
                if pointer.exists():
                    self.backup('prepare', {'previous_prepared': read_json(pointer)})
                atomic(pointer, encoded({'generation': generation}))
                return generation
            stage = Path(tempfile.mkdtemp(prefix='.stage-', dir=generations))
            try:
                files = {'origin.pem': assets['certificate'], 'origin.key': assets['private_key'],
                         'ca.pem': assets['ca_certificate'], 'config.json': encoded(config),
                         'nginx.conf': self.render(config, directory), 'service': self.unit(directory)}
                for name, value in files.items():
                    atomic(stage / name, value)
                # nginx -t opens files/creates temporary paths: only in this explicit mutation.
                check_runtime = stage / 'check-runtime'
                check_runtime.mkdir(mode=0o700)
                atomic(stage / 'check.conf', self.render(config, stage, check_runtime))
                execute([str(self.layout.nginx), '-t', '-e', 'stderr', '-p', str(check_runtime) + '/',
                         '-c', str(stage / 'check.conf')])
                shutil.rmtree(check_runtime)
                (stage / 'check.conf').unlink()
                atomic(stage / 'manifest.json', encoded({k: digest(v) for k, v in files.items()}))
                os.rename(stage, directory)
                fsync_dir(generations)
                if pointer.exists():
                    self.backup('prepare', {'previous_prepared': read_json(pointer)})
                atomic(pointer, encoded({'generation': generation}))
            finally:
                if stage.exists():
                    shutil.rmtree(stage)
            return generation

    def backup(self, action, contents):
        self.layout.mkdir(self.layout.backups)
        path = self.layout.backups / f'{action}-{uuid.uuid4().hex}.json'
        atomic(path, encoded(contents))
        return path

    def evidence(self, source, generation):
        self.layout.check(source, private=True)
        evidence = read_json(source)
        require(set(evidence) == GATES | {'generation', 'verified_at', 'evidence_reference'}, 'Niepełny raport bramek.')
        require(evidence['generation'] == generation and all(evidence[k] is True for k in GATES),
                'Aktywacja zablokowana: izolacja IPv4/IPv6, źródła, probes i brak publicznej trasy wymagają potwierdzenia.')
        timestamp = datetime.fromisoformat(evidence['verified_at'])
        require(timestamp.utcoffset() is not None and 0 <= (datetime.now(timezone.utc) - timestamp).total_seconds() <= 86400,
                'Raport musi mieć świadomy timestamp sprzed najwyżej 24 godzin.')
        require(isinstance(evidence['evidence_reference'], str) and evidence['evidence_reference'].startswith('/'),
                'Wymagany prywatny raport dowodów.')
        self.layout.check(Path(evidence['evidence_reference']), private=True)
        return evidence

    def activate(self, evidence_path):
        with self.lock():
            self.activate_locked(evidence_path)

    def activate_locked(self, evidence_path):
        generation, directory, config = self.prepared()
        self.layout.check(self.layout.nginx)
        require(self.host.local_ip(config['lan_ip']), 'LAN IP nie należy do tego hosta.')
        evidence = self.evidence(evidence_path, generation)
        journal = self.layout.state / 'active.json'
        if journal.exists() or journal.is_symlink():
            self.layout.check(journal, private=True)
            transaction = read_json(journal)
            require(transaction['generation'] == generation and transaction['phase'] == 'active',
                    'Niedokończona aktywacja: wykonaj rollback.')
            self.postcheck(config, (directory / 'service').read_bytes())
            return
        require(not self.layout.unit.exists() and not self.layout.unit.is_symlink() and not self.host.active(),
                'Istniejąca jednostka/instancja ingress: odmowa przejęcia.')
        self.layout.check(self.layout.unit.parent, directory=True)
        self.conflicts(config, activation=True)
        unit = (directory / 'service').read_bytes()
        backup = self.backup('activate', {'generation': generation, 'unit_before': None, 'evidence': evidence})
        transaction = {'generation': generation, 'phase': 'activating', 'unit_sha': digest(unit), 'backup': str(backup)}
        atomic(journal, encoded(transaction))  # write-ahead, also recoverable after SIGKILL
        try:
            atomic(self.layout.unit, unit, mode=0o644)
            self.host.control('daemon-reload')
            self.host.control('start')
            self.wait_for_ingress(config, unit)
            atomic(journal, encoded(transaction | {'phase': 'active'}))
        except BaseException:
            self.rollback_locked()
            raise

    def wait_for_ingress(self, config, unit):
        # Type=simple start can return before nginx has opened its sockets.
        deadline = time.monotonic() + 5
        while True:
            try:
                self.postcheck(config, unit)
                return
            except (Refusal, OSError):
                if not self.host.active() or time.monotonic() >= deadline:
                    raise
                time.sleep(0.1)

    def postcheck(self, config, unit):
        self.layout.check(self.layout.unit)
        require(self.layout.unit.read_bytes() == unit and self.host.active(), 'Usługa ingress nie jest zgodna/aktywna.')
        self.host.backend_ready(config)
        # Only the dedicated systemd cgroup's master may own the two sockets.
        pid = self.host.ingress_pid()
        require(pid.isdigit() and int(pid) > 1, 'Brak PID ingress.')
        sockets = [s for s in self.host.listeners() if s[1] in (config['ha_port'], config['browser_port'])]
        require(len(sockets) == 2 and all(s[0] == config['lan_ip'] and f'pid={pid},' in s[2] for s in sockets),
                'Listener ingress jest niezgodny (także IPv6/wildcard).')
        self.host.ingress_permissions(pid)
        # Establish TLS locally without sending CF headers, credentials or a browser request.
        self.host.tls_probe(config, self.layout.state / 'generations' /
                            read_json(self.layout.state / 'prepared.json')['generation'] / 'ca.pem')

    def rollback_locked(self, boot=False):
        journal = self.layout.state / 'active.json'
        if not journal.exists() and not journal.is_symlink():
            require(not self.layout.unit.exists() and not self.layout.unit.is_symlink() and not self.host.active(),
                    'Brak dziennika: odmowa cofnięcia obcej usługi.')
            return
        self.layout.check(journal, private=True)
        transaction = read_json(journal)
        backup = Path(transaction['backup'])
        require(backup.parent == self.layout.backups, 'Nieprawidłowa ścieżka backupu.')
        self.layout.check(backup, private=True)
        require(read_json(backup)['unit_before'] is None, 'Nieobsługiwany stan jednostki przed aktywacją.')
        if self.layout.unit.exists() or self.layout.unit.is_symlink():
            self.layout.check(self.layout.unit)
            require(digest(self.layout.unit.read_bytes()) == transaction['unit_sha'], 'Jednostka zmieniona; rollback wymaga inspekcji operatora.')
            if not boot:
                self.host.control('stop')
            require(not self.host.active(), 'Nie udało się zatrzymać ingress; pozostawiono dziennik i backup.')
            self.layout.unit.unlink()
            fsync_dir(self.layout.unit.parent)
        require(not self.host.active(), 'Ingress nadal aktywny; pozostawiono dziennik.')
        self.host.control('daemon-reload')
        atomic(backup, encoded(read_json(backup) | {'rolled_back': True}))
        journal.unlink()
        fsync_dir(journal.parent)

    def rollback(self):
        with self.lock():
            self.rollback_locked()


def main():
    parser = argparse.ArgumentParser(description='Opcjonalny Nginx WT; brak zmian aplikacji, pakietów, firewalla i cloudflared.')
    parser.add_argument('operation', choices=['plan', 'validate', 'prepare', 'verify', 'activate', 'rollback'])
    parser.add_argument('--config', type=Path)
    parser.add_argument('--evidence', type=Path)
    args = parser.parse_args()
    tool = Provisioner()
    try:
        os_release = dict(line.split('=', 1) for line in Path('/etc/os-release').read_text().splitlines() if '=' in line)
        require(os_release.get('ID', '').strip('"') in ('debian', 'ubuntu'),
                'Obsługiwany jest Debian/Ubuntu.')
        if args.operation in {'prepare', 'activate', 'rollback'}:
            require(not (tool.layout.state / 'cutover.json').exists(), 'Transakcja 9B2: użyj ingress_cutover.py recover/rollback-lan.')
            require(os.geteuid() == 0, 'Operacja wymaga roota.')
            os.umask(0o077)
        if args.operation in {'plan', 'validate', 'prepare'}:
            require(args.config is not None, 'Wymagany --config poza checkoutem.')
            if args.operation == 'prepare':
                generation = tool.prepare(args.config)
                print(f'Przygotowano nieaktywną generację: {generation}. Bez zmiany listenerów.')
            else:
                config, _, generation = tool.config(args.config)
                ha_busy = tool.conflicts(config)
                print(f'Walidacja read-only OK. Generacja: {generation}.')
                print(f'Port HA zajęty: {ha_busy}; jest to dopuszczalne wyłącznie przed aktywacją.')
                print('Aktywacja wymaga osobnej operacji, backendu split i raportu izolacji IPv4/IPv6.')
        elif args.operation == 'verify':
            generation, _, config = tool.prepared()
            if (tool.layout.state / 'active.json').exists() or (tool.layout.state / 'active.json').is_symlink():
                tool.layout.check(tool.layout.state / 'active.json', private=True)
                require(read_json(tool.layout.state / 'active.json')['phase'] == 'active', 'Niedokończona aktywacja: rollback.')
                tool.postcheck(config, tool.unit(tool.layout.state / 'generations' / generation))
            else:
                tool.conflicts(config)
            print(f'Weryfikacja read-only OK: {generation}. Nie dowodzi izolacji rzeczywistej sieci.')
        elif args.operation == 'activate':
            require(args.evidence is not None, 'Wymagany --evidence; brak automatycznej aktywacji.')
            tool.activate(args.evidence)
            print('Prywatny ingress aktywny; bez enable/autostartu i bez publicznego routingu.')
        else:
            tool.rollback()
            print('Ingress cofnięty; backup i przygotowana konfiguracja zachowane. Backend wymaga osobnego planu LAN.')
    except Refusal as error:
        print(f'Odmowa: {error}', file=sys.stderr)
        return 1
    except (ValueError, OSError, KeyError, IndexError, TypeError, subprocess.SubprocessError):
        # No traceback or input dump, even when a malformed file contains a secret.
        print('Odmowa/awaria. Sprawdź zależności, konfigurację, uprawnienia, TLS, porty i dziennik ingress; użyj runbooka.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
