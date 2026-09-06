#!/usr/bin/env bash
set -Eeuo pipefail
umask 0077

if [[ -z "${WORK_TRACKER_STAGED_UPDATER:-}" ]]; then
    staged_updater="$(mktemp /tmp/work-tracker-update.XXXXXX)"
    install -m 0700 "$0" "${staged_updater}"
    exec env WORK_TRACKER_STAGED_UPDATER="${staged_updater}" "${staged_updater}" "$@"
fi
[[ "$0" == "${WORK_TRACKER_STAGED_UPDATER}" && "$0" == /tmp/work-tracker-update.* ]] || {
    printf '[work-tracker] ERROR: Invalid staged updater path.\n' >&2
    exit 1
}
trap 'rm -f "${WORK_TRACKER_STAGED_UPDATER}"' EXIT

readonly APP_DIR="/opt/work-tracker"
readonly CONFIG_FILE="/etc/work-tracker/work-tracker.env"
readonly DATABASE_FILE="/var/lib/work-tracker/work_tracker.db"
readonly BACKUP_DIR="/var/backups/work-tracker"
readonly SERVICE_FILE="/etc/systemd/system/work-tracker.service"
readonly SERVICE_USER="work-tracker"
readonly MANIFEST_FILE="${APP_DIR}/deploy/config.manifest"

repository_was_clean=false
repository_changed=false
migration_started=false
service_was_active=false
backup_file=""
previous_commit=""
unit_backup=""

[[ -f "${APP_DIR}/deploy/update_rollback.sh" ]] || {
    printf '[work-tracker] ERROR: Missing rollback helpers.\n' >&2
    exit 1
}
# shellcheck source=deploy/update_rollback.sh
source "${APP_DIR}/deploy/update_rollback.sh"

on_exit() {
    local exit_status="$1"
    trap - ERR EXIT
    set +e
    if ((exit_status != 0)) && [[ "${repository_changed}" == true ]]; then
        perform_update_rollback \
            "${APP_DIR}" "${previous_commit}" "${repository_was_clean}" "${migration_started}" \
            "${DATABASE_FILE}" "${backup_file}" "${SERVICE_FILE}" "${unit_backup}" \
            "${SERVICE_USER}" "${service_was_active}"
    fi
    [[ -z "${unit_backup}" ]] || rm -f "${unit_backup}"
    rm -f "${WORK_TRACKER_STAGED_UPDATER}"
    exit "${exit_status}"
}
trap 'on_exit "$?"' EXIT

on_error() {
    local line="$1"
    printf '[work-tracker] ERROR: update failed near line %s. Any database backup has been retained.\n' "${line}" >&2
    printf '[work-tracker] Inspect: systemctl status work-tracker && journalctl -u work-tracker -n 100\n' >&2
}
trap 'on_error "${LINENO}"' ERR

[[ "${EUID}" -eq 0 ]] || { printf '[work-tracker] ERROR: Run updater as root or with sudo.\n' >&2; exit 1; }
[[ -d "${APP_DIR}/.git" ]] || { printf '[work-tracker] ERROR: No installation found at %s.\n' "${APP_DIR}" >&2; exit 1; }
[[ -f "${CONFIG_FILE}" ]] || { printf '[work-tracker] ERROR: Missing configuration: %s.\n' "${CONFIG_FILE}" >&2; exit 1; }
[[ -f "${DATABASE_FILE}" ]] || { printf '[work-tracker] ERROR: Missing database: %s.\n' "${DATABASE_FILE}" >&2; exit 1; }
[[ -f "${SERVICE_FILE}" ]] || { printf '[work-tracker] ERROR: Missing systemd unit: %s.\n' "${SERVICE_FILE}" >&2; exit 1; }
[[ -f "${APP_DIR}/deploy/common.sh" ]] || { printf '[work-tracker] ERROR: Missing deployment helpers.\n' >&2; exit 1; }

# shellcheck source=deploy/common.sh
source "${APP_DIR}/deploy/common.sh"
require_supported_os
id "${SERVICE_USER}" >/dev/null 2>&1 || fail "Missing system user: ${SERVICE_USER}."
getent group "${SERVICE_USER}" >/dev/null 2>&1 || fail "Missing system group: ${SERVICE_USER}."

cd "${APP_DIR}"
[[ -z "$(git status --porcelain --untracked-files=normal)" ]] || fail "Repository has local changes. Resolve them before updating."
repository_was_clean=true
git remote get-url origin >/dev/null
log "Checking access to the main branch on GitHub."
git ls-remote --exit-code origin refs/heads/main >/dev/null

install -d -o root -g root -m 0700 "${BACKUP_DIR}"
backup_timestamp="$(date -u +'%Y%m%dT%H%M%SZ')"
backup_file="${BACKUP_DIR}/work_tracker-${backup_timestamp}.db"
log "Creating SQLite backup: ${backup_file}"
sqlite3 "${DATABASE_FILE}" ".backup '${backup_file}'"
chmod 0600 "${backup_file}"

unit_backup="$(mktemp /tmp/work-tracker-service.XXXXXX)"
cp -p "${SERVICE_FILE}" "${unit_backup}"
if systemctl is-active --quiet work-tracker.service; then
    service_was_active=true
fi

previous_commit="$(git rev-parse HEAD)"
log "Fetching the latest main branch."
git fetch origin main
git merge --ff-only origin/main
repository_changed=true
new_commit="$(git rev-parse HEAD)"

[[ -f "${MANIFEST_FILE}" ]] || fail "Missing configuration manifest in the updated code."
# Load helper changes delivered by the new release before interpreting its manifest.
# shellcheck source=deploy/common.sh
source "${APP_DIR}/deploy/common.sh"
# shellcheck source=deploy/update_rollback.sh
source "${APP_DIR}/deploy/update_rollback.sh"
sync_missing_required_config "${MANIFEST_FILE}" "${CONFIG_FILE}"
validate_required_config "${MANIFEST_FILE}" "${CONFIG_FILE}"
chown root:"${SERVICE_USER}" "${CONFIG_FILE}"
chmod 0640 "${CONFIG_FILE}"

log "Updating backend dependencies."
[[ -x "${APP_DIR}/backend/.venv/bin/pip" ]] || fail "Backend virtualenv is missing. Run the installer first."
"${APP_DIR}/backend/.venv/bin/pip" install --upgrade "${APP_DIR}/backend"

log "Updating and building frontend."
(
    cd "${APP_DIR}/frontend"
    if [[ -f package-lock.json ]]; then
        npm ci --no-audit --no-fund
    else
        npm install --no-audit --no-fund --no-package-lock
    fi
    npm run build
)

log "Restoring read-only application access for the service user."
set_application_permissions "${APP_DIR}" root "${SERVICE_USER}" root

read_env_value "${CONFIG_FILE}" DATABASE_URL
database_url="${REPLY}"
validate_config_value database_url "${database_url}" || fail "DATABASE_URL is missing or invalid."

install -o root -g root -m 0644 "${APP_DIR}/deploy/work-tracker.service" "${SERVICE_FILE}"
systemctl daemon-reload
systemctl stop work-tracker.service
log "Applying database migrations."
migration_started=true
(
    cd "${APP_DIR}/backend"
    runuser -u "${SERVICE_USER}" -- env DATABASE_URL="${database_url}" .venv/bin/alembic upgrade head
)
migration_status="$(
    cd "${APP_DIR}/backend"
    runuser -u "${SERVICE_USER}" -- env DATABASE_URL="${database_url}" .venv/bin/alembic current
)"

systemctl restart work-tracker.service
systemctl is-active --quiet work-tracker.service || fail "Service failed to start after update."

printf '\nWork Tracker update completed.\n'
printf 'Previous commit: %s\n' "${previous_commit}"
printf 'New commit:      %s\n' "${new_commit}"
printf 'Migration:       %s\n' "${migration_status:-current}"
printf 'Backup retained: %s\n\n' "${backup_file}"
systemctl --no-pager --full status work-tracker.service || true
