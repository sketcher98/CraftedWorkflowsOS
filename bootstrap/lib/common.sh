#!/data/data/com.termux/files/usr/bin/bash
# common.sh — shared helpers for the Hermes/Termux bootstrap.
# Source from any bootstrap script:  . "$(dirname "$0")/../lib/common.sh"
set -euo pipefail

[ -z "${BOOTSTRAP_LIB_LOADED:-}" ] || return 0
BOOTSTRAP_LIB_LOADED=1

PREFIX="${PREFIX:-/data/data/com.termux/files/usr}"
# Canonical data root is ~/.hermes ALWAYS for bootstrap (ignore ambient
# HERMES_HOME override from a running Hermes profile session). Override only
# via BOOTSTRAP_HERMES_HOME.
export HERMES_HOME="${BOOTSTRAP_HERMES_HOME:-$HOME/.hermes}"
AGENT_DIR="$HERMES_HOME/hermes-agent"
VENV_DIR="$AGENT_DIR/venv"
BOOTSTRAP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MANIFESTS="$BOOTSTRAP_DIR/manifests"
SCRIPTS="$BOOTSTRAP_DIR/scripts"
LOGDIR="$HERMES_HOME/logs"
BOOTSTRAP_LOG="$LOGDIR/bootstrap.log"

NOW() { date +%Y%m%d-%H%M%S; }

log()  { echo "[bootstrap] $*"; }
info() { echo "[info]     $*"; }
warn() { echo "[warn]     $*"; }
die()  { echo "[FATAL] $*" >&2; exit 1; }

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "missing required command: $1"
}

# backup_file <path> — cp to .bak-<ts>, keep last 5 backups
backup_file() {
  local f="$1"
  [ -f "$f" ] || return 0
  local bak="${f}.bak-$(NOW)"
  cp -p "$f" "$bak"
  ls -1t "${f}".bak-* 2>/dev/null | tail -n +6 | xargs -r rm -f
  info "backup created: $bak"
}

# yaml_valid <file> — exit 0 if valid YAML (uses pick_python)
yaml_valid() {
  local f="$1" py
  py="$(pick_python)"
  [ -n "$py" ] || return 1
  "$py" -c 'import sys,yaml; yaml.safe_load(open(sys.argv[1])); sys.exit(0)' "$f" >/dev/null 2>&1
}

# pick_python — echo a python that has pyyaml: venv first, then system 3.13/3
pick_python() {
  local cand py=""
  for cand in "$VENV_DIR/bin/python" "$(command -v python3.13 || true)" "$(command -v python3 || true)"; do
    [ -n "$cand" ] || continue
    if "$cand" -c 'import yaml' >/dev/null 2>&1; then
      py="$cand"; break
    fi
  done
  echo "$py"
}

require_termux() {
  [ -d "$PREFIX" ] || die "not a Termux environment (PREFIX missing: $PREFIX)"
  [ -n "${PREFIX}" ]
}

is_empty_dir() { # $1 dir — true if missing or empty
  [ ! -d "$1" ] || [ -z "$(ls -A "$1" 2>/dev/null)" ]
}

run_timed() {
  local t0 t1
  t0="$(date +%s)"
  "$@"
  t1="$(date +%s)"
  echo "[timing] $* : $(( t1 - t0 ))s"
}

# ensure_logdir — create log dir + touch bootstrap log
ensure_logdir() {
  mkdir -p "$LOGDIR"
  [ -f "$BOOTSTRAP_LOG" ] || : > "$BOOTSTRAP_LOG"
}

# log_both <msg> — log to console and bootstrap log
log_both() {
  log "$*"
  ensure_logdir
  echo "[$(NOW)] $*" >> "$BOOTSTRAP_LOG"
}
