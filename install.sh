#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="${XDG_BIN_HOME:-$HOME/.local/bin}"
DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
APP_DIR="$DATA_DIR/ember"
ICON_DIR="$DATA_DIR/icons/hicolor/scalable/apps"
DESKTOP_DIR="$DATA_DIR/applications"

echo "==> Ember — Peak Pro companion"
echo "    $ROOT"

if ! command -v python3 >/dev/null; then
  echo "python3 is required" >&2
  exit 1
fi

echo "==> Python venv (system GTK + BLE deps)"
if [[ ! -x "$ROOT/.venv/bin/python" ]]; then
  python3 -m venv --system-site-packages "$ROOT/.venv"
fi
"$ROOT/.venv/bin/pip" install -q -U pip
"$ROOT/.venv/bin/pip" install -q 'bleak>=3.0.0' 'cbor2>=5.6.0' 'dbus-fast>=2.0.0'

echo "==> Wrappers"
mkdir -p "$BIN_DIR" "$APP_DIR" "$ICON_DIR" "$DESKTOP_DIR" "$UNIT_DIR"
ln -sfn "$ROOT/src" "$APP_DIR/src"
ln -sfn "$ROOT/.venv" "$APP_DIR/venv"

cat > "$BIN_DIR/ember" <<EOF
#!/usr/bin/env bash
export PYTHONPATH="$ROOT/src\${PYTHONPATH:+:\$PYTHONPATH}"
exec "$ROOT/.venv/bin/python" -m ember "\$@"
EOF
chmod +x "$BIN_DIR/ember" "$ROOT/install.sh"

echo "==> Desktop entry and icon"
install -m 644 "$ROOT/packaging/icons/ember.svg" "$ICON_DIR/ember.svg"
sed "s|^Exec=ember|Exec=$BIN_DIR/ember|" "$ROOT/packaging/ember.desktop" > "$DESKTOP_DIR/ember.desktop"
chmod 644 "$DESKTOP_DIR/ember.desktop"

echo "==> systemd user service"
sed \
  -e "s|%h/.local/share/ember/venv/bin/python|$ROOT/.venv/bin/python|g" \
  -e "s|%h/.local/share/ember/src|$ROOT/src|g" \
  "$ROOT/packaging/ember-daemon.service" > "$UNIT_DIR/ember-daemon.service"
systemctl --user daemon-reload
systemctl --user enable --now ember-daemon.service >/dev/null

if command -v update-desktop-database >/dev/null; then
  update-desktop-database "$DESKTOP_DIR" >/dev/null 2>&1 || true
fi
if command -v gtk-update-icon-cache >/dev/null; then
  gtk-update-icon-cache -f "$DATA_DIR/icons/hicolor" >/dev/null 2>&1 || true
fi

echo
echo "Installed."
echo "  App:     ember"
echo "  CLI:     ember scan | ember connect | ember status | ember heat start"
echo "  Waybar:  ember waybar"
echo "  Daemon:  systemctl --user status ember-daemon"
echo
echo "Wake the Peak Pro, keep it near the PC, and disconnect the phone app."
echo "If $BIN_DIR is not on PATH, add it or launch Ember from the app launcher."
