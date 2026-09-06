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
