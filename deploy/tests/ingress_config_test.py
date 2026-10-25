"""Static configuration contract; separate from real proxy/Access verification."""
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]


def test_tunnel_templates_protect_whole_hostname_and_verify_origin_tls():
    local = (ROOT / 'deploy/ingress/cloudflared.yml.example').read_text()
    remote = json.loads((ROOT / 'deploy/ingress/cloudflared-remote.json.example').read_text())
    rules = remote['config']['ingress']
    assert len(rules) == 2 and rules[1] == {'service': 'http_status:404'}
    assert 'path' not in rules[0] and 'path:' not in local
    origin = rules[0]['originRequest']
    assert origin['access'] == dict(required=True, teamName='<ACCESS_TEAM_SLUG>',
                                    audTag=['<ACCESS_APPLICATION_AUD>'])
    assert origin['noTLSVerify'] is False and origin['caPool'] and origin['originServerName']
    for text in ['required: true', 'teamName: <ACCESS_TEAM_SLUG>',
                 '- <ACCESS_APPLICATION_AUD>', 'noTLSVerify: false',
                 'TUNNEL_DISABLE_PATH_NORMALIZATION=false']:
        assert text in local


def test_runtime_profile_and_manifest_are_opt_in_and_keep_one_worker():
    unit = (ROOT / 'deploy/ingress/work-tracker.service.override.example').read_text()
    assert '--host 127.0.0.1' in unit
    assert '--workers 1 --no-proxy-headers' in unit
    assert '--proxy-headers ' not in unit and 'forwarded-allow-ips' not in unit
    manifest = (ROOT / 'deploy/config.manifest').read_text()
    assert 'INGRESS_PROFILE|optional|plain|lan|' in manifest
    assert 'BROWSER_HSTS_SECONDS|optional|plain|0|' in manifest
    subprocess.run(['bash', '-c', '''
source deploy/common.sh
validate_config_value ingress_profile lan
validate_config_value ingress_profile split
! validate_config_value ingress_profile public
validate_config_value hsts_seconds 0
validate_config_value hsts_seconds 31536000
! validate_config_value hsts_seconds 31536001
! validate_config_value hsts_seconds -1
! validate_config_value hsts_seconds 08
'''], cwd=ROOT, check=True)


def test_access_policy_has_only_explicit_email_allowlist():
    policy = json.loads((ROOT / 'deploy/ingress/access-policy.json.example').read_text())
    assert policy['domain'] == '<PUBLIC_HOSTNAME>' and policy['type'] == 'self_hosted'
    assert len(policy['policies']) == 1
    rule = policy['policies'][0]
    assert rule['decision'] == 'allow'
    assert rule['include'] == [{'email': {'email': '<ALLOWED_USER_EMAIL>'}}]
    assert not any(word in json.dumps(policy).lower() for word in ['bypass', 'everyone', 'service_auth'])
