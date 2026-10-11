#!/usr/bin/env bash
# Installs Planet Time for the current user (no root needed).
# Usage: ./install.sh            install or update
#        ./install.sh --uninstall remove the app, icon, launcher and extension
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"

APP_ID="local.LiveClock"
EXT_UUID="live-clock-icon@local"
APP_DIR="$DATA_HOME/live-clock"
EXT_DIR="$DATA_HOME/gnome-shell/extensions/$EXT_UUID"
ICON="$DATA_HOME/icons/hicolor/scalable/apps/$APP_ID.svg"
DESKTOP="$DATA_HOME/applications/$APP_ID.desktop"

refresh_caches() {
    command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -q -t "$DATA_HOME/icons/hicolor" 2>/dev/null || true
    command -v update-desktop-database >/dev/null && update-desktop-database -q "$DATA_HOME/applications" 2>/dev/null || true
}

if [[ "${1:-}" == "--uninstall" ]]; then
    command -v gnome-extensions >/dev/null && gnome-extensions disable "$EXT_UUID" 2>/dev/null || true
    rm -rf "$APP_DIR" "$EXT_DIR"
    rm -f "$ICON" "$DESKTOP"
    refresh_caches
    echo "Planet Time removed. Your settings are kept in ~/.config/live-clock/."
    exit 0
fi

mkdir -p "$APP_DIR" "$(dirname "$EXT_DIR")" "$(dirname "$ICON")" "$(dirname "$DESKTOP")"

rm -rf "$APP_DIR/liveclock"
cp "$SRC/app/live_clock.py" "$APP_DIR/"
cp -r "$SRC/app/liveclock" "$APP_DIR/"

rm -rf "$EXT_DIR"
cp -r "$SRC/extension/$EXT_UUID" "$EXT_DIR"

cp "$SRC/data/$APP_ID.svg" "$ICON"
sed "s|@APP_DIR@|$APP_DIR|g" "$SRC/data/$APP_ID.desktop.in" > "$DESKTOP"

refresh_caches

echo "Planet Time installed. Open it from the app grid."
echo
echo "To show the live time on the dock icon, enable the extension:"
echo "  gnome-extensions enable $EXT_UUID"
echo "On Wayland, log out and back in first so GNOME Shell sees the new extension."
