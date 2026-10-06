#!/usr/bin/env bash
set -Eeuo pipefail
umask 0027

# Fixed installed interpreter, isolated Python, no inherited configuration or
# password arguments. The module reads the production env file itself.
readonly PYTHON=/opt/work-tracker/backend/.venv/bin/python
if [[ "${EUID}" -eq 0 ]]; then
    exec runuser -u work-tracker -- env -i PATH=/usr/bin:/bin \
        "${PYTHON}" -I -B -m app.manage create-user "$@"
fi
exec env -i PATH=/usr/bin:/bin "${PYTHON}" -I -B -m app.manage create-user "$@"
