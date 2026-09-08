# Work Tracker — agent instructions

## Purpose

Work Tracker is a LAN-only application for recording work entry/exit events received from Home Assistant, deriving work sessions, calculating pay, allowing auditable corrections, and presenting/exporting monthly data.

Backend:
- Python
- FastAPI
- SQLAlchemy
- SQLite
- Alembic

Frontend:
- React
- TypeScript
- Vite

Production uses a systemd service on Debian/Ubuntu-style Linux. FastAPI serves the built frontend in production.

## Sources of truth

Before substantial work, read the relevant repository documentation.

Use these responsibilities:

- `AGENTS.md` — durable project rules and engineering invariants.
- `ROADMAP.md` — product scope, phase status, deferred work, and acceptance criteria.
- `PROJECT_STATUS.md` — current implemented state and important assumptions.
- `README.md` — installation, development, operation, API overview, and deployment usage.
- Code, tests, package configuration, Alembic history, and deploy configuration — authoritative for exact current implementation details.

Do not silently resolve contradictions between documentation and implementation. Inspect the code/tests/configuration, report the discrepancy, and update stale documentation when it is in scope.

Explicit task instructions take precedence over this file.

## Scope discipline

Implement only the requested scope.

Do not opportunistically implement later roadmap phases or deferred features. Features marked `DEFERRED` in `ROADMAP.md` must not be introduced unless the task explicitly changes their roadmap status.

Avoid unrelated refactors in focused changes. If a correct solution requires a significantly broader redesign than requested, explain the blocker instead of silently expanding scope.

## Core data invariant

Raw Home Assistant work events are immutable source data.

Never update, rewrite, replace, or delete a raw Home Assistant event in order to correct work time.

Corrections are a separate auditable layer.

The conceptual pipeline is:

`raw events → corrections → effective events → canonical pairing → valid work time → pay calculation → dashboard/report presentation`

Preserve this separation.

## Work-time pairing invariants

Never guess working time.

Only an unambiguous `entry → exit` pair creates finalized work time.

Canonical pairing is per technical location and deterministic by `(event_timestamp_utc, id)`.

Canonical outcomes include:

- `valid`
- `missing_exit`
- `duplicate_entry`
- `orphan_exit`
- `unusually_long_session`
- `ambiguous_timestamp`

Anomalies must remain visible rather than being converted into invented work.

A session longer than 16 hours is anomalous. A session of exactly 16 hours is valid.

A valid session crossing midnight or a month boundary belongs entirely to the local date/month of its entry.

Multiple valid sessions on one day are summed.

Only valid finalized sessions contribute finalized work time and work days.

## Time and timezone rules

Preserve both:

- the original/effective timezone-aware event timestamp;
- the corresponding UTC instant.

Durations are calculated from UTC instants.

Historical work-summary/report ownership uses the effective entry timestamp's local date according to the canonical work-time rules.

The live dashboard uses the requested IANA timezone to determine its local `today` and current month; durations still come from UTC instants.

Do not mix browser-local calendar dates with event-local dates to identify the same event or session. When identity matters, compare timestamps as instants rather than raw ISO strings.

Newly accepted work-event and correction timestamps must remain timezone-aware.

## Live dashboard rules

The backend dashboard is authoritative for live work status.

Do not reconstruct authoritative `working` / `outside` / `ambiguous` state in React from raw events or browser-local heuristics.

A `missing_exit` item may be presented as a currently running shift only when it contains exactly one entry and no exit, the dashboard reports `working` with a current session and a non-null running duration for today, and the entry matches the dashboard session as the same UTC instant.

All other `missing_exit` items remain actionable anomalies, including ambiguous or unmatched open entries.

Ambiguous states must fail safe: prefer leaving an anomaly visible over hiding a real problem.

Do not create synthetic exit events for live presentation.

Open shifts do not contribute finalized pay before a real valid exit exists.

The frontend may update the visible live timer locally between dashboard polls, but this must not alter persisted/domain state.

Meaningful dashboard state changes must keep the displayed monthly work/pay data coherent; normal live-timer progression must not cause unnecessary monthly refetches.

## Corrections

Corrections must remain auditable and separate from raw Home Assistant events.

Supported correction concepts are:

- timestamp override;
- ignored raw event;
- manual event.

Undo removes the correction layer; it must not mutate the original raw event.

Manual events must remain distinguishable from Home Assistant source events.

After corrections, calculations must use the effective event stream and the same canonical pairing rules as uncorrected data.

## Pay invariants

Pay is backend-authoritative.

Do not implement an independent salary calculation in the frontend.

Only finalized valid sessions contribute to pay.

Historical work/pay uses the effective entry date under the canonical work-time rules. Live dashboard pay uses the entry date interpreted in the dashboard's requested IANA timezone, matching dashboard month/day semantics.

Use `Decimal`-based monetary arithmetic and the existing rounding rules. Do not replace them with floating-point calculations.

Preserve historical rate behavior.

Do not silently combine incompatible currencies.

## Presentation rules

Normal user-facing application text should be natural Polish.

Code identifiers, API names, database names, Git names, and technical identifiers should remain English unless an existing external contract requires otherwise.

Normal user-facing time presentation in the UI, CSV, and PDF does not show seconds.

Seconds remain preserved internally in timestamps, domain/API calculations, corrections, work duration, and pay calculations.

Displayed minute-based duration represents completed minutes; do not round stored/calculated work time to the nearest minute.

## Location identifiers and aliases

Technical location identifiers are domain keys, for example `gabinet_zabki`.

Configured display names are presentation-only, for example `ARTE Stomatologia`.

Never replace canonical location identifiers in raw events, corrections, pairing, or stored domain relationships with display aliases.

Normal UI/PDF/CSV presentation uses the configured alias, with fallback to the canonical identifier when no alias exists. Administrative settings may also show the canonical identifier explicitly.

Changing an alias must not rewrite historical work data.

CSV output containing configurable location aliases must remain protected from spreadsheet formula injection without modifying the stored alias or PDF/UI presentation.

## Frontend architecture

Keep domain-authoritative calculations in the backend.

The frontend may format data, manage presentation state, navigate months, manage forms, display live timers, and trigger authoritative API refreshes.

The frontend must not duplicate canonical:

- work-session pairing;
- anomaly classification;
- salary calculation;
- authoritative dashboard status.

Preserve strict TypeScript compatibility.

## Database and migrations

Use Alembic for schema changes.

Do not rewrite an already-applied migration to change production history. Create a new migration only when the schema/data migration actually requires one.

Review generated migrations manually.

Migrations must preserve existing production data unless destructive behavior is explicitly requested and approved.

When a task does not require a migration, do not create one.

After migration-related changes, verify the Alembic upgrade path and current head.

## Deployment safety

Production data and configuration live outside the Git checkout. Do not move them into the repository.

Production conventions include:

- application code: `/opt/work-tracker`;
- configuration: `/etc/work-tracker/work-tracker.env`;
- SQLite data: `/var/lib/work-tracker/work_tracker.db`;
- backups: `/var/backups/work-tracker`;
- systemd service: `work-tracker.service`.

Preserve updater safety properties, including clean-worktree checks, SQLite backup before update, fast-forward-only updates, migration safety, rollback behavior, and preservation of existing data/configuration.

Required deployment configuration belongs in `deploy/config.manifest`.

Never commit or print real secrets or webhook tokens.

Work Tracker remains LAN-only unless an explicit task changes that decision. Do not introduce public exposure, tunnels, reverse proxies, or HTTPS deployment architecture as part of unrelated work.

## Validation

Use the repository's actual configured commands. Do not invent tools that are not configured.

For backend changes, normally run from `backend/` with the project virtual environment active:

```bash
pytest
```

For frontend changes, run:

```bash
cd frontend
npm test
npm run build
```

`npm run build` includes TypeScript checking.

For every patch, run:

```bash
git diff --check
```

If installation, updater, permissions, config-manifest, systemd, or deployment shell code changes, also run the relevant deployment/shell tests documented in `README.md` and present under `deploy/tests/`.

Some deployment tests require root privileges. If the environment cannot run a required check, state exactly which check was not run and why. Never report an unexecuted test as passing.

Focused tests are useful during development, but before declaring a substantial change ready, run the relevant complete backend/frontend suite for the affected area.

## Documentation maintenance

If a PR implements or completes roadmap scope:

- update `PROJECT_STATUS.md`;
- update the corresponding status/acceptance criteria in `ROADMAP.md`.

If installation, deployment, API usage, developer commands, or operational behavior changes, update `README.md`.

Do not copy transient PR-specific details, current commit SHAs, or temporary bug history into `AGENTS.md`.

Keep `AGENTS.md` focused on durable rules.

## Git and PR workflow

Before changing code, inspect the current branch and `git status`, and determine whether the task refers to an existing PR/branch.

If a task explicitly refers to an existing branch or PR, continue there unless instructed otherwise.

Do not create unrelated branches or PRs.

Do not merge a PR unless the user explicitly requests the merge.

Do not force-push or rewrite existing history unless explicitly instructed.

Before declaring work complete:

- run the required validation;
- inspect `git status`;
- inspect the final cumulative diff;
- check for unrelated files, local artifacts, generated secrets, or accidental data;
- confirm the implementation remains within requested scope.

## Review behavior

Treat correctness, data-integrity, security, and regression findings as blockers when they materially affect the requested change.

Do not broaden a focused bugfix merely to clean unrelated low-severity/nit findings.

When addressing review feedback:

- understand the root cause;
- preserve previously fixed regressions;
- add behavioral regression tests where appropriate;
- review the final cumulative diff again.

Prefer a simpler robust invariant over accumulating narrow heuristics.

## Definition of done

A change is not complete merely because the code compiles.

Before reporting a task as ready:

- requested behavior is implemented;
- relevant regression tests exist where appropriate;
- applicable test/build checks pass;
- schema/migration impact is understood;
- documentation is updated when required;
- no secrets or local artifacts are present;
- the final diff is focused;
- known blockers are reported clearly;
- no merge has been performed unless explicitly requested.
