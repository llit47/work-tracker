#!/usr/bin/env bash
# No tunnel starts. Supply an unpacked official cloudflared 2026.10.0 source tree.
set -Eeuo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
: "${CLOUDFLARED_SOURCE_DIR:?Set to official cloudflared 2026.10.0 source directory}"
GO_BIN="${WORK_TRACKER_GO_BIN:-go}"
EXPECTED_JWT_SHA='a29963c087d6c8ae033f6c5434b6b668366ac3c74e862d6ff83ca21308bd3811'
printf '%s  %s\n' "${EXPECTED_JWT_SHA}" "${CLOUDFLARED_SOURCE_DIR}/ingress/middleware/jwtvalidator.go" | sha256sum --check --status
TEST_DIR="$(mktemp -d)"
trap 'rm -rf "${TEST_DIR}"' EXIT
cp -a "${CLOUDFLARED_SOURCE_DIR}/." "${TEST_DIR}/"
cp "${ROOT_DIR}/deploy/tests/cloudflared/work_tracker_access_test.go" "${TEST_DIR}/ingress/middleware/"
cd "${TEST_DIR}"
"${GO_BIN}" test ./ingress/middleware -run 'TestWorkTrackerAccessBoundary' -v -count=1
