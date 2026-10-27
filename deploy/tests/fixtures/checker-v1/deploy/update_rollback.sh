#!/usr/bin/env bash

rollback_log() {
    printf '[work-tracker] %s\n' "$*" >&2
}

rollback_warning() {
    printf '[work-tracker] ROLLBACK WARNING: %s\n' "$*" >&2
}

# Compatibility tombstone for old staged callers. A backup is a historical
# snapshot, never proof that replacing today's database would preserve writes.
restore_database_backup() {
    rollback_warning "Automatic SQLite restore is disabled. Current DB/WAL/SHM and backups are preserved."
    return 1
}

restore_previous_application() {
    local app_dir="$1"
    local previous_commit="$2"
    local repository_was_clean="$3"
    local service_user="$4"
    local restored=0

    if [[ "${repository_was_clean}" != true ]]; then
        rollback_warning "Refusing git reset because the repository was not confirmed clean."
        return 1
    fi
    if ! (cd "${app_dir}" && git reset --hard "${previous_commit}"); then
        rollback_warning "Could not restore repository to ${previous_commit}."
        return 1
    fi

    if ! "${app_dir}/backend/.venv/bin/pip" install --upgrade "${app_dir}/backend"; then
        rollback_warning "Could not reinstall backend dependencies for the previous commit."
        restored=1
    fi
    if ! (
        cd "${app_dir}/frontend"
        if [[ -f package-lock.json ]]; then
            npm ci --no-audit --no-fund
        else
            npm install --no-audit --no-fund --no-package-lock
        fi
        npm run build
    ); then
        rollback_warning "Could not rebuild the frontend for the previous commit."
        restored=1
    fi
    if ! set_application_permissions "${app_dir}" root "${service_user}" root; then
        rollback_warning "Could not restore application permissions for the service user."
        restored=1
    fi
    return "${restored}"
}

perform_update_rollback() {
    local app_dir="$1"
    local previous_commit="$2"
    local repository_was_clean="$3"
    local migration_started="$4"
    local database_file="$5"
    local backup_file="$6"
    local service_file="$7"
    local unit_backup="$8"
    local service_user="$9"
    local service_was_active="${10}"
    local rollback_failed=0
    local database_safe=true
    local repository_restored=true
    local unit_restored=true
    local service_stopped=true

    rollback_log "Starting automatic rollback to ${previous_commit}."

    if [[ "${service_was_active}" == true || "${migration_started}" == true ]]; then
        systemctl stop work-tracker.service >/dev/null 2>&1 || true
        if systemctl is-active --quiet work-tracker.service; then
            service_stopped=false
            rollback_failed=1
            rollback_warning "Could not stop work-tracker.service during rollback."
        fi
    fi

    if [[ "${migration_started}" == true ]]; then
        rollback_warning "UPDATE REQUIRES MANUAL RECOVERY: Alembic may have changed schema/data."
        rollback_warning "Current SQLite, WAL/SHM, corrections, sessions and login history are NOT restored or deleted."
        rollback_warning "Candidate code/dependencies/unit are retained. Old application will NOT be started."
        local safety_runner="${11:-${app_dir}/deploy/update_safety.py}"
        if ! python3 "${safety_runner}" install; then
            rollback_warning "Could not install/validate the startup guard; inspect systemd before reboot."
        fi
        if ! python3 "${safety_runner}" hold; then
            # Compatibility with an older staged updater entering the new
            # rollback helper without first writing a transaction record.
            local emergency_dir
            emergency_dir="$(mktemp -d "${backup_file%/*}/update-manual.XXXXXX")"
            if [[ -n "${emergency_dir}" ]]; then
                cp -p "${unit_backup}" "${emergency_dir}/unit.before" || true
                python3 "${safety_runner}" begin --previous "${previous_commit}" \
                    --candidate "$(git -C "${app_dir}" rev-parse HEAD)" --directory "${emergency_dir}" --owner-pid "$$" \
                    && python3 "${safety_runner}" hold || true
            fi
        fi
        systemctl daemon-reload || rollback_warning "daemon-reload failed; inspect the startup guard."
        rollback_warning "Automatic database/code rollback REFUSED. Backup: ${backup_file}"
        rollback_warning "Previous commit: ${previous_commit}. Read the 9B2 manual recovery runbook."
        [[ "${service_stopped}" == true ]] || rollback_warning "Service stop was not confirmed; inspect it immediately."
        return 1
    fi
    rollback_log "Alembic was not invoked; database and sidecars are left untouched."

    if ! restore_previous_application "${app_dir}" "${previous_commit}" "${repository_was_clean}" "${service_user}"; then
        repository_restored=false
        rollback_failed=1
    fi

    if ! install -m 0644 "${unit_backup}" "${service_file}"; then
        rollback_warning "Could not restore the previous systemd unit."
        unit_restored=false
        rollback_failed=1
    fi
    if ! systemctl daemon-reload; then
        rollback_warning "systemctl daemon-reload failed."
        unit_restored=false
        rollback_failed=1
    fi

    if [[ -n "${11:-}" ]]; then
        if [[ "${repository_restored}" == true && "${unit_restored}" == true && "${service_stopped}" == true ]]; then
            if ! python3 "${11}" cancel || ! python3 "${11}" remove-legacy-guard; then
                rollback_warning "Could not clear a preparing-only update gate; old service remains blocked."
                database_safe=false
                rollback_failed=1
            fi
            systemctl daemon-reload || { unit_restored=false; rollback_failed=1; }
        else
            python3 "${11}" hold || true
        fi
    fi

    if [[ "${service_was_active}" == true ]]; then
        if [[ "${service_stopped}" == true && "${database_safe}" == true && "${repository_restored}" == true && "${unit_restored}" == true ]]; then
            if systemctl restart work-tracker.service; then
                rollback_log "Previous service restart requested; verify HTTP/proxy readiness before declaring it healthy."
            else
                rollback_warning "Previous service version could not be started."
                rollback_failed=1
            fi
        else
            rollback_warning "Service was not started because rollback did not restore every safety-critical element."
        fi
    else
        rollback_log "Service was inactive before the update and remains inactive."
    fi

    if ((rollback_failed == 0)); then
        rollback_log "ROLLBACK COMPLETE: update was reverted to ${previous_commit}."
        return 0
    fi

    rollback_warning "ROLLBACK INCOMPLETE. Do not start the service until the installation is inspected."
    rollback_warning "Database backup: ${backup_file}"
    rollback_warning "Previous commit: ${previous_commit}"
    rollback_warning "Diagnostics: systemctl status work-tracker"
    rollback_warning "Diagnostics: journalctl -u work-tracker -n 100"
    rollback_warning "Diagnostics: cd ${app_dir} && git status"
    return 1
}
