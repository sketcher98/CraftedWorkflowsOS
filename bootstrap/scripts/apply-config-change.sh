#!/data/data/com.termux/files/usr/bin/bash
# apply-config-change.sh — safe config change utility (doctrine: backup first,
# validate, rollback on failure). For "config changes take <15 min".
#
# Usage:
#   apply-config-change.sh validate
#   apply-config-change.sh set <dotted.path> <value>
#   apply-config-change.sh patch <patch.json>          # {"a.b.c": value, ...}
set -euo pipefail
. "$(dirname "$0")/../lib/common.sh"

EDITOR="$BOOTSTRAP_DIR/lib/config_edit.py"
CONFIG="$HERMES_HOME/config.yaml"
PY="$(pick_python)"

[ -f "$CONFIG" ] || die "no config at $CONFIG"
[ -n "$PY" ] || die "no Python with pyyaml found (venv or system python3.13)"

case "${1:-}" in
  validate)
    if yaml_valid "$CONFIG"; then
      log "config.yaml VALID"
      exit 0
    else
      die "config.yaml INVALID"
    fi
    ;;
  set)
    [ -n "${2:-}" ] && [ -n "${3:-}" ] || die "usage: apply-config-change.sh set <path> <value>"
    backup_file "$CONFIG"
    if "$PY" "$EDITOR" set --file "$CONFIG" --path "$2" --value "$3"; then
      yaml_valid "$CONFIG" || { warn "invalid after set — rolling back"; cp "$(ls -1t "$CONFIG".bak-* | head -1)" "$CONFIG"; die "rolled back"; }
      log "applied: $2 = <value>"
    else
      warn "set failed — rolling back"
      cp "$(ls -1t "$CONFIG".bak-* | head -1)" "$CONFIG"
      die "rolled back"
    fi
    ;;
  patch)
    [ -n "${2:-}" ] || die "usage: apply-config-change.sh patch <patch.json>"
    [ -f "$2" ] || die "patch file missing: $2"
    backup_file "$CONFIG"
    if "$PY" "$EDITOR" patch --file "$CONFIG" --patch "$2"; then
      yaml_valid "$CONFIG" || { warn "invalid after patch — rolling back"; cp "$(ls -1t "$CONFIG".bak-* | head -1)" "$CONFIG"; die "rolled back"; }
      log "patched from $2"
    else
      warn "patch failed — rolling back"
      cp "$(ls -1t "$CONFIG".bak-* | head -1)" "$CONFIG"
      die "rolled back"
    fi
    ;;
  *)
    die "usage: apply-config-change.sh {validate|set <path> <value>|patch <json>}"
    ;;
esac
