"""Real historical standalone v1 -> current v2, real fsync/replace/SIGKILL.

Layout/UID are private fixture observations. No production systemd evidence.
"""
import fcntl
import multiprocessing
import os
from pathlib import Path
import signal
from types import ModuleType

import pytest
from nginx_provision_test import p
import update_safety as u
from historical_fixtures import historical_file


def load_checker(path):
    module=ModuleType('checker_under_test');module.__file__=str(path)
    exec(compile(path.read_bytes(),str(path),'exec'),module.__dict__)
    return module


@pytest.fixture
def versions(tmp_path):
    layout=u.Layout();layout.uid=os.getuid();layout.boundary=tmp_path
    layout.state=tmp_path/'update-state.json'
    layout.guard=tmp_path/'systemd/work-tracker.service.d'/u.GUARD_NAME
    layout.backups=tmp_path/'backups';layout.deploy_lock=tmp_path/'deploy.lock'
    layout.guard.parent.mkdir(parents=True,mode=0o755);layout.guard.parent.parent.chmod(0o755);layout.backups.mkdir(mode=0o700)
    layout.deploy_lock.write_bytes(b'');layout.deploy_lock.chmod(0o600)
    source=historical_file('deploy/update_safety.py')
    historical=tmp_path/'historical-v1.py';u.atomic(historical,source)
    path=tmp_path/'update-safety.py';u.atomic(path,source,0o644)
    u.atomic(layout.guard,u.BOOTSTRAP_GUARD,0o644)
    old=load_checker(historical).Safety(layout)
    assert u.checker_version(source)==1
    return u.Safety(layout),old,path


def locked_migrate(safety):
    with safety.layout.deploy_lock.open('rb') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        safety.migrate_checker('a'*40)


def test_old_staged_helper_finishes_across_checkout_change_then_versions_migrate(versions):
    safety,old,path=versions
    directory=safety.layout.backups/'normal-update';directory.mkdir(mode=0o700)
    old.begin('a'*40,'b'*40,directory,os.getpid())
    safety.install()  # New checkout accepts exactly catalogued v1, not equality with v2.
    assert u.checker_version(path.read_bytes())==1
    with pytest.raises(u.Refusal): locked_migrate(safety)
    old.transition('migrating');old.transition('verifying')
    old.allow_start();safety.allow_start()
    old.transition('commit')  # The pinned, unchanged historical v1 implementation.
    guard=safety.layout.guard.read_bytes()
    locked_migrate(safety)
    assert u.checker_version(path.read_bytes())==2
    assert path.read_bytes()==Path(u.__file__).read_bytes() and safety.layout.guard.read_bytes()==guard
    assert not safety.layout.state.exists() and not safety.checker_journal.exists()
    safety.allow_start()
    backups=list(safety.layout.backups.glob('update-checker-*'))
    assert len(backups)==1 and u.checker_version((backups[0]/'checker-before.py').read_bytes())==1
    locked_migrate(safety)  # Idempotent, no second recovery point or transaction.
    assert list(safety.layout.backups.glob('update-checker-*'))==backups


@pytest.mark.parametrize('damage',['content','mode','owner','symlink'])
def test_unknown_foreign_or_unsafe_version_is_never_overwritten(versions,damage):
    safety,_,path=versions
    if damage=='content': u.atomic(path,b'foreign edited checker\n',0o644)
    if damage=='mode': path.chmod(0o660)
    if damage=='owner': safety.layout.uid+=1
    if damage=='symlink': path.unlink();path.symlink_to(safety.layout.guard)
    before=path.lstat()
    with pytest.raises((u.Refusal,OSError)): locked_migrate(safety)
    assert path.lstat().st_ino==before.st_ino and not safety.layout.state.exists()


@pytest.mark.parametrize('stage',['checker-journal','checker-written','checker-state-cleared'])
def test_real_sigkill_keeps_guard_and_resumes_before_after_atomic_replace(versions,stage):
    safety,_,path=versions
    guard=safety.layout.guard.read_bytes()
    def child():
        def kill(point):
            if point==stage: os.kill(os.getpid(),signal.SIGKILL)
        safety.checkpoint=kill
        locked_migrate(safety)
    process=multiprocessing.get_context('fork').Process(target=child)
    process.start();process.join(10)
    assert process.exitcode==-signal.SIGKILL
    assert safety.layout.guard.read_bytes()==guard and safety.checker_journal.exists()
    installed=load_checker(path).Safety(safety.layout)
    with pytest.raises(ValueError): installed.allow_start()  # Simulated boot, actual installed version.
    assert u.checker_version(path.read_bytes())==(1 if stage=='checker-journal' else 2)
    locked_migrate(safety)
    assert u.checker_version(path.read_bytes())==2 and not safety.checker_journal.exists()
    safety.allow_start()


def test_migration_recovery_cannot_bypass_another_update_state(versions):
    safety,_,path=versions
    def fail(stage):
        if stage=='checker-journal': raise RuntimeError('interruption')
    safety.checkpoint=fail
    with pytest.raises(RuntimeError): locked_migrate(safety)
    transaction=safety.read();safety.write(transaction|{'phase':'migrating'})
    safety.checkpoint=lambda _:None
    with pytest.raises(u.Refusal): locked_migrate(safety)
    assert safety.read()['phase']=='migrating' and u.checker_version(path.read_bytes())==1
    with pytest.raises(u.Refusal): safety.allow_start()


def test_orphan_preparing_state_without_migration_proof_requires_manual_recovery(versions):
    safety,_,_=versions
    directory=safety.layout.backups/'orphan';directory.mkdir(mode=0o700)
    safety.begin('a'*40,'b'*40,directory,os.getpid())
    with pytest.raises(u.Refusal): locked_migrate(safety)
    assert safety.read()['phase']=='preparing'


@pytest.mark.parametrize('phase',['preparing','migrating','verifying','manual'])
def test_no_normal_transaction_phase_can_be_bypassed(versions,phase):
    safety,_,path=versions
    directory=safety.layout.backups/'normal';directory.mkdir(mode=0o700)
    safety.begin('a'*40,'b'*40,directory,os.getpid())
    if phase in ('migrating','verifying'): safety.transition('migrating')
    if phase=='verifying': safety.transition('verifying')
    if phase=='manual': safety.hold()
    before=safety.layout.state.read_bytes()
    with pytest.raises(u.Refusal): locked_migrate(safety)
    assert safety.layout.state.read_bytes()==before and u.checker_version(path.read_bytes())==1


@pytest.mark.parametrize('damage',['backup','target'])
def test_corrupted_migration_proof_refuses_without_changing_guard_or_checker(versions,damage):
    safety,_,path=versions
    def fail(stage):
        if stage=='checker-journal': raise RuntimeError('interruption')
    safety.checkpoint=fail
    with pytest.raises(RuntimeError): locked_migrate(safety)
    journal=u.read_json(safety.checker_journal)
    if damage=='backup': u.atomic(Path(journal['transaction']['directory'])/'checker-before.py',b'corrupt')
    else: u.atomic(safety.checker_journal,u.encoded(journal|{'target_sha':'0'*64}))
    with pytest.raises(u.Refusal): locked_migrate(safety)
    assert u.checker_version(path.read_bytes())==1 and safety.layout.state.exists()


def test_ingress_mutations_refuse_checker_journal_even_after_update_state_removed(versions):
    from types import SimpleNamespace
    from ingress_cutover_test import c
    safety,_,_=versions
    u.atomic(safety.checker_journal,b'{}')
    tool=SimpleNamespace(layout=SimpleNamespace(update_state=safety.layout.state))
    with pytest.raises(p.Refusal): c.Cutover.require_no_update(tool)
