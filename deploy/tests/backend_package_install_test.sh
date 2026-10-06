#!/usr/bin/env bash
set -Eeuo pipefail

REPOSITORY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TEST_DIR="$(mktemp -d)"
trap 'rm -rf "${TEST_DIR}"' EXIT

VENV_DIR="${TEST_DIR}/venv"
python3 -m venv "${VENV_DIR}"
"${VENV_DIR}/bin/pip" install --upgrade pip
(
    cd "${REPOSITORY_DIR}"
    "${VENV_DIR}/bin/pip" install ./backend
)
"${VENV_DIR}/bin/python" -c 'import app'
"${VENV_DIR}/bin/python" -I -B -c 'from app.identity import hash_password, verify_password; password = "package-test-only-password"; assert verify_password(password, hash_password(password))'
"${VENV_DIR}/bin/python" -I -B -m app.manage --help >/dev/null
WEBHOOK_TOKEN='package-test-token-at-least-32-characters' \
DATABASE_URL='sqlite:///:memory:' \
    "${VENV_DIR}/bin/python" -c 'from app.main import app as fastapi_app; assert fastapi_app.title == "Work Tracker API"'
"${VENV_DIR}/bin/uvicorn" --version
MANAGEMENT_USER="$(id -un)"
if [[ "${EUID}" -eq 0 ]]; then
    MANAGEMENT_USER="${WORK_TRACKER_TEST_SERVICE_USER:-nobody}"
    # Match installed read-only code access when testing the root wrapper path.
    source "${REPOSITORY_DIR}/deploy/common.sh"
    set_application_permissions "${TEST_DIR}" root "$(id -gn "${MANAGEMENT_USER}")" root
fi
"${VENV_DIR}/bin/python" "${REPOSITORY_DIR}/deploy/tests/identity_management_test.py" \
    "${VENV_DIR}/bin/python" "${TEST_DIR}" "${MANAGEMENT_USER}"

printf 'Backend package installation test passed.\n'
