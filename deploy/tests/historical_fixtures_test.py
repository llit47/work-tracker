"""Integrity and explicit-history prerequisites for historical regressions."""
import hashlib
import shutil
import subprocess

import pytest
from nginx_provision_test import p
import update_safety as u
from historical_fixtures import BASE, FIXTURES, SHA256, historical_file, require_baseline_history


@pytest.mark.parametrize('name', SHA256)
def test_exact_historical_fixture_hash(name):
    data = historical_file(name)
    assert hashlib.sha256(data).hexdigest() == SHA256[name]
    if name == 'deploy/update_safety.py':
        assert hashlib.sha256(data).hexdigest() == u.CHECKER_V1_SHA


def test_modified_fixture_is_rejected_before_execution(tmp_path):
    shutil.copytree(FIXTURES, tmp_path / 'fixtures')
    checker = tmp_path / 'fixtures/deploy/update_safety.py'
    checker.write_bytes(checker.read_bytes() + b'\n# changed\n')
    with pytest.raises(ValueError, match='SHA-256 mismatch'):
        historical_file('deploy/update_safety.py', tmp_path / 'fixtures')


def test_checker_catalog_must_agree_with_fixture_pin(monkeypatch):
    monkeypatch.setattr(u, 'CHECKER_V1_SHA', '0' * 64)
    with pytest.raises(ValueError, match='disagrees with CHECKER_V1_SHA'):
        historical_file('deploy/update_safety.py')


def test_missing_baseline_is_an_actionable_failure_not_skip(tmp_path):
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    with pytest.raises(RuntimeError, match='git fetch --no-tags --depth=2 origin'):
        require_baseline_history(tmp_path)


def test_real_baseline_and_parent_are_available():
    require_baseline_history(p.ROOT)
    # A deliberately historical production baseline, not a PR-only code source.
    result = subprocess.run(['git', '-C', str(p.ROOT), 'show', BASE + ':update.sh'],
                            check=True, capture_output=True)
    assert b'work-tracker-update.' in result.stdout
