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

mkdir -p "${APP_DIR}/backend/.venv/bin" "${APP_DIR}/frontend" "${STATE_DIR}" "${BIN_DIR}"

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

# During/after migration: service is stopped and database backup is restored.
git -C "${APP_DIR}" reset --hard -q "${NEW_COMMIT}"
printf 'partially migrated database\n' >"${DATABASE_FILE}"
printf 'stale sidecar\n' >"${DATABASE_FILE}-wal"
printf 'new unit\n' >"${SERVICE_FILE}"
touch "${STATE_DIR}/active"
perform_update_rollback \
    "${APP_DIR}" "${PREVIOUS_COMMIT}" true true "${DATABASE_FILE}" "${BACKUP_FILE}" \
    "${SERVICE_FILE}" "${UNIT_BACKUP}" "${SERVICE_USER}" true
grep -qx 'backup database' "${DATABASE_FILE}"
[[ ! -e "${DATABASE_FILE}-wal" ]]
[[ -f "${BACKUP_FILE}" ]]
[[ "$(git -C "${APP_DIR}" rev-parse HEAD)" == "${PREVIOUS_COMMIT}" ]]
cmp -s "${ENV_FILE}" "${ENV_FILE}.snapshot"
[[ -f "${STATE_DIR}/active" ]]

# Failed DB restore must leave the service stopped and report diagnostics.
git -C "${APP_DIR}" reset --hard -q "${NEW_COMMIT}"
printf 'partially migrated again\n' >"${DATABASE_FILE}"
touch "${STATE_DIR}/active"
if perform_update_rollback \
    "${APP_DIR}" "${PREVIOUS_COMMIT}" true true "${DATABASE_FILE}" "${TEST_DIR}/missing-backup.db" \
    "${SERVICE_FILE}" "${UNIT_BACKUP}" "${SERVICE_USER}" true 2>"${TEST_DIR}/failure-output"; then
    printf 'Expected rollback failure was not reported.\n' >&2
    exit 1
fi
grep -q 'ROLLBACK INCOMPLETE' "${TEST_DIR}/failure-output"
grep -q "${PREVIOUS_COMMIT}" "${TEST_DIR}/failure-output"
grep -q 'missing-backup.db' "${TEST_DIR}/failure-output"
[[ ! -f "${STATE_DIR}/active" ]]
cmp -s "${ENV_FILE}" "${ENV_FILE}.snapshot"

printf 'Update rollback tests passed.\n'
