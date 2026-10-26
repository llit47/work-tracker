"""Controlled Debian/Ubuntu dependency bootstrap; never runs a generic Nginx.

The permanent mask and narrowly scoped policy survive package upgrades and an
interrupted apt. Foreign installations/policies are never changed.
"""
import os
from pathlib import Path
import shutil
import subprocess

import nginx_provision as p

POLICY = b'''#!/bin/sh
# Work Tracker owned: prevent generic nginx startup, including package upgrades.
while [ "${1#-}" != "$1" ]; do shift; done
case "$1" in nginx|nginx.service) exit 101 ;; esac
exit 0
'''


class Bootstrap:
    def __init__(self, provisioner):
        self.tool = provisioner
        self.layout = provisioner.layout
        self.policy = Path('/usr/sbin/policy-rc.d')
        self.mask = self.layout.unit.parent / 'nginx.service'

    def installed(self):
        result = subprocess.run(['dpkg-query', '-W', '-f=${Status}', 'nginx'],
                                capture_output=True, timeout=15)
        p.require(result.returncode in (0, 1), 'Nie można sprawdzić pakietu Nginx.')
        return result.returncode == 0 and result.stdout == b'install ok installed'

    def install_packages(self):
        # No upgrade/restart of an administrator's existing package installation.
        for args in (['apt-get', 'update'], ['apt-get', 'install', '-y', '--no-upgrade',
                      'nginx', 'openssl', 'iproute2']):
            try:
                subprocess.run(args, check=True, timeout=600, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL,
                               env=os.environ | {'DEBIAN_FRONTEND': 'noninteractive', 'NEEDRESTART_MODE': 'l'})
            except (OSError, subprocess.SubprocessError):
                raise p.Refusal('Instalacja zależności nieudana; blokady generic Nginx zachowano. Ponów bootstrap.') from None

    def available(self):
        return self.layout.nginx.is_file() and all(shutil.which(x) for x in ('openssl', 'ip', 'ss'))

    def run(self):
        with self.tool.lock():
            marker = self.layout.state / 'bootstrap.json'
            owned = marker.exists()
            installed = self.installed()
            if owned:
                self.layout.check(marker, private=True)
                p.require(p.read_json(marker) == {'policy_sha': p.digest(POLICY)}, 'Nieznany bootstrap.')
            elif installed:
                p.require(self.available(), 'Obcy Nginx: uzupełnij zależności osobno; odmowa zmiany pakietów/usługi.')
                self.layout.check(self.layout.nginx)
                return 'existing'  # No policy, mask, reload, stop, enable or apt.
            else:
                p.require(not self.layout.nginx.exists(), 'Nginx poza dpkg: odmowa przejęcia.')
                p.require(not self.mask.exists() and not self.mask.is_symlink(), 'Obca jednostka/maska nginx.service.')
                p.require(not self.policy.exists() and not self.policy.is_symlink(), 'Obca policy-rc.d: przygotuj zależności osobno.')
                p.require(not self.tool.host.generic_active() and not self.tool.host.generic_known(), 'Obca jednostka/usługa Nginx.')
                self.layout.check(self.mask.parent, directory=True)
                self.layout.check(self.policy.parent, directory=True)
                p.atomic(marker, p.encoded({'policy_sha': p.digest(POLICY)}))
            # Marker is write-ahead. Interruption before either file is recoverable.
            if self.mask.is_symlink():
                p.require(os.readlink(self.mask) == '/dev/null', 'Obca maska Nginx.')
            else:
                p.require(not self.mask.exists(), 'Obca jednostka Nginx.')
                self.mask.symlink_to('/dev/null')
                p.fsync_dir(self.mask.parent)
            if self.policy.exists() or self.policy.is_symlink():
                self.layout.check(self.policy)
                p.require(self.policy.read_bytes() == POLICY and os.access(self.policy, os.X_OK), 'Obca policy-rc.d.')
            else:
                p.atomic(self.policy, POLICY, 0o755)
            self.tool.host.control('daemon-reload')
            before = self.tool.host.bootstrap_listeners()
            if not installed or not self.available():
                self.install_packages()
            p.require(self.available(), 'Brak wymaganych zależności Nginx/OpenSSL/iproute2.')
            self.layout.check(self.layout.nginx)
            p.require(not self.tool.host.generic_active(), 'Generic Nginx działa; odmowa kontynuacji.')
            after = self.tool.host.bootstrap_listeners()
            p.require(after <= before, 'Wykryto nowy listener 80/443.')
            return 'owned'
