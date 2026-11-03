#!/data/data/com.termux/files/usr/bin/bash
# configure-hermes.sh — install config.yaml (from sanitized template) and .env
# (names-only template) into ~/.hermes, injecting secrets from an optional
# secrets bundle. Backs up existing files first; validates; rolls back on
# failure.
set -euo pipefail
. "$(dirname "$0")/../lib/common.sh"

SECRETS_BUNDLE="${BOOTSTRAP_SECRETS:-$HOME/storage/downloads/hermes-secrets-bundle.json}"

if [ -f "$SECRETS_BUNDLE" ]; then
  info "secrets bundle found: $SECRETS_BUNDLE"
else
  warn "no secrets bundle at $SECRETS_BUNDLE — config will be written with EMPTY secret fields"
  SECRETS_BUNDLE=""
fi

mkdir -p "$HERMES_HOME"

# ------------------------------------------------------------------- backups
backup_file "$HERMES_HOME/config.yaml"
backup_file "$HERMES_HOME/.env"
backup_file "$HERMES_HOME/auth.json"

# ---------------------------------------------------------------- config.yaml
TEMPLATE="$MANIFESTS/config.template.yaml"
[ -f "$TEMPLATE" ] || die "config template missing: $TEMPLATE"

TMP_RENDER="$HOME/tmp/bootstrap-config-render.yaml"
mkdir -p "$HOME/tmp"

PY="$(pick_python)"
[ -n "$PY" ] || die "no Python with pyyaml found (venv or system python3.13)"

if [ -n "$SECRETS_BUNDLE" ]; then
  "$PY" "$BOOTSTRAP_DIR/lib/config_edit.py" render \
    --template "$TEMPLATE" --secrets "$SECRETS_BUNDLE" --out "$TMP_RENDER"
else
  "$PY" "$BOOTSTRAP_DIR/lib/config_edit.py" render \
    --template "$TEMPLATE" --out "$TMP_RENDER"
fi

if ! yaml_valid "$TMP_RENDER"; then
  warn "rendered config invalid — restoring backup, aborting"
  [ -f "$HERMES_HOME/config.yaml.bak-"* ] && cp "$(ls -1t "$HERMES_HOME/config.yaml".bak-* | head -1)" "$HERMES_HOME/config.yaml" || true
  die "config render invalid"
fi

cp "$TMP_RENDER" "$HERMES_HOME/config.yaml"
log "config.yaml written ($(wc -c < "$HERMES_HOME/config.yaml") bytes)"

# -------------------------------------------------------------- .env (names)
if [ ! -f "$HERMES_HOME/.env" ]; then
  {
    echo "# .env — variable names from bootstrap manifest; values injected from secrets bundle"
    while read -r k; do
      [ -n "$k" ] && echo "${k}="
    done < "$MANIFESTS/env.template"
  } > "$HERMES_HOME/.env"
  log ".env scaffolded from template ($(wc -l < "$HERMES_HOME/.env") keys)"
fi

if [ -n "$SECRETS_BUNDLE" ]; then
  # merge env values from secrets bundle: fill empty keys, append missing,
  # never clobber existing non-empty values
  ENV_SEC="$HOME/tmp/bootstrap-env-secrets.tmp"
  "$PY" - "$SECRETS_BUNDLE" "$ENV_SEC" <<'PY'
import json, sys
secrets = json.load(open(sys.argv[1]))
env = secrets.get("env") or {}
with open(sys.argv[2], "w") as f:
    for k, v in env.items():
        f.write(f"{k}={v}\n")
PY
  "$PY" - "$ENV_SEC" "$HERMES_HOME/.env" <<'PY'
import sys
pairs = []
for line in open(sys.argv[1]):
    line = line.rstrip("\n")
    if "=" in line:
        k, _, v = line.partition("=")
        pairs.append((k, v))
path = sys.argv[2]
lines = open(path).read().splitlines()
out, seen = [], set()
for line in lines:
    if "=" in line:
        k, _, v = line.partition("=")
        seen.add(k)
        for nk, nv in pairs:
            if nk == k and not v:
                v = nv
                print(f"env key filled: {k}")
        out.append(f"{k}={v}")
    else:
        out.append(line)
for k, v in pairs:
    if k not in seen:
        out.append(f"{k}={v}")
        print(f"env key added: {k}")
open(path, "w").write("\n".join(out) + "\n")
PY
  rm -f "$ENV_SEC"
fi

# ------------------------------------------------------------- runtime dirs
mkdir -p "$HERMES_HOME/logs" "$HERMES_HOME/.runtime"
chmod 700 "$HERMES_HOME/.env" "$HERMES_HOME/config.yaml" 2>/dev/null || true

log "configure-hermes.sh complete"
