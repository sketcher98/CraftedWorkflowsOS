# CraftedWorkflowsOS Router — Decisions

> Architectural decisions that future sessions must not casually reverse.

---

## D-001: Hermes Core is Read-Only

**Decision:** The Hermes Agent installation, source, runtime, configuration, profiles, memory, integrations, and launcher are read-only infrastructure. Router work stays in `~/CraftedWorkflowsOS/router`.

**Rationale:** 4+ months lost to Hermes reinstalls. Zero tolerance for global pip/uv/rm on protected paths.

**Status:** ENFORCED — all audit work read-only; no Hermes files modified.

---

## D-002: Router Owns All Routing/Fallback

**Decision:** The router is the SOLE routing authority. Hermes never selects provider/model/key. Hermes owns credential storage/input only.

**Rationale:** Prevents dual-routing conflicts. Single canonical request ID.

**Status:** IMPLEMENTED — router handles provider fallback, preference fallback, key rotation within same request_id.

---

## D-003: 401 Rotates Keys Before Provider Fallback

**Decision:** 401 Unauthorized = key-level transient failure. Quarantine ONLY that key, try next configured key. Only after ALL keys fail 401 → provider exhausted → next provider.

**Rationale:** Single bad key shouldn't kill entire provider. NVIDIA has 3 keys.

**Status:** TEST ENV IMPLEMENTED — `KeyHealth`, `selectProviderKey()` with per-key cooldown. PRODUCTION: single KeyEnv only (GAP).

---

## D-004: 403/404 Are Hard Failures

**Decision:** 403 Forbidden and 404 Not Found = permanent/hard failure for affected route/key/provider. Must not be blindly hammered. Require recovery probing.

**Rationale:** These indicate structural problems (model removed, permission revoked), not transient overload.

**Status:** IMPLEMENTED — `HardFailed` flag, `hardFailureRecoveryLoop()`, `HardFailedWaitSeconds` (10s).

---

## D-005: 429/5xx/Timeout Are Transient

**Decision:** 429, 500+, timeout, connection failure = transient. Do not permanently quarantine. Cooldown with exponential backoff up to max.

**Rationale:** These are recoverable. Aggressive quarantine kills availability.

**Status:** IMPLEMENTED — `cooldown_seconds: 60`, `max_cooldown_seconds: 900`.

---

## D-006: No Transparent Fallback After Response Commitment

**Decision:** Once a legitimate SSE event has been sent downstream (HTTP 200 committed), DO NOT transparently fail over to another provider.

**Rationale:** User may have received partial output. Switching providers = corrupted/replayed conversation.

**Status:** IMPLEMENTED — `streamResponse()` commits on first valid SSE chunk; no provider switch after.

---

## D-007: 60s Stream Timeout Is Idle Timeout, Not Total Lifetime

**Decision:** Post-header timeout = 60 seconds of NO DATA arriving. Timer resets on each chunk. A stream may run much longer than 60s total.

**Rationale:** Long generations (700k tokens) need unbounded total time but bounded stall detection.

**Status:** IMPLEMENTED — goroutine + timer pattern in `streamResponse()`.

---

## D-008: Router Does Not Manufacture New Inbound Requests

**Decision:** One inbound router request = one request ID. Provider fallback, preference fallback, key rotation all happen within same request_id. Only Hermes creating a new HTTP request generates a new request_id.

**Rationale:** Telemetry must allow reconstruction. Router must not simulate retries via new requests.

**Status:** IMPLEMENTED — `proxy()` single invocation, fallback loops internal.

---

## D-009: SSE Preflight Is Mandatory

**Decision:** Before committing downstream HTTP response, inspect enough SSE data to detect application-level errors (HTTP 200 + SSE error payload).

**Rationale:** Providers return HTTP 200 with `{"error": {"code": 503}}` in SSE. Status code alone is insufficient.

**Status:** IMPLEMENTED — 1MiB buffer cap, inspects first meaningful event.

---

## D-010: Client Cancellation ≠ Provider Failure

**Decision:** If Hermes disconnects/cancels, terminate provider request promptly. Do NOT increment provider failure counters. Do NOT classify as provider instability.

**Rationale:** Client cancellation is normal, not a signal of provider health.

**Status:** IMPLEMENTED — context cancellation handled, no failure increment on cancel.

---

## D-011: Configuration Is Externalized

**Decision:** No hard-coded provider URLs, model lists, API keys, timeouts, cooldowns, or fallback chains. Config drives routing policy. Env vars provide secrets.

**Rationale:** Enables changes without rebuild. Safe for multi-environment.

**Status:** IMPLEMENTED — `config.json` with all routing policy.
---

## D-012: Multi-Key Routing Is Mandatory

**Decision:** Each provider must support multiple API keys with independent health state. NVIDIA: 3 keys, Zydit V1: 2+, Zydit V2: 2+.

**Rationale:** Single key = single point of failure. Key rotation without provider fallback preserves latency.

**Status:** **PRODUCTION IMPLEMENTED (Phase 1B complete 2026-10-10)** — `Provider.KeyEnvs`, `KeyHealth`, per-key `selectProviderKey()`, inner retry loop in `proxy()` (lines ~1433-1477) retries same route on 401 with next healthy key, `refreshProviderCatalog()` iterates `providerKeyEnvs()`. Previously: TEST ENV ONLY, production used single `KeyEnv`.

---

## D-013: Provider Fallback Order: NVIDIA → Zydit V1 → Zydit V2

**Decision:** For each preference, try NVIDIA first (lowest latency, highest rate limits), then Zydit V1, then Zydit V2.

**Rationale:** NVIDIA is primary; Zydit are fallbacks with different characteristics.

**Status:** IMPLEMENTED in config.json preferences.routes order.

---

## D-014: Preference Fallback Order: Kimi → GLM → Nemotron → DeepSeek

**Decision:** If all providers for a preference exhausted, fall back to next preference in config order.

**Rationale:** Kimi-K3 primary (1M context), GLM-5.3 alternative, Nemotron for reasoning, DeepSeek as final fallback.

**Status:** IMPLEMENTED in config.json preferences array order.

---

## D-015: Hard-Failure Recovery Probes Use Configured Keys

**Decision:** Recovery probes for 403/404 routes must use the configured key set, support multi-key rotation, not expose key values, not hold mutexes during I/O.

**Rationale:** Hard-failed routes may recover (temporary permission issues). Must verify with real credentials.

**Status:** IMPLEMENTED — `hardFailureRecoveryLoop()` background goroutine, uses `providerKeys()`.

---

## D-016: Pre-Header Timeout = 60 Seconds (Reduced from 120s)

**Decision:** If no provider response can be safely committed within 60s, classify as timeout and fallback.

**Rationale:** 120s was too long for user-facing requests. 60s balances patience vs responsiveness.

**Status:** IMPLEMENTED — `route_timeout_seconds: 120` in config (applies to upstream), pre-header logic 60s.

---

## D-017: All-Providers-Failed = Background Recovery, No Tight Loop

**Decision:** When all providers unavailable, return 503 immediately with Retry-After. Recovery continues in background. No hammering.

**Rationale:** Prevents resource exhaustion. Bounded observable waiting.

**Status:** IMPLEMENTED — `waitForRecovery(w, r)` returns 503 immediately; `hardFailureRecoveryLoop()` runs in background.

---

## D-018: Telemetry Records Key Env (Not Secret)

**Decision:** JSONL events may include `key_env` field for multi-key tracing. Never record actual secret values.

**Rationale:** Debugging needs to know which key was used. Secrets must never leak.

**Status:** TEST ENV — events include `key_env`. PRODUCTION — single key, not traced.

---

## D-019: Health Endpoint Exposes No Credentials

**Decision:** `GET /health` returns provider state (healthy, failures, cooldown, hard_failed, catalog_models) only. No keys, no tokens.

**Rationale:** Operational visibility without secret exposure.

**Status:** IMPLEMENTED — verified live.

---

## D-020: Test Environment Is Fully Isolated

**Decision:** Test env uses separate port (8644), separate config, separate mock providers (19990-19996). Never reaches real providers.

**Rationale:** Destructive tests (401, 403, timeout, 5xx) must not touch production.

**Status:** IMPLEMENTED — `test-env/` directory with mock binaries and configs.

---

## D-021: WaitForRecovery Returns 503 Immediately (Not Block)

**Decision:** Changed from blocking `time.Sleep(wait)` to returning 503 with `Retry-After` header. Recovery continues in background.

**Rationale:** Blocking the request thread prevents router from serving other requests. 503 lets Hermes retry.

**Status:** IMPLEMENTED — diff shows signature change `waitForRecovery(w, r)` with immediate 503 response.

---

## D-022: Catalog Refresh = 6 Hours (Production), 1 Hour (Test)

**Decision:** Production catalog refresh at 21600s. Test env at 3600s.

**Rationale:** Balance freshness vs API calls. Test needs faster iteration.

**Status:** IMPLEMENTED in respective configs.

---

## D-023: Production Binary Must Be >8MB (Size Check in startr)

**Decision:** `startr` verifies binary size >8MB to distinguish production (~10MB) from test builds (~6MB).

**Rationale:** Prevents accidentally starting test binary as production.

**Status:** IMPLEMENTED in `startr` verify_production_binary().

---

## D-024: Flock Lock Prevents Duplicate Router Instances

**Decision:** `startr` uses `flock -n` on `$HOME/.router-locks/router-startr.lock`.

**Rationale:** Concurrent invocations must serialize. Second invocation exits cleanly.

**Status:** IMPLEMENTED — verified idempotent.

---

## D-025: No Speculative Patching

**Decision:** Before changing production code: reproduce → inspect → identify exact failure → minimal change → test in isolation → regression matrix → deploy → verify.

**Rationale:** Historical mistakes from assumption-based patches.

**Status:** ENFORCED — this documentation task is read-only inspection.

---

## D-026: Historical Lessons (from Canonical Spec §41)

**Decision:** Document and enforce the following lessons from past failures to prevent recurrence.

**Lessons:**

1. **Mistake 1 — HTTP 200 ≠ Success**: Providers return HTTP 200 with SSE application errors (`{"error":{"code":503}}`). Must inspect SSE pre-commit. Never trust status code alone.

2. **Mistake 2 — No Provider Switch After Commitment**: Once partial assistant output delivered downstream, switching providers corrupts conversation. Commitment = no fallback.

3. **Mistake 3 — Client Cancellation ≠ Provider Failure**: Abandoned requests are normal user behavior. Do not increment failure counters or trigger cooldowns on client disconnect.

4. **Mistake 4 — No Mutexes During Network I/O**: Holding provider locks during `client.Do()` deadlocks concurrency. Release before network calls.

5. **Mistake 5 — Test Isolation Is Mandatory**: Tests that secretly reach production credentials are not tests. Mock providers must be self-contained with fake credentials.

6. **Mistake 6 — Router ≠ Hermes Retry Source**: One router `proxy()` invocation = one request ID. Hermes may create subsequent HTTP requests due to its own retry/continuation logic. Do not conflate.

7. **Mistake 7 — Router Logs ≠ Hermes Compatibility**: Internal telemetry proves router behavior, not client-visible wire behavior. Hermes E2E tests required.

**Rationale:** These 7 mistakes consumed 4+ months of debugging and multiple Hermes reinstalls. Institutional memory must persist across sessions.

**Status:** DOCUMENTED — enforced via ROUTER-DECISIONS.md and ROUTER-SPEC.md.

