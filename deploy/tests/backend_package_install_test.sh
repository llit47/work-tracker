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
WEBHOOK_TOKEN='package-test-token-at-least-32-characters' \
DATABASE_URL='sqlite:///:memory:' \
    "${VENV_DIR}/bin/python" -c 'from app.main import app as fastapi_app; assert fastapi_app.title == "Work Tracker API"'
"${VENV_DIR}/bin/uvicorn" --version

printf 'Backend package installation test passed.\n'
