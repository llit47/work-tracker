"""Public examples use an allowlist, so no operator identifiers live in this test.

Optional private UTF-8 denylist: one literal per line, outside checkout, 0600.
No diagnostic prints matched values. This scans content, not Git history.
"""
import ipaddress
import os
from pathlib import Path
import re
import stat
import subprocess

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = ipaddress.ip_network('10.123.45.0/24')
NETWORK_DEFINITIONS = {'10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16'}

# Public documentation/provider endpoints, not deployment inventory.
PUBLIC_DOC_HOSTS = {'github.com', 'raw.githubusercontent.com', 'registry.npmjs.org',
                    'deb.nodesource.com', 'developers.cloudflare.com', 'developers.google.com',
                    'nginx.org', 'www.debian.org', 'www.freedesktop.org', 'www.sqlite.org',
                    'cheatsheetseries.owasp.org', 'opencollective.com', 'tidelift.com'}


def example_host(host):
    host = host.lower()
    return (host in PUBLIC_DOC_HOSTS or host in {'localhost', 'host', 'other', 'user', 'test', 'server', 'testserver', 'work-tracker.home.arpa'}
            or host.endswith(('.invalid', '.example', '.test', '.testserver'))
            or any(host == name or host.endswith('.' + name) for name in ('example.com', 'example.net', 'example.org')))


def permitted_ip(address):
    if getattr(address, 'ipv4_mapped', None):
        return permitted_ip(address.ipv4_mapped)
    if address.is_loopback or address.is_unspecified:
        return True
    if address.version == 4:
        return any(address in ipaddress.ip_network(n) for n in
                   ('10.123.45.0/24', '192.0.2.0/24', '198.51.100.0/24', '203.0.113.0/24', '240.0.0.0/4'))
    # Protocol sentinels used by ingress/login tests, plus documentation space.
    return (address in ipaddress.ip_network('2001:db8::/32') or str(address) in
            {'fe80::1', '2a06:98c0:3600::103'})


def violations(data, private_literals=()):
    text = data.decode('utf-8', errors='replace')
    for token in re.findall(r'(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?:/\d{1,2})?', text):
        try:
            address = ipaddress.ip_interface(token)
        except ValueError:
            continue
        if not permitted_ip(address.ip) and token not in NETWORK_DEFINITIONS:
            return True
    for token in re.findall(r'(?<![a-zA-Z0-9_])(?:[0-9a-fA-F]*:){2,}[0-9a-fA-F:.]+', text):
        try:
            address = ipaddress.ip_address(token)
        except ValueError:
            continue
        if not permitted_ip(address):
            return True
    hosts = re.findall(r'(?:https?|wss?)://([a-zA-Z0-9.-]+)', text)
    hosts += re.findall(r"(?:APP_HOST|HOSTNAME|HOST|public_hostname|origin_tls_name)[\"']?\s*[:=]\s*[\"']([a-zA-Z][a-zA-Z0-9.-]*)[\"']", text)
    if any(not re.fullmatch(r'[0-9.]+', host) and not example_host(host) for host in hosts):
        return True
    if re.search(r'\b(?:LXC|VM)\s+[0-9]+\b', text):
        return True
    return any(value in data for value in private_literals)


def private_denylist():
    source = os.environ.get('WT_PRIVATE_INFRASTRUCTURE_DENYLIST')
    if not source:
        return ()
    path = Path(source)
    assert path.is_absolute() and not path.resolve().is_relative_to(ROOT.resolve()), 'Private denylist must be outside checkout'
    info = path.lstat()
    assert stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o600 and info.st_uid in (0, os.getuid())
    return tuple(line for line in path.read_bytes().splitlines() if line)


def test_repository_contains_no_operator_infrastructure():
    files = subprocess.check_output(['git', 'ls-files', '--cached', '--others', '--exclude-standard', '-z'], cwd=ROOT).split(b'\0')
    denied = private_denylist()
    bad = [os.fsdecode(name) for name in files if name and (ROOT / os.fsdecode(name)).is_file()
           and violations((ROOT / os.fsdecode(name)).read_bytes(), denied)]
    assert not bad, f'Infrastructure privacy violation in files (values withheld): {bad}'


def test_scanner_rejects_nonexample_addresses_inventory_and_private_literals():
    # Construct synthetic out-of-policy values; no real operator fixture.
    assert violations(bytes([49, 48, 46, 57, 57, 46, 56, 46, 55]))
    assert violations(b'VM ' + str(98765).encode())
    assert violations(b'private-test-host', (b'private-test-host',))
    assert not violations(b'10.123.45.10 10.0.0.0/8 <WORK_TRACKER_LAN_IP>')



def test_privacy_detects_hostnames_domains_public_ipv4_and_ipv6_without_operator_fixtures():
    assert violations(b'https://' + b'office.' + b'internal')
    assert violations(b'HOSTNAME="' + b'private-' + b'node"')
    assert violations(b'http://' + bytes([56, 46, 57, 46, 49, 48, 46, 49, 49]))
    assert violations(':'.join(('fd12', '3456', '', '9')).encode())
    assert violations(':'.join(('2600', '1234', '', '9')).encode())
    assert not violations(b'https://work.example.invalid http://localhost [2001:db8::9] ::ffff:192.0.2.1')
