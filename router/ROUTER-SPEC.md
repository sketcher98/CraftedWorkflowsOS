# Router Specification (Canonical)

> This document is the canonical router specification.
> Implementation must conform to this document.
> If implementation and this document disagree, implementation is considered non-conforming until explicitly resolved in ROUTER-DECISIONS.md.

---

## 1. Mission

A local HTTP router (`127.0.0.1:8643`) that sits between Hermes and model providers. It owns **routing, fallback, multi-key rotation, failure classification, SSE contract enforcement, and recovery**. Hermes owns **credential storage/input**. Hermes core remains **READ-ONLY** — no changes to Hermes for router features.

---

## 2. Non-Negotiable Safety Rules

- **Hermes core is READ-ONLY**. No Hermes code changes for router features.
- **Router owns provider/model/key fallback**. Hermes never implements fallback logic.
- **Hermes owns credential storage/input**. Router config stores environment-variable names only — never actual credentials.
- **Production changes require evidence + rollback capability**. No speculative patching.
- **Native Android 11 / ARMv7 / low-resource constraints** govern all implementation decisions.
- **No mutex held during network I/O**.
- **No goroutine leaks / unbounded retries**.
- **1 MiB SSE preflight cap** (`1 << 20` bytes).
- **60s pre-header timeout** (ResponseHeaderTimeout).
- **60s post-header stream idle timeout** (reset on each successful read).
+ **Pre-header timeout**: `route_timeout_seconds` (config, default 60s production, 30s test)
+ **Post-header stream idle timeout**: `stream_idle_timeout_seconds` (config, default 60s fixed in code, 30s test-env)
- **Client cancellation must NOT be counted as provider instability**.
- **Same request ID preserved across router fallback chain**.
- **Router must NOT manufacture inbound requests**.
- **All-provider exhaustion must be bounded and observable** (503 + Retry-After, no aggressive spin).

---

## 3. Runtime Environment

- **Host**: Android 11, Termux, ARMv7 (32-bit)
- **Python**: 3.13.x only (3.14 breaks Hermes)
- **Memory**: Constrained (~2GB available)
- **Network**: Mobile data, variable latency
- **Process model**: Single router process, background goroutines for catalog refresh + hard-failure recovery
- **Binary**: `~/CraftedWorkflowsOS/router/router` (~10MB production)
- **Launcher**: `~/bin/startr` (lock-guarded, health-verified, production-binary-verified)

---

## 4. Architecture

```
Hermes (READ-ONLY core)
    │
    ▼ HTTP POST /v1/chat/completions
localhost:8643 (router)
    │
    ├──► Provider A (nvidia) — key_env: NVIDIA_API_KEY
    ├──► Provider B (zydit-v1) — key_env: HERMES_CUSTOM_API_ZYDIT_IN_API_KEY
    └──► Provider C (zydit-v2) — key_env: HERMES_CUSTOM_API_ZYDIT_IN_API_KEY
```

**Router responsibilities**:
- Preference ladder resolution (model → preference → ordered routes)
- Provider health + catalog gating
- Multi-key selection + per-key cooldown/quarantine
- Failure classification (transient vs permanent)
- SSE preflight (detect upstream application errors BEFORE downstream commitment)
- Stream idle timeout (60s post-header)
- Request ID propagation
- JSONL telemetry (`router-events.jsonl`)
- `/health` + `/v1/models` endpoints
- Background catalog refresh (configurable, default 6h)
- Background hard-failure recovery probe (60s tick)

---

## 5. Preferences

**Preference** = named bucket with `context_window` and ordered `routes[]`.

**Resolution** (in `preferenceForModel`):
1. Exact match on `preference.Name` (case-insensitive)
2. Exact match on any `route.Model` (case-insensitive)
3. Unknown model → 400 Bad Request

**Fallback**: When ALL routes in a preference fail (transient or permanent), move to next preference in config order. Emit `preference_fallback` telemetry.

Current preferences (config.json):
1. `kimi-k3` (1,048,576 ctx) → nvidia → zydit-v1 → zydit-v2
2. `glm-5.3` (1,048,576 ctx) → nvidia → zydit-v1 → zydit-v2
3. `nemotron` (1,048,576 ctx) → nvidia → zydit-v1 → zydit-v2
4. `deepseek-v4.1-flash` (1,048,576 ctx) → nvidia only

---

## 6. Provider Hierarchy

**Provider** = `name`, `base_url`, `key_env` (backward compat), `key_envs[]` (authoritative).

**Key resolution** (`providerKeyEnvs`):
1. `key_env` (if non-empty)
2. Each entry in `key_envs[]`
3. Deduplicated, order preserved

**Provider selection**: Iterates routes in preference order. Skips:
- Provider not configured
- Route hard-failed (`RouteHealth.HardFailed`)
- Route unhealthy (cooldown active)
- Route unavailable (catalog known and model absent)

**Catalog**: Refreshed per-provider via `GET /models` using first working key. Unknown catalog = don't block route (real completion request remains authoritative).

---

## 7. Multi-Key Architecture

**Per-key state** (`KeyHealth`): `Failures`, `CooldownTil`, `HardFailed`.

**Key selection** (`selectProviderKey`):
- Iterates `providerKeyEnvs()` in order
- Returns first key where `!HardFailed` and `now.After(CooldownTil)`
- Returns `(keyEnv, key, true)` or `("", "", false)` if none usable

**On 401 (Unauthorized)**:
- `providerKeyFailed(p, keyEnv)` — increments key failures, applies cooldown
- **Retry same route** (inner loop in `proxy`) so `selectProviderKey` picks next healthy key
- Only after ALL keys exhausted → provider fallback

**On 2xx**: `providerKeySucceeded(p, keyEnv)` — resets key failures/cooldown/hard-fail

**Key quarantine** (401 after failure_limit): `providerKeyQuarantined` → `HardFailed=true`, `CooldownTil=zero`

---

## 8. Failure Classification

| HTTP Status | Classification | Action |
|-------------|----------------|--------|
| 429 (Too Many Requests) | Transient | `providerFailed`, retry next route |
| 5xx | Transient | `providerFailed`, retry next route |
| Network error / timeout | Transient | `providerFailed`, retry next route |
| 401 (Unauthorized) | Transient (key-level) | `providerKeyFailed`, retry same route with next key |
| 403 (Forbidden) | **Permanent** | `providerHardFailed`, provider fallback |
| 404 (Not Found) | **Permanent** | `providerHardFailed`, provider fallback |
| Other 4xx | Non-retryable | No fallback, return error |

**SSE Application Errors** (detected in preflight before commitment):
- Parsed from `data: {"error": {...}}` SSE events
- Treated as **transient** → `providerFailed`, provider fallback
- **Must occur BEFORE downstream commitment** (headers written)

---

## 9. SSE Contract

**Preflight** (before `WriteHeader`):
- Buffers up to **1 MiB** (`maxPreflightBytes = 1 << 20`)
- Scans for complete SSE events (`

`, `

`, `

`)
- Inspects each event for `data: {"error": {...}}`
- On application error: **abort, close upstream, return error** → triggers provider fallback
- On meaningful data (non-error): **commit** (write headers + buffered data)
- On cap reached without meaningful event: **commit opaque stream**

**Post-commit** (after `WriteHeader`):
- Streams bytes directly to client
- **Idle timeout**: `stream_idle_timeout_seconds` (config, default 60s production, 30s test-env) — reset on each read
- On idle timeout / read error / EOF: log `stream_error`, **NO provider fallback**
- **Client cancellation** (context done): log, close upstream, **NO provider fallback**, **NO instability marking**

**Post-Commit Stream Failure Contract (Spec #42)**:
- **Verified in test-env** with mock `stream_commit_then_drop`:
  1. Upstream sends 1 valid SSE chunk → router commits (writes headers + chunk)
  2. Upstream goes silent (no more data, no close)
  3. Router hits `stream_idle_timeout_seconds` (30s test, 300s prod)
  4. Router terminates stream: sends `data: {"error": {"message": "upstream stream stalled...", "type": "stream_error", "code": "stream_stalled"}}`
  5. Router sends `data: [DONE]`
  6. **NO provider fallback after commitment** — per spec
- Client receives: 200 OK + 1 chunk + stream_error + [DONE]
- Hermes interaction: **needs E2E verification** (Spec #36)

---

## 10. Request/Retry Semantics

- **Request ID**: `req-<unixnano>-<pid%10000>` generated once per inbound request
- **Preserved** across all fallback hops via `X-Request-ID` header
- **Routing headers set BEFORE upstream call**: `X-Router-Provider`, `X-Router-Model`, `X-Router-Preference`, `X-Router-Fallback*`, `X-Router-Preference-Fallback*`, `X-Router-Skipped-Hard-Failed`
- **No fallback after commitment** (headers written to client)
- **Timeouts**:
  - Pre-header: `ResponseHeaderTimeout` = `route_timeout_seconds` (config, default 60s production, 30s test)
  - Post-header idle: `stream_idle_timeout_seconds` (config, default 60s production, 30s test-env)
- **Client cancellation** → immediate return, no retry, no telemetry pollution

---

## 11. Recovery

**Route cooldown** (transient failures):
- `providerFailed` → exponential backoff (base `cooldown_seconds`, 2x per extra failure, capped at `max_cooldown_seconds`)
- Auto-expires when `time.Now().After(CooldownTil)`

**Hard failure** (403/404):
- `providerHardFailed` → `RouteHealth.HardFailed = true`
- **Background recovery loop** (60s tick): `tryRecoverHardFailed` makes test request with each healthy key
- On 2xx: clears `HardFailed`, `Failures=0`, `CooldownTil=zero`, emits `route_recovered` (context=background)

**Key recovery**:
- Key cooldown auto-expires
- Background catalog refresh probes keys; 2xx → `providerKeySucceeded`

**Exhaustion** (all preferences exhausted):
- `waitForRecovery` → 503 + `Retry-After` header
- Wait = earliest cooldown, or `all_failed_wait_seconds` (default 15s), or `hard_failed_wait_seconds` (test-env only) if only hard-failed routes exist
- **Non-blocking** — returns immediately, recovery continues in background

---

## 12. Concurrency

- **Per-provider mutex** (`p.mu`) protects `routes`, `keys`, `catalog`
- **Config mutex** (`cfgMu`) protects preference/provider slices
- **Event mutex** (`eventMu`) serializes JSONL writes
- **NO global lock during network I/O** — mutexes released before `client.Do`, `resp.Body.Read`
- **Per-request goroutines**: reader goroutine in `streamResponse`, timer goroutines
- **No unbounded goroutine creation** — one reader per request, timers stopped on exit

---

## 13. Resource Constraints

- **SSE preflight buffer**: 1 MiB max
- **Read buffer**: 32 KiB
- **Catalog refresh**: 15s timeout, 10s dial/TLS
- **Hard-failure probe**: 10s timeout, 5s dial/TLS
- **Route timeout**: configurable (`route_timeout_seconds`), default 60s production
- **Health endpoint**: synchronous, no external calls

---

## 14. Observability

**JSONL telemetry** (`router-events.jsonl`):
- `route_attempt` — before upstream call
- `route_result` — after attempt (success/transient/permanent/timeout/stream_error)
- `route_cooldown` — cooldown applied
- `route_quarantined` — hard failure
- `route_recovered` — background recovery success
- `preference_fallback` — preference ladder fallback

### Event Schema

All events share these base fields:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `timestamp` | string | yes | RFC3339Nano timestamp (UTC) |
| `event` | string | yes | Event type: `route_attempt`, `route_result`, `route_cooldown`, `route_quarantined`, `route_recovered`, `preference_fallback` |
| `request_id` | string | yes | Correlation ID: `req-<unixnano>-<pid%10000>` |
| `provider` | string | yes | Provider name (e.g., `nvidia`, `zydit-v1`) |
| `model` | string | yes | Model ID (e.g., `kimi-k3`, `glm-5.3`) |
| `preference` | string | yes | Preference name from config (e.g., `kimi-k3`) |
| `outcome` | string | route_result, route_cooldown, route_quarantined, route_recovered | Result: `success`, `transient`, `permanent`, `timeout`, `stream_error`, `cooldown`, `permanent`, `recovered` |
| `status` | int | route_result | HTTP status code from upstream |
| `latency_ms` | int64 | route_result | Round-trip latency in milliseconds |
| `error` | string | route_result (on failure) | Error message |
| `key_env` | string | route_result, route_cooldown, route_quarantined, route_recovered | Environment variable name of the key used (e.g., `NVIDIA_API_KEY`) |
| `cooldown_seconds` | int | route_cooldown | Cooldown duration applied |
| `cooldown_until` | string | route_cooldown | RFC3339 timestamp when cooldown expires |
| `from_preference` | string | preference_fallback | Source preference name |
| `to_preference` | string | preference_fallback | Destination preference name |
| `context` | string | route_result, route_recovered | `request` (in-flight request) or `background` (recovery loop) |

**Structured logs** (`router.log`): same events + debug detail, microsecond timestamps

**Health endpoint** (`/health`): per-provider aggregate (healthy, failures, cooldown, hard_failed, catalog_models)

---

## 15. Configuration

**File**: `config.json` at router working directory

**Schema**:
```json
{
  "host": "127.0.0.1",
  "port": 8643,
  "catalog_refresh_seconds": 21600,
  "retry": {
    "failure_limit": 1,
    "cooldown_seconds": 60,
    "max_cooldown_seconds": 900,
    "all_failed_wait_seconds": 15,
    "hard_failed_wait_seconds": 10,
    "route_timeout_seconds": 60
  },
  "preferences": [...],
  "providers": [...]
}
```

**Backward compatibility**: `key_env` (string) still read; `key_envs` (array) is authoritative.

**Environment**: Actual API keys in `~/.hermes/.env` (sourced by `startr`), never in config.

---

## 16. Test Environment

**Location**: `~/CraftedWorkflowsOS/router/test-env/`

**Components**:
- `main.go` — test router with multi-key support (`key_envs`), 120s route timeout
- `config-test.json` — port 8644, fast timeouts (2s cooldown, 30s route timeout)
- `mock-providers` — Go HTTP servers on 19990-19992 with `/control` endpoint for behavior injection
- `test-router` — compiled test router binary
- `e2e_test.py` — isolated Hermes run against test router (temp HERMES_HOME)
- `config-e2e.json` — E2E test config

**Mock behaviors** (via `/control`):
- `503` — return 503 Service Unavailable
- `401` — return 401 Unauthorized
- `403` — return 403 Forbidden
- `404` — return 404 Not Found
- `success` — return valid SSE stream
- `stream_30s_gap` / `stream_90s_gap` — SSE with idle gaps
- `stream_failure` — connection termination after headers
- `stream_commit_then_drop` — sends 1 SSE chunk then hangs forever (tests Spec #42 post-commit stream failure)

**Test matrix** (see ROUTER-TEST-MATRIX.md)

---

## 17. Hermes Compatibility Requirements

- Hermes calls `POST http://127.0.0.1:8643/v1/chat/completions` with standard OpenAI-compatible payload
- Hermes sets `Authorization: Bearer <key>` (router strips, injects provider key)
- Hermes receives SSE stream — **must handle 503 + Retry-After by retrying**
- Hermes **must NOT** treat post-commit stream failure as retryable router fallback
- Hermes **must** preserve request ID for correlation
- Router returns standard OpenAI error format on failure

---

## 18. Deployment Requirements

- **Production binary**: `~/CraftedWorkflowsOS/router/router` (built from `main.go`)
- **Launcher**: `~/bin/startr` — verifies binary identity (cwd=router dir, exe=router binary, size>8MB), port ownership, health
- **Lock file**: `~/.router-locks/router-startr.lock` (flock, prevents concurrent startr)
- **Safe failure mode**: If production process exists but unhealthy → exit 3, **no auto-restart**
- **Port conflict**: If 8643 occupied by unknown process → exit 5, manual intervention
- **Rollback**: `router.working-baseline-20261006`, `main.go.working-baseline-20261006`, `config.json.working-baseline-20261006`

---

## 19. Completion Criteria

A router change is **PRODUCTION-VERIFIED** only when:
1. All focused unit tests pass (401, 403, 404, 429, 5xx, timeout, stream_idle, stream_precommit_error)
2. **Post-commit stream failure test passes** (Spec #42) — mock `stream_commit_then_drop` verified in test-env
3. E2E test passes: Hermes → router → 503 → Hermes retry → recovery
4. Production router runs stably under real traffic
5. Telemetry matches expected event sequences
6. No goroutine leaks, no mutex contention under load
7. Rollback tested and documented

---

## 20. Hermes Operating Instruction (from Canonical Spec §48)

When executing this specification:

**Act as the engineer responsible for fixing the router, not as an assistant explaining how I could fix it.**

- Inspect the existing implementation first.
- Do not ask to repeatedly rediscover information already present in this specification.
- Do not modify Hermes core.
- Do not make speculative changes.
- Do not claim success without evidence.
- When a requirement is unclear, inspect the existing code/history/tests before asking.
- When a test fails, diagnose and fix the router/test environment rather than requesting manual babysitting.
- Preserve rollback.
- Prefer one complete, coherent implementation over accumulating symptom-specific patches.

At every major stage report:

```
REQUIREMENT
STATUS
EVIDENCE
REMAINING GAP
```

**Ultimate success condition**:

> **I start Hermes, use it normally, and provider/key failures are handled automatically by the router without me having to intervene.**

