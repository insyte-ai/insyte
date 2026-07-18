#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
VERSION="$(cd "$PROJECT_ROOT" && uv run python -c 'from insyte import __version__; print(__version__)')"
ARCH="$(uname -m)"
APP_DIST="$PROJECT_ROOT/dist/desktop"
WORK_DIR="$PROJECT_ROOT/build/desktop"
APP_PATH="$APP_DIST/Insyte.app"
DMG_PATH="$PROJECT_ROOT/dist/Insyte-macOS-${ARCH}.dmg"
ICON_SOURCE="$PROJECT_ROOT/assets/logo-mark.png"
ICON_WORK="$PROJECT_ROOT/build/macos-icon"
ICONSET_DIR="$ICON_WORK/Insyte.iconset"
ICON_BASE="$ICON_WORK/Insyte-square.png"
ICON_PATH="$ICON_WORK/Insyte.icns"

mkdir -p "$ICON_WORK"
rm -rf "$ICONSET_DIR"
mkdir -p "$ICONSET_DIR"
sips --padToHeightWidth 377 377 --padColor 050912 "$ICON_SOURCE" --out "$ICON_BASE" >/dev/null
for icon_size in 16 32 128 256 512; do
  sips --resampleHeightWidth "$icon_size" "$icon_size" "$ICON_BASE" \
    --out "$ICONSET_DIR/icon_${icon_size}x${icon_size}.png" >/dev/null
  retina_size=$((icon_size * 2))
  sips --resampleHeightWidth "$retina_size" "$retina_size" "$ICON_BASE" \
    --out "$ICONSET_DIR/icon_${icon_size}x${icon_size}@2x.png" >/dev/null
done
iconutil --convert icns "$ICONSET_DIR" --output "$ICON_PATH"

cd "$PROJECT_ROOT"
uv run pyinstaller \
  --noconfirm \
  --clean \
  --windowed \
  --name Insyte \
  --icon "$ICON_PATH" \
  --collect-data certifi \
  --collect-submodules sqlglot.dialects \
  --osx-bundle-identifier ai.insyte.desktop \
  --distpath "$APP_DIST" \
  --workpath "$WORK_DIR" \
  --specpath "$WORK_DIR" \
  --add-data "$PROJECT_ROOT/src/insyte/studio_dist:insyte/studio_dist" \
  --add-data "$PROJECT_ROOT/src/insyte/nl/report_skill.md:insyte/nl" \
  "$PROJECT_ROOT/src/insyte/desktop.py"

set_plist_version() {
  local key="$1"
  /usr/libexec/PlistBuddy -c "Set :$key $VERSION" "$APP_PATH/Contents/Info.plist" 2>/dev/null || \
    /usr/libexec/PlistBuddy -c "Add :$key string $VERSION" "$APP_PATH/Contents/Info.plist"
}
set_plist_version "CFBundleShortVersionString"
set_plist_version "CFBundleVersion"

if [[ -n "${APPLE_DEVELOPER_IDENTITY:-}" ]]; then
  codesign --force --deep --options runtime --timestamp \
    --sign "$APPLE_DEVELOPER_IDENTITY" "$APP_PATH"
else
  echo "APPLE_DEVELOPER_IDENTITY is unset; applying ad-hoc signing for local testing only."
  codesign --force --deep --sign - "$APP_PATH"
fi

# Exercise dynamically loaded runtime modules inside the frozen bundle.
INSYTE_DESKTOP_VALIDATE_BUNDLE=1 "$APP_PATH/Contents/MacOS/Insyte"

# Catch frozen-only missing imports before producing a DMG.
SMOKE_PORT=43838
SMOKE_LOG="$WORK_DIR/desktop-smoke.log"
INSYTE_DESKTOP_NO_BROWSER=1 INSYTE_DESKTOP_PORT="$SMOKE_PORT" \
  "$APP_PATH/Contents/MacOS/Insyte" >"$SMOKE_LOG" 2>&1 &
SMOKE_PID=$!
smoke_cleanup() {
  kill "$SMOKE_PID" 2>/dev/null || true
  wait "$SMOKE_PID" 2>/dev/null || true
}
trap smoke_cleanup EXIT
SMOKE_OK=0
for _ in {1..30}; do
  if curl --silent --fail "http://127.0.0.1:${SMOKE_PORT}/api/setup/status" >/dev/null; then
    SMOKE_OK=1
    break
  fi
  if ! kill -0 "$SMOKE_PID" 2>/dev/null; then
    break
  fi
  sleep 0.2
done
if [[ "$SMOKE_OK" != "1" ]]; then
  cat "$SMOKE_LOG"
  exit 1
fi
smoke_cleanup
trap - EXIT

hdiutil create \
  -volname Insyte \
  -srcfolder "$APP_PATH" \
  -ov \
  -format UDZO \
  "$DMG_PATH"

shasum -a 256 "$DMG_PATH" > "$DMG_PATH.sha256"
echo "$DMG_PATH"
