# Historical deployment test fixtures

`checker-v1/` contains exact, unedited Git blobs from the audited runtime at
`d3cb1a6849feeee9690235fe6545578fdb1954e8`. That commit identifies provenance
only: tests never resolve it or require it to exist in their Git object store.
These sources are test data, not supported installation scripts.

Every file has an explicitly pinned SHA-256 in
[`historical_fixtures.py`](../historical_fixtures.py). The standalone
`deploy/update_safety.py` must also match production `CHECKER_V1_SHA`:
`fcfb8ee82d5c404e721b3e8188b07c514b60f69a4139669be390060353cb6476`.
Tests verify bytes before loading, copying or adapting historical code. Do not
format or update these files when changing current deployment code. Changes to
a historical fixture or its pins require a separate provenance review.

The standalone checker migration tests execute this original implementation.
The old-release update integration test uses the historical updater, checker,
rollback helper, common/environment helpers, manifest and unit. Its backend and
frontend come from the real Phase 9B1 baseline: those application trees and
`.gitignore` were unchanged between that baseline and the fixture release.
Only OS paths/UIDs are adapted in disposable copies. Package/build/systemd
observations remain simulated; Git, SQLite, Alembic, Uvicorn, checker transitions,
atomic writes and interruption behavior execute real implementations.

## Required production baseline history

`first_upgrade_test.py` deliberately tests the actual production baseline
`5d974b100a92f0b7aed6aefa9939f0d5383c9cac`, including its original updater,
repository identity and ancestry checks. Its parent is needed for the
unknown-baseline rejection test. These are not PR-only source dependencies.
The full historical tree remains in disposable directories; it is not copied
into public fixtures, including any obsolete infrastructure examples.

Use a full-history test clone (`git clone` without `--depth`; CI checkout with
`fetch-depth: 0`). For an existing shallow **test checkout**, either run
`git fetch --unshallow origin` or explicitly supply only the required history:

```bash
git fetch --no-tags --depth=2 origin \
  5d974b100a92f0b7aed6aefa9939f0d5383c9cac:refs/work-tracker-tests/phase9b1-base
backend/.venv/bin/pytest -q deploy/tests/historical_fixtures_test.py \
  deploy/tests/checker_migration_test.py deploy/tests/first_upgrade_test.py
```

This setup changes no worktree files or checked-out branch. Do not run test
setup on a production checkout. Tests do not fetch network history themselves:
missing baseline objects produce an actionable failure, never SKIP/PASS. A
local fetch transfers the prepared baseline to disposable test clones, including
when the source is shallow and the baseline is under the test-only ref.

To check squash/shallow portability from a prepared checkout:

```bash
backend/.venv/bin/python deploy/tests/history_portability_check.py
```

The checker creates a one-commit snapshot of the current nonignored worktree,
clones it at depth 1, verifies that absent baseline history fails explicitly,
and runs standalone checker regressions without any historical Git objects.
It then supplies only the baseline and parent by an offline local fetch,
asserts that the old PR commit is absent and runs the remaining suites above in
that shallow checkout. Any failure is fatal; essential regressions are not
silently skipped. Local sockets and the backend test dependencies are required.
This does not validate real systemd/root/reboot behavior; that remains Phase 9C.
Run this check and other updater suites sequentially: first-upgrade deliberately
checks shared `/proc` for staged updater processes and refuses concurrent jobs.
