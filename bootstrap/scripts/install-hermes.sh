#!/data/data/com.termux/files/usr/bin/bash
# install-hermes.sh — get Hermes agent source at the pinned commit, create the
# 3.13 venv, install pinned deps (offline-first from restored uv cache), then
# editable-install the agent with the Termux constraints file.
set -euo pipefail
. "$(dirname "$0")/../lib/common.sh"

ALLOW_ONLINE="${ALLOW_ONLINE:-0}"
PIN_FILE="$MANIFESTS/hermes-pin.txt"
[ -f "$PIN_FILE" ] || die "pin manifest missing: $PIN_FILE"

# shellcheck disable=SC1090
PIN_COMMIT="$(sed -n 's/^commit=//p' "$PIN_FILE")"
PIN_VERSION="$(sed -n 's/^version=//p' "$PIN_FILE")"
[ -n "$PIN_COMMIT" ] && [ -n "$PIN_VERSION" ] || die "bad pin file: $PIN_FILE"

need_cmd python3.13
need_cmd git

# ---------------------------------------------------------------- agent source
if [ -d "$AGENT_DIR/.git" ]; then
  info "agent repo present — verifying pin $PIN_COMMIT"
  CURRENT="$(git -C "$AGENT_DIR" rev-parse HEAD 2>/dev/null || true)"
  if [ "$CURRENT" != "$PIN_COMMIT" ]; then
    warn "agent HEAD ($CURRENT) != pin ($PIN_COMMIT) — checking out pinned commit"
    git -C "$AGENT_DIR" fetch --depth 1 origin "$PIN_COMMIT" || die "fetch $PIN_COMMIT failed"
    git -C "$AGENT_DIR" checkout FETCH_HEAD
  fi
else
  log "cloning hermes-agent at $PIN_COMMIT ..."
  mkdir -p "$HERMES_HOME" "$AGENT_DIR"
  git init -q "$AGENT_DIR"
  git -C "$AGENT_DIR" remote add origin https://github.com/NousResearch/hermes-agent.git
  if ! git -C "$AGENT_DIR" fetch --depth 1 origin "$PIN_COMMIT"; then
    warn "git fetch failed — falling back to release tarball"
    TMP_TGZ="$HOME/tmp/hermes-agent-$PIN_COMMIT.tar.gz"
    mkdir -p "$HOME/tmp"
    curl -fsSL -o "$TMP_TGZ" "https://github.com/NousResearch/hermes-agent/archive/${PIN_COMMIT}.tar.gz"
    tar -xzf "$TMP_TGZ" --strip-components=1 -C "$AGENT_DIR"
  else
    git -C "$AGENT_DIR" checkout -q FETCH_HEAD
  fi
fi

# ------------------------------------------------------------------------ venv
if [ ! -x "$VENV_DIR/bin/python" ]; then
  log "creating venv (python3.13) at $VENV_DIR ..."
  python3.13 -m venv "$VENV_DIR"
fi
"$VENV_DIR/bin/python" -V

# ------------------------------------------------------------------ pinned deps
LOCK="$MANIFESTS/venv-lock.txt"
[ -f "$LOCK" ] || die "venv lock missing: $LOCK"

need_cmd uv
OFFLINE=()
if [ -d "$HOME/.cache/uv" ] && [ -n "$(ls -A "$HOME/.cache/uv" 2>/dev/null)" ]; then
  OFFLINE=(--offline)
  info "uv cache present — installing pinned deps OFFLINE"
else
  warn "no uv cache — will need network for deps (ARMv7 may require source builds)"
fi

if ! run_timed uv pip install --python "$VENV_DIR/bin/python" "${OFFLINE[@]}" \
     --requirement "$LOCK"; then
  if [ "${#OFFLINE[@]}" -gt 0 ] && [ "$ALLOW_ONLINE" = "1" ]; then
    warn "offline install failed — retrying online (--allow-online)"
    run_timed uv pip install --python "$VENV_DIR/bin/python" --requirement "$LOCK"
  else
    die "pip install failed. If the offline bundle was not restored, regenerate it on the donor device (scripts/make-offline-bundle.sh) or rerun with ALLOW_ONLINE=1 after review."
  fi
fi

# ------------------------------------------------- editable install of the agent
log "editable install of hermes-agent (constraints-termux.txt) ..."
export ANDROID_API_LEVEL="$(getprop ro.build.version.sdk 2>/dev/null || echo 30)"
if ! run_timed "$VENV_DIR/bin/pip" install -e "$AGENT_DIR" \
     -c "$AGENT_DIR/constraints-termux.txt" --no-build-isolation; then
  die "editable install failed (build deps? Python maturin in venv, rust pkg, ANDROID_API_LEVEL). See docs/PROVISIONING.md troubleshooting."
fi

# ------------------------------------------------------------------ verification
log "verifying hermes CLI ..."
if ! "$VENV_DIR/bin/hermes" --version 2>&1 | grep -q "$PIN_VERSION"; then
  warn "hermes --version does not report $PIN_VERSION — check install"
else
  info "hermes --version OK ($PIN_VERSION)"
fi

log "install-hermes.sh complete"
