#!/data/data/com.termux/files/usr/bin/bash
# restore-uv-cache.sh — restore ~/.cache/uv from the offline bundle.
# NEVER deletes an existing cache; only extracts/merges on top.
set -euo pipefail
. "$(dirname "$0")/../lib/common.sh"

BUNDLE="${BOOTSTRAP_BUNDLE:-$HOME/storage/downloads/hermes-offline-bundle.tar.gz}"

if [ ! -f "$BUNDLE" ]; then
  warn "offline bundle not found: $BUNDLE — skipping (online fallback will be attempted)"
  exit 0
fi

# already healthy cache?
if [ -d "$HOME/.cache/uv" ] && [ -n "$(ls -A "$HOME/.cache/uv" 2>/dev/null)" ]; then
  info "uv cache already present ($(du -sh "$HOME/.cache/uv" | cut -f1)) — leaving untouched"
  exit 0
fi

mkdir -p "$HOME/.cache"
log "restoring uv cache from $BUNDLE ..."
case "$BUNDLE" in
  *.zst) tar --zstd -xf "$BUNDLE" -C "$HOME/.cache" ;;
  *)      tar -xzf "$BUNDLE" -C "$HOME/.cache" ;;
esac

if [ -d "$HOME/.cache/uv" ] && [ -n "$(ls -A "$HOME/.cache/uv" 2>/dev/null)" ]; then
  log "uv cache restored: $(du -sh "$HOME/.cache/uv" | cut -f1)"
else
  die "bundle restored but ~/.cache/uv is empty — bundle may be malformed"
fi
