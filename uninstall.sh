#!/usr/bin/env bash
# Removes Ember. Your dab history (~/.local/share/ember/dabs.json) and settings
# (~/.config/ember) are kept; delete those folders too for a clean removal.
set -euo pipefail

BIN_DIR="${XDG_BIN_HOME:-$HOME/.local/bin}"
DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
DATA_DIR="$DATA_HOME/ember"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
PLUGIN_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/omarchy/plugins"
PLUGIN_ID="auxxed.ember"

systemctl --user disable --now ember-daemon.service >/dev/null 2>&1 || true
rm -f "$UNIT_DIR/ember-daemon.service"
systemctl --user daemon-reload >/dev/null 2>&1 || true

rm -f "$BIN_DIR/ember"
rm -rf "$DATA_DIR/venv"
rm -f "$DATA_DIR/src"
# Left behind by the old GTK app.
rm -f "$DATA_HOME/applications/ember.desktop" "$DATA_HOME/icons/hicolor/scalable/apps/ember.svg"

echo "Ember removed. Dab history ($DATA_DIR) and settings (~/.config/ember) were kept."

target="$PLUGIN_DIR/$PLUGIN_ID"
if [[ -L $target ]]; then
  command -v omarchy >/dev/null && omarchy plugin disable "$PLUGIN_ID" >/dev/null 2>&1 || true
  rm -f "$target"
  command -v omarchy-shell >/dev/null && omarchy-shell -q shell rescanPlugins
elif [[ -d $target ]] && command -v omarchy >/dev/null; then
  # Last, because this deletes the folder this script runs from.
  exec omarchy plugin remove "$PLUGIN_ID" --yes
fi
