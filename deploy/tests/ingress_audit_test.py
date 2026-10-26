"""Audit regressions. Real nginx; systemd ordering/apt remain simulated (9C).

Archive failures must not take down committed HA; restoring anything still
requires trusted journal and intact backups. All fixtures are disposable.
"""
import base64
import os
from pathlib import Path
import subprocess

import pytest
from nginx_provision_test import setup, tls, p
from ingress_cutover_test import cutover, bootstrap, activate, c, b
from ingress_artifacts import migrate
from env_file import parse, EnvError


@pytest.mark.parametrize('boot', [False, True])
@pytest.mark.parametrize('archive', ['ok', 'missing', 'sha', 'permissions'])
def test_committed_recovery_does_not_depend_on_archive(cutover, capsys, boot, archive):
    tool = activate(cutover)
    backup = Path(tool.journal_state()['backup'])
    original = backup.read_bytes()
    if archive == 'missing':
        backup.unlink()
    elif archive == 'sha':
        p.atomic(backup, original + b'\n')
    elif archive == 'permissions':
        backup.chmod(0o644)
    before = tool.layout.env.read_bytes(), tool.host.calls[:], tool.host.app_calls[:]
    tool.recover(boot=boot)
    assert before == (tool.layout.env.read_bytes(), tool.host.calls, tool.host.app_calls)
    assert tool.host.active()
    assert ('rollback NIEDOSTĘPNY' in capsys.readouterr().err) == (archive != 'ok')
    p.atomic(backup, original)
    tool.recover(rollback=True)


@pytest.mark.parametrize('phase', ['pending', 'active'])
def test_needed_rollback_without_archive_never_changes_running_state(cutover, phase):
    tool = activate(cutover)
    state = tool.journal_state()
    backup = Path(state['backup'])
    original = backup.read_bytes()
    p.atomic(tool.journal, p.encoded(state | {'phase': phase}))
    backup.unlink()
    before = tool.layout.env.read_bytes(), tool.host.calls[:], tool.host.app_calls[:]
    with pytest.raises(OSError):
        tool.recover(rollback=phase == 'active')
    assert before == (tool.layout.env.read_bytes(), tool.host.calls, tool.host.app_calls)
    assert tool.host.active()
    p.atomic(backup, original)
    tool.recover(rollback=True)


@pytest.mark.parametrize('damage', ['mode', 'phase', 'hash', 'extra', 'duplicate'])
def test_corrupt_journal_is_never_treated_as_committed(cutover, damage):
    tool = activate(cutover)
    original = tool.journal.read_bytes()
    state = tool.journal_state()
    if damage == 'mode':
        tool.journal.chmod(0o644)
    elif damage == 'duplicate':
        p.atomic(tool.journal, original.replace(b'"phase":', b'"phase": "pending", "phase":'))
    else:
        delta = {'phase': {'phase': 'unknown'}, 'hash': {'backup_sha': 'invalid'}, 'extra': {'extra': True}}[damage]
        p.atomic(tool.journal, p.encoded(state | delta))
    with pytest.raises(p.Refusal):
        tool.recover(boot=True)
    assert tool.host.active()
    p.atomic(tool.journal, original)
    tool.recover(rollback=True)


def test_final_snapshot_uses_dropin_changed_during_confirmation(cutover):
    tool, evidence = cutover
    path = tool.layout.dropins / 'operator.conf'
    new = b'[Service]\nNice=2\n'
    tool.activate(evidence, lambda _: p.atomic(path, new, 0o644), lambda _: None)
    saved = tool.transaction()[1]['dropins']['operator.conf']['data']
    assert base64.b64decode(saved) == new
    tool.recover(rollback=True)
    assert path.read_bytes() == new and tool.host.lan


def test_changes_after_final_snapshot_are_not_overwritten(cutover):
    tool, evidence = cutover
    path = tool.layout.dropins / 'operator.conf'
    old = path.read_bytes()
    def later(_):
        p.atomic(path, b'[Service]\nNice=3\n', 0o644)
    with pytest.raises(p.Refusal):
        tool.activate(evidence, lambda _: None, later)
    assert path.read_bytes() == b'[Service]\nNice=3\n'
    assert tool.journal.exists()
    p.atomic(path, old, 0o644)
    tool.recover()


@pytest.mark.parametrize('point', ['artifact-journal', 'artifact-written'])
@pytest.mark.parametrize('kind', ['recovery', 'policy'])
def test_owned_v1_to_v2_retry_after_interruption(cutover, bootstrap, monkeypatch, kind, point):
    tool, _ = cutover
    if kind == 'recovery':
        path = tool.layout.recovery_unit
        old, new, mode = tool.recovery_bytes(1), tool.recovery_bytes(2), 0o644
        operation = tool.install_recovery
    else:
        path = bootstrap.policy
        old, new, mode = b.POLICY_V1, b.POLICY, 0o755
        p.atomic(tool.layout.state / 'bootstrap.json', p.encoded({'policy_sha': p.digest(old)}))
        monkeypatch.setattr(bootstrap, 'installed', lambda: True)
        p.atomic(bootstrap.layout.nginx, b'test binary', 0o700)
        operation = bootstrap.run
    p.atomic(path, old, mode)
    tool.host.crash = point
    with pytest.raises(p.Refusal):
        operation()
    pending = list(tool.layout.state.glob('*-migration.json'))
    assert pending
    assert path.read_bytes() in (old, new)
    operation()
    assert path.read_bytes() == new and not list(tool.layout.state.glob('*-migration.json'))
    before = path.stat().st_mtime_ns
    operation()
    assert path.stat().st_mtime_ns == before
    assert any(base64.b64encode(old).decode() in backup.read_text()
               for backup in tool.layout.backups.glob('*.json'))


@pytest.mark.parametrize('damage', ['foreign', 'permission', 'backup'])
def test_artifact_migration_refuses_unknown_or_tampered_state(cutover, damage):
    tool, _ = cutover
    path = tool.layout.recovery_unit
    p.atomic(path, tool.recovery_bytes(1), 0o644)
    if damage == 'foreign':
        p.atomic(path, b'admin unit, same name\n', 0o644)
    elif damage == 'permission':
        path.chmod(0o666)
    else:
        tool.host.crash = 'artifact-written'
        with pytest.raises(p.Refusal):
            tool.install_recovery()
        record = p.read_json(tool.layout.state / 'recovery-unit-migration.json')
        Path(record['backup']).unlink()
    before = path.read_bytes()
    with pytest.raises((p.Refusal, OSError)):
        tool.install_recovery()
    assert path.read_bytes() == before


def test_systemd_directories_have_explicit_modes_under_private_umask(cutover):
    tool, _ = cutover
    (tool.layout.dropins / 'operator.conf').unlink()
    tool.layout.dropins.rmdir()
    previous = os.umask(0o077)
    try:
        activate(cutover)
    finally:
        os.umask(previous)
    for path in (tool.layout.dropins, tool.layout.ingress_link.parent, tool.layout.recovery_link.parent):
        assert path.stat().st_mode & 0o777 == 0o755
    tool.recover(rollback=True)


@pytest.mark.parametrize('value,expected', [
    ('test-token-32-characters-at-least-123', 'test-token-32-characters-at-least-123'),
    ('"test-token-32-characters-at-least-123"', 'test-token-32-characters-at-least-123'),
    ("'test-token-32-characters-at-least-123'", 'test-token-32-characters-at-least-123'),
    (r'test\-token-32-characters-at-least-123', 'test-token-32-characters-at-least-123'),
    (r'"literal\$and\\slash"', 'literal$and\\slash'),
    (r'"literal\q"', r'literal\q'),
])
def test_environment_file_decodes_without_shell_or_secret_output(tmp_path, capsys, value, expected):
    data = f'WEBHOOK_TOKEN={value}\n'.encode()
    assert c.values(data)['WEBHOOK_TOKEN'] == expected
    path = tmp_path / 'app.env'
    path.write_bytes(data)
    result = subprocess.run(['bash', '-c', f'source "{p.ROOT}/deploy/common.sh"; read_env_value "{path}" WEBHOOK_TOKEN; [[ "$REPLY" == "$EXPECTED_TEST_VALUE" ]]'],
                            env=os.environ | {'EXPECTED_TEST_VALUE': expected}, capture_output=True)
    assert result.returncode == 0
    assert not result.stdout and not result.stderr and not capsys.readouterr().out


@pytest.mark.parametrize('data', [b'WEBHOOK_TOKEN="unterminated', b'WEBHOOK_TOKEN=foo\\\nbar',
                                  b'WEBHOOK_TOKEN=one\nWEBHOOK_TOKEN=two', b'WEBHOOK_TOKEN="a"suffix'])
def test_unsupported_env_is_refused_without_echo(data):
    with pytest.raises(p.Refusal) as error:
        c.values(data)
    assert 'unterminated' not in str(error.value) and 'suffix' not in str(error.value)


@pytest.mark.parametrize('failure', ['timeout', 'dpkg'])
def test_apt_error_diagnostics_are_redacted_and_explain_operator_repair(cutover, monkeypatch, failure):
    tool, _ = cutover
    def run(*args, **kwargs):
        if failure == 'timeout':
            raise subprocess.TimeoutExpired('apt-get', 600, stderr=b'private-output-never-echo')
        raise subprocess.CalledProcessError(100, 'apt-get', stderr=b'private-output-never-echo')
    monkeypatch.setattr(b.subprocess, 'run', run)
    with pytest.raises(p.Refusal) as error:
        b.Bootstrap(tool.tool).install_packages()
    assert 'dpkg --audit' in str(error.value) and 'private-output' not in str(error.value)


def test_nonroot_runtime_runner_returns_skip_not_success():
    if os.geteuid() == 0:
        pytest.skip('Nonroot branch requires unprivileged runner; real root runtime remains 9C')
    result = subprocess.run(['bash', str(p.ROOT / 'deploy/tests/service_user_runtime_test.sh')], capture_output=True)
    assert result.returncode == 77 and b'NOT RUN' in result.stderr


def test_recovery_artifact_changed_during_window_is_rechecked(cutover):
    tool, evidence = cutover
    def changed(_):
        p.atomic(tool.layout.recovery_unit, b'admin change during confirmation\n', 0o644)
    with pytest.raises(p.Refusal):
        tool.activate(evidence, changed, lambda _: None)
    assert not tool.journal.exists() and not tool.host.app_calls
    assert tool.layout.recovery_unit.read_bytes() == b'admin change during confirmation\n'


def test_existing_systemd_directory_permissions_are_preserved(cutover):
    tool, _ = cutover
    parent = tool.layout.recovery_link.parent
    parent.mkdir(mode=0o700)
    tool.install_recovery()
    assert parent.stat().st_mode & 0o777 == 0o700


@pytest.mark.parametrize('boot', [False, True])
def test_recovery_unit_cli_exit_success_with_missing_active_archive(cutover, monkeypatch, capsys, boot):
    tool = activate(cutover)
    path = Path(tool.journal_state()['backup'])
    original = path.read_bytes()
    path.unlink()
    before = tool.host.calls[:], tool.host.app_calls[:], tool.layout.env.read_bytes()
    monkeypatch.setattr(c, 'Cutover', lambda: tool)
    monkeypatch.setattr(c.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(c.signal, 'signal', lambda *args: None)
    monkeypatch.setattr(c.sys, 'argv', ['ingress_cutover.py', 'recover'] + (['--boot'] if boot else []))
    assert c.main() == 0
    assert 'rollback NIEDOSTĘPNY' in capsys.readouterr().err
    assert before == (tool.host.calls, tool.host.app_calls, tool.layout.env.read_bytes())
    p.atomic(path, original)
    tool.recover(rollback=True)
