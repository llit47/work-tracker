#!/usr/bin/env bash
set -Eeuo pipefail

TEST_DIR="$(mktemp -d)"
trap 'rm -rf "${TEST_DIR}"' EXIT

APP_DIR="${TEST_DIR}/work-tracker"
TEST_OWNER="$(id -un)"
TEST_GROUP="$(id -gn)"
chmod 0750 "${TEST_DIR}"

mkdir -p "${APP_DIR}/backend/.venv/bin" "${APP_DIR}/backend/app" "${APP_DIR}/.git/objects"
printf '#!/usr/bin/env bash\nprintf "alembic executable reached\\n"\n' >"${APP_DIR}/backend/.venv/bin/alembic"
printf '#!/usr/bin/env bash\nprintf "uvicorn executable reached\\n"\n' >"${APP_DIR}/backend/.venv/bin/uvicorn"
printf 'application source\n' >"${APP_DIR}/backend/app/main.py"
printf 'private git metadata\n' >"${APP_DIR}/.git/config"
chmod 0700 "${APP_DIR}/backend/.venv/bin/alembic" "${APP_DIR}/backend/.venv/bin/uvicorn"
chmod 0600 "${APP_DIR}/backend/app/main.py" "${APP_DIR}/.git/config"

# shellcheck source=../common.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/common.sh"
set_application_permissions "${APP_DIR}" "${TEST_OWNER}" "${TEST_GROUP}" "${TEST_GROUP}"

[[ "$(stat -c '%a' "${APP_DIR}/backend/.venv/bin")" == 750 ]]
[[ "$(stat -c '%a' "${APP_DIR}/backend/.venv/bin/alembic")" == 750 ]]
[[ "$(stat -c '%a' "${APP_DIR}/backend/app/main.py")" == 640 ]]
[[ "$(stat -c '%a' "${APP_DIR}/.git")" == 700 ]]
[[ "$(stat -c '%a' "${APP_DIR}/.git/config")" == 600 ]]
[[ -z "$(find "${APP_DIR}" -path "${APP_DIR}/.git" -prune -o -type d -perm /0027 -print -quit)" ]]
[[ -z "$(find "${APP_DIR}" -path "${APP_DIR}/.git" -prune -o -type f -perm /0027 -print -quit)" ]]

namei -l "${APP_DIR}/backend/.venv/bin/alembic"
namei -l "${APP_DIR}/backend/.venv/bin/uvicorn"
"${APP_DIR}/backend/.venv/bin/alembic" | grep -qx 'alembic executable reached'
"${APP_DIR}/backend/.venv/bin/uvicorn" | grep -qx 'uvicorn executable reached'

printf 'Application permission layout test passed.\n'
