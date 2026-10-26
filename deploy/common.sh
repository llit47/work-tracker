#!/usr/bin/env bash

# Shared deployment helpers. Calling scripts enable strict mode themselves.
WORK_TRACKER_DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

log() {
    printf '[work-tracker] %s\n' "$*"
}

fail() {
    printf '[work-tracker] ERROR: %s\n' "$*" >&2
    exit 1
}

require_root() {
    [[ "${EUID}" -eq 0 ]] || fail "Run this command as root or with sudo."
}

require_supported_os() {
    [[ -r /etc/os-release ]] || fail "Cannot identify the operating system."
    # shellcheck disable=SC1091
    . /etc/os-release
    case "${ID:-}" in
        debian|ubuntu) ;;
        *) fail "Only Debian and Ubuntu are supported (detected: ${ID:-unknown})." ;;
    esac
}

set_application_permissions() {
    local app_dir="$1"
    local app_owner="$2"
    local reader_group="$3"
    local private_group="$4"

    if [[ ! -d "${app_dir}" || "${app_dir}" == "/" ]]; then
        printf '[work-tracker] ERROR: Invalid application directory for permissions: %s.\n' "${app_dir}" >&2
        return 1
    fi
    id "${app_owner}" >/dev/null 2>&1 || {
        printf '[work-tracker] ERROR: Missing application owner: %s.\n' "${app_owner}" >&2
        return 1
    }
    getent group "${reader_group}" >/dev/null 2>&1 || {
        printf '[work-tracker] ERROR: Missing application reader group: %s.\n' "${reader_group}" >&2
        return 1
    }
    getent group "${private_group}" >/dev/null 2>&1 || {
        printf '[work-tracker] ERROR: Missing private application group: %s.\n' "${private_group}" >&2
        return 1
    }

    chown -R -h "${app_owner}:${reader_group}" "${app_dir}" || return 1
    find "${app_dir}" -type d -exec chmod 0750 {} + || return 1
    find "${app_dir}" -type f -perm /111 -exec chmod 0750 {} + || return 1
    find "${app_dir}" -type f ! -perm /111 -exec chmod 0640 {} + || return 1

    # Git metadata is needed only by root-owned deployment scripts, not by the service process.
    if [[ -d "${app_dir}/.git" ]]; then
        chown -R -h "${app_owner}:${private_group}" "${app_dir}/.git" || return 1
        find "${app_dir}/.git" -type d -exec chmod 0700 {} + || return 1
        find "${app_dir}/.git" -type f -exec chmod 0600 {} + || return 1
    fi
}

require_prompt_fd() {
    if ! { true <&3; } 2>/dev/null; then
        { exec 3</dev/tty; } 2>/dev/null \
            || fail "Interactive configuration requires a terminal. Set missing required keys in the env file and retry."
    fi
}

env_has_key() {
    local env_file="$1"
    local key="$2"
    grep -q -E "^${key}=" "${env_file}"
}

read_env_value() {
    local env_file="$1"
    local key="$2"
    local line
    line="$(grep -m1 -E "^${key}=" "${env_file}")" || return 1
    REPLY="${line#*=}"
}

validate_config_value() {
    local validator="$1"
    local value="$2"
    case "${validator}" in
        token)
            [[ ${#value} -ge 32 ]] || return 1
            [[ "${value}" =~ ^[A-Za-z0-9._~-]+$ ]]
            ;;
        port)
            [[ "${value}" =~ ^[0-9]+$ ]] && ((value >= 1 && value <= 65535))
            ;;
        host)
            [[ -n "${value}" && "${value}" =~ ^[A-Za-z0-9.:_-]+$ ]]
            ;;
        database_url)
            [[ "${value}" == "sqlite:////var/lib/work-tracker/work_tracker.db" ]]
            ;;
        cors|browser_origins)
            python3 "${WORK_TRACKER_DEPLOY_DIR}/../backend/app/origins.py" "${validator}" "${value}"
            ;;
        nonempty)
            [[ -n "${value}" ]]
            ;;
        boolean)
            [[ "${value}" == true || "${value}" == false ]]
            ;;
        ingress_profile)
            [[ "${value}" == lan || "${value}" == split ]]
            ;;
        hsts_seconds)
            [[ "${value}" =~ ^(0|[1-9][0-9]{0,7})$ ]] && ((value <= 31536000))
            ;;
        *) return 1 ;;
    esac
}

generate_webhook_token() {
    openssl rand -hex 32
}

prompt_config_value() {
    local visibility="$1"
    local default_value="$2"
    local prompt="$3"
    local validator="$4"
    local value

    require_prompt_fd
    while true; do
        if [[ -n "${default_value}" ]]; then
            printf '%s [%s]: ' "${prompt}" "${default_value}"
        elif [[ "${visibility}" == secret ]]; then
            printf '%s (input hidden): ' "${prompt}"
        else
            printf '%s: ' "${prompt}"
        fi

        if [[ "${visibility}" == secret ]]; then
            IFS= read -r -s value <&3 || fail "Missing configuration input. Set the required key in the env file and retry."
            printf '\n'
        else
            IFS= read -r value <&3 || fail "Missing configuration input. Set the required key in the env file and retry."
        fi

        if [[ -z "${value}" && -n "${default_value}" ]]; then
            value="${default_value}"
        elif [[ -z "${value}" && "${validator}" == token ]]; then
            value="$(generate_webhook_token)"
            log "Generated a webhook token and stored it without displaying it."
        fi

        if validate_config_value "${validator}" "${value}"; then
            REPLY="${value}"
            return 0
        fi
        printf 'Invalid value. Please try again.\n' >&2
    done
}

validate_manifest_row() {
    local key="$1"
    local requirement="$2"
    local visibility="$3"
    local default_value="$4"
    local validator="$5"
    [[ "${key}" =~ ^[A-Z][A-Z0-9_]*$ ]] || fail "Invalid key in configuration manifest."
    [[ "${requirement}" == required || "${requirement}" == optional ]] || fail "Invalid requirement for ${key}."
    [[ "${visibility}" == plain || "${visibility}" == secret ]] || fail "Invalid visibility for ${key}."
    [[ "${visibility}" != secret || -z "${default_value}" ]] || fail "Secret ${key} cannot define a displayed default."
    case "${validator}" in
        token|port|host|database_url|cors|browser_origins|nonempty|boolean|ingress_profile|hsts_seconds) ;;
        *) fail "Invalid validator for ${key}." ;;
    esac
}

sync_missing_required_config() {
    local manifest_file="$1"
    local env_file="$2"
    local key requirement visibility default_value prompt validator

    while IFS='|' read -r key requirement visibility default_value prompt validator; do
        [[ -z "${key}" || "${key}" == \#* ]] && continue
        validate_manifest_row "${key}" "${requirement}" "${visibility}" "${default_value}" "${validator}"
        [[ "${requirement}" == required ]] || continue
        env_has_key "${env_file}" "${key}" && continue

        log "New required configuration key detected: ${key}"
        prompt_config_value "${visibility}" "${default_value}" "${prompt}" "${validator}"
        # A leading newline also handles older env files without a final newline.
        printf '\n%s=%s\n' "${key}" "${REPLY}" >>"${env_file}"
    done <"${manifest_file}"
}

validate_required_config() {
    local manifest_file="$1"
    local env_file="$2"
    local key requirement visibility default_value prompt validator

    while IFS='|' read -r key requirement visibility default_value prompt validator; do
        [[ -z "${key}" || "${key}" == \#* ]] && continue
        validate_manifest_row "${key}" "${requirement}" "${visibility}" "${default_value}" "${validator}"
        if ! read_env_value "${env_file}" "${key}"; then
            [[ "${requirement}" == optional ]] && continue
            fail "Missing required configuration key: ${key}."
        fi
        validate_config_value "${validator}" "${REPLY}" || fail "Invalid value for configuration key: ${key}."
    done <"${manifest_file}"
    if env_has_key "${env_file}" BROWSER_ALLOWED_ORIGINS; then
        read_env_value "${env_file}" BROWSER_ALLOWED_ORIGINS
        local browser_origins="${REPLY}" cors_origins=""
        if read_env_value "${env_file}" CORS_ORIGINS; then cors_origins="${REPLY}"; fi
        python3 "${WORK_TRACKER_DEPLOY_DIR}/../backend/app/origins.py" subset "${browser_origins}" "${cors_origins}" \
            || fail "CORS_ORIGINS must be a subset of BROWSER_ALLOWED_ORIGINS."
        local ingress_profile=lan hsts_seconds=0 cookie_secure=false
        if read_env_value "${env_file}" INGRESS_PROFILE; then ingress_profile="${REPLY}"; fi
        if read_env_value "${env_file}" BROWSER_HSTS_SECONDS; then hsts_seconds="${REPLY}"; fi
        if read_env_value "${env_file}" SESSION_COOKIE_SECURE; then cookie_secure="${REPLY}"; fi
        python3 "${WORK_TRACKER_DEPLOY_DIR}/../backend/app/origins.py" profile \
            "${ingress_profile}" "${browser_origins}" "${cors_origins}" "${cookie_secure}" "${hsts_seconds}" \
            || fail "Inconsistent ingress/browser configuration."
    fi
}

# Installer choice is deliberately separate from the persisted app profile.
choose_ingress_mode() {
    require_prompt_fd
    printf 'Tryb instalacji: LAN HTTP (lan, domyślny) albo przygotowanie HTTPS / Split Ingress (https).\n'
    while true; do
        printf 'Tryb [lan]: '
        IFS= read -r REPLY <&3 || fail "Brak wyboru trybu instalacji."
        REPLY="${REPLY:-lan}"
        [[ "${REPLY}" == lan || "${REPLY}" == https ]] && return 0
        printf 'Wpisz lan albo https.\n' >&2
    done
}

prepare_optional_ingress() {
    local app_dir="$1" mode="$2"
    [[ "${mode}" == https ]] || return 0
    log "HTTPS: przygotowanie nieaktywnego ingressu. Działający LAN pozostaje do jawnego cutoveru."
    python3 "${app_dir}/deploy/ingress_cutover.py" bootstrap || return 1
    python3 "${app_dir}/deploy/ingress_cutover.py" configure || return 1
    log "Przygotowano HTTPS. Brak aktywacji, publicznej trasy, Access i konfiguracji firewalla."
}

check_ingress_update_state() {
    local journal="${1:-/etc/work-tracker/ingress/cutover.json}"
    [[ ! -e "${journal}" && ! -L "${journal}" ]] && return 0
    python3 - "${journal}" <<'PYTHON'
import json
import pathlib
import sys
try:
    path = pathlib.Path(sys.argv[1])
    assert not path.is_symlink()
    state = json.loads(path.read_bytes())
    assert state['phase'] == 'active'
except (OSError, ValueError, KeyError, AssertionError):
    print('Niedokończony cutover: przed update wykonaj ingress_cutover.py recover.', file=sys.stderr)
    sys.exit(1)
PYTHON
}

acquire_deployment_lock() {
    local lock="${1:-/run/work-tracker-deploy.lock}"
    [[ ! -L "${lock}" && ( ! -e "${lock}" || -f "${lock}" ) ]] || fail "Invalid deployment lock."
    exec 9>"${lock}"
    chmod 0600 "${lock}"
    flock -n 9 || fail "Another update/cutover is running."
}
