#!/usr/bin/env bash
# Sets up OmaPuffco's backend: a Python environment for the Bluetooth
# libraries, the `omapuffco` command, and the user systemd daemon the bar
# widget talks to. Everything lands in your home directory; no root access is
# needed. Safe to re-run, and it migrates an install from when the project was
# called Ember.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"
DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
BIN_DIR="${XDG_BIN_HOME:-$HOME/.local/bin}"
DATA_DIR="$DATA_HOME/omapuffco"
UNIT_DIR="$CONFIG_HOME/systemd/user"
PLUGIN_DIR="$CONFIG_HOME/omarchy/plugins"
SHELL_CONFIG="$CONFIG_HOME/omarchy/shell.json"
VENV="$DATA_DIR/venv"
PLUGIN_ID="auxxed.omapuffco"
LEGACY_PLUGIN_ID="auxxed.ember"
INSTALL_LINE="omarchy plugin add https://github.com/Auxxed/omapuffco --enable && ~/.config/omarchy/plugins/$PLUGIN_ID/install.sh"

say() { printf '==> %s\n' "$*"; }
die() { printf 'omapuffco: %s\n' "$*" >&2; exit 1; }

say "OmaPuffco — Peak Pro controls ($ROOT)"

command -v python3 >/dev/null || die "python3 is required"
python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' || die "Python 3.10 or newer is required"
command -v bluetoothctl >/dev/null || die "BlueZ is required (bluetoothctl not found)"
command -v systemctl >/dev/null || die "systemd is required to run the background daemon"

if [[ $ROOT == "$PLUGIN_DIR/$LEGACY_PLUGIN_ID" ]]; then
  die "this copy is still installed under the old Ember name. Run: omarchy plugin remove $LEGACY_PLUGIN_ID --yes && $INSTALL_LINE"
fi

if [[ -f $UNIT_DIR/ember-daemon.service ]]; then
  say "Migrating from Ember"
  systemctl --user disable --now ember-daemon.service >/dev/null 2>&1 || true
  rm -f "$UNIT_DIR/ember-daemon.service"
  systemctl --user daemon-reload
fi
if [[ -d $CONFIG_HOME/ember && ! -e $CONFIG_HOME/omapuffco ]]; then
  mv "$CONFIG_HOME/ember" "$CONFIG_HOME/omapuffco"
fi
if [[ -f $DATA_HOME/ember/dabs.json && ! -e $DATA_DIR/dabs.json ]]; then
  mkdir -p "$DATA_DIR"
  mv "$DATA_HOME/ember/dabs.json" "$DATA_DIR/dabs.json"
fi
if [[ -L $DATA_HOME/ember/venv ]]; then rm "$DATA_HOME/ember/venv"; fi
if [[ -L $DATA_HOME/ember/src ]]; then rm "$DATA_HOME/ember/src"; fi
rmdir "$DATA_HOME/ember" 2>/dev/null || true
if [[ -f $BIN_DIR/ember ]] && grep -q -- '-m ember' "$BIN_DIR/ember"; then
  rm "$BIN_DIR/ember"
fi

say "Python environment ($VENV)"
mkdir -p "$DATA_DIR" "$BIN_DIR" "$UNIT_DIR"
[[ -x $VENV/bin/python ]] || python3 -m venv "$VENV"
"$VENV/bin/pip" install -q --upgrade pip
"$VENV/bin/pip" install -q -r "$ROOT/requirements.txt"

say "omapuffco command ($BIN_DIR/omapuffco)"
cat > "$BIN_DIR/omapuffco" <<EOF
#!/usr/bin/env bash
export PYTHONPATH="$ROOT/src\${PYTHONPATH:+:\$PYTHONPATH}"
exec "$VENV/bin/python" -m omapuffco "\$@"
EOF
chmod +x "$BIN_DIR/omapuffco"

say "Background daemon (systemd user service)"
sed \
  -e "s|%h/.local/share/omapuffco/venv|$VENV|g" \
  -e "s|%h/.local/share/omapuffco/src|$ROOT/src|g" \
  "$ROOT/packaging/omapuffco-daemon.service" > "$UNIT_DIR/omapuffco-daemon.service"
systemctl --user daemon-reload
systemctl --user enable omapuffco-daemon.service >/dev/null
systemctl --user restart omapuffco-daemon.service

if command -v omarchy >/dev/null; then
  say "Omarchy bar widget"
  mkdir -p "$PLUGIN_DIR"

  # Remember where the old Ember widget sat so the new one takes its place.
  placement=""
  if [[ -f $SHELL_CONFIG ]] && command -v jq >/dev/null; then
    placement=$(jq -r --arg id "$LEGACY_PLUGIN_ID" '
      (.bar.layout // {}) | to_entries[]
      | .key as $section
      | (.value | map(if type == "object" then .id else . end) | index($id)) as $i
      | select($i != null) | "\($section) \($i)"' "$SHELL_CONFIG" 2>/dev/null | head -n1) || placement=""
  fi

  if [[ -L $PLUGIN_DIR/$LEGACY_PLUGIN_ID ]]; then
    omarchy plugin disable "$LEGACY_PLUGIN_ID" >/dev/null 2>&1 || true
    rm "$PLUGIN_DIR/$LEGACY_PLUGIN_ID"
  elif [[ -d $PLUGIN_DIR/$LEGACY_PLUGIN_ID ]]; then
    omarchy plugin remove "$LEGACY_PLUGIN_ID" --yes >/dev/null 2>&1 || true
  fi

  if [[ ! -e $PLUGIN_DIR/$PLUGIN_ID && ! -L $PLUGIN_DIR/$PLUGIN_ID ]]; then
    # Run from a plain clone rather than `omarchy plugin add`: link the
    # checkout in as a development plugin.
    ln -s "$ROOT" "$PLUGIN_DIR/$PLUGIN_ID"
  fi
  omarchy-shell -q shell rescanPlugins
  for _ in $(seq 40); do
    if omarchy plugin list --json 2>/dev/null | jq -e --arg id "$PLUGIN_ID" 'any(.[]; .id == $id)' >/dev/null 2>&1; then
      break
    fi
    sleep 0.05
  done
  omarchy plugin enable "$PLUGIN_ID" >/dev/null 2>&1 || true
  if [[ -n $placement ]]; then
    read -r section index <<<"$placement"
    omarchy bar move "$PLUGIN_ID" --section "$section" --index "$index" >/dev/null 2>&1 || true
  fi
fi

echo
echo "OmaPuffco is installed."
echo "  Wake the Peak Pro, keep it near this computer, and disconnect the phone app"
echo "  (the Peak accepts one connection at a time). Then click the OmaPuffco widget"
echo "  in the bar and choose Connect, or run: omapuffco connect"
echo "  If anything doesn't work, run: omapuffco doctor"
case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) echo "  Add $BIN_DIR to your PATH to use the omapuffco command in a terminal." ;;
esac
