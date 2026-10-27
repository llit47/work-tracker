#!/usr/bin/env python3
"""Read-only readiness gate used by ordinary LAN updates before commit."""
import argparse
from pathlib import Path
import sys

from env_file import parse
from nginx_provision import Host, Refusal, require


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--readiness-timeout', type=int, default=60)
    args = parser.parse_args()
    try:
        env = parse(Path('/etc/work-tracker/work-tracker.env').read_bytes())
        require(env.get('INGRESS_PROFILE', 'lan') == 'lan', 'Readiness LAN wymaga profilu LAN.')
        host = Host()
        config = host.wait_lan_health(dict(lan_ip=env['APP_HOST'], ha_port=int(env['APP_PORT'])), args.readiness_timeout)
        host.lan_ready(config)
        host.app_config_preserved(env)
    except Refusal as error:
        print(f'Odmowa readiness LAN: {error}', file=sys.stderr)
        return 1
    except (OSError, ValueError, KeyError):
        print('Błąd readiness LAN; sprawdź konfigurację i usługę (wartości ukryte).', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
