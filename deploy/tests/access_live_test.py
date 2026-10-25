"""PHASE 9 ONLY: opt-in live Access probes. Never run by the backend suite.

WT_PHASE9_ACCESS_FIXTURE must point to a mode-0600 JSON file outside checkout.
Required keys: origin; tokens mapping allowed/expired/wrong_audience/wrong_team/
denied_user to real operator-provided Access tokens. A denied-user token may
not be obtainable: keep that test BLOCKED and verify the IdP denial manually.
No token, cookie, response body or URL query is printed on failure.
These probes do not activate routing and do not ingest HA events.
"""
import json
import os
from pathlib import Path
import stat

import httpx
import pytest


class RedactedFixture(dict):
    def __repr__(self):
        return '<real Access fixture: redacted>'


@pytest.fixture(scope='module')
def live():
    filename = os.environ.get('WT_PHASE9_ACCESS_FIXTURE')
    if not filename:
        pytest.fail('BLOCKED / REQUIRES PHASE 9 VERIFICATION: no real Access fixture')
    path = Path(filename)
    if path.resolve().is_relative_to(Path(__file__).resolve().parents[2]):
        pytest.fail('Access fixture must remain outside the checkout')
    assert stat.S_IMODE(path.stat().st_mode) == 0o600, 'Access fixture must be mode 0600'
    data = RedactedFixture(json.loads(path.read_text()))
    assert data['origin'].startswith('https://')
    allowed = data['tokens'].get('allowed')
    if not allowed:
        pytest.fail('BLOCKED / REQUIRES PHASE 9 VERIFICATION: missing positive control')
    response = httpx.get(data['origin'] + '/api/health',
        headers={'Cookie': 'CF_Authorization=' + allowed}, follow_redirects=False, timeout=15)
    assert response.status_code == 200, 'Real allowed identity must reach WT before negative probes'
    return data


@pytest.mark.parametrize('case', ['missing', 'forged', 'expired', 'wrong_audience', 'wrong_team', 'denied_user'])
@pytest.mark.parametrize('path', ['/', '/api/health', '/api/auth/me', '/api/openapi.json'])
def test_live_access_denies_invalid_identity(live, case, path):
    token = '' if case == 'missing' else ('not.a.signed-token' if case == 'forged' else live['tokens'].get(case))
    if token is None:
        pytest.fail('BLOCKED / REQUIRES PHASE 9 VERIFICATION: missing ' + case + ' fixture')
    headers = {'Cookie': 'CF_Authorization=' + token} if token else {}
    # Cookies exercise real edge Access policy. Header alone is not authentication.
    response = httpx.get(live['origin'] + path, headers=headers, follow_redirects=False, timeout=15)
    assert response.status_code in {302, 303, 403}, 'Access must deny before WT'
    assert not response.headers.get('content-type', '').startswith('application/json'), 'WT rejection cannot prove Access denial'
    assert response.headers.get('server', '').lower() == 'cloudflare', 'Must traverse real edge'


def test_live_allowed_access_still_requires_work_tracker_session(live):
    token = live['tokens'].get('allowed')
    if not token:
        pytest.fail('BLOCKED / REQUIRES PHASE 9 VERIFICATION: missing allowed identity')
    headers = {'Cookie': 'CF_Authorization=' + token}
    with httpx.Client(base_url=live['origin'], headers=headers, follow_redirects=False, timeout=15) as client:
        assert client.get('/api/health').status_code == 200, 'Positive Access control failed'
        response = client.get('/api/dashboard')
        assert response.status_code == 401, 'WT session must remain required'
        assert response.headers.get('cache-control') == 'no-store'
        for path in ['/api/webhook/home-assistant', '/api/webhook/home-assistant/correction']:
            for method in ['GET', 'HEAD', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS']:
                assert client.request(method, path).status_code == 403, 'Public HA must be denied'
