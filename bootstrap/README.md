# Hermes / Termux Bootstrap

Reproducible, version-controlled provisioning for Hermes Agent on
Android/Termux (ARMv7, Python 3.13). Goal: a fresh device goes from Termux
installed to a working Hermes environment in **<15 minutes with zero manual
steps**, verified by an automated smoke suite.

```
bootstrap/
├── bootstrap.sh                    # single entry point (orchestrator)
├── lib/
│   ├── common.sh                   # shared helpers (backups, validation, python pick)
│   └── config_edit.py              # deterministic YAML editor (render/set/patch/validate)
├── manifests/
│   ├── termux-packages.txt         # pinned Termux package set (live capture)
│   ├── venv-lock.txt               # pinned venv packages (pip freeze, editable/local paths removed)
│   ├── hermes-pin.txt              # agent git commit + version
│   ├── config.template.yaml        # sanitized config template (NO secrets)
│   └── env.template                # env variable NAMES only
├── templates/
│   ├── hermes/hermes-launcher      # ~/bin/hermes (LD_PRELOAD etc.)
│   └── launchers/                  # ~/.local/bin gateway/profile/webui wrappers
├── scripts/
│   ├── preflight.sh                # environment checks (read-only)
│   ├── install-termux-packages.sh  # pkg install from manifest
│   ├── restore-uv-cache.sh         # offline bundle -> ~/.cache/uv
│   ├── install-hermes.sh           # pinned source + venv + offline deps + editable install
│   ├── configure-hermes.sh         # config.yaml/.env from templates + secrets injection
│   ├── apply-config-change.sh      # safe config edits (backup → validate → rollback)
│   ├── setup-launchers.sh          # ~/bin + ~/.local/bin wrappers
│   ├── setup-gateway.sh            # crontab watchdog entry
│   ├── smoke-tests.sh              # acceptance suite (15 checks)
│   ├── make-manifests.sh           # donor: regenerate pinned manifests
│   ├── make-offline-bundle.sh      # donor: bundle ~/.cache/uv (ARMv7 build inputs)
│   ├── make-state-bundle.sh        # donor: bundle skills/plugins/memories/cron/profiles
│   └── make-secrets-bundle.sh      # donor: bundle secrets JSON (config + env values)
└── docs/
    └── PROVISIONING.md             # full runbook
```

## Quickstart

Fresh device (after installing Termux from F-Droid):

```bash
# 1. get this repo onto the device (git clone / adb push / storage copy)
# 2. run the bootstrap — single command, unattended:
cd CraftedWorkflowsOS/bootstrap
./bootstrap.sh --unattended \
  --bundle  ~/storage/downloads/hermes-offline-bundle.tar.gz \
  --secrets ~/storage/downloads/hermes-secrets-bundle.json \
  --state   ~/storage/downloads/hermes-state-bundle.tar.gz
```

That runs: preflight → packages → uv-cache restore → agent+venv → config →
state restore → launchers → gateway watchdog → smoke suite.

No bundles? Run without them: provisioning still works but will need network
for deps and leave empty secret fields (see docs).

## Safety & design rules (termux-safety doctrine)

- **No `pkg upgrade`**, no cache deletion, never `rm -rf` on
  `~/.cache/*`, `~/.hermes/*` — the bootstrap never deletes anything; it only
  merges and backs up (`*.bak-<ts>`, last 5 kept).
- **Config changes always** backup → apply → validate → rollback on failure
  (`apply-config-change.sh`).
- **No secrets in this repo.** `config.template.yaml` contains shapes and env
  variable *names* only; real values come from the secrets bundle at
  configure time (never committed).
- Fresh-device mode refuses to touch an already-populated `~/.hermes`
  unless `--overwrite` is passed deliberately.
- Offline-first installs: if the uv-cache bundle is present, deps install
  with `uv pip install --offline`; network fallback requires
  `--allow-online`.

## Verification

```bash
cd CraftedWorkflowsOS/bootstrap
./scripts/smoke-tests.sh        # 15 checks, exit 0 = pass
```

See `docs/PROVISIONING.md` for the full runbook, donor preparation, and
troubleshooting.
