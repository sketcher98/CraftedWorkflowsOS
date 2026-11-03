#!/data/data/com.termux/files/usr/bin/bash
# smoke-tests.sh — acceptance smoke suite for a provisioned Hermes/Termux
# environment. Read-only; safe to run anytime. Exit 0 = hard gates pass
# (warnings allowed).
set -euo pipefail
. "$(dirname "$0")/../lib/common.sh"

ALLOW_UNRESOLVED_SECRETS="${ALLOW_UNRESOLVED_SECRETS:-0}"
PIN_FILE="$MANIFESTS/hermes-pin.txt"
PIN_VERSION="$(sed -n 's/^version=//p' "$PIN_FILE" 2>/dev/null || echo unknown)"

PASS=0; WARN=0; FAIL=0
declare -a FAILED=()

ok()   { PASS=$((PASS+1)); printf '  PASS  %s\n' "$1"; }
w()    { WARN=$((WARN+1)); printf '  WARN  %s\n' "$1"; }
f()    { FAIL=$((FAIL+1)); FAILED+=("$1"); printf '  FAIL  %s\n' "$1"; }

t0="$(date +%s)"
log "Smoke suite starting (pin version=$PIN_VERSION)"

# S01 Termux
if [ -d "$PREFIX" ] && [ -x "$PREFIX/bin/pkg" ]; then ok "S01 termux present"; else f "S01 termux missing"; fi

# S02 arch
case "$(uname -m)" in
  armv7l|armv8l) ok "S02 arch $(uname -m)";;
  *) w "S02 arch $(uname -m) (expected armv7l)";;
esac

# S03 storage symlink
if [ -L "$HOME/storage/shared" ]; then ok "S03 storage symlink"; else w "S03 storage symlink missing"; fi

# S04 venv python
if [ -x "$VENV_DIR/bin/python" ]; then
  PYV="$("$VENV_DIR/bin/python" -V 2>&1 | awk '{print $2}')"
  case "$PYV" in
    3.13.*) ok "S04 venv python $PYV";;
    *) f "S04 venv python $PYV (expected 3.13.x)";;
  esac
else
  f "S04 venv python missing"
fi

# S05 venv hermes binary
if [ -x "$VENV_DIR/bin/hermes" ]; then ok "S05 hermes binary"; else f "S05 hermes binary missing"; fi

# S06 hermes --version
if [ -x "$VENV_DIR/bin/hermes" ]; then
  VER="$("$VENV_DIR/bin/hermes" --version 2>&1 | head -1 || true)"
  if [ "$PIN_VERSION" != "unknown" ] && printf '%s' "$VER" | grep -q "$PIN_VERSION"; then
    ok "S06 hermes --version $VER"
  else
    f "S06 hermes --version='$VER' (expected $PIN_VERSION)"
  fi
fi

# S07 config.yaml valid
CFG="$HERMES_HOME/config.yaml"
if [ -f "$CFG" ]; then
  if yaml_valid "$CFG" "$VENV_DIR/bin/python"; then ok "S07 config.yaml parses"; else f "S07 config.yaml INVALID"; fi
else
  f "S07 config.yaml missing"
fi

# S08 no leftover placeholders
if [ -f "$CFG" ]; then
  if grep -q '{{' "$CFG" 2>/dev/null; then
    if [ "$ALLOW_UNRESOLVED_SECRETS" = "1" ]; then w "S08 placeholder tokens remain in config (allowed by flag)"; else f "S08 placeholder tokens remain in config (inject secrets or set ALLOW_UNRESOLVED_SECRETS=1)"; fi
  else
    ok "S08 no placeholder tokens in config"
  fi
fi

# S09 auto-load skills present for at least one profile
SKILL_FOUND=0
if [ -d "$HERMES_HOME/profiles" ]; then
  for p in "$HERMES_HOME/profiles"/*/skills; do
    [ -d "$p" ] || continue
    if [ -d "$p/integrations/provider-registry" ] && [ -d "$p/autonomous-ai-agents/craftedworkflows-coo" ]; then
      SKILL_FOUND=1
    fi
  done
fi
if [ "$SKILL_FOUND" = "1" ]; then ok "S09 auto-load skills present"; else w "S09 auto-load skills not found under profiles (restore from state bundle)"; fi

# S10 cron jobs parse (if present)
CRONPY="$(pick_python)"
if [ -f "$HERMES_HOME/cron/jobs.json" ]; then
  if "$CRONPY" -c 'import json,sys; json.load(open(sys.argv[1]))' "$HERMES_HOME/cron/jobs.json" 2>/dev/null; then
    ok "S10 cron/jobs.json parses"
  else
    f "S10 cron/jobs.json INVALID"
  fi
else
  w "S10 cron/jobs.json absent (fine on fresh install before state restore)"
fi

# S11 crontab watchdog
if crontab -l 2>/dev/null | grep -Fq "gateway-watchdog"; then ok "S11 crontab watchdog entry"; else f "S11 crontab watchdog entry missing"; fi

# S12 launchers executable
if [ -x "$HOME/bin/hermes" ] && [ -x "$HOME/.local/bin/gateway-start" ]; then ok "S12 launchers executable"; else f "S12 launchers not executable"; fi

# S13 LD_PRELOAD target
if [ -f "$PREFIX/lib/libpython3.13.so" ]; then ok "S13 libpython3.13.so"; else f "S13 libpython3.13.so missing"; fi

# S14 disk headroom
FREE_MB=$(( $(df -P /data 2>/dev/null | awk 'NR==2 {print $4}') / 1024 ))
if [ "$FREE_MB" -ge 1024 ]; then ok "S14 disk ${FREE_MB}MB free"; else w "S14 disk low (${FREE_MB}MB)"; fi

# S15 .env exists
if [ -f "$HERMES_HOME/.env" ]; then ok "S15 .env present"; else w "S15 .env missing"; fi

elapsed=$(( $(date +%s) - t0 ))
echo
log "RESULTS: PASS=$PASS WARN=$WARN FAIL=$FAIL (${elapsed}s)"
if [ "$FAIL" -gt 0 ]; then
  printf 'FAILED: %s\n' "${FAILED[@]}"
  exit 1
fi
log "SMOKE PASSED (warnings above are informational)"
exit 0
