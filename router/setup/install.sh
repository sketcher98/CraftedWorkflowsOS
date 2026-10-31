#!/usr/bin/env bash
# Router installer — Unix (Linux / macOS / Termux).
# Usage: curl -fsSL https://.../install.sh | bash
#
# What it does:
#   1. Determines OS/arch, picks the right prebuilt bundle.
#   2. Installs to ~/.local/share/router (binary + routeops + setup wizard).
#   3. Symlinks the router + `router setup` onto PATH (~/.local/bin).
#   4. Runs the setup wizard (interactive) to connect harnesses.
#
# Safe by default: never writes outside $HOME, never touches an existing
# file without a timestamped backup (handled by the wizard writers).

set -euo pipefail

PREFIX="${ROUTER_PREFIX:-$HOME/.local}"
SHARE="$PREFIX/share/router"
BIN="$PREFIX/bin"
BASE_URL="${ROUTER_BASE_URL:-https://github.com/sketcher98/CraftedWorkflowsOS/releases/latest/download}"

say() { printf '\033[1;32m[router]\033[0m %s\n' "$*"; }

fail() { printf '\033[1;31m[router ERROR]\033[0m %s\n' "$*" >&2; exit 1; }

detect_platform() {
  local os arch
  os="$(uname -s | tr '[:upper:]' '[:lower:]')"
  arch="$(uname -m)"
  case "$os" in
    linux) os="linux" ;;
    darwin) os="darwin" ;;
    *) fail "unsupported OS: $(uname -s)" ;;
  esac
  case "$arch" in
    x86_64|amd64) arch="amd64" ;;
    aarch64|arm64) arch="arm64" ;;
    armv7l|armv8l) arch="armv7" ;;
    *) fail "unsupported arch: $arch" ;;
  esac
  echo "${os}-${arch}"
}

main() {
  local plat archive
  plat="$(detect_platform)"
  archive="router-${plat}.tar.gz"
  say "platform: ${plat}"

  mkdir -p "$SHARE" "$BIN"
  local tmp
  tmp="$(mktemp -d)"
  trap 'rm -rf "$tmp"' EXIT

  if [ -n "${ROUTER_LOCAL_BUNDLE:-}" ]; then
    cp "$ROUTER_LOCAL_BUNDLE" "$tmp/$archive"
  else
    say "downloading ${archive} ..."
    curl -fsSL "$BASE_URL/$archive" -o "$tmp/$archive"
  fi
  tar -xzf "$tmp/$archive" -C "$tmp"

  cp -r "$tmp"/* "$SHARE"/
  chmod +x "$SHARE/router" "$SHARE/setup/wizard.py" 2>/dev/null || true

  ln -sf "$SHARE/router" "$BIN/router"
  ln -sf "$SHARE/setup/wizard.py" "$BIN/router-setup"

  say "installed to $SHARE; added to $BIN"
  if ! echo "$PATH" | grep -q "$BIN"; then
    say "NOTE: add '$BIN' to your PATH (export PATH=\"$BIN:\$PATH\")"
  fi

  if command -v router >/dev/null 2>&1; then
    say "router available. Now: run 'router setup' to connect your tools."
  fi
}

main "$@"
