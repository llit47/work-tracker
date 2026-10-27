#!/usr/bin/env bash
set -Eeuo pipefail

TEST_DIR="$(mktemp -d)"
trap 'rm -rf "${TEST_DIR}"' EXIT

APP_DIR="${TEST_DIR}/app"
STATE_DIR="${TEST_DIR}/state"
BIN_DIR="${TEST_DIR}/bin"
DATABASE_FILE="${TEST_DIR}/work_tracker.db"
BACKUP_FILE="${TEST_DIR}/backup.db"
SERVICE_FILE="${TEST_DIR}/work-tracker.service"
UNIT_BACKUP="${TEST_DIR}/previous.service"
ENV_FILE="${TEST_DIR}/work-tracker.env"
SERVICE_USER="$(id -un)"

mkdir -p "${APP_DIR}/backend/.venv/bin" "${APP_DIR}/frontend" "${APP_DIR}/deploy" "${STATE_DIR}" "${BIN_DIR}"

printf '#!/usr/bin/env bash\nprintf "pip %%s\\n" "$*" >>"${ROLLBACK_TEST_STATE_DIR}/commands"\n' >"${APP_DIR}/backend/.venv/bin/pip"
printf '#!/usr/bin/env bash\nprintf "npm %%s\\n" "$*" >>"${ROLLBACK_TEST_STATE_DIR}/commands"\n' >"${BIN_DIR}/npm"
printf '%s\n' \
    '#!/usr/bin/env bash' \
    'printf "systemctl %s\n" "$*" >>"${ROLLBACK_TEST_STATE_DIR}/commands"' \
    'case "$1" in' \
    '  is-active) [[ -f "${ROLLBACK_TEST_STATE_DIR}/active" ]] ;;' \
    '  stop) rm -f "${ROLLBACK_TEST_STATE_DIR}/active" ;;' \
    '  restart) touch "${ROLLBACK_TEST_STATE_DIR}/active" ;;' \
    '  daemon-reload) true ;;' \
    '  *) true ;;' \
    'esac' >"${BIN_DIR}/systemctl"
chmod +x "${APP_DIR}/backend/.venv/bin/pip" "${BIN_DIR}/npm" "${BIN_DIR}/systemctl"
printf '{}\n' >"${APP_DIR}/frontend/package-lock.json"
cat >"${APP_DIR}/deploy/update_safety.py" <<'PYTHON'
# Shell-flow double only; durable gate behavior is covered in update_safety_test.
import os
import sys
with open(os.environ['ROLLBACK_TEST_STATE_DIR'] + '/commands', 'a') as stream:
    stream.write('gate ' + sys.argv[1] + '\n')
PYTHON
printf 'old release\n' >"${APP_DIR}/release-marker"

git -C "${APP_DIR}" init -q
git -C "${APP_DIR}" config user.name 'Rollback Test'
git -C "${APP_DIR}" config user.email 'rollback@example.invalid'
git -C "${APP_DIR}" add .
git -C "${APP_DIR}" commit -qm 'old release'
PREVIOUS_COMMIT="$(git -C "${APP_DIR}" rev-parse HEAD)"
printf 'new release\n' >"${APP_DIR}/release-marker"
git -C "${APP_DIR}" add release-marker
git -C "${APP_DIR}" commit -qm 'new release'
NEW_COMMIT="$(git -C "${APP_DIR}" rev-parse HEAD)"

printf 'old unit\n' >"${UNIT_BACKUP}"
printf 'new unit\n' >"${SERVICE_FILE}"
printf 'WEBHOOK_TOKEN=secret-value-that-must-not-change\n' >"${ENV_FILE}"
cp "${ENV_FILE}" "${ENV_FILE}.snapshot"
printf 'backup database\n' >"${BACKUP_FILE}"
export ROLLBACK_TEST_STATE_DIR="${STATE_DIR}"
export PATH="${BIN_DIR}:${PATH}"

# shellcheck source=../update_rollback.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/update_rollback.sh"

permission_calls=0
set_application_permissions() {
    [[ "$1" == "${APP_DIR}" && "$2" == root && "$3" == "${SERVICE_USER}" && "$4" == root ]]
    permission_calls=$((permission_calls + 1))
}

# Before migration: repository/unit are restored, database is untouched.
printf 'live database before migration\n' >"${DATABASE_FILE}"
touch "${STATE_DIR}/active"
perform_update_rollback \
    "${APP_DIR}" "${PREVIOUS_COMMIT}" true false "${DATABASE_FILE}" "${BACKUP_FILE}" \
    "${SERVICE_FILE}" "${UNIT_BACKUP}" "${SERVICE_USER}" true
[[ "$(git -C "${APP_DIR}" rev-parse HEAD)" == "${PREVIOUS_COMMIT}" ]]
grep -qx 'live database before migration' "${DATABASE_FILE}"
grep -qx 'old unit' "${SERVICE_FILE}"
cmp -s "${ENV_FILE}" "${ENV_FILE}.snapshot"
[[ -f "${STATE_DIR}/active" ]]

# Once Alembic might have run: no SQLite/code/unit restore, even with a
# missing/corrupt backup. Keep sidecars and accepted writes; never restart old.
for backup_state in present missing corrupt; do
    git -C "${APP_DIR}" reset --hard -q "${NEW_COMMIT}"
    printf 'accepted events and partially migrated database\n' >"${DATABASE_FILE}"
    printf 'live wal\n' >"${DATABASE_FILE}-wal"
    printf 'live shm\n' >"${DATABASE_FILE}-shm"
    printf 'new unit\n' >"${SERVICE_FILE}"
    candidate_backup="${BACKUP_FILE}"
    case "${backup_state}" in
        missing) candidate_backup="${TEST_DIR}/missing.db" ;;
        corrupt) printf 'corrupt backup\n' >"${BACKUP_FILE}" ;;
    esac
    touch "${STATE_DIR}/active"
    if perform_update_rollback \
        "${APP_DIR}" "${PREVIOUS_COMMIT}" true true "${DATABASE_FILE}" "${candidate_backup}" \
        "${SERVICE_FILE}" "${UNIT_BACKUP}" "${SERVICE_USER}" true 2>"${TEST_DIR}/failure-output"; then
        printf 'Expected manual recovery refusal was not reported.\n' >&2
        exit 1
    fi
    grep -q 'UPDATE REQUIRES MANUAL RECOVERY' "${TEST_DIR}/failure-output"
    grep -q 'Automatic database/code rollback REFUSED' "${TEST_DIR}/failure-output"
    grep -qx 'accepted events and partially migrated database' "${DATABASE_FILE}"
    grep -qx 'live wal' "${DATABASE_FILE}-wal"
    grep -qx 'live shm' "${DATABASE_FILE}-shm"
    grep -qx 'new unit' "${SERVICE_FILE}"
    [[ "$(git -C "${APP_DIR}" rev-parse HEAD)" == "${NEW_COMMIT}" ]]
    [[ ! -f "${STATE_DIR}/active" ]]
    cmp -s "${ENV_FILE}" "${ENV_FILE}.snapshot"
done
grep -qx 'gate install' "${STATE_DIR}/commands"
grep -qx 'gate hold' "${STATE_DIR}/commands"
[[ "${permission_calls}" -eq 1 ]]
# Legacy callers cannot restore a historical snapshot either.
if restore_database_backup "${DATABASE_FILE}" "${BACKUP_FILE}"; then exit 1; fi
grep -qx 'accepted events and partially migrated database' "${DATABASE_FILE}"

printf 'Update rollback tests passed.\n'

# First 8B1-A update: missing operator config fails before migration/new service.
# Late raw events must survive this pre-migration rollback; env stays untouched.
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/common.sh"
set_application_permissions() { return 0; }
git -C "${APP_DIR}" reset --hard -q "${NEW_COMMIT}"
printf 'BROWSER_ALLOWED_ORIGINS|required|plain||Explicit browser origin|browser_origins\n' >"${TEST_DIR}/new.manifest"
printf 'live data including new raw events\n' >"${DATABASE_FILE}"
touch "${STATE_DIR}/active"
if (exec 3</dev/null; sync_missing_required_config "${TEST_DIR}/new.manifest" "${ENV_FILE}") >"${TEST_DIR}/config-error" 2>&1; then
    printf 'Missing origin unexpectedly accepted.\n' >&2
    exit 1
fi
grep -q BROWSER_ALLOWED_ORIGINS "${TEST_DIR}/config-error"
perform_update_rollback \
    "${APP_DIR}" "${PREVIOUS_COMMIT}" true false "${DATABASE_FILE}" "${BACKUP_FILE}" \
    "${SERVICE_FILE}" "${UNIT_BACKUP}" "${SERVICE_USER}" true
grep -qx 'live data including new raw events' "${DATABASE_FILE}"
cmp -s "${ENV_FILE}" "${ENV_FILE}.snapshot"
[[ "$(git -C "${APP_DIR}" rev-parse HEAD)" == "${PREVIOUS_COMMIT}" ]]
[[ -f "${STATE_DIR}/active" ]]
printf 'Missing browser configuration rollback test passed.\n'
