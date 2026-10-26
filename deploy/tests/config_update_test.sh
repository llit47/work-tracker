#!/usr/bin/env bash
set -Eeuo pipefail

TEST_DIR="$(mktemp -d)"
trap 'rm -rf "${TEST_DIR}"' EXIT

# shellcheck source=../common.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/common.sh"

MANIFEST_FILE="${TEST_DIR}/config.manifest"
ENV_FILE="${TEST_DIR}/work-tracker.env"
INPUT_FILE="${TEST_DIR}/input"
OUTPUT_FILE="${TEST_DIR}/output"
SNAPSHOT_FILE="${TEST_DIR}/snapshot"

printf '%s\n' \
    'EXISTING|required|plain||Existing value|nonempty' \
    'NEW_DEFAULT|required|plain|safe-default|New value with default|nonempty' \
    'NEW_PROMPT|required|plain||New value without default|nonempty' \
    'NEW_SECRET|required|secret||New secret|token' \
    'OPTIONAL_NEW|optional|plain|optional-default|Optional value|nonempty' >"${MANIFEST_FILE}"
# Deliberately omit the final newline to cover older hand-edited env files.
printf 'EXISTING=keep-me\nUNKNOWN_KEY=keep-this-too' >"${ENV_FILE}"
printf '\nentered-value\n\n' >"${INPUT_FILE}"
exec 3<"${INPUT_FILE}"

sync_missing_required_config "${MANIFEST_FILE}" "${ENV_FILE}" >"${OUTPUT_FILE}"

grep -qx 'EXISTING=keep-me' "${ENV_FILE}"
grep -qx 'UNKNOWN_KEY=keep-this-too' "${ENV_FILE}"
grep -qx 'NEW_DEFAULT=safe-default' "${ENV_FILE}"
grep -qx 'NEW_PROMPT=entered-value' "${ENV_FILE}"
! grep -q '^OPTIONAL_NEW=' "${ENV_FILE}"
read_env_value "${ENV_FILE}" NEW_SECRET
SECRET_VALUE="${REPLY}"
validate_config_value token "${SECRET_VALUE}"
! grep -Fq "${SECRET_VALUE}" "${OUTPUT_FILE}"

# Running with unchanged configuration must neither prompt nor modify the file.
cp "${ENV_FILE}" "${SNAPSHOT_FILE}"
exec 3</dev/null
sync_missing_required_config "${MANIFEST_FILE}" "${ENV_FILE}"
cmp -s "${ENV_FILE}" "${SNAPSHOT_FILE}"

printf 'Configuration update tests passed.\n'

# The real 8A2 manifest requires an explicit choice for LAN HTTP cookies.
REAL_MANIFEST="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/config.manifest"
printf '%s\n' 'APP_HOST=0.0.0.0' 'APP_PORT=8000' \
    'WEBHOOK_TOKEN=public-test-only-webhook-token-32-characters' \
    'DATABASE_URL=sqlite:////var/lib/work-tracker/work_tracker.db' \
    'UNKNOWN_KEY=preserved' >"${ENV_FILE}"
printf 'http://10.123.45.10:8000\n\n' >"${INPUT_FILE}"
exec 3<"${INPUT_FILE}"
sync_missing_required_config "${REAL_MANIFEST}" "${ENV_FILE}"
validate_required_config "${REAL_MANIFEST}" "${ENV_FILE}"
grep -qx 'SESSION_COOKIE_SECURE=false' "${ENV_FILE}"
grep -qx 'UNKNOWN_KEY=preserved' "${ENV_FILE}"
validate_config_value boolean true
validate_config_value boolean false
! validate_config_value boolean ''
! validate_config_value boolean maybe
cp "${ENV_FILE}" "${SNAPSHOT_FILE}"
exec 3</dev/null
sync_missing_required_config "${REAL_MANIFEST}" "${ENV_FILE}"
cmp -s "${ENV_FILE}" "${SNAPSHOT_FILE}"
printf 'Session cookie configuration tests passed.\n'

# 8B1-A has no guessed origin/default. Existing LXC config needs an explicit choice.
grep -qx 'BROWSER_ALLOWED_ORIGINS=http://10.123.45.10:8000' "${ENV_FILE}"
for invalid in '' '*' null 'http://host/' 'http://user@host' 'http://host:65536' 'http://host?query'; do
    ! validate_config_value browser_origins "${invalid}"
done
validate_config_value browser_origins 'http://10.123.45.10:8000,https://work.example.com'
validate_config_value browser_origins 'http://[::1]:8000'
validate_config_value cors ''
! validate_config_value cors '*'

# Missing setting with no operator input fails without writing a broad allowlist.
sed '/^BROWSER_ALLOWED_ORIGINS=/d' "${ENV_FILE}" >"${ENV_FILE}.missing"
cp "${ENV_FILE}.missing" "${SNAPSHOT_FILE}"
if (exec 3</dev/null; sync_missing_required_config "${REAL_MANIFEST}" "${ENV_FILE}.missing") >"${OUTPUT_FILE}" 2>&1; then
    printf 'Missing origin unexpectedly accepted.\n' >&2
    exit 1
fi
grep -q 'BROWSER_ALLOWED_ORIGINS' "${OUTPUT_FILE}"
cmp -s "${ENV_FILE}.missing" "${SNAPSHOT_FILE}"
if (validate_required_config "${REAL_MANIFEST}" "${ENV_FILE}.missing") >"${OUTPUT_FILE}" 2>&1; then exit 1; fi
grep -q 'Missing required configuration key: BROWSER_ALLOWED_ORIGINS' "${OUTPUT_FILE}"

# Optional CORS entries are validated too and cannot widen the mutation allowlist.
printf 'CORS_ORIGINS=http://unapproved.example\n' >>"${ENV_FILE}"
if (validate_required_config "${REAL_MANIFEST}" "${ENV_FILE}") >"${OUTPUT_FILE}" 2>&1; then exit 1; fi
grep -q 'subset' "${OUTPUT_FILE}"
printf 'Browser origin configuration tests passed.\n'

# 8B2 remains optional for LAN; existing values survive updates without prompts.
sed -i '/^CORS_ORIGINS=/d' "${ENV_FILE}"
! env_has_key "${ENV_FILE}" INGRESS_PROFILE
! env_has_key "${ENV_FILE}" BROWSER_HSTS_SECONDS
printf 'INGRESS_PROFILE=split\nBROWSER_HSTS_SECONDS=300\n' >>"${ENV_FILE}"
if (validate_required_config "${REAL_MANIFEST}" "${ENV_FILE}") >"${OUTPUT_FILE}" 2>&1; then exit 1; fi
grep -q 'Inconsistent ingress/browser configuration' "${OUTPUT_FILE}"
sed -i -e 's|^BROWSER_ALLOWED_ORIGINS=.*|BROWSER_ALLOWED_ORIGINS=https://work.example.invalid|' \
    -e 's/^SESSION_COOKIE_SECURE=.*/SESSION_COOKIE_SECURE=true/' "${ENV_FILE}"
cp "${ENV_FILE}" "${SNAPSHOT_FILE}"
exec 3</dev/null
sync_missing_required_config "${REAL_MANIFEST}" "${ENV_FILE}"
validate_required_config "${REAL_MANIFEST}" "${ENV_FILE}"
cmp -s "${ENV_FILE}" "${SNAPSHOT_FILE}"
validate_config_value ingress_profile lan
validate_config_value ingress_profile split
! validate_config_value ingress_profile public
validate_config_value hsts_seconds 0
validate_config_value hsts_seconds 31536000
! validate_config_value hsts_seconds 31536001
! validate_config_value hsts_seconds -1
printf 'Optional split ingress configuration tests passed.\n'
