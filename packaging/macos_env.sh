#!/bin/bash
# Shared by the source launcher and app builder; source this file with Bash.
WB_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WB_PYTHON="$WB_ROOT/.venv/bin/python"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

wb_prepare_python() {
    if [[ "$(uname -s)" != Darwin ]]; then
        printf '%s\n' 'This launcher requires macOS. Windows users should use the existing .bat launcher.' >&2
        return 1
    fi
    if [[ ! -x "$WB_PYTHON" ]]; then
        if [[ -e "$WB_ROOT/.venv" ]]; then
            printf '%s\n' 'The project .venv is incomplete. Move it aside and launch again to create a new environment.' >&2
            return 1
        fi
        local candidate python=""
        for candidate in python3 python3.14 python3.13 python3.12 python3.11 python3.10; do
            if command -v "$candidate" >/dev/null 2>&1 &&
                "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 10))' >/dev/null 2>&1; then
                python="$(command -v "$candidate")"
                break
            fi
        done
        if [[ -z "$python" ]]; then
            printf '%s\n' 'Source mode requires Python 3.10 or newer. Install Python from python.org (or brew install python), then launch again.' >&2
            printf '%s\n' 'The packaged WBCreditWidget.app does not require a separate Python installation.' >&2
            return 1
        fi
        "$python" -m venv "$WB_ROOT/.venv"
    fi
    if ! "$WB_PYTHON" -c 'import sys; sys.exit(sys.version_info < (3, 10) or sys.platform != "darwin")'; then
        printf '%s\n' 'The project .venv must use macOS Python 3.10 or newer. Move it aside and launch again.' >&2
        return 1
    fi
}

wb_install_requirements() {
    local required installed="" marker="$WB_ROOT/.venv/.wb-widget-requirements"
    required="$(cksum < "$WB_ROOT/requirements.txt")"
    if [[ -f "$marker" ]]; then
        IFS= read -r installed < "$marker" || true
    fi
    if [[ "$installed" != "$required" ]]; then
        printf '%s\n' 'Installing source dependencies into the project .venv...'
        "$WB_PYTHON" -m pip install --disable-pip-version-check -r "$WB_ROOT/requirements.txt"
        printf '%s\n' "$required" > "$marker"
    fi
}
