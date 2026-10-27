# RouteOps TUI — Complete Screen Design Spec

## Overview (Default Screen)
```
╭──────────────────────────────────────────────────────────────────────────────────────────╮
│ ● LIVE   Router: ONLINE   PID: 26320   Uptime: 2h 15m ✓ UP   Port: 8643              18:42:15 │
├──────────────────────────────────────────────────────────────────────────────────────────┤
│ ┌ LAST COMPLETED REQUEST ──────────────────────────────────────────────────────────────┐ │
│ │ Preference: nemotron                                                                 │ │
│ │ nvidia ── 429 ──→ zydit-v1 ── 200                                                    │ │
│ │ Model: nvidia/nemotron-3-ultra-550b-a55b                                             │ │
│ │ Status: success (200)                                                                │ │
│ │ Latency: 1.2s                                                                        │ │
│ └──────────────────────────────────────────────────────────────────────────────────────┘ │
│ ┌ PROVIDER HEALTH ─────────────────────────────────────────────────────────────────────┐ │
│ │ ⚠ Provider-level health from /health — not per-model/key                             │ │
│ │                                                                                       │ │
│ │ nvidia        ████████████  HEALTHY       fails:0          —  models:80  keys:3       │ │
│ │ zydit-v1      ████████░░░░  COOLDOWN      fails:2    45s   models:81  keys:3       │ │
│ │ zydit-v2      ████████████  HEALTHY       fails:0          —  models:81  keys:3       │ │
│ │                                                                                       │ │
│ │ Total: 3  OK: 2  Degraded: 1                                                         │ │
│ └──────────────────────────────────────────────────────────────────────────────────────┘ │
│ ┌ LIVE EVENTS ─────────────────────────────────────────────────────────────────────────┐ │
│ │ 18:41:52  nvidia / nvidia/nemotron-3-ultra-550b-a55b     ATTEMPT                     │ │
│ │ 18:41:54  → fallback zydit-v1                                                          │ │
│ │ 18:41:55  zydit-v1 / nvidia/nemotron-3-ultra-550b-a55b     200 OK                    │ │
│ │ 18:41:58  nvidia / deepseek-ai/deepseek-v4.1-flash       ATTEMPT                     │ │
│ │ 18:42:00  zydit-v2 / deepseek-ai/deepseek-v4.1-flash       200 OK                    │ │
│ └──────────────────────────────────────────────────────────────────────────────────────┘ │
│ ┌ SYSTEM ──────────────────────────────────────────────────────────────────────────────┐ │
│ │ Router ● ONLINE    Hermes ● RUNNING    Memory 71%    Disk 89%                         │ │
│ │ Uptime: 2h 15m   ✓ UP                                                                  │ │
│ └──────────────────────────────────────────────────────────────────────────────────────┘ │
│ [p] Providers  [r] Routes  [y] History  [d] Diagnostics  [s] Settings  [k] API Keys  [q] Quit  [Space] Pause │
│ EXTERNAL OBSERVER — READ-ONLY  |  Last=completed  |  No Hermes correlation  |  Active route NOT visible  |  Key-level health NOT visible  |  Production preference fallback unobserved │
╰──────────────────────────────────────────────────────────────────────────────────────────╯
```

**Keys:** `p`/`r`/`y`/`d`/`s`/`k` = screens, `q`/`Esc` = quit, `Space` = pause

---

## Providers Screen (`p`)
### List View
```
╭──────────────────────────────────────────────────────────────────────────────────────────╮
│ ● PROVIDERS  [3 providers]  [Enter=detail]  [h/Esc] Back                                  │
├──────────────────────────────────────────────────────────────────────────────────────────┤
│ ▶ nvidia        ████████████  HEALTHY       fails:0  models:80  keys:3                    │
│   zydit-v1      ████████░░░░  COOLDOWN      fails:2  models:81  keys:3                    │
│   zydit-v2      ████████████  HEALTHY       fails:0  models:81  keys:3                    │
│                                                                                           │
│   ↑↓ 1-3 of 3                                                                            │
╰──────────────────────────────────────────────────────────────────────────────────────────╯
```
**Keys:** `↑/↓` = navigate, `Enter` = detail, `h`/`Esc` = back

### Detail View (`Enter` on provider)
```
╭──────────────────────────────────────────────────────────────────────────────────────────╮
│ ┌ DETAIL: nvidia ──────────────────────────────────────────────────────────────────────┐ │
│ │ Status: HEALTHY                                                                       │ │
│ │ Failures: 0                                                                           │ │
│ │ Catalog models: 80                                                                    │ │
│ │ Base URL: https://integrate.api.nvidia.com/v1                                         │ │
│ │                                                                                       │ │
│ │ KEY POOLS:                                                                            │ │
│ │   [0] NVIDIA_API_KEY_1                                                                │ │
│ │   [1] NVIDIA_API_KEY_2                                                                │ │
│ │   [2] NVIDIA_API_KEY_3                                                                │ │
│ │                                                                                       │ │
│ │ KEY POOL HEALTH (from /health + config):                                              │ │
│ │   [0] NVIDIA_API_KEY_1 ████████████  HEALTHY                                         │ │
│ │   [1] NVIDIA_API_KEY_2 ████████████  HEALTHY                                         │ │
│ │   [2] NVIDIA_API_KEY_3 ████████████  HEALTHY                                         │ │
│ │                                                                                       │ │
│ │ RECENT EVENTS:                                                                        │ │
│ │   18:41:52  route_attempt  ATTEMPT                                                    │ │
│ │   18:41:54  route_fallback  fallback zydit-v1                                        │ │
│ │   18:41:55  route_result    200 OK                                                   │ │
│ └──────────────────────────────────────────────────────────────────────────────────────┘ │
```
**Keys:** `↑/↓` = scroll detail, `h`/`Esc` = back to list

---

## Routes Screen (`r`)
### Matrix View (Preference Tabs)
```
╭──────────────────────────────────────────────────────────────────────────────────────────╮
│ ROUTES  [kimi-k3] [glm-5.3] [nemotron] [deepseek-v4.1-flash]  [Tab=next pref]  [←/→=scroll]  [h/Esc=back] │
├──────────────────────────────────────────────────────────────────────────────────────────┤
│ ┌ nemotron ────────────────────────────────────────────────────────────────────────────┐ │
│ │ Model                        nvidia          zydit-v1         zydit-v2                 │ │
│ ├──────────────────────────────────────────────────────────────────────────────────────┤ │
│ │ nvidia/nemotron-3-ultra...   ●               ●               ●                        │ │
│ │                                                                                       │ │
│ │ Legend: ●=healthy  ◐=degraded  ○=unknown  ·=not configured                           │ │
│ └──────────────────────────────────────────────────────────────────────────────────────┘ │
│ ←→ 1-3 of 3                                                                             │
╰──────────────────────────────────────────────────────────────────────────────────────────╯
```
**Keys:** `Tab` = cycle preference, `←/→` = horizontal scroll, `h`/`Esc` = back

---

## History Screen (`y`)
### Table View (Filterable)
```
╭──────────────────────────────────────────────────────────────────────────────────────────╮
│ ● HISTORY  [ALL]  [f=filter]  [Enter=detail]  [h/Esc=back]                               │
├──────────────────────────────────────────────────────────────────────────────────────────┤
│ Time      Type                 Provider       Model                      Status  Outcome  │
├──────────────────────────────────────────────────────────────────────────────────────────┤
│ ▶ 18:41:52  route_attempt      nvidia         nemotron-3-ultra...       ATTEMPT           │
│   18:41:54  route_fallback     zydit-v1       fallback                  FALLBACK          │
│   18:41:55  route_result       zydit-v1       nemotron-3-ultra...       200    OK         │
│   18:41:58  route_attempt      nvidia         deepseek-v4.1-flash       ATTEMPT           │
│   18:42:00  route_result       zydit-v2       deepseek-v4.1-flash       200    OK         │
│   18:42:03  route_cooldown     nvidia         cooldown                  60s               │
│   18:42:05  provider_health    zydit-v1       health                    DEGRADED          │
│                                                                                           │
│   ↑↓ 1-7 of 247                                                                          │
╰──────────────────────────────────────────────────────────────────────────────────────────╯
```
**Filters (press `f` to cycle):** ALL → ATTEMPTS → RESULTS → FALLBACKS → COOLDOWNS → HEALTH → ERRORS

### Detail View (`Enter` on event)
```
╭──────────────────────────────────────────────────────────────────────────────────────────╮
│ ┌ EVENT DETAIL ────────────────────────────────────────────────────────────────────────┐ │
│ │ Timestamp: 2026-10-14T18:41:55.123456+00:00                                          │ │
│ │ Event Type: route_result                                                               │ │
│ │ Request ID: req_abc123                                                                 │ │
│ │ Provider: zydit-v1                                                                     │ │
│ │ Model: nvidia/nemotron-3-ultra-550b-a55b                                              │ │
│ │ Preference: nemotron                                                                   │ │
│ │ Context: request                                                                       │ │
│ │ Status Code: 200                                                                       │ │
│ │ Latency: 1.2s                                                                          │ │
│ │ Outcome: OK                                                                            │ │
│ │ Error: (none)                                                                          │ │
│ └──────────────────────────────────────────────────────────────────────────────────────┘ │
```

---

## Diagnostics Screen (`d`)
### Tab 1: Connectivity
```
╭──────────────────────────────────────────────────────────────────────────────────────────╮
│ DIAGNOSTICS  [Connectivity] [Latency] [Errors] [Binary]  [1-4=tabs]  [h/Esc=back]        │
├──────────────────────────────────────────────────────────────────────────────────────────┤
│ CONNECTIVITY CHECKS                                                                       │
│ ═══════════════════════                                                                   │
│   /health endpoint:     ● REACHABLE                                                       │
│   /v1/models endpoint:  ● REACHABLE                                                       │
│   config.json:          ● READABLE                                                        │
│   router-events.jsonl:  ● READABLE                                                        │
│   router process:       ● RUNNING                                                         │
│     PID: 26320                                                                           │
│     Identity: PRODUCTION                                                                  │
│     Binary: /home/CraftedWorkflowsOS/router/router                                       │
│     CWD: /home/CraftedWorkflowsOS/router                                                  │
│     Uptime: 2h 15m                                                                       │
│                                                                                           │
│ ROUTER HEALTH ENDPOINT RESPONSE                                                           │
│ ═════════════════════════════                                                             │
│   nvidia       : HEALTHY   (failures=0, models=80)                                       │
│   zydit-v1     : COOLDOWN  (failures=2, models=81)                                       │
│   zydit-v2     : HEALTHY   (failures=0, models=81)                                       │
╰──────────────────────────────────────────────────────────────────────────────────────────╯
```

### Tab 2: Latency Drill-Down
```
╭──────────────────────────────────────────────────────────────────────────────────────────╮
│ DIAGNOSTICS  [Connectivity] [Latency] [Errors] [Binary]  [1-4=tabs]  [h/Esc=back]        │
├──────────────────────────────────────────────────────────────────────────────────────────┤
│ LATENCY DRILL-DOWN (from JSONL route_result events)                                       │
│ ══════════════════════════════════════════                                                │
│   Total requests: 142                                                                     │
│   Min: 45ms                                                                               │
│   P50: 890ms                                                                              │
│   P90: 2.1s                                                                               │
│   P99: 4.8s                                                                               │
│   Max: 8.2s                                                                               │
│                                                                                           │
│ PER-PROVIDER LATENCY                                                                       │
│ ═══════════════════════                                                                    │
│   nvidia:                                                                                 │
│     Count: 45  Min: 45ms  P50: 720ms  P90: 1.8s  Max: 5.2s                              │
│   zydit-v1:                                                                               │
│     Count: 52  Min: 67ms  P50: 1.1s  P90: 2.8s  Max: 8.2s                               │
│   zydit-v2:                                                                               │
│     Count: 45  Min: 89ms  P50: 980ms  P90: 2.4s  Max: 6.7s                              │
╰──────────────────────────────────────────────────────────────────────────────────────────╯
```

### Tab 3: Errors
```
╭──────────────────────────────────────────────────────────────────────────────────────────╮
│ DIAGNOSTICS  [Connectivity] [Latency] [Errors] [Binary]  [1-4=tabs]  [h/Esc=back]        │
├──────────────────────────────────────────────────────────────────────────────────────────┤
│ ERROR ANALYSIS (from JSONL events)                                                        │
│ ═══════════════════════════                                                               │
│ Total errors: 12                                                                          │
│ Total fallbacks: 8                                                                        │
│ Total cooldowns: 5                                                                        │
│                                                                                           │
│ TOP ERRORS                                                                                 │
│ ═════════════                                                                              │
│   [4x] upstream SSE application error: Service temporarily overloaded                    │
│   [3x] context canceled                                                                   │
│   [2x] rate limit exceeded                                                                │
│   [2x] connection timeout                                                                 │
│   [1x] invalid model                                                                      │
│                                                                                           │
│ RECENT FALLBACKS                                                                           │
│ ═════════════════                                                                          │
│   18:41:54  nemotron → nemotron  via zydit-v1                                            │
│   18:41:58  deepseek → deepseek  via zydit-v2                                            │
│   18:39:12  glm-5.3 → glm-5.3  via zydit-v2                                              │
│                                                                                           │
│ RECENT COOLDOWNS                                                                           │
│ ══════════════════                                                                         │
│   18:42:03  nvidia  60s                                                                   │
│   18:38:15  zydit-v1  120s                                                                │
╰──────────────────────────────────────────────────────────────────────────────────────────╯
```

### Tab 4: Binary Verification
```
╭──────────────────────────────────────────────────────────────────────────────────────────╮
│ DIAGNOSTICS  [Connectivity] [Latency] [Errors] [Binary]  [1-4=tabs]  [h/Esc=back]        │
├──────────────────────────────────────────────────────────────────────────────────────────┤
│ BINARY VERIFICATION                                                                       │
│ ══════════════════════                                                                   │
│ PID: 26320                                                                                │
│ Identity: PRODUCTION                                                                      │
│ Executable: /home/CraftedWorkflowsOS/router/router                                       │
│ Working Dir: /home/CraftedWorkflowsOS/router                                             │
│ Uptime: 2h 15m                                                                            │
│                                                                                           │
│ SIZE CHECK                                                                                │
│ ══════════════                                                                             │
│   Binary size: 13.8MB  ✓ MATCHES ~10MB (production)                                      │
│                                                                                           │
│ PATH VERIFICATION                                                                         │
│ ══════════════════                                                                         │
│   Exe path:  ✓ /home/CraftedWorkflowsOS/router/router                                    │
│   CWD:       ✓ /home/CraftedWorkflowsOS/router                                           │
│                                                                                           │
│ RESULT: PRODUCTION ROUTER VERIFIED ✓                                                     │
╰──────────────────────────────────────────────────────────────────────────────────────────╯
```
**Keys:** `1-4` = tabs, `←/→`/`h`/`l` = switch tabs, `↑/↓` = scroll

---

## Settings Screen (`s`)
### Tab 1: Monitoring
```
╭──────────────────────────────────────────────────────────────────────────────────────────╮
│ SETTINGS  [Monitoring] [Display] [Thresholds] [Advanced]  [1-4=tabs]  [h/Esc=back]       │
├──────────────────────────────────────────────────────────────────────────────────────────┤
│ MONITORING SETTINGS                                                                       │
│ ══════════════════════                                                                    │
│ ▶ Auto-refresh: ON  (true/false)                                                          │
│   Refresh interval: 2s  (seconds)                                                         │
│                                                                                           │
│ WORKER POLL INTERVALS (read-only)                                                         │
│ ═════════════════════════════════════════════════════════════════════════════════════════════ │
│   JSONL tail:     1.5s                                                                    │
│   /health:        2.5s                                                                    │
│   /v1/models:     30s                                                                     │
│   config.json:    60s                                                                     │
│   Process:        10s                                                                     │
╰──────────────────────────────────────────────────────────────────────────────────────────╯
```
**Keys:** `1-4` = tabs, `↑/↓` = select, `Enter` = edit, `Esc` = cancel

### Tab 2: Display
```
╭──────────────────────────────────────────────────────────────────────────────────────────╮
│ SETTINGS  [Monitoring] [Display] [Thresholds] [Advanced]  [1-4=tabs]  [h/Esc=back]       │
├──────────────────────────────────────────────────────────────────────────────────────────┤
│ DISPLAY SETTINGS                                                                          │
│ ══════════════════════                                                                    │
│ ▶ Event density: normal  (compact/normal/verbose)                                        │
│   Show disclaimers: ON  (true/false)                                                      │
│                                                                                           │
│ COLOR THEME: Boxed (fixed)                                                                │
│ ══════════════════════════════════════════════════════════════════════════════════════════════ │
│   HEALTHY:    Green  (curses.color_pair(2))                                              │
│   COOLDOWN:   Yellow (curses.color_pair(3))                                              │
│   HARD-FAILED: Red   (curses.color_pair(4))                                              │
│   WARNING:    Yellow (curses.color_pair(3))                                              │
│   INFO:       Cyan   (curses.color_pair(5))                                              │
╰──────────────────────────────────────────────────────────────────────────────────────────╯
```

### Tab 3: Thresholds
```
╭──────────────────────────────────────────────────────────────────────────────────────────╮
│ SETTINGS  [Monitoring] [Display] [Thresholds] [Advanced]  [1-4=tabs]  [h/Esc=back]       │
├──────────────────────────────────────────────────────────────────────────────────────────┤
│ THRESHOLDS                                                                                │
│ ══════════════════════                                                                    │
│   Not yet configurable in v0.1                                                            │
│                                                                                           │
│ FUTURE:                                                                                   │
│   Cooldown warning threshold                                                              │
│   Failure count alert threshold                                                           │
│   Latency P99 alert threshold                                                             │
│   Error rate alert threshold                                                              │
╰──────────────────────────────────────────────────────────────────────────────────────────╯
```

### Tab 4: Advanced
```
╭──────────────────────────────────────────────────────────────────────────────────────────╮
│ SETTINGS  [Monitoring] [Display] [Thresholds] [Advanced]  [1-4=tabs]  [h/Esc=back]       │
├──────────────────────────────────────────────────────────────────────────────────────────┤
│ ADVANCED SETTINGS                                                                         │
│ ══════════════════════                                                                    │
│ ▶ Config path: /home/CraftedWorkflowsOS/router/config.json                                │
│   Log level: INFO  (DEBUG/INFO/WARNING/ERROR)                                            │
│   Persist settings: ON  (true/false)                                                      │
│                                                                                           │
│ READ-ONLY PATHS                                                                           │
│ ══════════════════════                                                                    │
│   Router events:  /home/CraftedWorkflowsOS/router/router-events.jsonl                    │
│   Router config:  /home/CraftedWorkflowsOS/router/config.json                            │
│   Router log:     /home/CraftedWorkflowsOS/router/router.log                             │
│   Router binary:  /home/CraftedWorkflowsOS/router/router                                 │
╰──────────────────────────────────────────────────────────────────────────────────────────╯
```

---

## API Keys Screen (`k`)
### List View
```
╭──────────────────────────────────────────────────────────────────────────────────────────╮
│ ● API KEYS  [3 providers]  [Enter=detail]  [h/Esc=back]                                   │
├──────────────────────────────────────────────────────────────────────────────────────────┤
│ ▶ nvidia        ████████████  HEALTHY       fails:0  models:80  keys:3                    │
│   zydit-v1      ████████░░░░  COOLDOWN      fails:2  models:81  keys:3                    │
│   zydit-v2      ████████████  HEALTHY       fails:0  models:81  keys:3                    │
│                                                                                           │
│   ↑↓ 1-3 of 3                                                                            │
╰──────────────────────────────────────────────────────────────────────────────────────────╯
```

### Detail View (`Enter` on provider)
```
╭──────────────────────────────────────────────────────────────────────────────────────────╮
│ ┌ KEY DETAIL: nvidia ──────────────────────────────────────────────────────────────────┐ │
│ │ KEY POOL CONFIGURATION (from config.json)                                             │ │
│ │ ══════════════════════════════════════════════════════════════════════════════════════════ │
│ │   [0] NVIDIA_API_KEY_1                                                                │ │
│ │   [1] NVIDIA_API_KEY_2                                                                │ │
│ │   [2] NVIDIA_API_KEY_3                                                                │ │
│ │                                                                                       │ │
│ │ KEY POOL HEALTH (from /health + config)                                               │ │
│ │ ══════════════════════════════════════════════════════════════════════════════════════════════ │
│ │   [0] NVIDIA_API_KEY_1 ████████████  HEALTHY                                         │ │
│ │   [1] NVIDIA_API_KEY_2 ████████████  HEALTHY                                         │ │
│ │   [2] NVIDIA_API_KEY_3 ████████████  HEALTHY                                         │ │
│ │                                                                                       │ │
│ │ ACTIONS (Tier 2 - requires CEO approval)                                              │ │
│ │ ═══════════════════════════════════════════════════════════════════════════════════════════════ │
│ │   [r] Rotate key (create new, retire old)                                             │ │
│ │   [d] Deactivate key                                                                  │ │
│ │   [a] Activate key                                                                    │ │
│ │                                                                                       │ │
│ │ COOLDOWNS                                                                              │ │
│ │ ════════════════════════════════════════════════════════════════════════════════════════════════ │
│ │   No active cooldowns                                                                 │ │
│ │                                                                                       │ │
│ │ RECENT EVENTS                                                                          │ │
│ │ ═════════════════════════════════════════════════════════════════════════════════════════════════ │
│ │   18:41:52  route_attempt  ATTEMPT                                                    │ │
│ │   18:41:55  route_result   200 OK                                                    │ │
│ └──────────────────────────────────────────────────────────────────────────────────────┘ │
```

---

## Global Key Map

| Key | Action |
|-----|--------|
| `p` | Providers screen |
| `r` | Routes screen |
| `y` | History screen |
| `d` | Diagnostics screen |
| `s` | Settings screen |
| `k` | API Keys screen |
| `q` | Quit (from Overview) |
| `Esc` | Back / Close detail |
| `Space` | Pause/Resume |
| `Tab` | Cycle preferences (Routes) |
| `1-4` | Switch tabs (Diagnostics/Settings) |
| `←/→` | Horizontal scroll / tab switch |
| `↑/↓` | Navigate lists |
| `Enter` | Open detail / toggle setting |
| `f` | Cycle history filter |
| `h` | Back / Close detail |

---

## Color Palette (curses.color_pair)

| Pair | Color | Use |
|------|-------|-----|
| 1 | White | Borders, titles, default |
| 2 | Green | HEALTHY, uptime, success |
| 3 | Yellow | COOLDOWN, warning |
| 4 | Red | HARD-FAILED, error |
| 5 | Cyan | Info, timestamps |
| 6 | Magenta | Highlight |
| 7 | Blue | Debug |
| 8 | White/Blue | Status bar |
| 9 | Black/White | Selected item |

---

## Architecture Notes

- **Read-only by default** — No write operations without Tier 2 approval
- **Provider-agnostic** — No hardcoded provider names in code
- **Health = provider-level only** — Key health inferred, not observed
- **Live events = last 5 from JSONL** — Full history in History screen
- **Fallback chain = reconstructed from JSONL** — Shows actual status codes
- **Uptime from /proc** — Fallback to btime on Android
- **Config worker = immediate first poll** — Keys show immediately on startup