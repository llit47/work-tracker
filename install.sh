#!/usr/bin/env bash
set -Eeuo pipefail
umask 0027

readonly APP_DIR="/opt/work-tracker"
readonly CONFIG_DIR="/etc/work-tracker"
readonly CONFIG_FILE="${CONFIG_DIR}/work-tracker.env"
readonly DATA_DIR="/var/lib/work-tracker"
readonly DATABASE_FILE="${DATA_DIR}/work_tracker.db"
readonly BACKUP_DIR="/var/backups/work-tracker"
readonly SERVICE_FILE="/etc/systemd/system/work-tracker.service"
readonly SERVICE_USER="work-tracker"
readonly REPOSITORY_URL="https://github.com/llit47/work-tracker.git"

fail_early() {
    printf '[work-tracker] ERROR: %s\n' "$*" >&2
    exit 1
}

on_install_error() {
    printf '[work-tracker] ERROR: installation failed near line %s. Review the command output above.\n' "$1" >&2
}
trap 'on_install_error "${LINENO}"' ERR

[[ "${EUID}" -eq 0 ]] || fail_early "Run the installer as root or with sudo."
[[ -r /etc/os-release ]] || fail_early "Cannot identify the operating system."
# shellcheck disable=SC1091
. /etc/os-release
case "${ID:-}" in
    debian|ubuntu) ;;
    *) fail_early "Only Debian and Ubuntu are supported (detected: ${ID:-unknown})." ;;
esac
[[ -r /dev/tty ]] || fail_early "The installer requires an interactive terminal."
[[ ! -e "${APP_DIR}" ]] || fail_early "${APP_DIR} already exists. Use its update.sh or inspect the directory manually."

printf '[work-tracker] Installing required system packages...\n'
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y ca-certificates curl git gnupg openssl python3 python3-pip python3-venv sqlite3

node_major=0
if command -v node >/dev/null 2>&1; then
    node_major="$(node --version | sed -E 's/^v([0-9]+).*/\1/')"
fi
if ((node_major < 18)); then
    nodesource_setup="$(mktemp)"
    trap 'rm -f "${nodesource_setup}"' EXIT
    curl -fsSL https://deb.nodesource.com/setup_22.x -o "${nodesource_setup}"
    bash "${nodesource_setup}"
    rm -f "${nodesource_setup}"
    trap - EXIT
    DEBIAN_FRONTEND=noninteractive apt-get install -y nodejs
fi
if ! command -v npm >/dev/null 2>&1; then
    DEBIAN_FRONTEND=noninteractive apt-get install -y npm
fi
command -v npm >/dev/null 2>&1 || fail_early "npm installation failed."
node_major="$(node --version | sed -E 's/^v([0-9]+).*/\1/')"
((node_major >= 18)) || fail_early "Node.js 18 or newer is required."
command -v systemctl >/dev/null 2>&1 || fail_early "systemd is required."

printf '[work-tracker] Cloning application code...\n'
git clone --branch main --single-branch "${REPOSITORY_URL}" "${APP_DIR}"

# shellcheck source=deploy/common.sh
source "${APP_DIR}/deploy/common.sh"
exec 3</dev/tty

if ! getent group "${SERVICE_USER}" >/dev/null 2>&1; then
    groupadd --system "${SERVICE_USER}"
fi
if ! id "${SERVICE_USER}" >/dev/null 2>&1; then
    useradd --system --gid "${SERVICE_USER}" --home-dir "${DATA_DIR}" --shell /usr/sbin/nologin "${SERVICE_USER}"
fi

install -d -o root -g "${SERVICE_USER}" -m 0750 "${CONFIG_DIR}"
install -d -o "${SERVICE_USER}" -g "${SERVICE_USER}" -m 0750 "${DATA_DIR}"
install -d -o root -g root -m 0700 "${BACKUP_DIR}"

if [[ ! -e "${CONFIG_FILE}" ]]; then
    prompt_config_value plain "0.0.0.0" "Backend listen address" host
    app_host="${REPLY}"
    prompt_config_value plain "8000" "Backend port" port
    app_port="${REPLY}"
    prompt_config_value secret "" "Webhook token (leave empty to generate one)" token
    webhook_token="${REPLY}"
    prompt_config_value plain "" "Browser CORS origins, comma-separated (empty for same-origin)" cors
    cors_origins="${REPLY}"

    config_temp="$(mktemp "${CONFIG_DIR}/work-tracker.env.XXXXXX")"
    {
        printf '# Managed configuration for Work Tracker.\n'
        printf 'APP_HOST=%s\n' "${app_host}"
        printf 'APP_PORT=%s\n' "${app_port}"
        printf 'WEBHOOK_TOKEN=%s\n' "${webhook_token}"
        printf 'DATABASE_URL=sqlite:////var/lib/work-tracker/work_tracker.db\n'
        printf 'CORS_ORIGINS=%s\n' "${cors_origins}"
    } >"${config_temp}"
    chown root:"${SERVICE_USER}" "${config_temp}"
    chmod 0640 "${config_temp}"
    mv "${config_temp}" "${CONFIG_FILE}"
else
    log "Keeping existing configuration at ${CONFIG_FILE}."
    sync_missing_required_config "${APP_DIR}/deploy/config.manifest" "${CONFIG_FILE}"
fi
unset webhook_token
validate_required_config "${APP_DIR}/deploy/config.manifest" "${CONFIG_FILE}"
chown root:"${SERVICE_USER}" "${CONFIG_FILE}"
chmod 0640 "${CONFIG_FILE}"

log "Creating Python virtual environment and installing backend."
python3 -m venv "${APP_DIR}/backend/.venv"
"${APP_DIR}/backend/.venv/bin/pip" install --upgrade pip
"${APP_DIR}/backend/.venv/bin/pip" install "${APP_DIR}/backend"

log "Installing and building frontend."
(
    cd "${APP_DIR}/frontend"
    if [[ -f package-lock.json ]]; then
        npm ci --no-audit --no-fund
    else
        npm install --no-audit --no-fund --no-package-lock
    fi
    npm run build
)

log "Granting the service user read-only access to the application."
set_application_permissions "${APP_DIR}" root "${SERVICE_USER}" root

read_env_value "${CONFIG_FILE}" DATABASE_URL
database_url="${REPLY}"
log "Applying database migrations."
(
    cd "${APP_DIR}/backend"
    runuser -u "${SERVICE_USER}" -- env DATABASE_URL="${database_url}" .venv/bin/alembic upgrade head
)

install -o root -g root -m 0644 "${APP_DIR}/deploy/work-tracker.service" "${SERVICE_FILE}"
systemctl daemon-reload
systemctl enable --now work-tracker.service
systemctl is-active --quiet work-tracker.service || fail "Service failed to start."

detected_address="$(hostname -I 2>/dev/null | awk '{print $1}')"
[[ -n "${detected_address}" ]] || detected_address="<server-LAN-IP>"
read_env_value "${CONFIG_FILE}" APP_HOST
app_host="${REPLY}"
if [[ "${app_host}" != "0.0.0.0" ]]; then
    detected_address="${app_host}"
fi
read_env_value "${CONFIG_FILE}" APP_PORT
app_port="${REPLY}"

printf '\nWork Tracker installation completed.\n'
printf 'Application/API: http://%s:%s\n' "${detected_address}" "${app_port}"
printf 'Configuration: %s\n' "${CONFIG_FILE}"
printf 'Database: %s\n' "${DATABASE_FILE}"
printf 'Service status: sudo systemctl status work-tracker\n'
printf 'Logs: sudo journalctl -u work-tracker -f\n'
printf 'Restart: sudo systemctl restart work-tracker\n'
printf 'Update: sudo %s/update.sh\n\n' "${APP_DIR}"
systemctl --no-pager --full status work-tracker.service || true
