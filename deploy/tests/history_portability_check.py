"""Run historical regressions in an isolated shallow/squashed worktree snapshot.

Usage: backend/.venv/bin/python deploy/tests/history_portability_check.py
No network fetches; required BASE objects come from the invoking checkout.
"""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

from historical_fixtures import BASE, require_baseline_history

ROOT = Path(__file__).resolve().parents[2]
PR_ONLY = 'd3cb1a6849feeee9690235fe6545578fdb1954e8'


def git(directory, *args):
    return subprocess.check_output(['git', '-C', str(directory), *map(str, args)], stderr=subprocess.PIPE)


def run_suite(repository, report, *files):
    subprocess.run([sys.executable, '-m', 'pytest', '-q', '-x', '--tb=short',
                    '--junitxml=' + str(report), *files], cwd=repository, check=True)
    cases = ET.parse(report).findall('.//testcase')
    assert cases and not any(case.find('skipped') is not None for case in cases), \
        'Essential historical regressions must execute, not SKIP'
    return len(cases)


def main():
    require_baseline_history(ROOT)
    with tempfile.TemporaryDirectory(prefix='wt-history-portability-') as temporary:
        snapshot, shallow = (Path(temporary) / name for name in ('snapshot', 'shallow'))
        snapshot.mkdir()
        # Include the current test patch, but never ignored runtime files/.git.
        names = git(ROOT, 'ls-files', '--cached', '--others', '--exclude-standard', '-z').split(b'\0')
        for name in names:
            if not name:
                continue
            relative = Path(name.decode())
            source, target = ROOT / relative, snapshot / relative
            if source.is_file():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
        git(snapshot, 'init', '-q', '--initial-branch=main')
        git(snapshot, 'add', '.')
        git(snapshot, '-c', 'user.name=Portability Test', '-c', 'user.email=test@example.invalid',
            '-c', 'commit.gpgsign=false', 'commit', '-qm', 'Disposable squashed snapshot')
        git(snapshot, 'clone', '-q', '--depth=1', snapshot.as_uri(), shallow)
        assert git(shallow, 'rev-parse', '--is-shallow-repository').strip() == b'true'
        try:
            require_baseline_history(shallow)
        except RuntimeError as error:
            assert 'git fetch --no-tags --depth=2 origin' in str(error)
        else:
            raise AssertionError('Missing baseline was incorrectly accepted')
        print('PASS: missing real baseline fails explicitly, without SKIP', flush=True)
        # Standalone checker needs no historical Git objects at all.
        passed = run_suite(shallow, Path(temporary) / 'checker.xml',
                           'deploy/tests/checker_migration_test.py')
        # Explicit offline preparation, equivalent to the documented origin fetch.
        git(shallow, 'fetch', '-q', '--no-tags', '--depth=2', ROOT,
            BASE + ':refs/work-tracker-tests/phase9b1-base')
        require_baseline_history(shallow)
        absent = subprocess.run(['git', '-C', str(shallow), 'cat-file', '-e', PR_ONLY],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        assert absent.returncode != 0, 'PR-only commit unexpectedly present'
        assert git(shallow, 'rev-parse', '--is-shallow-repository').strip() == b'true'
        print('PASS: shallow squashed snapshot has BASE + parent and no PR-only commit', flush=True)
        passed += run_suite(shallow, Path(temporary) / 'first-upgrade.xml',
                            'deploy/tests/historical_fixtures_test.py',
                            'deploy/tests/first_upgrade_test.py')
    print(f'Shallow/squash regressions: PASS={passed} FAIL=0 SKIP=0; preparation checks=2 PASS', flush=True)


if __name__ == '__main__':
    main()
