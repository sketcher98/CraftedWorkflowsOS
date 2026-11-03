#!/data/data/com.termux/files/usr/bin/bash
# bootstrap.sh — Hermes/Termux provisioning orchestrator.
#
# Fresh-device acceptance (zero manual steps):
#   ./bootstrap.sh --unattended --bundle <offline-bundle.tar.gz> \
#                  --secrets <secrets-bundle.json> --state <state-bundle.tar.gz>
#
# Then verify with:
#   ./bootstrap.sh --smoke
#
# Subcommands / flags:
#   --dry-run            plan only, execute nothing
#   --unattended         no prompts; refuse to touch a populated ~/.hermes
#   --overwrite          allow proceeding even if ~/.hermes is populated
#   --only <step>        run one step: preflight|packages|cache|agent|venv|
#                        config|launchers|gateway|state|smoke
#   --bundle <path>      offline uv-cache bundle (default ~/storage/downloads/hermes-offline-bundle.tar.gz)
#   --secrets <path>     secrets bundle JSON (default ~/storage/downloads/hermes-secrets-bundle.json)
#   --state <path>       state bundle tar.gz (optional)
#   --allow-online       allow network pip fallback if offline install fails
#   --start-gateway      start gateway after setup (not part of acceptance)
#   --help
set -euo pipefail
. "$(dirname "$0")/lib/common.sh"

DRY_RUN=0
UNATTENDED=0
OVERWRITE=0
ALLOW_ONLINE=0
START_GATEWAY=0
ONLY=""
HELP=0

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=1 ;;
    --unattended) UNATTENDED=1 ;;
    --overwrite) OVERWRITE=1 ;;
    --allow-online) ALLOW_ONLINE=1 ;;
    --start-gateway) START_GATEWAY=1 ;;
    --only) shift; ONLY="$1" ;;
    --bundle) shift; export BOOTSTRAP_BUNDLE="$1" ;;
    --secrets) shift; export BOOTSTRAP_SECRETS="$1" ;;
    --state) shift; BOOTSTRAP_STATE="$1" ;;
    --help|-h) HELP=1 ;;
    *) die "unknown arg: $1 (see --help)" ;;
  esac
  shift
done

if [ "$HELP" = "1" ]; then
  sed -n '2,40p' "$0"
  exit 0
fi

# ------------------------------------------------------------------- defaults
if [ "$UNATTENDED" = "0" ] && [ ! -t 0 ]; then
  warn "non-interactive stdin and no --unattended — treating as unattended"
  UNATTENDED=1
fi
export UNATTENDED ALLOW_ONLINE

BOOTSTRAP_STATE="${BOOTSTRAP_STATE:-$HOME/storage/downloads/hermes-state-bundle.tar.gz}"
export BOOTSTRAP_BUNDLE="${BOOTSTRAP_BUNDLE:-$HOME/storage/downloads/hermes-offline-bundle.tar.gz}"
export BOOTSTRAP_SECRETS="${BOOTSTRAP_SECRETS:-$HOME/storage/downloads/hermes-secrets-bundle.json}"

STEP_RUN() {
  local name="$1"; shift
  if [ -n "$ONLY" ] && [ "$ONLY" != "$name" ]; then return 0; fi
  log "===== step: $name"
  if [ "$DRY_RUN" = "1" ]; then
    info "(dry-run) would run: $*"
    return 0
  fi
  "$@"
}

# ---------------------------------------------------------------- orchestrate
log "Hermes/Termux bootstrap — start ($(date))"
log "  mode: $([ "$DRY_RUN" = 1 ] && echo DRY-RUN || echo EXECUTE) $([ "$UNATTENDED" = 1 ] && echo UNATTENDED || echo interactive)"

step_preflight() {
  local extra=""
  [ "$UNATTENDED" = "1" ] && [ "$OVERWRITE" = "0" ] && extra="--require-empty-home"
  STEP_RUN preflight "$SCRIPTS/preflight.sh" $extra
}

step_packages() { STEP_RUN packages "$SCRIPTS/install-termux-packages.sh"; }
step_cache()    { STEP_RUN cache "$SCRIPTS/restore-uv-cache.sh"; }
step_agent()    { STEP_RUN agent "$SCRIPTS/install-hermes.sh"; }
step_config()   { STEP_RUN config "$SCRIPTS/configure-hermes.sh"; }
step_launchers(){ STEP_RUN launchers "$SCRIPTS/setup-launchers.sh"; }
step_gateway()  {
  local extra=""
  [ "$START_GATEWAY" = "1" ] && extra="--start"
  STEP_RUN gateway "$SCRIPTS/setup-gateway.sh" $extra
}
step_state() {
  if [ ! -f "$BOOTSTRAP_STATE" ]; then
    warn "--state bundle not found: $BOOTSTRAP_STATE (skipping state restore)"
    return 0
  fi
  STEP_RUN state sh -c "mkdir -p '$HERMES_HOME' && tar -xzf '$BOOTSTRAP_STATE' -C '$HERMES_HOME'"
}
step_smoke() {
  STEP_RUN smoke "$SCRIPTS/smoke-tests.sh"
}

step_preflight
step_packages
step_cache
step_agent
step_config
[ "$DRY_RUN" = "0" ] && step_state
step_launchers
step_gateway
step_smoke

log "bootstrap finished $([ "$DRY_RUN" = 1 ] && echo '(dry-run — nothing executed)' || echo 'successfully')"
