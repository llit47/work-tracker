"""Durable stop gate tests; no production files or SQLite writes."""
import os
from pathlib import Path
import subprocess
import sys

import pytest
from nginx_provision_test import p
import update_safety as u


@pytest.fixture
def safety(tmp_path):
    layout = u.Layout()
    layout.uid = os.getuid()
    layout.boundary = tmp_path
    layout.state = tmp_path / 'update-state.json'
    layout.guard = tmp_path / 'systemd' / 'work-tracker.service.d' / u.GUARD_NAME
    layout.backups = tmp_path / 'backups'
    layout.guard.parent.parent.mkdir(mode=0o755)
    layout.backups.mkdir(mode=0o700)
    directory = layout.backups / 'transaction'
    directory.mkdir(mode=0o700)
    safety = u.Safety(layout)
    safety.begin('a' * 40, 'b' * 40, directory, os.getpid())
    return safety


def test_stop_gate_only_live_owner_can_start_candidate_in_verification(safety):
    with pytest.raises(u.Refusal): safety.allow_start()
    safety.transition('migrating')
    with pytest.raises(u.Refusal): safety.allow_start()
    safety.transition('verifying')
    safety.allow_start()
    safety.transition('commit')
    safety.allow_start()
    assert not safety.layout.state.exists()


@pytest.mark.parametrize('damage', ['pid', 'start', 'boot', 'manual', 'unknown', 'corrupt', 'permissions'])
def test_gate_fail_closed_across_dead_pid_reuse_reboot_unknown_state(safety, damage):
    safety.transition('migrating')
    safety.transition('verifying')
    record = safety.read()
    if damage in ('pid', 'start', 'boot'):
        record['owner'][damage] = 999999999 if damage == 'pid' else 'not-current'
        safety.write(record)
    elif damage == 'manual': safety.hold()
    elif damage == 'unknown': safety.write(record | {'phase': 'unknown'})
    elif damage == 'corrupt': u.atomic(safety.layout.state, b'not-json')
    else: safety.layout.state.chmod(0o644)
    with pytest.raises((u.Refusal, OSError, ValueError)): safety.allow_start()
    assert safety.layout.state.exists()


def test_pre_migration_cancel_preserves_backup_and_allows_old_start(safety):
    directory = Path(safety.read()['directory'])
    safety.transition('cancel')
    safety.allow_start()
    assert (directory / 'transaction.json').is_file()
    assert safety.layout.guard.stat().st_mode & 0o777 == 0o644


def test_cancel_after_alembic_is_refused(safety):
    safety.transition('migrating')
    with pytest.raises(u.Refusal): safety.transition('cancel')
    safety.hold()
    with pytest.raises(u.Refusal): safety.allow_start()


def test_guard_never_overwrites_admin_file(safety):
    u.atomic(safety.layout.guard, b'[Service]\nExecStartPre=admin\n', 0o644)
    with pytest.raises(u.Refusal): safety.install()
    assert safety.layout.guard.read_bytes() == b'[Service]\nExecStartPre=admin\n'


def test_guard_for_legacy_code_removed_only_after_safe_cancel(safety, tmp_path):
    with pytest.raises(u.Refusal): safety.remove_legacy_guard(tmp_path / 'old-app')
    safety.transition('cancel')
    safety.remove_legacy_guard(tmp_path / 'old-app')
    assert not safety.layout.guard.exists()


@pytest.mark.parametrize('statuses,expected,summary', [([0,77],0,'PASS=1 SKIP=1 FAIL=0'),
    ([77,77],77,'PASS=0 SKIP=2 FAIL=0'), ([77,1,0],1,'PASS=1 SKIP=1 FAIL=1')])
def test_collective_runner_handles_expected_skip_under_set_e(tmp_path, statuses, expected, summary):
    checks=[]
    for index, code in enumerate(statuses):
        path=tmp_path / f'check-{index}.sh'
        path.write_text(f'#!/bin/sh\nexit {code}\n')
        checks.append(str(path))
    result = subprocess.run(['bash', str(p.ROOT / 'deploy/tests/run_shell_tests.sh'), *checks], capture_output=True)
    assert result.returncode == expected
    assert summary in result.stdout.decode()


@pytest.mark.parametrize('value', ['0', '4', '301', '08', '18446744073709551676', 'invalid'])
def test_updater_rejects_invalid_timeout_before_any_host_operation(value):
    # Actual staging/argument parser runs, then exits before root/layout checks.
    env = {key: val for key, val in os.environ.items() if key != 'WORK_TRACKER_STAGED_UPDATER'}
    result = subprocess.run(['bash', str(p.ROOT / 'update.sh'), '--readiness-timeout', value],
                            env=env, capture_output=True)
    assert result.returncode == 1
    assert b'5..300' in result.stderr
    assert b'Missing' not in result.stderr and b'root' not in result.stderr
