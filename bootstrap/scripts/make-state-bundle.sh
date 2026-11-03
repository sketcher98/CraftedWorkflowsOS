#!/data/data/com.termux/files/usr/bin/bash
# make-state-bundle.sh — bundle non-secret Hermes state (skills, plugins,
# memories, cron jobs, profiles) for restore on a fresh device.
# Secrets (.env, auth.json, mcp tokens, cookies) are NOT included — use
# make-secrets-bundle.sh for those.
set -euo pipefail
. "$(dirname "$0")/../lib/common.sh"

OUT="${BOOTSTRAP_STATE_OUT:-$HOME/storage/downloads/hermes-state-bundle.tar.gz}"
mkdir -p "$HOME/storage/downloads"

INCLUDE=()
for d in "$HERMES_HOME/skills" "$HERMES_HOME/plugins" "$HERMES_HOME/memories" \
         "$HERMES_HOME/cron" "$HERMES_HOME/profiles"; do
  [ -d "$d" ] && INCLUDE+=("${d#$HERMES_HOME/}")
done

[ "${#INCLUDE[@]}" -gt 0 ] || die "nothing to bundle under $HERMES_HOME"

log "bundling state ($(IFS=,; echo "${INCLUDE[*]}")) -> $OUT ..."
tar -czf "$OUT" -C "$HERMES_HOME" "${INCLUDE[@]}"
log "state bundle: $(du -h "$OUT" | cut -f1)"
log "make-state-bundle.sh complete"
