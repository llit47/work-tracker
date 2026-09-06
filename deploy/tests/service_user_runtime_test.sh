#!/usr/bin/env bash
set -Eeuo pipefail

if [[ "${EUID}" -ne 0 ]]; then
    printf 'Service-user runtime test skipped: root is required to switch users.\n'
    exit 0
fi

command -v runuser >/dev/null 2>&1 || { printf 'runuser is required.\n' >&2; exit 1; }
command -v curl >/dev/null 2>&1 || { printf 'curl is required.\n' >&2; exit 1; }

REPOSITORY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SERVICE_USER="${WORK_TRACKER_TEST_SERVICE_USER:-nobody}"
SERVICE_GROUP="$(id -gn "${SERVICE_USER}")"
TEST_DIR="$(mktemp -d)"
APP_DIR="${TEST_DIR}/work-tracker"
DATA_DIR="${TEST_DIR}/data"
DATABASE_FILE="${DATA_DIR}/work_tracker.db"
SERVER_PID=""

cleanup() {
    if [[ -n "${SERVER_PID}" ]]; then
        kill "${SERVER_PID}" >/dev/null 2>&1 || true
        wait "${SERVER_PID}" >/dev/null 2>&1 || true
    fi
    rm -rf "${TEST_DIR}"
}
trap cleanup EXIT

install -d -o root -g "${SERVICE_GROUP}" -m 0750 "${TEST_DIR}" "${APP_DIR}"
cp -a "${REPOSITORY_DIR}/backend" "${APP_DIR}/backend"
rm -rf "${APP_DIR}/backend/.venv"
python3 -m venv "${APP_DIR}/backend/.venv"
"${APP_DIR}/backend/.venv/bin/pip" install --upgrade pip
"${APP_DIR}/backend/.venv/bin/pip" install "${APP_DIR}/backend"
install -d -o "${SERVICE_USER}" -g "${SERVICE_GROUP}" -m 0750 "${DATA_DIR}"

# shellcheck source=../common.sh
source "${REPOSITORY_DIR}/deploy/common.sh"
set_application_permissions "${APP_DIR}" root "${SERVICE_GROUP}" root

namei -l "${APP_DIR}/backend/.venv/bin/alembic"
namei -l "${APP_DIR}/backend/.venv/bin/uvicorn"
runuser -u "${SERVICE_USER}" -- "${APP_DIR}/backend/.venv/bin/alembic" --version
runuser -u "${SERVICE_USER}" -- "${APP_DIR}/backend/.venv/bin/uvicorn" --version
runuser -u "${SERVICE_USER}" -- "${APP_DIR}/backend/.venv/bin/python" -c 'import app'
runuser -u "${SERVICE_USER}" -- env \
    WEBHOOK_TOKEN='service-user-test-token-at-least-32-characters' \
    DATABASE_URL='sqlite:///:memory:' \
    "${APP_DIR}/backend/.venv/bin/python" \
    -c 'from app.main import app; assert app.title == "Work Tracker API"'

(
    cd "${APP_DIR}/backend"
    runuser -u "${SERVICE_USER}" -- env DATABASE_URL="sqlite:///${DATABASE_FILE}" \
        .venv/bin/alembic upgrade head
    runuser -u "${SERVICE_USER}" -- env DATABASE_URL="sqlite:///${DATABASE_FILE}" \
        .venv/bin/alembic current | grep -q '20260906_01'
)

PORT="$("${APP_DIR}/backend/.venv/bin/python" -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')"
(
    cd "${APP_DIR}/backend"
    exec runuser -u "${SERVICE_USER}" -- env \
        WEBHOOK_TOKEN='service-user-test-token-at-least-32-characters' \
        DATABASE_URL="sqlite:///${DATABASE_FILE}" \
        .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port "${PORT}"
) >"${TEST_DIR}/uvicorn.log" 2>&1 &
SERVER_PID="$!"

health_ok=false
for _ in {1..50}; do
    if curl -fsS "http://127.0.0.1:${PORT}/api/health" | grep -q '"status":"ok"'; then
        health_ok=true
        break
    fi
    sleep 0.1
done
if [[ "${health_ok}" != true ]]; then
    cat "${TEST_DIR}/uvicorn.log" >&2
    exit 1
fi

kill "${SERVER_PID}"
wait "${SERVER_PID}" || true
SERVER_PID=""
printf 'Service-user migration and runtime test passed.\n'
