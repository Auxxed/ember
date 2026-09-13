#!/usr/bin/env bash
# Sets up QuickPuff's backend: a Python environment for the Bluetooth
# libraries, the `quickpuff` command, and the user systemd daemon the bar
# widget talks to. Everything lands in your home directory; no root access is
# needed. Safe to re-run, and it migrates an install from when the project was
# called OmaPuffco or Ember.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"
DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
BIN_DIR="${XDG_BIN_HOME:-$HOME/.local/bin}"
DATA_DIR="$DATA_HOME/quickpuff"
UNIT_DIR="$CONFIG_HOME/systemd/user"
PLUGIN_DIR="$CONFIG_HOME/omarchy/plugins"
SHELL_CONFIG="$CONFIG_HOME/omarchy/shell.json"
VENV="$DATA_DIR/venv"
PLUGIN_ID="auxxed.quickpuff"
# Earlier names of this project, newest first.
LEGACY_NAMES=(omapuffco ember)
INSTALL_LINE="omarchy plugin add https://github.com/Auxxed/quickpuff --enable && ~/.config/omarchy/plugins/$PLUGIN_ID/install.sh"

say() { printf '==> %s\n' "$*"; }
die() { printf 'quickpuff: %s\n' "$*" >&2; exit 1; }

say "QuickPuff — Peak Pro controls ($ROOT)"

command -v python3 >/dev/null || die "python3 is required"
python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' || die "Python 3.10 or newer is required"
command -v bluetoothctl >/dev/null || die "BlueZ is required (bluetoothctl not found)"
command -v systemctl >/dev/null || die "systemd is required to run the background daemon"

for legacy in "${LEGACY_NAMES[@]}"; do
  if [[ $ROOT == "$PLUGIN_DIR/auxxed.$legacy" ]]; then
    die "this copy is still installed under the old plugin id auxxed.$legacy. Run: omarchy plugin remove auxxed.$legacy --yes && $INSTALL_LINE"
  fi
done

# Earlier names: stop their daemon, carry settings and dab history across, and
# drop their command.
for legacy in "${LEGACY_NAMES[@]}"; do
  if [[ -f $UNIT_DIR/$legacy-daemon.service ]]; then
    say "Migrating from the $legacy install"
    systemctl --user disable --now "$legacy-daemon.service" >/dev/null 2>&1 || true
    rm -f "$UNIT_DIR/$legacy-daemon.service"
    systemctl --user daemon-reload
  fi
  if [[ -d $CONFIG_HOME/$legacy && ! -e $CONFIG_HOME/quickpuff ]]; then
    mv "$CONFIG_HOME/$legacy" "$CONFIG_HOME/quickpuff"
  fi
  old_data="$DATA_HOME/$legacy"
  if [[ -d $old_data && ! -L $old_data ]]; then
    mkdir -p "$DATA_DIR"
    for item in "$old_data"/* "$old_data"/.[!.]*; do
      [[ -e $item || -L $item ]] || continue
      name="$(basename "$item")"
      case $name in
        # Rebuilt below: a venv's scripts hard-code the path it was made at.
        venv | src) rm -rf "$item" ;;
        *) [[ -e $DATA_DIR/$name ]] || mv "$item" "$DATA_DIR/$name" ;;
      esac
    done
    rmdir "$old_data" 2>/dev/null || true
  fi
  if [[ -f $BIN_DIR/$legacy ]] && grep -q -- "-m $legacy" "$BIN_DIR/$legacy"; then
    rm "$BIN_DIR/$legacy"
  fi
done

say "Python environment ($VENV)"
mkdir -p "$DATA_DIR" "$BIN_DIR" "$UNIT_DIR"
[[ -x $VENV/bin/python ]] || python3 -m venv "$VENV"
"$VENV/bin/pip" install -q --upgrade pip
"$VENV/bin/pip" install -q -r "$ROOT/requirements.txt"

say "quickpuff command ($BIN_DIR/quickpuff)"
cat > "$BIN_DIR/quickpuff" <<EOF
#!/usr/bin/env bash
export PYTHONPATH="$ROOT/src\${PYTHONPATH:+:\$PYTHONPATH}"
exec "$VENV/bin/python" -m quickpuff "\$@"
EOF
chmod +x "$BIN_DIR/quickpuff"

say "Background daemon (systemd user service)"
sed \
  -e "s|%h/.local/share/quickpuff/venv|$VENV|g" \
  -e "s|%h/.local/share/quickpuff/src|$ROOT/src|g" \
  "$ROOT/packaging/quickpuff-daemon.service" > "$UNIT_DIR/quickpuff-daemon.service"
systemctl --user daemon-reload
systemctl --user enable quickpuff-daemon.service >/dev/null
systemctl --user restart quickpuff-daemon.service

if command -v omarchy >/dev/null; then
  say "Omarchy bar widget"
  mkdir -p "$PLUGIN_DIR"

  # Remember where the old widget sat so the new one takes its place.
  placement=""
  if [[ -f $SHELL_CONFIG ]] && command -v jq >/dev/null; then
    for legacy in "${LEGACY_NAMES[@]}"; do
      placement=$(jq -r --arg id "auxxed.$legacy" '
        (.bar.layout // {}) | to_entries[]
        | .key as $section
        | (.value | map(if type == "object" then .id else . end) | index($id)) as $i
        | select($i != null) | "\($section) \($i)"' "$SHELL_CONFIG" 2>/dev/null | head -n1) || placement=""
      [[ -n $placement ]] && break
    done
  fi

  for legacy in "${LEGACY_NAMES[@]}"; do
    old_plugin="$PLUGIN_DIR/auxxed.$legacy"
    if [[ -L $old_plugin ]]; then
      omarchy plugin disable "auxxed.$legacy" >/dev/null 2>&1 || true
      rm "$old_plugin"
    elif [[ -d $old_plugin ]]; then
      omarchy plugin remove "auxxed.$legacy" --yes >/dev/null 2>&1 || true
    fi
  done

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
echo "QuickPuff is installed."
echo "  Wake the Peak Pro, keep it near this computer, and disconnect the phone app"
echo "  (the Peak accepts one connection at a time). Then click the QuickPuff widget"
echo "  in the bar and choose Connect, or run: quickpuff connect"
echo "  If anything doesn't work, run: quickpuff doctor"
case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) echo "  Add $BIN_DIR to your PATH to use the quickpuff command in a terminal." ;;
esac
