#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/packaging/macos_env.sh"
wb_prepare_python
if ! "$WB_PYTHON" -c 'import platform, sys; sys.exit(platform.machine() != "arm64")'; then
    printf '%s\n' 'This build targets Apple Silicon. Use a native arm64 Python and Terminal, not an Intel/Rosetta .venv.' >&2
    exit 1
fi
wb_install_requirements
"$WB_PYTHON" -m pip install --disable-pip-version-check 'pyinstaller>=6.0,<7'
/bin/bash "$ROOT/packaging/generate_macos_icon.sh"
"$WB_PYTHON" -m PyInstaller \
    --noconfirm \
    --distpath "$ROOT/dist" \
    --workpath "$ROOT/build/pyinstaller-macos" \
    "$ROOT/packaging/WBCreditWidget-macOS.spec"
printf '\nBuilt app: %s\n' "$ROOT/dist/WBCreditWidget.app"
printf '%s\n' 'The app bundles Python and its dependencies. Copy it to Applications, or double-click to launch.'
printf '%s\n' 'This local build is ad-hoc signed, not Developer ID signed or notarized for public distribution.'
