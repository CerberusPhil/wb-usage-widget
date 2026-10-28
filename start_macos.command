#!/bin/bash
set -Eeuo pipefail

show_failure() {
    local status=$?
    if [[ "$status" -ne 0 ]]; then
        printf '\nWorkbuddy widget could not start (exit %s). See the error above.\n' "$status" >&2
        if [[ -t 0 ]]; then
            read -r -p 'Press Return to close this window...' || true
        fi
    fi
    exit "$status"
}
trap show_failure EXIT

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$ROOT/packaging/macos_env.sh"
wb_prepare_python
wb_install_requirements
"$WB_PYTHON" "$ROOT/scripts/wb_widget_app.py" "$@"
