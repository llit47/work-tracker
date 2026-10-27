"""Pinned historical runtime sources; never resolve a PR commit at test runtime."""
import hashlib
from pathlib import Path
import subprocess


FIXTURES = Path(__file__).resolve().parent / 'fixtures/checker-v1'
BASE = '5d974b100a92f0b7aed6aefa9939f0d5383c9cac'
SHA256 = {
    'update.sh': '0b4032918fa38ac8db9b3f0751e538f8ecfd470f8748059ff3515fabfbaf2b0f',
    'deploy/common.sh': 'c16be6084198f31ded8927ca2f9c187babe3d19abde8531a4f2effb42e598f83',
    'deploy/env_file.py': '4f60d348d43afade9c3fafa2fd2111e7597267b582cc2de2ae562af1f50235ca',
    'deploy/update_rollback.sh': '4bbc72ece7d89d93e9a63dd4b28bff97255e1332dfa41efd6d4cac5fcc83f52f',
    'deploy/update_safety.py': 'fcfb8ee82d5c404e721b3e8188b07c514b60f69a4139669be390060353cb6476',
    'deploy/config.manifest': '07f12c87e0e2c532bb4d903774097c6f7091b161cf604511bc785d4d80f8ff7e',
    'deploy/work-tracker.service': '2e7c12cda74d1f18d47587511c8a2c2208670980e7b0e895e75b6c4cdb80887d',
}


def historical_file(name, directory=FIXTURES):
    expected = SHA256[name]
    data = (directory / name).read_bytes()
    if hashlib.sha256(data).hexdigest() != expected:
        raise ValueError(f'Historical fixture SHA-256 mismatch: {name}')
    if name == 'deploy/update_safety.py':
        from update_safety import CHECKER_V1_SHA
        if expected != CHECKER_V1_SHA:
            raise ValueError('Historical checker pin disagrees with CHECKER_V1_SHA')
    return data


def require_baseline_history(repository):
    # BASE is a real production baseline, unlike the PR-only runtime fixtures.
    # Its parent is used by the unknown-baseline rejection regression.
    for revision in (BASE + '^{commit}', BASE + '^1^{commit}'):
        result = subprocess.run(['git', '-C', str(repository), 'cat-file', '-e', revision],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if result.returncode:
            raise RuntimeError(
                'First-upgrade regressions require the real Phase 9B1 baseline and its parent. '
                'Use a full-history checkout or prepare this checkout explicitly with: '
                f'git fetch --no-tags --depth=2 origin {BASE}:refs/work-tracker-tests/phase9b1-base. '
                'Tests never fetch network history or silently skip this requirement.')
