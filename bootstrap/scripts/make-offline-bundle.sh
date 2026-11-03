#!/data/data/com.termux/files/usr/bin/bash
# make-offline-bundle.sh — bundle the uv cache (~/.cache/uv, the irreplaceable
# ARMv7 build inputs) into ~/storage/downloads/hermes-offline-bundle.tar.gz.
# Run on the donor device after make-manifests.sh.
set -euo pipefail
. "$(dirname "$0")/../lib/common.sh"

OUT="${BOOTSTRAP_BUNDLE_OUT:-$HOME/storage/downloads/hermes-offline-bundle.tar.gz}"

[ -d "$HOME/.cache/uv" ] || die "no uv cache at ~/.cache/uv"
mkdir -p "$HOME/storage/downloads"

log "bundling uv cache -> $OUT ..."
tar -czf "$OUT" -C "$HOME/.cache" uv
log "bundle: $(du -h "$OUT" | cut -f1)"
log "make-offline-bundle.sh complete"
