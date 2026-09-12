#!/usr/bin/env bash
set -euo pipefail

BIN_DIR="${XDG_BIN_HOME:-$HOME/.local/bin}"
DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

systemctl --user disable --now ember-daemon.service >/dev/null 2>&1 || true
rm -f "$UNIT_DIR/ember-daemon.service"
systemctl --user daemon-reload >/dev/null 2>&1 || true

rm -f "$BIN_DIR/ember"
rm -f "$DATA_DIR/applications/ember.desktop"
rm -f "$DATA_DIR/icons/hicolor/scalable/apps/ember.svg"
rm -rf "$DATA_DIR/ember"

echo "Ember uninstalled. Config in ~/.config/ember was left in place."
