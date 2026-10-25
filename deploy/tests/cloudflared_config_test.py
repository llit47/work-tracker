"""Official cloudflared binary parses/matches the example; NEVER starts a tunnel."""
import os
from pathlib import Path
import re
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_real_cloudflared_configuration_and_whole_hostname_matching(tmp_path):
    binary = os.environ.get('WORK_TRACKER_CLOUDFLARED_BIN')
    if not binary:
        pytest.fail('Set WORK_TRACKER_CLOUDFLARED_BIN to official cloudflared >= 2026.10.0')
    version = subprocess.check_output([binary, '--version'], text=True)
    release = re.search(r'version (\d+)\.(\d+)\.(\d+)', version)
    assert release and tuple(map(int, release.groups())) >= (2026, 10, 0)
    template = (ROOT / 'deploy/ingress/cloudflared.yml.example').read_text()
    for key, value in dict(TUNNEL_UUID='00000000-0000-0000-0000-000000000000',
        CREDENTIALS_FILE_OUTSIDE_CHECKOUT=str(tmp_path / 'unused.json'),
        PUBLIC_HOSTNAME='work.example.invalid', WORK_TRACKER_LAN_IP='192.0.2.10',
        BROWSER_TLS_PORT='8443', ORIGIN_TLS_NAME='origin.example.invalid',
        CA_PEM_PATH_ON_CLOUDFLARED_LXC=str(tmp_path / 'ca.pem'),
        ACCESS_TEAM_SLUG='fixture-team', ACCESS_APPLICATION_AUD='fixture-audience').items():
        template = template.replace(f'<{key}>', value)
    config = tmp_path / 'cloudflared.yml'
    config.write_text(template)
    env = os.environ | {'TUNNEL_DISABLE_PATH_NORMALIZATION': 'false'}
    prefix = [binary, '--config', str(config), 'tunnel', 'ingress']
    subprocess.run([*prefix, 'validate'], check=True, capture_output=True, env=env)
    for path in ['/', '/api/auth/login', '/api//webhook/home-assistant',
                 '/api/%2e%2e/webhook/home-assistant', '/api/./webhook/home-assistant']:
        result = subprocess.check_output([*prefix, 'rule', f'https://work.example.invalid{path}'],
                                         text=True, stderr=subprocess.STDOUT, env=env)
        assert 'Matched rule #0' in result, result
    result = subprocess.check_output([*prefix, 'rule', 'https://alias.example.invalid/'],
                                     text=True, stderr=subprocess.STDOUT, env=env)
    assert 'http_status:404' in result
