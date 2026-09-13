#!/usr/bin/env bash
# Removes QuickPuff. Your dab history (~/.local/share/quickpuff/dabs.json) and
# settings (~/.config/quickpuff) are kept; delete those folders too for a clean
# removal.
set -euo pipefail

BIN_DIR="${XDG_BIN_HOME:-$HOME/.local/bin}"
DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
DATA_DIR="$DATA_HOME/quickpuff"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
PLUGIN_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/omarchy/plugins"
PLUGIN_ID="auxxed.quickpuff"

systemctl --user disable --now quickpuff-daemon.service >/dev/null 2>&1 || true
rm -f "$UNIT_DIR/quickpuff-daemon.service"
systemctl --user daemon-reload >/dev/null 2>&1 || true

rm -f "$BIN_DIR/quickpuff"
rm -rf "$DATA_DIR/venv"

echo "QuickPuff removed. Dab history ($DATA_DIR) and settings (~/.config/quickpuff) were kept."

target="$PLUGIN_DIR/$PLUGIN_ID"
if [[ -L $target ]]; then
  command -v omarchy >/dev/null && omarchy plugin disable "$PLUGIN_ID" >/dev/null 2>&1 || true
  rm -f "$target"
  command -v omarchy-shell >/dev/null && omarchy-shell -q shell rescanPlugins
elif [[ -d $target ]] && command -v omarchy >/dev/null; then
  # Last, because this deletes the folder this script runs from.
  exec omarchy plugin remove "$PLUGIN_ID" --yes
fi
