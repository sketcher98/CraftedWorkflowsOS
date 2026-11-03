# PROVISIONING.md — Hermes/Termux Bootstrap Runbook

## 1. Objective

Rebuild a working Hermes/Termux environment on a fresh Android device in
**<15 minutes, zero manual steps**, from version-controlled scripts + pinned
manifests, and prove it with an automated smoke suite.

Target environment (device): Infinix X6511B / Android 11 / Termux
0.119.0-beta.3 / ARMv7 32-bit / Python 3.13.13 (Hermes venv) / Hermes Agent
0.21.3 (pin `966959da0896efbce7c741328bcb5f90af22b6e8`).

## 2. Layout

See `README.md` in this directory for the tree.

## 3. Donor preparation (run on the CURRENT working device)

Regenerate the pinned manifests so they reflect reality:

```bash
cd CraftedWorkflowsOS/bootstrap
./scripts/make-manifests.sh      # rewrites manifests/ from live system (no secrets)
```

Produce the three bundles into `~/storage/downloads/`:

```bash
./scripts/make-offline-bundle.sh   # hermes-offline-bundle.tar.gz (~76 MB uv cache — ARMv7 build inputs)
./scripts/make-state-bundle.sh     # hermes-state-bundle.tar.gz (skills/plugins/memories/cron/profiles)
./scripts/make-secrets-bundle.sh   # hermes-secrets-bundle.json (plaintext secrets! chmod 600, store offline)
```

Notes:

- The uv cache (`~/.cache/uv`) contains irreplaceable ARMv7 sdist/wheel build
  inputs (maturin, pydantic-core, jiter, ...). Without the bundle, a fresh
  device must build from source online — slow/fragile. Always carry it.
- The secrets bundle is plaintext. Keep it offline (internal storage /
  encrypted transfer). It is NOT part of the repo.
- State bundle excludes secrets by design (skills, plugins, memories, cron,
  profiles only).

## 4. Fresh-device flow

### Prerequisites
- Termux installed (F-Droid). Nothing else.
- Repo + bundles available on the device
  (`~/storage/downloads/hermes-{offline-bundle,state-bundle,secrets-bundle}.*`).

### Single command
```bash
cd CraftedWorkflowsOS/bootstrap
./bootstrap.sh --unattended
```

`--unattended` implies a FRESH, empty `~/.hermes`; the preflight step refuses
to continue if `~/.hermes` is already populated (add `--overwrite` only after
human review). If stdin is not a terminal (e.g., automated runner), the
bootstrap treats itself as unattended.

### Step order (bootstrap.sh)
1. `preflight` — Termux, arch, uid, core commands, storage symlink, disk
   ≥2048 MB, fresh-home gate, bundle presence.
2. `packages` — `pkg update`, tur-repo, `pkg install -y` from
   `manifests/termux-packages.txt` (286 pkgs; never `pkg upgrade`).
3. `cache` — restore `~/.cache/uv` from the offline bundle (merge; never
   delete existing).
4. `agent` — source at pinned commit (git shallow fetch → tarball fallback),
   `python3.13 -m venv`, offline `uv pip install` of `venv-lock.txt`
   (119 pkgs), editable `pip install -e . -c constraints-termux.txt
   --no-build-isolation` with `ANDROID_API_LEVEL`, then `hermes --version`
   check.
5. `config` — render `config.yaml` from the sanitized template; inject secret
   fields from the secrets bundle; scaffold `.env` from names and merge env
   values (fill empty keys, never clobber non-empty); validate YAML; rollback
   on failure. Backups of anything replaced.
6. `state` — extract state bundle on top of `~/.hermes` (if provided).
7. `launchers` — install `~/bin/hermes`, `~/.local/bin/*` wrappers from
   templates, `~/bin` symlinks, PATH persistence.
8. `gateway` — write the crontab watchdog entry (`*/5 * * * * .../gateway-watchdog`).
9. `smoke` — run the acceptance suite (15 checks).

### Flags
| Flag | Meaning |
|------|---------|
| `--dry-run` | plan only, execute nothing |
| `--unattended` | no prompts; require empty `~/.hermes` (unless `--overwrite`) |
| `--overwrite` | proceed even if `~/.hermes` populated |
| `--only <step>` | run a single step (`preflight|packages|cache|agent|config|launchers|gateway|state|smoke`) |
| `--bundle <path>` | offline bundle (default `~/storage/downloads/hermes-offline-bundle.tar.gz`) |
| `--secrets <path>` | secrets bundle (default `~/storage/downloads/hermes-secrets-bundle.json`) |
| `--state <path>` | state bundle (default `~/storage/downloads/hermes-state-bundle.tar.gz`) |
| `--allow-online` | allow network pip fallback if offline install fails |
| `--start-gateway` | start the gateway after setup (not part of acceptance) |

## 5. Smoke suite (`scripts/smoke-tests.sh`)

Read-only; exit 0 only when all HARD gates pass (warnings allowed).

| ID | Check | Hard |
|----|-------|------|
| S01 | Termux prefix + pkg | ✔ |
| S02 | arch armv7l | warn |
| S03 | storage symlink | warn |
| S04 | venv python 3.13.x | ✔ |
| S05 | venv hermes binary | ✔ |
| S06 | `hermes --version` matches pin | ✔ |
| S07 | config.yaml parses | ✔ |
| S08 | no `{{` placeholder tokens in config | ✔ (warn if `ALLOW_UNRESOLVED_SECRETS=1`) |
| S09 | auto-load skills present (provider-registry, craftedworkflows-coo) | warn |
| S10 | cron/jobs.json parses | ✔ |
| S11 | crontab watchdog entry | ✔ |
| S12 | launchers executable | ✔ |
| S13 | libpython3.13.so (LD_PRELOAD target) | ✔ |
| S14 | disk headroom ≥1024 MB | warn |
| S15 | `.env` present | warn |

On a fresh install WITHOUT the state bundle, S09/S10 show warnings — expected.
S08 fails only if secret injection was requested but placeholders remain.

## 6. Day-to-day config changes

```bash
./scripts/apply-config-change.sh validate
./scripts/apply-config-change.sh set model.default mercury-3
./scripts/apply-config-change.sh patch /path/changes.json   # {"a.b.c": value, ...}
```

Behavior: backup `config.yaml` (`.bak-<ts>`, last 5 kept) → apply → validate →
rollback on failure. Prefer this over hand-editing; it encodes the termux-
safety config-edits doctrine.

## 7. Troubleshooting

| Symptom | Cause / fix |
|---------|-------------|
| preflight P6 disk low | needs ≥2 GB free on `/data` |
| `uv pip install` offline fails | bundle missing/stale → regenerate with `make-offline-bundle.sh` on donor, or rerun with `--allow-online` after review |
| editable install fails on build | `maturin` missing in venv (should come from lock), Rust toolchain not installed, or `ANDROID_API_LEVEL` unset — re-run step 4 after `pkg install rust maturin` equivalents |
| `hermes --version` ≠ pin | source checkout mismatch → check `manifests/hermes-pin.txt` and re-run agent step |
| smoke S08 fails | secrets not injected → run configure with `--secrets` bundle |
| crontab watchdog missing | run `setup-gateway.sh` (or full bootstrap) |
| suspicious behavior after restore | ambient `HERMES_HOME`/`BOOTSTRAP_HERMES_HOME` exported from a Hermes profile session overrides the canonical root — `unset BOOTSTRAP_HERMES_HOME` and re-run. The bootstrap always targets `~/.hermes` unless BOOTSTRAP_HERMES_HOME is set explicitly. |
| `~/.hermes` populated & bootstrap refuses | intentional fresh-device guard — add `--overwrite` only after deliberate review |

## 8. Upgrade path

Hermes upgrades are intentionally NOT part of bootstrap (doctrine: review +
approval first). When upgrading on a working device, follow the verified path
in `hermes-termux-deployment` skill `references/upgrade-hermes-termux.md`
(release tarball + constrained pip install), then regenerate manifests with
`make-manifests.sh`.

## 9. Acceptance checklist (fresh device)

1. `./bootstrap.sh --unattended ...` exits 0.
2. Final smoke run reports `PASS=15 WARN=0 FAIL=0`.
3. Full run <15 minutes (packages step dominates; offline bundle present).
4. `~/bin/hermes --version` → `Hermes Agent v0.21.3 ... upstream 966959da`.
5. Crontab contains the gateway-watchdog entry.
6. No secrets appear in the repo (`grep -r '{{SECRET}}\|sk-' bootstrap/` empty).
