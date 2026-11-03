#!/data/data/com.termux/files/usr/bin/bash
# setup-launchers.sh — install ~/bin/hermes, ~/.local/bin daemon/profile
# wrappers (from versioned templates), and the ~/bin symlinks matching the
# live layout.
set -euo pipefail
. "$(dirname "$0")/../lib/common.sh"

HERMES_TEMPLATE="$BOOTSTRAP_DIR/templates/hermes/hermes-launcher"
LAUNCHER_TEMPLATES="$BOOTSTRAP_DIR/templates/launchers"

[ -f "$HERMES_TEMPLATE" ] || die "hermes launcher template missing"
[ -d "$LAUNCHER_TEMPLATES" ] || die "launcher templates dir missing"

mkdir -p "$HOME/bin" "$HOME/.local/bin"

# ---------------------------------------------------------------- ~/bin/hermes
install_hermes_launcher() {
  sed "s|/data/data/com.termux/files/home|\$HOME|g" "$HERMES_TEMPLATE" > "$HOME/bin/hermes"
  chmod 700 "$HOME/bin/hermes"
  log "installed $HOME/bin/hermes"
}
if [ -f "$HOME/bin/hermes" ]; then
  if ! cmp -s "$HOME/bin/hermes" <(sed "s|/data/data/com.termux/files/home|\$HOME|g" "$HERMES_TEMPLATE"); then
    backup_file "$HOME/bin/hermes"
    install_hermes_launcher
  else
    info "$HOME/bin/hermes already matches template"
  fi
else
  install_hermes_launcher
fi

# ------------------------------------------------------- ~/.local/bin wrappers
for tpl in "$LAUNCHER_TEMPLATES"/*; do
  name="$(basename "$tpl")"
  target="$HOME/.local/bin/$name"
  sed "s|/data/data/com.termux/files/home|\$HOME|g" "$tpl" > "$target"
  chmod +x "$target"
done
log "installed $(ls -1 "$LAUNCHER_TEMPLATES" | wc -l) launchers into ~/.local/bin"

# ------------------------------------------------------- ~/bin symlinks (gateway etc.)
for name in gateway-logs gateway-perma-enable gateway-perma-stop gateway-start gateway-status gateway-stop gateway-watchdog hermes-mem hermes-start-all hermes-stop-all; do
  if [ ! -e "$HOME/bin/$name" ]; then
    ln -s ../.local/bin/"$name" "$HOME/bin/$name"
  fi
done
log "~/bin symlinks ensured"

# ------------------------------------------------------------- PATH persistence
PROFILE="$HOME/.profile"
if [ -f "$PROFILE" ]; then
  if ! grep -q 'HOME/.local/bin' "$PROFILE" 2>/dev/null; then
    echo 'export PATH="$HOME/bin:$HOME/.local/bin:$PATH"' >> "$PROFILE"
    log "added PATH export to ~/.profile"
  fi
fi

log "setup-launchers.sh complete"
