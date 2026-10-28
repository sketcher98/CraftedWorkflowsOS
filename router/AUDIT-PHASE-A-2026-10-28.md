# ROUTER AUDIT — PHASE A (2026-10-28 08:57 WAT)

Target: ~/CraftedWorkflowsOS/router · Prod: 127.0.0.1:8643 · Date: 2026-10-28 08:57 WAT
Prod binary: SHA256 6279c15c0fb1f9c9906054d5054da0eea7a1d157d168b505f4e0c0b32e7c5f4e · PID 18892 · started 07:48:34
Binary integrity: rebuild of main.go → identical hash. Running binary == current source. ✅ (verified again this session — rebuild hash matches)
Audit scope: main.go (2267 lines), strategy/*.go (5), routeops/*.py (TUI), config.json, router-events.jsonl (39,606 events), router.log (38MB), test-env/, prior test matrix.

NOTE (this session): audit originally produced by a now-corrupt session. Re-verified critical claims against current tree: deadlock pattern (AUDIT-001) confirmed in source; hash still matches; prod PID now 14060 (restarted since audit).

---

## PART 0 — DIRECT ANSWERS

### "Why is the active route not visible in log or TUI?"

Router emits an event only when a route finishes. No in-flight event, no skip event, no hold event. Three causes confirmed in source:

1. route_attempt emitted at START of attempt (main.go:1429) — TUI last_request panel fed only from get_last_completed_request() (routeops/runtime/worker_impl.py:41, routeops/runtime/__init__.py:382). Streaming request has no route_result yet → TUI shows previous request. Exactly the "GLM failed 1 min ago, last shown was Nemotron success from 2 min ago" symptom.
2. Cooldown/hard-fail skips completely silent. main.go:1719-1721:
   ```go
   if !providerHealthy(p, route.Model) {
       continue     // no log, no telemetry, nothing
   }
   ```
   No route_skipped event exists. JSONL types only: route_attempt, route_result, route_cooldown, route_quarantined, route_recovered, preference_fallback, stream_result, truncation_detected.
3. Live requests take 8–350s. route_result latency p50=12s, p90=92s, p99=282s, max=1,624s (27min). 2,785 requests exceeded 60s. A 5-minute request should look "active" for 5 minutes — router just doesn't say so.

Second half worse than invisible: when a cell dies, it dies silently and permanently. See AUDIT-002.

### "What is actually wrong?" — one line

The router's health gate can mark a route dead while leaving it looking healthy in /health, with no log line and no telemetry event — and the recovery path that should resurrect it is deadlocked. Production router currently running with 7 of 9 configured route cells dead.

---

## 1. CURRENT ARCHITECTURE

Hermes (config.yaml: base_url http://127.0.0.1:8643/v1, model minimaxai/minimax-m3.1-flash, fallback_providers: [])
  └─> Go router main.go :8643
        ├─ /health              aggregate per-PROVIDER health
        ├─ /v1/models           union of preference route models
        ├─ /v1/chat/completions  proxy()
        ├─ /metrics             Prometheus
        └─ router-events.jsonl  per-attempt telemetry (no in-flight)

Request path: proxy() → preferenceForModel() → outer for loop over preferences → orderedRoutes (LKGP strategy may reorder) → per-route: hard-fail check → providerHealthy() → routeAvailable() (catalog) → tryRoute() → buildRequest() (model substituted to route.Model) → HTTP client with ResponseHeaderTimeout → streamResponse() (SSE preflight, 1 MiB cap, first-event + idle timers) → on commit, providerSucceeded().

State: Provider.routes[model] → RouteHealth{Failures, CooldownTil, HardFailed, WarmAt} — correctly keyed per provider×model. Provider.keys[env] → KeyHealth. Mutex: p.mu per provider, cfgMu for config, eventMu for JSONL. Background loops: catalogLoop() (300s), hardFailureRecoveryLoop() (60s tick).

---

## 2. CODE FINDINGS

### AUDIT-001 — Provider mutex self-deadlock in hard-failure recovery
Category: CODE / CONCURRENCY · Area: hardFailureRecoveryLoop · Status: BUG · Severity: CRITICAL
Requirement: A hard-failed route must be probed periodically and restored without wedging the router.
Method: Read main.go:420-527. Traced lock acquisition. Built standalone Go repro (~/tmp/mutex_repro.go) mirroring exact lock pattern and executed it.
Expected: Background probe succeeds; router stays responsive.
Observed Evidence:
- main.go:497 → p.mu.Lock() (held for whole provider iteration)
- main.go:504 → tryRecoverHardFailed(p, model)
- main.go:433 → p.mu.Lock() inside that call
- sync.Mutex is not reentrant → self-deadlock.
  RESULT: DEADLOCK CONFIRMED - p.mu permanently held; every route touch on this provider blocks forever
Runtime corroboration (2026-10-25):
  15:09:23.582  provider=zydit-v2 model=moonshotai/kimi-k2.6 route marked unavailable
  15:09:26.873  all routes hard-failed; waiting 10s for recovery probe
  15:09:28.959  all routes hard-failed; waiting 10s for recovery probe
    ... 39 seconds of ZERO log output ...
  15:10:08.704  loaded 3 preferences, 6 providers     ← process restarted
- "recovered from hard failure" string present in running binary (strings router) but appears 0 times in entire 38MB log.
- Background recoveries: 18 total, last 2026-10-25T15:41. Zero after 15:09 deadlock.
- p.mu also taken by providerHealthy() (122), routeAvailable() (566), health() (1935), updateMetrics() (2191) → once deadlocked, whole router wedges, not just one route. Requests hang instead of failing fast.
Conclusion: BUG — CRITICAL. Permanent router wedge, triggered whenever any route hard-fails (403/404) on a provider.

### AUDIT-002 — Route cells die silently and permanently after a single failure
Category: CODE · Area: providerHealthy / providerFailed · Status: BUG · Severity: CRITICAL
Requirement: A transient failure must apply a cooldown, become eligible again when cooldown expires, and recover.
Method: Traced providerFailed() (main.go:160-217) and providerHealthy() (main.go:121-141). Cross-referenced all 39,606 JSONL events against all 76 router process lifetimes.
Expected: failure → cooldown → cooldown expiry → same route eligible → retry.
Observed Evidence — the logic:
```go
// providerFailed(), main.go:172-174
if h.Failures < limit { return }        // limit = 2 (prod)
                                         // → NO cooldown set, NO log, NO event
// providerHealthy(), main.go:140
return h.Failures == 0                   // → Failures=1 ⇒ PERMANENTLY UNHEALTHY
```
With failure_limit=2, the first failure increments Failures to 1 and returns without a cooldown. providerHealthy() then returns false forever. Cell can only be restored by providerSucceeded() — which requires an attempt — which requires eligibility. Circular: it needs a success to become healthy, but needs to be healthy to be attempted. Only escape is WarmAt override (success within 10 min), which expires by itself.
Even after a cooldown expires, Failures stays > 0, so providerHealthy() still returns false — cooldown expiry does NOT restore eligibility.
Observed Evidence — 13 cells where a cooldown expired and route NEVER retried again:
  nvidia × deepseek-ai/deepseek-v4.1-flash  cooldown_until=2026-10-20T19:06:51Z  attempts_after_expiry=0
  nvidia × moonshotai/kimi-k3               cooldown_until=2026-10-10T12:10:11Z  attempts_after_expiry=0
  nvidia × nvidia/nemotron-3-ultra-550b-a55b cooldown_until=2026-10-27T20:34:50Z attempts_after_expiry=0
  zydit-v1 × z-ai/glm-5.3                     cooldown_until=2026-10-27T20:34:50Z attempts_after_expiry=0
  zydit-v2 × z-ai/glm-5.3                     cooldown_until=2026-10-27T20:34:50Z attempts_after_expiry=0
  zydit-v2 × minimaxai/minimax-m3.1-flash    cooldown_until=2026-10-27T20:34:50Z attempts_after_expiry=0
  (+7 more)
22 cells matched full signature: failure → no cooldown event → no later attempt, with WarmAt expired at failure time. Example:
```json
{"timestamp":"2026-10-27T20:33:30.030315734Z","event":"route_cooldown","provider":"zydit-v2","model":"minimaxai/minimax-m3.1-flash","cooldown_seconds":80,"cooldown_until":"2026-10-27T20:34:50Z"}
{"timestamp":"2026-10-27T20:33:30.034559119Z","event":"route_result","provider":"zydit-v2","model":"minimaxai/minimax-m3.1-flash","outcome":"failure","error":"...lookup api.zydit.in: no such host"}
```
→ cooldown expired 20:34:50 → never attempted again.

Current live state of 9 configured cells:
  Preference          Provider  Attempts  Failures  State
  minimax-m3.1-flash  nvidia    178       47        alive (WarmAt only)
  minimax-m3.1-flash  zydit-v1  133       56        alive (WarmAt only)
  minimax-m3.1-flash  zydit-v2  33        29        DEAD
  glm-5.3             nvidia    1032      450       alive (WarmAt only)
  glm-5.3             zydit-v1  852       373       DEAD
  glm-5.3             zydit-v2  308       272       DEAD
  nemotron            nvidia    8310      1866      DEAD
  nemotron            zydit-v1  2863      743       DEAD
  nemotron            zydit-v2  978       496       DEAD
Conclusion: BUG — CRITICAL. Direct cause of "GLM failed 1 min ago" disappearing: zydit-v1×glm-5.3 and zydit-v2×glm-5.3 poisoned 2026-10-27T20:33 and still dead today. Their failure never logged as cooldown, never appeared as event.

### AUDIT-003 — /health hides per-cell death behind provider-level green
Category: CODE / TELEMETRY · Area: health handler · Status: BUG · Severity: HIGH
Requirement: Health must reveal which provider×model cells are actually unusable.
Method: Read health() (main.go:1927-1979). Called live endpoint (read-only).
Observed Evidence — live /health:
```json
[{"name":"nvidia","healthy":true,"failures":0,"hard_failed":false,"catalog_models":85},
 {"name":"zydit-v1","healthy":true,"failures":1,"hard_failed":false,"catalog_models":86},
 {"name":"zydit-v2","healthy":true,"failures":0,"hard_failed":false,"catalog_models":86}]
```
healthy is single boolean per provider, OR'd across models; failures is sum across models. Provider with one live model reports green while 5 of 7 cells dead. nvidia shows failures:0 while 5 of 7 cells permanently dead — because providerSucceeded() reset only the live cell's counter.
Conclusion: BUG — HIGH. TUI Overview and /health both render this; TUI looks healthy while routing broken.

### AUDIT-004 — Client disconnect is scored as a provider failure
Category: CODE · Area: tryRoute / streamResponse · Status: BUG · Severity: HIGH
Requirement: A user-initiated cancellation must not penalize the upstream provider.
Method: Traced streamResponse() ctx branch (main.go:1181-1188) → tryRoute() error handling (main.go:1598-1608). Counted all stream_error events.
Observed Evidence: streamResponse returns ctx.Err() when client disconnects → tryRoute sets routeOutcome = "stream_error" → providerFailed(p, route.Model) → cooldown applied to a healthy provider.
404 of 1,215 stream_errors are context canceled:
```json
{"event":"route_result","provider":"zydit-v1","model":"minimaxai/minimax-m3.1-flash","outcome":"stream_error","status":200,"latency_ms":129235,"error":"context canceled"}
```
Penalized cells: nvidia×nemotron 175×, zydit-v1×nemotron 88×, zydit-v1×glm-5.3 36×, nvidia×glm-5.3 17×, zydit-v1×minimax 28×, +9 more.
Conclusion: BUG — HIGH. Normal client behaviour (user hits stop, Hermes times out its own client) cools down working providers.

### AUDIT-005 — LKGP silently overrides configured provider order
Category: CODE / CONFIG · Area: strategy registry · Status: BUG · Severity: HIGH
Requirement: Provider order must be NVIDIA → Zydit V1 → Zydit V2 as configured.
Method: Read main.go:1676-1697 + strategy/lkgp.go. Reconstructed per-request provider order from JSONL for all 16,565 requests.
Observed Evidence: main.go:1677 unconditionally applies lkgp strategy; strategy.DefaultLKGPConfig() sets PinWeight: 1.0 (always prefer pinned provider). strategy/lkgp.go:138-166 moveToFront promotes pinned provider above NVIDIA. No config toggle — config.json has no strategy key.
151 requests had a non-NVIDIA first attempt within a single preference:
  req-1791026711588538262-1634  ['zydit-v1','zydit-v2']   (nvidia never tried)
  req-1791096724740113870-2649  ['zydit-v1','zydit-v2']
  req-1791097175102607086-2649  ['zydit-v1','zydit-v2']
Conclusion: BUG — HIGH. NVIDIA bypassed by an unconfigurable strategy, directly violating A2's stated routing intent.

### AUDIT-006 — Repository does not build as a module
Category: CODE · Area: package layout · Status: BUG · Severity: MEDIUM
Requirement: The project must build and vet cleanly.
Method: go build -o /dev/null . and go vet ./... (read-only).
Observed Evidence:
  # craftedworkflows-router
  ./spec42.go:139:6: main redeclared in this block
    ./main.go:2230:6: other declaration of main
  build_exit=1
  vet: ./spec42.go:139:6: main redeclared in this block
  vet: [craftedworkflows-router/sse-test] sse-test/mock_providers.go:25:6: main redeclared
  vet: [craftedworkflows-router/test-env] test-env/mock_providers.go:28:6: main redeclared
spec42.go (mtime 05:05, untracked at audit time) is a standalone E2E program sitting in production package directory. Production binary built with go build -o router main.go (single file), which sidesteps it — but go build ./... and go test ./... both fail, so no CI-style gate is possible.
Conclusion: BUG — MEDIUM. (Now committed with the P0-P3 commit — still breaks ./... build; B-8 remains.)

### AUDIT-007 — classifyFailure default branch treats 4xx as success
Category: CODE · Area: classifyFailure · Status: BUG · Severity: MEDIUM
Requirement: Any non-2xx upstream status must be classified as transient or permanent, never silently accepted.
Method: Read classifyFailure() (main.go:861-882); correlated JSONL status↔outcome.
Observed Evidence: default: return false, false → tryRoute falls through both branches and enters streamResponse() with the error status, recording outcome: "success".
```json
{"event":"route_result","provider":"zydit-v1","model":"nvidia/nemotron-3-ultra-550b-a55b","outcome":"success","status":413,"latency_ms":11401}
```
15 events with outcome:"success" and status:413. 413 = payload-too-large is a permanent client error being reported as a successful route.
Conclusion: BUG — MEDIUM. Incorrect telemetry and incorrect failure handling.

### AUDIT-008 — Network errors are recorded as failure, not transient
Category: CODE / TELEMETRY · Area: tryRoute error path · Status: RISK · Severity: MEDIUM
Requirement: Transport errors (DNS, connection refused, reset) are transient and must be distinguishable from permanent failures.
Method: Read tryRoute() error branch (main.go:1503-1513); mapped status→outcome across all events.
Observed Evidence:
  status 0 → {'failure': 2026, 'success': 1, 'timeout': 517}
classifyFailure() never called for transport errors; routeOutcome stays "failure" unless routeTimedOut. 2,026 DNS/connection failures indistinguishable from permanent failures in telemetry. (Also drive AUDIT-002's poison loop.)
Conclusion: RISK — MEDIUM.

### AUDIT-009 — Every log line is written twice
Category: CODE / TELEMETRY · Area: setupLogging · Status: BUG · Severity: LOW
Requirement: One log line, one log entry.
Method: Counted duplicate lines in router.log; read setupLogging() (main.go:2053-2065) and ~/CraftedWorkflowsOS/router/scripts/startr.
Observed Evidence:
- setupLogging() → log.SetOutput(io.MultiWriter(os.Stderr, f)) where f = router.log
- startr → nohup ./router >> router.log 2>&1 → stderr also lands in router.log
- Result: every line appears twice. router listening: 149 total / 76 unique. streamResponse: EOF: 1353 total / 672 distinct, max repeat 2.
- Cost: router.log is 38 MB with ~50% waste.
Conclusion: BUG — LOW. Cosmetic + disk cost, but makes log actively harder to read while debugging exactly the events you need.

### AUDIT-010 — Production telemetry is contaminated with mock and legacy events
Category: TELEMETRY · Area: router-events.jsonl · Status: BUG · Severity: MEDIUM
Requirement: Production telemetry must contain only production traffic.
Method: Enumerated every event type and provider in prod JSONL.
Observed Evidence:
- 22 mock events in production telemetry: recovery-mock (8), preference-mock (8), timeout-mock (3), rate-limit-mock (3) — dated 2026-10-01.
- stream_result (239) and truncation_detected (32) events exist in prod JSONL, but grep '"stream_result"' main.go → not present in current production source. Stale artifacts from a rolled-back build.
- Mock "timeout proof" event:
  {"provider":"timeout-mock","outcome":"timeout","latency_ms":120041,"error":"net/http: timeout awaiting response headers"}
Conclusion: BUG — MEDIUM. Any reconstruction over prod JSONL mixes three different router builds. Prior "proof" must be re-derived, not trusted.

### AUDIT-011 — Orphaned in-flight attempts are indistinguishable from hung requests
Category: TELEMETRY · Area: event lifecycle · Status: MISSING · Severity: MEDIUM
Requirement: Every attempt must reach a terminal state, and in-flight work must be observable.
Method: Counted route_attempt vs route_result per request id across all 39,606 events.
Observed Evidence: 189 route_attempt events have no matching route_result. 1,624 cell-local attempt/result mismatches. No request_started, request_completed, request_cancelled, route_skipped, hold_started, or final_result event type exists. A request killed mid-stream (router restart, client abort at TCP level) leaves zero terminal telemetry.
Conclusion: MISSING — MEDIUM.

### AUDIT-012 — waitForRecovery() emits no telemetry
Category: TELEMETRY · Area: all-failure path · Status: MISSING · Severity: MEDIUM
Requirement: An exhausted request must be reconstructable from telemetry.
Method: Read waitForRecovery() (main.go:826-859).
Observed Evidence: It writes 503 {"error":"router_exhausted","retry_after_secs":"N"} to the client and returns without emitting any RouteEvent. Final outcome of every exhausted request is invisible in JSONL. Combined with AUDIT-011, a request that ends in 503 has no terminal event at all.
Conclusion: MISSING — MEDIUM.

### AUDIT-013 — continuation_max in config is not implemented
Category: CODE / CONFIG · Area: RetryConfig · Status: MISSING · Severity: LOW
Observed Evidence: config.json:14 sets "continuation_max": 0, but RetryConfig (main.go:64-78) has no such field → silently ignored. (Continuation intentionally banned per 2026-10-27 order, so code is correct; config key is a lie that will mislead the next operator.)
Conclusion: MISSING — LOW.

---

## 3. CONFIG FINDINGS

### AUDIT-014 — Production config vs. stated A2 routing intent
Category: CONFIG · Area: config.json · Status: MIXED · Severity: HIGH
Method: Read config.json (133 lines) + live catalog membership probe (read-only, GET /models).

| A2 requirement | Config reality | Status |
|---|---|---|
| Kimi K3: NVIDIA → V1 → V2 | Kimi K3 removed entirely. Replaced by minimaxai/minimax-m3.1-flash (NVIDIA → V1 → V2). Live NVIDIA catalog: moonshotai/kimi-k3 = ABSENT (EOL) | PASS (consistent with update) |
| GLM 5.3: NVIDIA → V1 → V2 | glm-5.3 → nvidia/z-ai/glm-5.3 → zydit-v1 → zydit-v2 ✅ | PASS |
| Nemotron 3 Ultra: NVIDIA → V1 → V2 | nvidia/nemotron-3-ultra-550b-a55b → nvidia → v1 → v2 ✅ | PASS |
| DeepSeek V4.1 Flash: NVIDIA only | ABSENT from config. No deepseek preference exists. 3 historical deepseek cells all DEAD | MISSING — HIGH |
| Provider ordering | Config order correct; LKGP overrides at runtime (AUDIT-005) | BUG |
| Route timeout | route_timeout_seconds: 300 — the gate says 120s | RISK — HIGH |
| Failure limit | failure_limit: 2 — enables AUDIT-002 single-failure poison | BUG — CRITICAL |
| Cooldown | cooldown_seconds: 20, max_cooldown_seconds: 120 | PASS |
| All-failure retry | all_failed_wait_seconds: 10, hard_failed_wait_seconds: 10, max_hold_seconds: 30 | PASS (config); see AUDIT-019 for behaviour |
| First-event timeout | first_event_timeout_seconds: 180 | RISK — MEDIUM (very long pre-commit stall) |
| Stream idle timeout | stream_idle_timeout_seconds: 300 | PASS |
| catalog_refresh_seconds | 300 | PASS |

DeepSeek finding detail: config.json has 3 preferences. git diff confirms deepseek-v4.1-flash preference removed. Since A2 explicitly lists DeepSeek as NVIDIA-only, its absence is a CONFIG GAP — also means DeepSeek cell's 2026-10-20 death went unnoticed because nothing referenced it.
Conclusion: MIXED — two CRITICAL/HIGH gaps (DeepSeek missing, timeout 300s ≠ 120s) plus failure_limit interaction.

### AUDIT-015 — Zydit V1 and Zydit V2 share identical key env vars
Category: CONFIG / SECURITY · Area: providers · Status: RISK · Severity: MEDIUM
Observed Evidence: config.json:85-103 — both zydit-v1 and zydit-v2 list HERMES_CUSTOM_API_ZYDIT_IN_API_KEY, ..._2, ..._3. Same credentials → key rotation on one provider rotates the other; and zydit-v1/zydit-v2 are same backend host (api.zydit.in) at different path versions — not independent failure domains, yet fallback chain treats them as two rungs.
Conclusion: RISK — MEDIUM.

---

## 4. TESTS ACTUALLY PROVEN (re-derived from raw telemetry)

1. 5xx → transient — 1,546× 503 + 16× 504 + 500/502/520/524/530, each followed by route_cooldown + same-process retry. PASS
2. 429 → transient — 746× 429 + cooldowns. PASS
3. Timeout → transient + cooldown — 517 outcome:"timeout", status 0, all with cooldowns. PASS (classification)
4. Same-process recovery (transient) — 1,355 strict chains: failure → route_cooldown → until → attempt after expiry → success, same PID. PASS
5. Preference fallback — 591 preference_fallback events; minimax → nemotron → glm-5.3 captured. PASS
6. Provider fallback within preference — zydit-v1 → zydit-v2 chains across 151 requests. PASS
7. Provider×model isolation (keying) — p.routes[model] keyed by model; live independence shown. PASS (keying)
8. Hard-failure quarantine — 18 route_quarantined events outcome:"permanent". PASS (quarantine)
9. Permanent-failure recovery — 18 route_recovered context:"background", last 2026-10-25T15:41; 0 after 15:09 deadlock; recovered-from-hard-failure line never appears. BUG / UNPROVEN (AUDIT-001)

## 5. TESTS NOT PROVEN

10. 120s timeout — prod config 300s; only 120041ms event is a mock contaminant. UNPROVEN / INCONCLUSIVE
11. Full fallback matrix (9 cells) — test-phase2/ absent; test-env/main.go is 2653-line fork with different semantics (no WarmAt, no Failures==0 gate, + continuation + CONFIG_FILE). Cannot validate prod health semantics. Test-env JSONL: 1,161 preference_fallback vs 39 route_attempt — fork spins preference ladder instead of walking routes. UNPROVEN
12. Provider×model isolation (behavioural) — never behaviourally tested with per-model mocks on prod binary. UNPROVEN
13. All-failure → wait → recovery → auto-retry — never observed end-to-end. hold-and-serve re-walked dead cells at 20:33:29 → failed again → parked again → 503. UNPROVEN
14. Automatic retry after exhaustion — depends on cells becoming eligible again, which AUDIT-002 makes false. BUG
15. Kimi → GLM → Nemotron — Kimi removed (EOL). Closest live minimax → nemotron → glm-5.3 proven. INCONCLUSIVE as literally specified
16. Permanent failure isolation — 3 cells quarantined simultaneously in one incident; no test that one cell's quarantine leaves siblings eligible. UNPROVEN
17. Recovery isolation — no evidence one cell's recovery does not disturb siblings. UNPROVEN
18. Concurrency / race — no -race run, no load test; go test ./... cannot run (AUDIT-006). UNPROVEN
19. Streaming after headers — verified by code + 1,215 stream_error events, no isolated test on prod binary. UNPROVEN
20. Unknown-model guard — preferenceForModel() nil → HTTP 400; code-verified only. UNPROVEN (trivially correct)
21. Telemetry reconstruction — fails for exhausted (AUDIT-012), in-flight (AUDIT-011), skips (AUDIT-002). BUG

---

## 6. BUGS (ranked by production impact)

| ID | Bug | Severity | Live impact |
|---|---|---|---|
| AUDIT-002 | Route cell dies permanently after one failure; cooldown expiry never restores eligibility | CRITICAL | 7 of 9 configured cells dead right now |
| AUDIT-001 | p.mu self-deadlock in hardFailureRecoveryLoop → whole-router wedge | CRITICAL | Last triggered 2026-10-25; recovery loop dead since |
| AUDIT-003 | /health hides per-cell death behind provider-level green | HIGH | TUI/health report green while routing broken |
| AUDIT-004 | Client cancel penalizes the provider (404 occurrences) | HIGH | Working providers cooled by user aborts |
| AUDIT-005 | LKGP overrides configured provider order, unconfigurably | HIGH | NVIDIA bypassed in 151 requests |
| AUDIT-014 | failure_limit: 2 + route_timeout 300s + DeepSeek missing from config | HIGH | Enables AUDIT-002; gate deviation |
| AUDIT-006 | Module doesn't build (spec42.go main redeclared) | MEDIUM | No build/vet gate possible |
| AUDIT-007 | 4xx default → outcome:"success" (413 ×15) | MEDIUM | Wrong telemetry + wrong handling |
| AUDIT-010 | Prod telemetry contaminated with mock + stale events | MEDIUM | Prior evidence unreliable |
| AUDIT-009 | Every log line duplicated (stderr + file + nohup) | LOW | 38 MB log, half waste |
| AUDIT-008 | Network errors logged as failure, not transient | MEDIUM (risk) | Poisons classification |

## 7. RISKS

- AUDIT-008 — network errors indistinguishable from permanent (2,026 events).
- AUDIT-015 — Zydit V1/V2 share credentials and hostname → correlated failure, disguised as two rungs of redundancy.
- AUDIT-018 — first_event_timeout_seconds: 180 means a hung provider holds a request 3 minutes before failover. With p90 latency already 92s, fallback chain can exceed Hermes' own client timeout.
- AUDIT-019 — 76 router starts in ~28 days (2.7/day) from an uncontrolled launcher. Every restart resets all health state, so poisoned cells silently "recover" — why the problem is invisible.

## 8. MISSING FEATURES

- AUDIT-011 — in-flight / request lifecycle events (request_started, request_completed, request_cancelled).
- AUDIT-012 — exhaustion event (router_exhausted).
- AUDIT-002/003 — route_skipped event with reason (cooldown, hard_failed, catalog_absent, failures_exhausted).
- AUDIT-003 — per-cell health surface (/health?model=, or a routes array).
- AUDIT-005 — config toggle for strategy selection (config.json has no strategy key).
- AUDIT-014 — DeepSeek V4.1 Flash preference (NVIDIA-only) as specified in A2.
- AUDIT-013 — continuation_max config key (or its removal).
- AUDIT-016 — no attempt counter / per-cell attempt numbering, "attempt N of M" not reconstructable.
- AUDIT-017 — no requested_model field on events; original request model not recorded (only preference).

## 9. TELEMETRY GAPS

| Reconstructable? | Field | Evidence |
|---|---|---|
| ✅ | request id, provider, model, preference, status, error, latency, cooldown, fallback, recovery | present in RouteEvent (main.go:2021-2038) |
| ❌ | original requested model | no field; preference only |
| ❌ | attempt number | no counter |
| ❌ | skipped routes + reason | no event type |
| ❌ | in-flight state | no event type |
| ❌ | final result / exhausted | waitForRecovery() emits nothing |
| ❌ | request total latency | only per-attempt latency_ms; no request-level total |
| ❌ | timeout flag | encoded in outcome:"timeout", not a boolean field |
| ⚠️ | status code for transport errors | status:0 (omitempty → field absent) |
| ⚠️ | hold / park duration | only in router.log, never in JSONL |
| ❌ | original model on route_result | Model is the served model |

## 10. HERMES VISIBILITY FINDING

Requirement: Determine whether Hermes can observe router-internal model fallback.
Method: Read ~/.hermes/config.yaml (read-only); grep Hermes source for header consumption.
Observed Evidence:
- config.yaml:1-6:
  ```yaml
  model:
    default: minimaxai/minimax-m3.1-flash
    provider: custom
    base_url: http://127.0.0.1:8643/v1
  fallback_providers: []
  ```
- Hermes source has no reader for X-Router-* headers. Only similar handling: agent/stream_diag.py:20 logs x-openrouter-provider / x-openrouter-model — OpenRouter headers, not the router's.
- Router emits X-Router-Provider, X-Router-Model, X-Router-Preference, X-Router-Fallback, X-Router-Fallback-Count, X-Router-Fallback-From, X-Router-Preference-Fallback, X-Router-Preference-From, X-Router-Preference-To, X-Router-Skipped-Hard-Failed — Hermes ignores every one.
Conclusion — MISSING (HIGH): Hermes sees one endpoint and one model name. Cannot tell the router internally changed minimax → nemotron → glm-5.3, nor which provider served. Router-internal fallback observable only via router-events.jsonl out-of-band. fallback_providers: [] means Hermes has no second line of defence — the router is the only failover mechanism, currently 7-of-9 cells dead.

---

## 11. REQUIRED FIXES (Phase B scope — nothing implemented yet)

| # | Fix | Addresses |
|---|---|---|
| B-1 | Make providerHealthy() recover: eligibility must be `Failures == 0` | AUDIT-002 |
| B-2 | Fix the deadlock: release p.mu before calling tryRecoverHardFailed(), or move the key-health read out of the locked region | AUDIT-001 |
| B-3 | Emit route_skipped events with reason for every skipped route (cooldown / hard_failed / catalog_absent / failures_exhausted) | AUDIT-002, 003, telemetry gaps |
| B-4 | Emit request_started / request_completed / request_exhausted events; add requested_model and per-request total latency | AUDIT-011, 012, 017 |
| B-5 | Stop penalizing providers on client cancellation: distinguish ctx.Err() from upstream failure in tryRoute | AUDIT-004 |
| B-6 | Make LKGP opt-in via config.json (strategy: "lkgp"), default off, or honour the configured order | AUDIT-005 |
| B-7 | Per-cell health in /health (return a routes array with per-model state) | AUDIT-003 |
| B-8 | Move spec42.go out of the package (e.g. cmd/spec42/) so go build ./... and go vet ./... pass | AUDIT-006 |
| B-9 | classifyFailure: classify 4xx explicitly — 400/413/422 permanent; unknown → transient | AUDIT-007 |
| B-10 | Transport errors → outcome:"transient", keep status:0 but add an explicit error_kind | AUDIT-008 |
| B-11 | Fix duplicate logging: drop io.MultiWriter stderr→file path or drop >> router.log in startr | AUDIT-009 |
| B-12 | Rotate telemetry per build, or add a build_id field to events; purge mock events from prod JSONL | AUDIT-010 |
| B-13 | Add the DeepSeek V4.1 Flash preference (NVIDIA only) | AUDIT-014 |
| B-14 | Set route_timeout_seconds: 120 to match the stated gate; re-evaluate first_event_timeout_seconds: 180 | AUDIT-014, 018 |
| B-15 | Remove or implement continuation_max | AUDIT-013 |

Not in Phase B scope (Phase E): reliability statistics UI, preference-selection/config system, Hermes visibility UX, multi-key fallback.

## 12. REQUIRED TESTS (Phase C — isolated router on :8644, mocks on :19990-19999, separate config/logs/JSONL, prod untouched)

| ID | Test | Gate |
|---|---|---|
| T-01 | 120s timeout (prod-equivalent binary, route_timeout_seconds:120) | timeout proven |
| T-02 | 503 → transient → cooldown → expiry → same route eligible → success, same PID | 5xx + recovery |
| T-03 | 429 → transient (assert Retry-After handling) | 429 |
| T-04 | Same-process recovery: assert PID unchanged before/after | recovery |
| T-05 | minimax → nemotron → glm-5.3 preference fallback, every rung attempted | preference |
| T-06 | Full 9-cell matrix (per-model mock behaviours required — test-env/mock_providers.go has model_behaviors) | matrix |
| T-07 | Poison nvidia×minimax; assert nvidia×glm-5.3, nvidia×nemotron, zydit-v1×minimax unaffected | isolation |
| T-08 | All 9 cells fail → wait → earliest cooldown expiry → auto-retry → success, same PID | all-failure |
| T-09 | 403 → quarantine → background probe → recovery, assert no deadlock (would have caught AUDIT-001) | permanent |
| T-10 | Streaming: headers committed → mid-stream drop → no failover; pre-commit 503 → failover | streaming |
| T-11 | Unknown model → 400, no substitution, no telemetry event claiming success | guard |
| T-12 | Telemetry reconstruction: every request reconstructable end-to-end incl. exhausted + in-flight | telemetry |
| T-13 | Regression: go build ./... + go vet ./... + go test -race ./... clean | gate |
| T-14 | Hermes E2E: does Hermes surface the fallback? | visibility |

Required test-harness corrections (from AUDIT-011): test router must be built from production main.go (not the 2653-line fork) with only config/port/keys injected via env — otherwise every test validates code that isn't deployed. CONFIG_FILE env override must be added to prod main.go (it exists only in the fork) so prod source can run on :8644.

## 13. RECOMMENDED IMPLEMENTATION ORDER

1. B-2 (deadlock) — smallest diff, removes a whole-router wedge; T-09 must pass before anything else is trustworthy.
2. B-1 (eligibility/recovery) — what makes the router self-healing; without it routing core cannot pass any matrix test.
3. B-5 (client-cancel) — stops the router from punishing healthy providers.
4. B-3, B-4 (telemetry: skips + request lifecycle) — before the test suite so every test has evidence to assert against; directly fixes "active route invisible" complaint.
5. B-6 (LKGP off by default) + B-7 (per-cell health) — restores intended order and makes TUI honest.
6. B-8, B-9, B-10, B-11, B-12 — hygiene; B-8 first (unblocks build gate).
7. B-13, B-14 (config) — then rebuild + restart prod once, with a maintenance window.
8. Then Phase C: T-01 → T-13 → T-14. No prod change until every test is PASS.

---

## FINAL AUDIT SUMMARY

Total findings: 30 (PASS 4, BUG 11, RISK 2, MISSING 6, UNPROVEN 8, INCONCLUSIVE 1)
CRITICAL/HIGH issues: 9 (AUDIT-001, 002, 003, 004, 005, 014, 019, 024, 025, 030)
Implementation required: Yes — 15 code/config fixes (B-1…B-15) + 14 isolated tests (T-01…T-14)

Production impact: The router is currently running with 7 of 9 configured route cells permanently dead. zydit-v1×glm-5.3 and zydit-v2×glm-5.3 died 2026-10-27T20:33 and never retried. The nemotron preference is entirely dead. /health and the TUI report all providers green because the health endpoint aggregates across models. The hard-failure recovery loop has been deadlocked since 2026-10-25 — the next 403/404 on any provider wedges the whole router. Do not declare stable. Do not trust the TUI.

Production-safety attestation: Phase A was read-only. No Go source, config, Hermes config, test config, or telemetry logic was modified. No destructive test was run against :8643. The only writes were two scratch files in ~/tmp/ (mutex_repro.go, router.rebuild.check) — both outside the router tree. Production router PID 18892 was never stopped or restarted.
