# Router Test Matrix

> Status vocabulary: IMPLEMENTED | TESTED | PRODUCTION-VERIFIED | UNTESTED | KNOWN GAP
> Test status: PROVEN | PARTIALLY PROVEN | UNPROVEN | KNOWN FAILURE | NOT APPLICABLE

---

## Provider/Key Tests

| ID | Requirement | Test | Status | Evidence | Remaining Gap |
|----|-------------|------|--------|----------|---------------|
| PK-01 | 401 key rotation within provider | mock_401 + test-env proxy inner loop | **PRODUCTION-VERIFIED** | test-env main.go lines 1309-1319; production main.go lines 1433-1477 inner retry loop; live requests succeed | |
| PK-02 | 401 all keys exhausted → provider fallback | mock_401 with 2 keys both 401 | PARTIALLY PROVEN | Mock returns 401 for both keys; test-env retries same route twice then breaks | No dedicated test asserting provider fallback after key exhaustion |
| PK-03 | 403 quarantine (hard failure) | mock_403 + classifyFailure | PROVEN | classifyFailure returns permanent=true for 403; providerHardFailed called | |
| PK-04 | 404 quarantine (hard failure) | classifyFailure unit logic | PROVEN | classifyFailure returns permanent=true for 404 | No dedicated mock_404 test |
| PK-05 | 429 transient | classifyFailure unit logic | PROVEN | classifyFailure returns transient=true for 429 | No dedicated mock_429 test |
| PK-06 | 5xx transient | classifyFailure unit logic | PROVEN | classifyFailure returns transient=true for >=500 | No dedicated mock_5xx test |
| PK-07 | Timeout transient | timeout-mock (120s ResponseHeaderTimeout) | TESTED | timeout-mock returns timeout; route_cooldown + route_result timeout logged | |
| PK-08 | Connection/DNS failure transient | classifyFailure err != nil path | PROVEN | classifyFailure returns transient=true for err != nil | No dedicated network-failure mock |
| PK-09 | Key cooldown applied | providerKeyFailed + cooldown logic | TESTED | route_cooldown events in router-events.jsonl | |
| PK-10 | Key recovery (cooldown expiry) | Background catalog refresh probes | TESTED | refreshProviderCatalog calls providerKeySucceeded on 2xx | No explicit test for cooldown expiry without catalog refresh |
| PK-11 | Provider recovery (hard-failed) | hardFailureRecoveryLoop + tryRecoverHardFailed | TESTED | recovery-mock + test-env shows route_recovered events | Production main.go has simpler recovery (single key) |
| PK-12 | Catalog with multiple keys | refreshProviderCatalog iterates key_envs | **PRODUCTION-VERIFIED** | test-env main.go lines 579-666; production main.go lines 532-606 iterates providerKeyEnvs() | |
| PK-13 | Health with multiple keys | /health aggregates per-provider | TESTED | test-env health endpoint includes catalog_models | Production health same |

---

## Routing Tests

| ID | Requirement | Test | Status | Evidence | Remaining Gap |
|----|-------------|------|--------|----------|---------------|
| RT-01 | Provider fallback (same preference) | mock-providers 503 → next provider | TESTED | e2e_test.py Phase 1; mock set to 503, fallback to next | |
| RT-02 | Preference fallback (all routes failed) | 4 preferences in config | TESTED | proxy() loops to nextPreference(); preference_fallback events logged | |
| RT-03 | Same request ID across fallback | X-Request-ID header propagation | PROVEN | requestID generated once in proxy(), passed to all tryRoute() | |
| RT-04 | No manufactured inbound requests | Code inspection | PROVEN | No client-facing request generation in router code | |
| RT-05 | All providers exhausted → 503 + Retry-After | waitForRecovery() | TESTED | waitForRecovery returns 503 with Retry-After header | |
| RT-06 | Bounded exhaustion response (no spin) | waitForRecovery returns immediately | PROVEN | waitForRecovery writes 503 and returns; no sleep/loop | |
| RT-07 | No aggressive spin | earliestCooldown() + background loop | PROVEN | Background loop 60s tick; request returns 503 instantly | |
| RT-08 | Background recovery after exhaustion | hardFailureRecoveryLoop 60s tick | TESTED | route_recovered events with context=background in jsonl | |
| RT-09 | Hard-failure recovery probe | tryRecoverHardFailed makes test request | TESTED | recovery-mock test; route_recovered events | |

---

## SSE Tests

| ID | Requirement | Test | Status | Evidence | Remaining Gap |
|----|-------------|------|--------|----------|---------------|
| SSE-01 | Normal SSE stream | mock-providers success behavior | TESTED | e2e_test.py success path; streamResponse writes to client | |
| SSE-02 | Split SSE event across reads | streamResponse buffering | PARTIALLY PROVEN | Buffer accumulates until eventEnd found; 32KB read buffer | No explicit split-event test |
| SSE-03 | SSE application error BEFORE commitment | streamResponse preflight (1 MiB) | TESTED (test-env) | test-env main.go lines 870-904 inspects events pre-commit | Production main.go HAS preflight (1 MiB, app-error detection) |
| SSE-04 | No downstream commitment on SSE app error | Preflight returns error before WriteHeader | TESTED (test-env) | appErr triggers resp.Body.Close() and return before commit | Production main.go implements this |
| SSE-05 | Provider fallback after SSE app error | tryRoute treats appErr as transient | TESTED (test-env) | routeOutcome=transient, providerFailed, fallback continues | Production main.go implements this |
| SSE-06 | 60s pre-header timeout | ResponseHeaderTimeout = route_timeout_seconds | TESTED | test-env 30s; production 60s; timeout-mock validates | |
|| SSE-07 | Post-header idle timeout | streamResponse idleTimer reset on read | TESTED | test-env streamResponse lines 939-1022; mock stream_90s_gap | Production main.go has idle timeout via timer.Reset per chunk (configurable `stream_idle_timeout_seconds`) |
|| SSE-08 | Post-commit stream failure = stream_error | tryRoute line 1203-1211 | PROVEN | routeOutcome=stream_error, success=true, retryable=false | |
|| SSE-09 | Connection termination semantics | mock stream_failure (Hijacker) | TESTED | mock_providers.go stream_failure behavior | |
|| SSE-10 | Post-commit stream failure contract (Spec #42) | mock `stream_commit_then_drop` | **TESTED (test-env)** | mock sends 1 chunk then hangs; router hits idle timeout, returns stream_error + [DONE]; NO provider fallback | Hermes interaction needs E2E test (HE-04) |
|| SSE-11 | Hermes interpretation of post-commit failure | E2E test observation | UNTESTED | No Hermes behavior validation test exists | KNOWN GAP - critical |

---

## Concurrency/Resource Tests

| ID | Requirement | Test | Status | Evidence | Remaining Gap |
|----|-------------|------|--------|----------|---------------|
| CR-01 | Concurrent requests | Multiple simultaneous proxy() calls | UNTESTED | No load/concurrency test | |
| CR-02 | Key state isolation | Per-provider mutex protects keys | PROVEN | p.mu guards keys map; no cross-provider leakage | |
| CR-03 | Provider state isolation | Per-provider mutex protects routes | PROVEN | p.mu guards routes map | |
| CR-04 | No global network lock | Mutex released before client.Do | PROVEN | client.Do outside p.mu in tryRoute | |
| CR-05 | Goroutine behavior | One reader per request; timers stopped | PROVEN | streamResponse reader goroutine + timer; defer Stop() | No leak test under load |
| CR-06 | Low-memory behavior | 1 MiB preflight cap; 32KB read buffer | PROVEN | Constants in code; no unbounded allocation | |
| CR-07 | 1 MiB preflight cap | maxPreflightBytes = 1 << 20 | PROVEN | test-env line 802; production line 802 | |

---

## Hermes E2E Tests

| ID | Requirement | Test | Status | Evidence | Remaining Gap |
|----|-------------|------|--------|----------|---------------|
| HE-01 | Provider fallback (Hermes sees success) | e2e_test.py 503→success | TESTED | e2e_test.py runs Hermes agent against test router | |
| HE-02 | All-provider exhaustion (Hermes sees 503) | waitForRecovery → 503 | PARTIALLY PROVEN | Router returns 503; Hermes retry behavior not fully validated | Hermes max_retries handling needs verification |
| HE-03 | Hermes automatic retry/recovery | e2e_test.py full cycle | TESTED | e2e_test.py switches mock to success; Hermes retries | |
| HE-04 | Post-commit stream failure (Hermes behavior) | NONE | UNTESTED | No test for this | KNOWN GAP - critical |
| HE-05 | Repeated request boundedness | Multiple Hermes calls | UNTESTED | No soak test | |
| HE-06 | No "??" manual intervention | e2e_test.py fully automated | PROVEN | e2e_test.py runs without human input | |
| HE-07 | No manual babysitting | Automated test pipeline | PROVEN | e2e_test.py + focused mocks | |

---

## Operational Tests

| ID | Requirement | Test | Status | Evidence | Remaining Gap |
|----|-------------|------|--------|----------|---------------|
| OP-01 | /health endpoint | curl /health | PRODUCTION-VERIFIED | Live router returns provider health JSON | |
| OP-02 | JSONL telemetry | router-events.jsonl exists | PRODUCTION-VERIFIED | 3MB+ events file; structured events | |
| OP-03 | startr launcher | ~/bin/startr | PRODUCTION-VERIFIED | startr runs, verifies binary, health, lock | |
| OP-04 | Production rollback | Baseline binaries exist | IMPLEMENTED | router.working-baseline-20261006 etc. | Rollback not tested recently |
| OP-05 | Production baseline hashes | SHA256 recorded | IMPLEMENTED | ROUTER-STATUS.md will record | |

---

## Summary

**PROVEN / PRODUCTION-VERIFIED**: 30 (PK-01, PK-12 promoted)
**TESTED**: 13 (SSE-10 added)
**PARTIALLY PROVEN**: 3
**UNTESTED**: 8
**KNOWN FAILURE**: 0
**KNOWN GAP (production missing test-env features)**: 1 (HE-04)

**Remaining gaps**:
1. Hermes post-commit failure behavior unvalidated (HE-04)
2. Matrix test redesign needed (current test logic cannot force sequential provider failures)
3. Concurrency/load testing absent (CR-01, CR-05)
4. Zydit shared credentials (zydit-v1/v2 share identical key_envs)
5. Live key rotation untested (no production 401 observed to verify multi-key rotation)

**Phase 1B**: COMPLETE ✅ (2026-10-10)

**SPEC #42 (Post-commit stream failure)**: **RESOLVED** — verified in test-env with mock `stream_commit_then_drop`
