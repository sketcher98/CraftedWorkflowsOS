# Router Status (Durable Snapshot)

> Generated: 2026-10-31
> Source of truth for current implementation state. If a hash or value in
> this file disagrees with the live system, the live system wins — record
> the drift here.

---

## LIVE FACTS (verified 2026-10-31)

| Item | Value |
|------|-------|
| Router PID | 31623 |
| Listener | 127.0.0.1:8643 |
| Binary SHA-256 | `9bcf94bbf309…` — catalog-meta build (swap 23:5x) |
| Binary source commit | `eb0a492` (catalog aggregation + Meta field) |
| Build stamp | `eb0a492` in telemetry |
| Repo main.go | matches running binary (aggregation verified live: 1 event/provider/cycle; skipped_keys in meta, not error) |
| Repo config.json SHA-256 | `837bcacc…` — `catalog_refresh_seconds` now 1800 (was 300) |
| Providers | 6: nvidia, zydit-v1/v2/v3/v4, mercury |
| Preferences | 3: `minimaxai/minimax-m3.1-flash`, `nvidia/nemotron-3-ultra-550b-a55b`, `z-ai/glm-5.3-flash` |
| Retry | `failure_limit: 2`, `cooldown_seconds: 15`, `max_cooldown_seconds: 120`, `all_failed_wait_seconds: 10` |
| Timeouts | `route_timeout_seconds: 120`, `stream_idle_timeout_seconds: 300`, `first_event_timeout_seconds: 180` |
| Start | `bash ~/CraftedWorkflowsOS/router/scripts/startr` |

## ROUTER PRODUCTION READINESS (audit concurred 2026-10-31)

- Core routing proven live: request lifecycle, headers, client-cancel
  isolation (D-010 holds: 222 aborts, 0 provider cool-downs), health,
  metrics, model list.
- **Multi-key**: nvidia has 3 distinct keys (distinct hashes). Zydit
  keys are byte-identical triple-copies of one key — counts as one
  effective key.
- **Concurrency suite: 3/6** — C04 expectation logic broken (nemotron
  3/3 success mis-read), C05 0/6, C06 0/4. Multi-key-under-load claim
  NOT proven. (P4 open.)
- **HE-04** (post-commit stream failure): router behavior verified via
  mock `stream_commit_then_drop` (test-env), BUT `he04_test.py`
  filtered on `"he04"` in request_id — router IDs are
  `req-<unixnano>-<pid>`, so the filter never matched; the script
  printed and exited, asserting nothing. Marked **PARTIAL — script is
  being made to assert** (P3).
- **Prometheus key gauges**: FIXED (counts, not last-write) — swapped
  into production 2026-10-31. Live check: nvidia=3 (3 distinct keys),
  zydit-v1..v4=1.
- **Catalog refresh degraded on mobile data**: 517 `catalog_skip`
  events (100% DNS/timeout). Router keeps last-good catalog; routing
  works, catalog-driven gating is blind. Root cause: mobile DNS, not
  code.
- **Latency margin thin**: 24h first-chunk p50 82.7s / p90 117.7s /
  max 118.8s vs 120s route timeout. One slow cold start blows the
  pre-header budget. Currently holding.
- **Disk at 96% (1.1GB free)** — phone-wide fuse. Cleanup = P5.

## HERMES PROFILE DEFAULTS (P0 fixed 2026-10-31)

All 7 profiles (commercial, creative, delivery, engineering, finance,
marketing, operations) now point at `nvidia/nemotron-3-ultra-550b-a55b`
via the router. Backups: `config.yaml.bak-P0-20261031-212318` per
profile. Previously they set bare `nemotron`, which the router 400'd
("model 'nemotron' not configured in router preferences").

---

## Historical Artifacts (rollback/verification reference)

| Artifact | SHA256 | Notes |
|----------|--------|-------|
| Production binary before 2026-10-31 swap | `30e5e5af…` | superseded by P2 gauge-fix build when swapped |
| Pre-Phase1B rollback binary | `b68830cd72a6aec2…` | single-key, pre-inner-retry |
| Pre-Phase1B rollback main.go | `b98e842d35f87830…` | no inner retry loop |
| Pre-Phase1B rollback config.json | `1eb965c8d1b8af76…` | same as Phase1B config |
| Historical rollback binary (single-key) | `af96085f7a5b4d6e…` | pre-multi-key |
| Historical rollback main.go | `488225df6e0d8110…` | pre-multi-key |
| Historical rollback config.json | `9511cf7419671e45…` | no key_envs |
| Pre-SSE-preflight live binary | `85ee6bb3c280ea86…` | spec §40 era |
| startr | `83634386c78b9819…` | launcher |
| stopr | `da860a3a562b8c6d…` | launcher |

---

## SESSION CONTINUATION RULE

Before any router modification:

1. Verify binary SHA256
2. Verify main.go SHA256
3. Verify config.json SHA256
4. Verify PID + /health
5. Compare against the LIVE FACTS table above
6. Record drift here
7. Create rollback snapshot
8. Only then modify production

Do not modify router implementation, binary, config, tests, or Hermes
without the CEO-approved plan for that change.
