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

readiness_timeout=60
first_upgrade_directory=""
if [[ "${1:-}" == --first-upgrade ]]; then
    [[ "$#" -ge 2 ]] || exit 1
    first_upgrade_directory="$2"
    shift 2
fi
if (($#)); then
    [[ "$#" == 2 && "$1" == --readiness-timeout && "$2" =~ ^[1-9][0-9]{0,2}$ ]] || { printf 'Usage: update.sh [--readiness-timeout 5..300]\n' >&2; exit 1; }
    readiness_timeout="$2"
fi
((readiness_timeout >= 5 && readiness_timeout <= 300)) || { printf 'Readiness timeout must be 5..300 seconds.\n' >&2; exit 1; }

repository_was_clean=false
repository_changed=false
migration_started=false
service_was_active=false
backup_file=""
previous_commit=""
unit_backup=""
ingress_update=false
previous_manifest_sha=""
safety_runner=""
update_directory=""
helper_directory="${APP_DIR}/deploy"
first_candidate=""
first_previous=""
if [[ -n "${first_upgrade_directory}" ]]; then
    # Before executing any handoff code, require a private root-owned tree.
    python3 -I -c 'import os,pathlib,stat,sys
p=pathlib.Path(sys.argv[1]); root=pathlib.Path(sys.argv[2])
assert p.is_absolute() and p.parent == root and str(p)==os.path.abspath(p)
for f in (p/"deploy/first_upgrade.py",p/"handoff.json"):
 for q in (f,*f.parents):
  s=q.lstat(); assert s.st_uid==0 and not stat.S_ISLNK(s.st_mode) and not s.st_mode&0o022
' "${first_upgrade_directory}" "${BACKUP_DIR}"
    first_context="$(python3 "${first_upgrade_directory}/deploy/first_upgrade.py" accept \
        --directory "${first_upgrade_directory}" --updater "$0" --owner "$$")"
    read -r first_previous first_candidate <<<"${first_context}"
    helper_directory="${first_upgrade_directory}/deploy"
fi

[[ -f "${helper_directory}/update_rollback.sh" ]] || {
    printf '[work-tracker] ERROR: Missing rollback helpers.\n' >&2
    exit 1
}
# shellcheck source=deploy/update_rollback.sh
source "${helper_directory}/update_rollback.sh"

on_exit() {
    local exit_status="$1"
    trap - ERR EXIT
    set +e
    if ((exit_status != 0)) && [[ "${repository_changed}" == true ]]; then
        perform_update_rollback \
            "${APP_DIR}" "${previous_commit}" "${repository_was_clean}" "${migration_started}" \
            "${DATABASE_FILE}" "${backup_file}" "${SERVICE_FILE}" "${unit_backup}" \
            "${SERVICE_USER}" "${service_was_active}" "${safety_runner}"
    fi
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
source "${helper_directory}/common.sh"
require_supported_os
if [[ -z "${first_upgrade_directory}" ]]; then acquire_deployment_lock; fi
check_ingress_update_state
if [[ -z "${first_upgrade_directory}" ]]; then python3 "${APP_DIR}/deploy/update_safety.py" check; fi
if [[ -e /etc/work-tracker/ingress/cutover.json || -L /etc/work-tracker/ingress/cutover.json ]]; then
    ingress_update=true
    previous_manifest_sha="$(sha256sum "${MANIFEST_FILE}" | cut -d ' ' -f 1)"
    python3 "${APP_DIR}/deploy/ingress_cutover.py" update-check --manifest-sha "${previous_manifest_sha}"
fi
id "${SERVICE_USER}" >/dev/null 2>&1 || fail "Missing system user: ${SERVICE_USER}."
getent group "${SERVICE_USER}" >/dev/null 2>&1 || fail "Missing system group: ${SERVICE_USER}."

cd "${APP_DIR}"
[[ -z "$(git status --porcelain --untracked-files=normal)" ]] || fail "Repository has local changes. Resolve them before updating."
repository_was_clean=true
git remote get-url origin >/dev/null
log "Checking access to the main branch on GitHub."
git ls-remote --exit-code origin refs/heads/main >/dev/null

if [[ -n "${first_upgrade_directory}" ]]; then
    update_directory="${first_upgrade_directory}"
    backup_file="${update_directory}/online.db"
    safety_runner="${update_directory}/deploy/update_safety.py"
    unit_backup="${update_directory}/unit.before"
else
install -d -o root -g root -m 0700 "${BACKUP_DIR}"
backup_timestamp="$(date -u +'%Y%m%dT%H%M%SZ')"
update_directory="$(mktemp -d "${BACKUP_DIR}/update-${backup_timestamp}.XXXXXX")"
backup_file="${update_directory}/online.db"
log "Creating online SQLite backup: ${backup_file} (never used for automatic restore)."
sqlite3 "${DATABASE_FILE}" ".backup '${backup_file}'"
chmod 0600 "${backup_file}"
safety_runner="${update_directory}/update_safety.py"
install -m 0700 "${APP_DIR}/deploy/update_safety.py" "${safety_runner}"
unit_backup="${update_directory}/unit.before"
cp -p "${SERVICE_FILE}" "${unit_backup}"
chmod 0600 "${unit_backup}"
fi
if systemctl is-active --quiet work-tracker.service; then
    service_was_active=true
fi

previous_commit="$(git rev-parse HEAD)"
if [[ -n "${first_upgrade_directory}" ]]; then
    previous_commit="${first_previous}"
    candidate_commit="${first_candidate}"
else
log "Fetching the latest main branch."
git fetch origin main
if [[ "${ingress_update}" == true ]]; then
    python3 "${APP_DIR}/deploy/ingress_cutover.py" update-check --manifest-sha "${previous_manifest_sha}" --incoming
fi
candidate_commit="$(git rev-parse origin/main)"
python3 "${safety_runner}" begin --previous "${previous_commit}" --candidate "${candidate_commit}" \
    --directory "${update_directory}" --owner-pid "$$"
fi
# From this point, a killed updater leaves a durable startup gate.
repository_changed=true
systemctl daemon-reload
git merge --ff-only "${candidate_commit}"
new_commit="$(git rev-parse HEAD)"

[[ -f "${MANIFEST_FILE}" ]] || fail "Missing configuration manifest in the updated code."
# Load helper changes delivered by the new release before interpreting its manifest.
# shellcheck source=deploy/common.sh
source "${APP_DIR}/deploy/common.sh"
# shellcheck source=deploy/update_rollback.sh
source "${APP_DIR}/deploy/update_rollback.sh"
if [[ "${ingress_update}" == true ]]; then
    python3 "${APP_DIR}/deploy/ingress_cutover.py" update-check --manifest-sha "${previous_manifest_sha}"
fi
log "Validating required configuration and browser origins before dependency updates, migrations or restart."
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

if [[ "${ingress_update}" != true ]]; then
    install -o root -g root -m 0644 "${APP_DIR}/deploy/work-tracker.service" "${SERVICE_FILE}"
fi
systemctl daemon-reload
systemctl stop work-tracker.service
# Stop returns only after termination; require explicit inactive state and no
# MainPID before the migration checkpoint. This is not used as a restore proof.
[[ "$(systemctl show -p ActiveState --value work-tracker.service)" == inactive ]] || fail "Backend stop not confirmed."
[[ "$(systemctl show -p MainPID --value work-tracker.service)" == 0 ]] || fail "Backend PID remains after stop."
checkpoint_file="${update_directory}/pre-migration.db"
log "Creating SQLite checkpoint after service stop: ${checkpoint_file}"
sqlite3 "${DATABASE_FILE}" ".backup '${checkpoint_file}'"
chmod 0600 "${checkpoint_file}"
log "Applying database migrations. Any later failure requires manual recovery; no SQLite restore."
migration_started=true
python3 "${safety_runner}" migrating
(
    cd "${APP_DIR}/backend"
    runuser -u "${SERVICE_USER}" -- env DATABASE_URL="${database_url}" .venv/bin/alembic upgrade head
)
migration_status="$(
    cd "${APP_DIR}/backend"
    runuser -u "${SERVICE_USER}" -- env DATABASE_URL="${database_url}" .venv/bin/alembic current
)"

python3 "${safety_runner}" verifying
systemctl restart work-tracker.service
if [[ "${ingress_update}" == true ]]; then
    python3 "${APP_DIR}/deploy/ingress_cutover.py" update-verify --readiness-timeout "${readiness_timeout}"
else
    if [[ -n "${first_upgrade_directory}" ]]; then
        python3 "${first_upgrade_directory}/deploy/first_upgrade.py" verify --directory "${first_upgrade_directory}" --readiness-timeout "${readiness_timeout}"
        # Run as the service identity: no root-owned SQLite sidecars.
        runuser -u "${SERVICE_USER}" -- "${APP_DIR}/backend/.venv/bin/python" -c \
            'import sqlite3; c=sqlite3.connect("file:/var/lib/work-tracker/work_tracker.db?mode=ro",uri=True); assert c.execute("PRAGMA quick_check").fetchall()==[("ok",)]'
    else
        systemctl is-active --quiet work-tracker.service || fail "Service failed to start after update."
    fi
fi
python3 "${safety_runner}" commit

printf '\nWork Tracker update completed.\n'
printf 'Previous commit: %s\n' "${previous_commit}"
printf 'New commit:      %s\n' "${new_commit}"
printf 'Migration:       %s\n' "${migration_status:-current}"
printf 'Backup retained: %s\n\n' "${backup_file}"
systemctl --no-pager --full status work-tracker.service || true
