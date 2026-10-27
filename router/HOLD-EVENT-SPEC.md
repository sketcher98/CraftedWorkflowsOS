# Hold-and-Serve Observability — Implementation Spec (Steps 2-3)

Status: QUEUED 2026-10-27. Do NOT implement until router/main.go strategy
refactor (strategy.Registry / LKGPStrategy imports) is committed and stable —
editing main.go now = merge collision. Settings-screen half (Step 1) already
shipped: commit after dede525/37e8bd3.

Problem: holdAndServe() writes only log.Printf lines — zero JSONL events.
RouteOps reads JSONL exclusively, so a parked request looks identical to a
hang in Overview/History.

## Step 2: Router emits request_held JSONL event (main.go)

Same emission pattern as route_attempt (one writeEvent/appendEvent call).
Emit at ALL THREE exits of holdAndServe(), only after the request actually
parked (held_ms > 0):

  {
    "event": "request_held",
    "request_id": <current request id>,
    "ts": <RFC3339>,
    "model": <requested preference>,
    "max_hold_seconds": <cfg.Retry.MaxHoldSeconds>,
    "held_ms": <actual ms parked>,
    "outcome": "served" | "max_hold_exceeded" | "client_cancelled"
  }

Exit mapping:
  - tick.C cooldown expired (return true)  -> outcome="served" — emit BEFORE returning
  - holdDeadline.C (return false)          -> outcome="max_hold_exceeded"
  - r.Context().Done() (return false)      -> outcome="client_cancelled"

TUI joins on request_id: the route_result that follows a "served" hold already
exists — no new correlation machinery needed.

## Step 3: TUI parses request_held (routeops/)

1. models/request.py — add held_ms, hold_outcome fields (from_json passthrough)
2. runtime/__init__.py — parse event_type "request_held" (mirror route_cooldown path)
3. screens/overview_curses.py — timeline gets a yellow segment line:
   "held 12.3s -> re-walk" (curses.color_pair(3), YELLOW) between the last
   route_cooldown and the next route_attempt
4. screens/history_curses.py — add "request_held" to the fallback-event filter
   set (~line 90)
5. screens/diagnostics_curses.py — hold count + avg/max held_ms summary row

## Verification gate (do not skip — Oct 24 lesson)

1. Rebuild test-env binaries AFTER editing main.go
2. Test-env acceptance: all mocks 503 -> request kimi-k3 -> park -> flip one
   mock to success mid-hold -> client gets 200
3. grep request_held test-env/router-events.jsonl MUST show outcome=served
   with held_ms ~= 10000
4. Headless TUI render (MockTerm per integrations/routeops-tui skill) MUST
   show the yellow held line — no "code compiles" claims
5. Only then: port to prod main.go, go build, startr swap in a user-announced
   maintenance window, live smoke glm-5.3 stream:true

Constraints: continuation stitching stays BANNED — this spec adds telemetry
only, no retry/continuation logic. NEVER touch prod router without approval.