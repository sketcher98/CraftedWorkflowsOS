#!/data/data/com.termux/files/usr/bin/bash
# preflight.sh — environment checks for bootstrap.
# Safe to run anytime; makes NO changes.
set -euo pipefail
. "$(dirname "$0")/../lib/common.sh"

UNATTENDED="${UNATTENDED:-0}"
REQUIRE_EMPTY_HOME=0
[ "${1:-}" = "--require-empty-home" ] && REQUIRE_EMPTY_HOME=1

fail=0
note() { info "$1"; }
bad()  { warn "PREFLIGHT FAIL: $1"; fail=1; }

log "Preflight — $(uname -a | cut -c1-120)"

# 1. Termux
if [ -d "$PREFIX" ] && [ -x "$PREFIX/bin/pkg" ]; then
  note "P1 Termux OK ($PREFIX)"
else
  bad "P1 Termux missing — install Termux from F-Droid first"
fi

# 2. Architecture
ARCH="$(uname -m)"
if [ "$ARCH" = "armv7l" ] || [ "$ARCH" = "armv8l" ]; then
  note "P2 arch OK ($ARCH)"
else
  bad "P2 arch=$ARCH (expected armv7l) — manifests were captured for ARMv7; reuse may fail"
fi

# 3. Unprivileged
if [ "$(id -u)" = "0" ]; then
  bad "P3 running as root — bootstrap expects unprivileged Termux uid"
else
  note "P3 unprivileged OK (uid $(id -u))"
fi

# 4. Core commands
for c in curl git tar pkg jq; do
  if command -v "$c" >/dev/null 2>&1; then :; else bad "P4 missing command: $c"; fi
done
command -v python3.13 >/dev/null 2>&1 || note "P4 note: python3.13 not yet present (will be installed with Termux packages)"

# 5. Storage symlink (termux-setup-storage)
if [ -L "$HOME/storage/shared" ]; then
  note "P5 storage OK"
else
  note "P5 warning: ~/storage symlinks missing (run 'termux-setup-storage' or bootstrap will attempt it)"
fi

# 6. Disk headroom
FREE_KB="$(df -P /data 2>/dev/null | awk 'NR==2 {print $4}')"
FREE_MB=$(( ${FREE_KB:-0} / 1024 ))
if [ "$FREE_MB" -ge 2048 ]; then
  note "P6 disk OK (${FREE_MB} MB free)"
else
  bad "P6 disk low: ${FREE_MB} MB free (need >=2048 MB)"
fi

# 7. Existing Hermes home
if is_empty_dir "$HERMES_HOME"; then
  note "P7 fresh ~/.hermes (empty/missing) OK"
else
  if [ "$REQUIRE_EMPTY_HOME" = "1" ]; then
    bad "P7 ~/.hermes already populated — refusing unattended fresh-device run (use --overwrite only after review)"
  else
    warn "P7 ~/.hermes already populated — bootstrap will backup before any change"
  fi
fi

# 8. Offline bundle present?
BUNDLE="${BOOTSTRAP_BUNDLE:-$HOME/storage/downloads/hermes-offline-bundle.tar.gz}"
if [ -f "$BUNDLE" ]; then
  note "P8 offline bundle OK ($BUNDLE, $(du -h "$BUNDLE" | cut -f1))"
else
  note "P8 note: offline bundle not found at $BUNDLE (online fallback will be attempted; ARMv7 may need builds)"
fi

[ "$fail" = "0" ] || die "preflight failed ($fail checks) — resolve above before continuing"
log "Preflight PASSED"
