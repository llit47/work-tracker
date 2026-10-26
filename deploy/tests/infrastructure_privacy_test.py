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


def violations(data, private_literals=()):
    text = data.decode('utf-8', errors='replace')
    for token in re.findall(r'(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?:/\d{1,2})?', text):
        try:
            address = ipaddress.ip_interface(token)
        except ValueError:
            continue
        private = any(address.ip in ipaddress.ip_network(network) for network in NETWORK_DEFINITIONS)
        if private and address.ip not in EXAMPLES and token not in NETWORK_DEFINITIONS:
            return True
    if re.search(r'\b(?:LXC|VM)\s+[0-9]+\b', text):
        return True
    return any(value in data for value in private_literals)


def private_denylist():
    source = os.environ.get('WT_PRIVATE_INFRASTRUCTURE_DENYLIST')
    if not source:
        return ()
    path = Path(source)
    assert path.is_absolute() and not path.is_relative_to(ROOT), 'Private denylist must be outside checkout'
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
