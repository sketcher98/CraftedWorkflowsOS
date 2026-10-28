# FULL FILE-BY-FILE AUDIT — Router + RouteOps
# Date: 2026-10-28 · Scope: main.go (2564 lines), strategy/, all routeops/*.py, startr, config.json

## A. ROUTER (main.go) — findings

### A1. REAL BUG: GAP-003 retry_after_seconds is dead code (main.go:1708)
The Retry-After capture sits AFTER the `if transient` block, which already returns
for 429s (line 1683-1706). So the capture never executes for 429s — the field is
always 0 in route_result events. Fix: move the capture INTO the transient/429 branch
before the return.

### A2. Metrics bug: updateMetrics() uses .Inc() on gauges (main.go:2505-2515)
keysHardFailed / keysInCooldown / keysHealthy use Inc() on GAUGE vectors. Gauges must
use Set(). Since updateMetrics runs on every /metrics scrape, these counters inflate
monotonically forever. Fix: Set(1)/Set(0) like the route gauges.

### A3. Background recovery omits health_transition (main.go:576-582)
tryRecoverHardFailed success path emits route_recovered but NOT health_transition
(unhealthy_to_hardfailed -> unhealthy_to_healthy). Inconsistent with providerSucceeded.
Fix: add the health_transition event there.

### A4. Inconsistency: catalog list uses route.Model (route_result) but requested_model
main.go:1544 Model = route.Model (served). RequestedModel set only on request_started.
Reconstruction cannot pair "Hermes asked X, router served Y" from route_result alone
unless it cross-references request_started. Minor, documented.

### A5. holdAndServe: hard-failed + cooldown coexistence
If a route is BOTH hard-failed and cooling, earliestCooldown marks hasHardFailed=true,
continue — the cooldown is skipped. waitForRecovery then returns HardFailedWaitSeconds.
Correct semantics, but doc-only.

### A6. classifyFailure 3xx treated as transient (default branch)
2xx handled explicitly; everything else <400 falls to `return true, false` (transient).
3xx redirect responses will be treated as transport-transient and fail the route.
Low impact (upstreams don't redirect), but explicit handling would be cleaner.

### A7. buildRequest copies ALL client headers incl. Content-Length/Connection
main.go:1039-1046. Copies client headers, then sets Content-Type. It excludes only Host.
Content-Length of the ORIGINAL body may be copied while the rewritten body differs —
Go's http will override Content-Length when body is set? It sets it from the reader;
the copied header may conflict. Should skip Content-Length explicitly. Check.

### A8. streamResponse: timer resets only on data; EOF path fine.
No issue found. Preflight 1MiB cap + app-error detection good.

### A9. RouteHealth.Keys field (struct line 44) is declared but never used.
Dead field. RouteHealth maps per route but key health lives on Provider.keys. Remove.

### A10. config validation: strategy value not validated
validateConfig never checks cfg.Strategy is "" or "lkgp". Unknown value silently behaves
like "". Add validation.

## B. ROUTEOPS (TUI) — findings

### B1. CONFIRMED BUG: keymgmt detail view is dead (screens/keymgmt_curses.py)
self.show_key_detail set on Enter, but everything reads self.show_detail. Detail mode
never activates — Enter on a provider does nothing. FIXED in this audit (unified to
show_detail).

### B2. CONFIRMED BUG: Routes screen 's' shadowed global Settings shortcut
I bound strategy toggle to 's' in routes_curses.py, which conflicts with nav_shell's
global 's' -> Settings. FIXED in this audit (re-bound to 'g').

### B3. Settings screen is editable in code but the skill/spec says read-only
settings_curses.py header claims "Fully editable: poll intervals (live), display toggles,
router config (safe write)". The routeops-tui skill documents Settings as read-only by
design. One of the two is stale — needs a decision: either reinstate read-only or
update the skill.

### B4. worker_impl JSONLWorker: reconstruction every poll over 1000-event buffer
get_last_completed_request re-scans and reconstructs ALL buffered events every poll
(1.5s). O(1000) per poll with json parsing — acceptable but wasteful; could cache.
Minor.

### B5. History index: refresh throttled to 3s, filters extended earlier. OK.

### B6. metrics_curses: no auth on /metrics; Prometheus text parse has edge cases
(line with no '}' etc.) — acceptable.

### B7. nav_shell: 's' on Settings + q quits only from Overview. Consistent.

### B8. Per-Key screen: "test_verdict" uses OK/ALL KEYS DEAD — fine (live-test text, not health state).

### B9. startr: verified safe (lock, identity check, no auto-restart on unhealthy). No restart loop found in source; the earlier PID churn was a concurrent Hermes session.

## C. FIXED THIS SESSION
1. keymgmt show_key_detail -> show_detail (3 sites) — resurrects dead detail view
2. Routes strategy toggle key 's' -> 'g' — unshadows global Settings

## D. RECOMMENDED (pending your go)
1. main.go: move retry_after capture into 429 branch (A1)
2. main.go: gauges Set() not Inc() (A2)
3. main.go: health_transition on background recovery (A3)
4. main.go: skip Content-Length in buildRequest header copy (A7)
5. main.go: validate strategy value (A10)
6. main.go: drop dead RouteHealth.Keys field (A9)
7. Resolve Settings read-only vs editable spec conflict (B3)
