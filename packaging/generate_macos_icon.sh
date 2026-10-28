#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SOURCE="$ROOT/icons/dashboard-icon-1024.png"
OUTPUT="$ROOT/build/macos"
ICONSET="$OUTPUT/widget_icon.iconset"

if [[ "$(uname -s)" != Darwin ]]; then
    printf '%s\n' 'Icon generation requires the macOS sips and iconutil tools.' >&2
    exit 1
fi
mkdir -p "$ICONSET"
for size in 16 32 128 256 512; do
    /usr/bin/sips -z "$size" "$size" "$SOURCE" --out "$ICONSET/icon_${size}x${size}.png" >/dev/null
    double=$((size * 2))
    /usr/bin/sips -z "$double" "$double" "$SOURCE" --out "$ICONSET/icon_${size}x${size}@2x.png" >/dev/null
done
/usr/bin/iconutil -c icns "$ICONSET" -o "$OUTPUT/widget_icon.icns"
printf 'Generated icon: %s\n' "$OUTPUT/widget_icon.icns"
