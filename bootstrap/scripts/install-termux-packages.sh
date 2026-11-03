#!/data/data/com.termux/files/usr/bin/bash
# install-termux-packages.sh — install the pinned Termux package set from
# manifests/termux-packages.txt. Idempotent. Never runs `pkg upgrade`.
set -euo pipefail
. "$(dirname "$0")/../lib/common.sh"

MANIFEST="$MANIFESTS/termux-packages.txt"
[ -f "$MANIFEST" ] || die "manifest missing: $MANIFEST"

require_termux

log "Installing Termux packages from manifest ($(wc -l < "$MANIFEST") packages)"

# apt update (needed on a fresh device; safe/idempotent)
log "apt update ..."
pkg update || die "pkg update failed — check network/mirror"

# ensure tur-repo for packages that need it
if ! dpkg -s tur-repo >/dev/null 2>&1; then
  log "installing tur-repo ..."
  pkg install -y tur-repo
fi

# build package name list (name only, skip versions)
PKG_NAMES="$(cut -f1 "$MANIFEST" | tr '\n' ' ')"

log "pkg install -y (${#PKG_NAMES} chars of names) — this is the long step"
if [ "${UNATTENDED:-0}" != "1" ]; then
  read -r -p "Install $(echo "$PKG_NAMES" | wc -w) packages now? [y/N] " ans
  [ "$ans" = "y" ] || [ "$ans" = "Y" ] || die "aborted by user"
fi

run_timed pkg install -y $PKG_NAMES

log "Package install complete. Verifying ..."
MISSING=0
while IFS=$'\t' read -r pkg ver; do
  dpkg -s "$pkg" >/dev/null 2>&1 || { warn "missing after install: $pkg"; MISSING=1; }
done < "$MANIFEST"
[ "$MISSING" = "0" ] || die "some packages failed to install (see above)"
log "All $(wc -l < "$MANIFEST") manifest packages present."
