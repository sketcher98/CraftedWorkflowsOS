#!/data/data/com.termux/files/usr/bin/bash
# setup-gateway.sh — create runtime dirs and install the crontab watchdog
# entry (every 5 min) so the Hermes gateway auto-restarts.
# Optional: --start to launch the gateway immediately.
set -euo pipefail
. "$(dirname "$0")/../lib/common.sh"

START_GATEWAY=0
[ "${1:-}" = "--start" ] && START_GATEWAY=1

mkdir -p "$HERMES_HOME/logs" "$HERMES_HOME/.runtime"

WATCHDOG="$HOME/.local/bin/gateway-watchdog"
if [ ! -x "$WATCHDOG" ]; then
  die "gateway-watchdog missing — run setup-launchers.sh first"
fi

# ------------------------------------------------------------ crontab entry
LINE="*/5 * * * * $WATCHDOG"
EXISTING="$(crontab -l 2>/dev/null || true)"
if printf '%s\n' "$EXISTING" | grep -Fq "$WATCHDOG"; then
  info "crontab watchdog entry already present"
else
  log "adding crontab watchdog entry"
  { printf '%s\n' "$EXISTING"; echo "$LINE"; } | crontab -
fi
crontab -l | grep -F "$WATCHDOG" || die "crontab entry failed to persist"

if [ "$START_GATEWAY" = "1" ]; then
  log "starting gateway (gateway-start) ..."
  "$HOME/.local/bin/gateway-start"
fi

log "setup-gateway.sh complete"
