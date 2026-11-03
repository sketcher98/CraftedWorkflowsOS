#!/data/data/com.termux/files/usr/bin/bash
# make-secrets-bundle.sh — build the secrets bundle JSON for restore on a
# fresh device. Contains REAL credentials in plaintext inside the JSON (chmod
# 600). Store offline only; do NOT commit or transfer over untrusted links.
set -euo pipefail
. "$(dirname "$0")/../lib/common.sh"

OUT="${BOOTSTRAP_SECRETS_OUT:-$HOME/storage/downloads/hermes-secrets-bundle.json}"
mkdir -p "$HOME/storage/downloads"

"$VENV_DIR/bin/python" - "$OUT" <<'PY'
import json, os, sys, yaml

OUT = sys.argv[1]
home = os.path.expanduser("~")
cfg = yaml.safe_load(open(os.path.join(home, ".hermes/config.yaml")))

def parse_path(path):
    parts = []
    for tok in path.split("."):
        if "[" in tok:
            head, rest = tok.split("[", 1)
            if head: parts.append(head)
            parts.extend(int(i) for i in rest.rstrip("]").split("]["))
        else:
            parts.append(tok)
    return parts

def get_path(node, parts):
    cur = node
    for p in parts:
        cur = cur[p]
    return cur

# Fields that are literal secrets (non env-var references)
SECRET_PATHS = [
    "model.api_key",
    "mcp_servers.notion.auth",
    "mcp_servers.composio.headers.Authorization",
    "mcp_servers.zernio.headers.Authorization",
    "mcp_servers.twelve-data.auth",
    "mcp_servers.arcade-x.auth",
]

config_sec = {}
for p in SECRET_PATHS:
    try:
        v = get_path(cfg, parse_path(p))
    except (KeyError, TypeError, IndexError):
        continue
    if isinstance(v, str) and v:
        config_sec[p] = v

env_sec = {}
envf = os.path.join(home, ".hermes/.env")
if os.path.exists(envf):
    with open(envf) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                if v:
                    env_sec[k] = v

bundle = {"config": config_sec, "env": env_sec}
with open(OUT, "w") as f:
    json.dump(bundle, f, indent=2)
os.chmod(OUT, 0o600)
print(f"secrets bundle: {OUT}")
print(f"  config secret fields: {len(config_sec)}")
print(f"  env values: {len(env_sec)}")
print("WARNING: plaintext secrets — store offline, never commit")
PY

log "make-secrets-bundle.sh complete"
