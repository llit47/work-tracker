#!/usr/bin/env bash
# 0: available checks passed (summary may include SKIP), 77: all skipped, 1: FAIL.
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if (($#)); then
    checks=("$@")
else
    checks=("${ROOT}/config_update_test.sh" "${ROOT}/update_rollback_test.sh"
            "${ROOT}/application_permissions_test.sh" "${ROOT}/backend_package_install_test.sh"
            "${ROOT}/service_user_runtime_test.sh")
fi
passed=0 skipped=0 failed=0
for check in "${checks[@]}"; do
    if bash "${check}"; then
        printf 'PASS: %s\n' "${check##*/}"
        passed=$((passed + 1))
    else
        status=$?
        if ((status == 77)); then
            printf 'SKIP / NOT RUN: %s\n' "${check##*/}"
            skipped=$((skipped + 1))
        else
            printf 'FAIL (exit %s): %s\n' "${status}" "${check##*/}" >&2
            failed=$((failed + 1))
        fi
    fi
done
printf 'Shell checks: PASS=%s SKIP=%s FAIL=%s\n' "${passed}" "${skipped}" "${failed}"
((failed == 0)) || exit 1
((passed > 0)) || exit 77
