#!/usr/bin/env bash
# Sets up Ember's backend: a Python environment for the Bluetooth libraries,
# the `ember` command, and the user systemd daemon the bar widget talks to.
# Everything lands in your home directory; no root access is needed.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
BIN_DIR="${XDG_BIN_HOME:-$HOME/.local/bin}"
DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/ember"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
PLUGIN_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/omarchy/plugins"
VENV="$DATA_DIR/venv"
PLUGIN_ID="auxxed.ember"

say() { printf '==> %s\n' "$*"; }
die() { printf 'ember: %s\n' "$*" >&2; exit 1; }

say "Ember — Peak Pro controls ($ROOT)"

command -v python3 >/dev/null || die "python3 is required"
python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' || die "Python 3.10 or newer is required"
command -v bluetoothctl >/dev/null || die "BlueZ is required (bluetoothctl not found)"
command -v systemctl >/dev/null || die "systemd is required to run the background daemon"

say "Python environment ($VENV)"
mkdir -p "$DATA_DIR" "$BIN_DIR" "$UNIT_DIR"
# Older installs linked these to a venv inside the checkout. The venv now lives
# outside the plugin folder, which `omarchy plugin` requires to be symlink-free.
[[ -L $VENV ]] && rm "$VENV"
[[ -L $DATA_DIR/src ]] && rm "$DATA_DIR/src"
[[ -x $VENV/bin/python ]] || python3 -m venv "$VENV"
"$VENV/bin/pip" install -q --upgrade pip
"$VENV/bin/pip" install -q -r "$ROOT/requirements.txt"

say "ember command ($BIN_DIR/ember)"
cat > "$BIN_DIR/ember" <<EOF
#!/usr/bin/env bash
export PYTHONPATH="$ROOT/src\${PYTHONPATH:+:\$PYTHONPATH}"
exec "$VENV/bin/python" -m ember "\$@"
EOF
chmod +x "$BIN_DIR/ember"

say "Background daemon (systemd user service)"
sed \
  -e "s|%h/.local/share/ember/venv|$VENV|g" \
  -e "s|%h/.local/share/ember/src|$ROOT/src|g" \
  "$ROOT/packaging/ember-daemon.service" > "$UNIT_DIR/ember-daemon.service"
systemctl --user daemon-reload
systemctl --user enable ember-daemon.service >/dev/null
systemctl --user restart ember-daemon.service

if command -v omarchy >/dev/null; then
  say "Omarchy bar widget"
  mkdir -p "$PLUGIN_DIR"
  if [[ ! -e $PLUGIN_DIR/$PLUGIN_ID && ! -L $PLUGIN_DIR/$PLUGIN_ID ]]; then
    # Run from a plain clone rather than `omarchy plugin add`: link the
    # checkout in as a development plugin.
    ln -s "$ROOT" "$PLUGIN_DIR/$PLUGIN_ID"
    omarchy-shell -q shell rescanPlugins
  fi
  omarchy plugin enable "$PLUGIN_ID" >/dev/null 2>&1 || true
fi

echo
echo "Ember is installed."
echo "  Wake the Peak Pro, keep it near this computer, and disconnect the phone app"
echo "  (the Peak accepts one connection at a time). Then click the Ember widget"
echo "  in the bar and choose Connect, or run: ember connect"
case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) echo "  Add $BIN_DIR to your PATH to use the ember command in a terminal." ;;
esac
