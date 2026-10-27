# RouteOps TUI — Screen Design Specification

## Overview
- **Theme**: Boxed (fixed) — Unicode box drawing (┌┐└┘├┤─│)
- **Colors**: curses standard pairs — Green(2), Yellow(3), Red(4), Cyan(5), Magenta(6), Blue(7), White/Blue(8), Black/White(9)
- **Navigation**: Single-key global shortcuts, `Esc`/`h` back, `q` quit from Overview
- **Density**: Compact by default, configurable

---

## Screen Map

```
┌─────────────────────────────────────────────────────────────┐
│  OVERVIEW (default)           │  [p] PROVIDERS              │
├─────────────────────────────────────────────────────────────┤
│  LAST COMPLETED REQUEST       │  Provider list (scrollable) │
│  PROVIDER HEALTH              │  Enter → Detail drawer      │
│  LIVE EVENTS                  │  Keys, recent events        │
│  SYSTEM                       │  [h/Esc] Back               │
├─────────────────────────────────────────────────────────────┤
│  [r] ROUTES                   │  [y] HISTORY                │
├─────────────────────────────────────────────────────────────┤
│  Preference tabs (Tab)        │  Filterable event table     │
│  Horizontal scroll (←/→)      │  7 filters (f)              │
│  Provider×Model matrix        │  Enter → Detail             │
│  Separate health column       │  [h/Esc] Back               │
├─────────────────────────────────────────────────────────────┤
│  [d] DIAGNOSTICS              │  [s] SETTINGS               │
├─────────────────────────────────────────────────────────────┤
│  4 tabs (1-4):                │  4 tabs (1-4):              │
│  1 Connectivity               │  1 Monitoring               │
│  2 Latency drill-down         │  2 Display                  │
│  3 Errors                     │  3 Thresholds               │
│  4 Binary verification        │  4 Advanced                 │
│  ↑/↓ scroll                   │  ↑/↓ edit, Enter=save       │
├─────────────────────────────────────────────────────────────┤
│  [k] API KEYS                 │  GLOBAL KEYS                │
├─────────────────────────────────────────────────────────────┤
│  Provider list                │  q/Esc = Quit/Back          │
│  Enter → Key detail           │  Space = Pause/Resume       │
│  Health bars, actions         │  Tab = Cycle preference     │
│  Cooldowns, recent events     │  ←/→ = Horizontal scroll    │
└─────────────────────────────────────────────────────────────┘
```

---

## 1. OVERVIEW (Default)

### Layout
```
┌ LIVE   Router: ONLINE   PID: 12345   Up: 2h 15m ✓ UP   Port: 8643    18:30:45 ┐
├────────────────────────────────────────────────────────────────────────────────┤
┌ LAST COMPLETED REQUEST ────────────────────────────────────────────────────────┐
│ Preference: nemotron                                                           │
│ nvidia ── 429 ──→ zydit-v1 ── 200                                             │
│ Model: nvidia/nemotron-3-ultra-550b-a55b                                       │
│ Status: success (200)                                                          │
│ Latency: 1.2s                                                                  │
└────────────────────────────────────────────────────────────────────────────────┘
┌ PROVIDER HEALTH ───────────────────────────────────────────────────────────────┐
│ ⚠ Provider-level health from /health — not per-model/key                       │
│                                                                                │
│ nvidia        ████████████  HEALTHY       fails:0          —  models:80  keys:3 │
│ zydit-v1      ████████░░░░  COOLDOWN      fails:2    expired  models:81  keys:3 │
│ zydit-v2      ████████████  HARD-FAILED   fails:5    45s     models:81  keys:3 │
│                                                                                │
│ Total: 3  OK: 1  Degraded: 1  Hard-failed: 1                                   │
└────────────────────────────────────────────────────────────────────────────────┘
┌ LIVE EVENTS ───────────────────────────────────────────────────────────────────┐
│ 18:30:10  nvidia / nvidia/nemotron-3-ultra-550b-a55b     OK                    │
│ 18:30:25  → fallback zydit-v1                                                     │
│ 18:30:26  zydit-v1 / nvidia/nemotron-3-ultra-550b-a55b     OK                  │
│ 18:30:40  → cooldown 60s                                                         │
│ 18:30:45  nvidia / deepseek-ai/deepseek-v4.1-flash     ATTEMPT                  │
└────────────────────────────────────────────────────────────────────────────────┘
┌ SYSTEM ────────────────────────────────────────────────────────────────────────┐
│ Router ● ONLINE    Hermes ● RUNNING    Memory 71%    Disk 89%                  │
│ Uptime: 2h 15m   ✓ UP                                                          │
└────────────────────────────────────────────────────────────────────────────────┘
[p] Providers  [r] Routes  [y] History  [d] Diagnostics  [s] Settings  [k] API Keys  [q] Quit  [Space] Pause
EXTERNAL OBSERVER — READ-ONLY  |  Last=completed  |  No Hermes correlation  |  Active route NOT visible  |  Key-level health NOT visible  |  Production preference fallback unobserved
```

### Components
| Region | Content | Colors |
|--------|---------|--------|
| Header | Live status, PID, uptime, port, timestamp | Green (live), Cyan (timestamp) |
| Last Completed | Preference, fallback chain (── 429 ──→), model, status, latency | Green=success, Red=failed |
| Provider Health | Bar (████████░░), status, failures, cooldown, models, keys | Green/Yellow/Red bars |
| Live Events | Timestamp, provider/model, outcome, fallback/cooldown markers | White=attempt, Green=OK, Red=error |
| System | Router status, Hermes status, memory, disk, uptime | Green=online, Red=offline |
| Key Help | Navigation shortcuts | Cyan (dim) |
| Disclaimer | Mandatory read-only notice | Blue/White (dim) |

---

## 2. PROVIDERS

### List View
```
● PROVIDERS  [3 providers]  [Enter=detail]  [h/Esc] Back
────────────────────────────────────────────────────────────
▶ nvidia        ████████████  HEALTHY       fails:0          —  models:80  keys:3
  zydit-v1      ████████░░░░  COOLDOWN      fails:2    expired  models:81  keys:3
  zydit-v2      ████████████  HARD-FAILED   fails:5    45s     models:81  keys:3
  ↑↓ 1-3 of 3
```

### Detail Drawer (Enter)
```
┌ DETAIL: nvidia ────────────────────────────────────────────────────────────────┐
│ Status: HEALTHY                                                                │
│ Failures: 0                                                                    │
│ Catalog models: 80                                                             │
│                                                                                │
│ KEY POOLS:                                                                     │
│   [0] NVIDIA_API_KEY_1                                                         │
│   [1] NVIDIA_API_KEY_2                                                         │
│   [2] NVIDIA_API_KEY_3                                                         │
│                                                                                │
│ Base URL: https://integrate.api.nvidia.com/v1                                  │
│                                                                                │
│ RECENT EVENTS:                                                                 │
│   18:30:10  route_attempt     ATTEMPT                                          │
│   18:30:11  route_result      OK                                               │
│   18:30:25  route_fallback    → fallback zydit-v1                              │
└────────────────────────────────────────────────────────────────────────────────┘
  ↑↓ 1-12 of 12
```

### Navigation
| Key | Action |
|-----|--------|
| `↑/↓` | Scroll list / scroll detail |
| `Enter` | Open detail drawer |
| `h` / `Esc` | Close detail / back to overview |

---

## 3. ROUTES

### Layout
```
ROUTES  [kimi-k3]  [glm-5.3]  [nemotron]  [deepseek-v4.1-flash]  [Tab=next pref]  [←/→=scroll]  [h/Esc=back]
──────────────────────────────────────────────────────────────────────────────────────────────────────────────
│ Model                    nvidia          zydit-v1         zydit-v2                                          │
├──────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ moonshotai/kimi-k3       ●                ●                ●                                                 │
│ z-ai/glm-5.3             ●                ●                ●                                                 │
│ nvidia/nemotron-3...     ●                ◐                ○                                                 │
│ deepseek-ai/deepse...    ○                ●                ●                                                 │
├──────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ Legend: ●=healthy  ◐=degraded  ○=unknown  ·=not configured                                              │
│ ←→ 1-3 of 3                                                                                               │
```

### Components
| Element | Description |
|---------|-------------|
| Tab Bar | Preference names, active highlighted, `Tab` cycles |
| Matrix | Rows=models, Cols=providers, `←/→` horizontal scroll |
| Symbols | ● healthy, ◐ degraded, ○ unknown, · not configured |
| Health Column | Separate column showing provider health summary |

### Navigation
| Key | Action |
|-----|--------|
| `Tab` | Next preference |
| `←/→` | Horizontal scroll |
| `h` / `Esc` | Back to overview |

---

## 4. HISTORY

### Table View
```
● HISTORY  [ALL]  [f=filter]  [Enter=detail]  [h/Esc=back]
──────────────────────────────────────────────────────────────────────────────────────────────────
Time       Type                 Provider       Model                          Status   Outcome
──────────────────────────────────────────────────────────────────────────────────────────────────
▶ 18:30:10 route_attempt        nvidia         nvidia/nemotron-3-ultra...     ATTEMPT  
  18:30:11 route_result         nvidia         nvidia/nemotron-3-ultra...     200      OK
  18:30:25 route_fallback       zydit-v1       nvidia/nemotron-3-ultra...     429      FALLBACK
  18:30:26 route_result         zydit-v1       nvidia/nemotron-3-ultra...     200      OK
  18:30:40 route_cooldown       nvidia         nvidia/nemotron-3-ultra...     60s      COOLDOWN
  ↑↓ 1-5 of 1,247
```

### Detail View (Enter)
```
┌ EVENT DETAIL ──────────────────────────────────────────────────────────────────┐
│ Timestamp: 2026-10-14T18:30:10.123456Z                                          │
│ Event Type: route_attempt                                                       │
│ Request ID: req_abc123                                                          │
│ Provider: nvidia                                                                │
│ Model: nvidia/nemotron-3-ultra-550b-a55b                                        │
│ Preference: nemotron                                                            │
│ Context: request                                                                │
│ Status Code: 429                                                                │
│ Outcome: fallback                                                               │
│ Latency: —                                                                      │
│ Preference Fallback: nemotron → glm-5.3                                         │
└──────────────────────────────────────────────────────────────────────────────────┘
```

### Filters (f key cycles)
| Filter | Events Shown |
|--------|--------------|
| `ALL` | All events |
| `ATTEMPTS` | route_attempt only |
| `RESULTS` | route_result only |
| `FALLBACKS` | route_fallback, route_cooldown, route_quarantined, route_recovered |
| `COOLDOWNS` | route_cooldown only |
| `HEALTH` | provider_health only |
| `ERRORS` | route_result with error |

---

## 5. DIAGNOSTICS

### Tab 1: Connectivity
```
DIAGNOSTICS  [Connectivity]  [Latency]  [Errors]  [Binary]  [1-4=tabs]  [h/Esc=back]
────────────────────────────────────────────────────────────────────────────────────────
┌ Connectivity ─────────────────────────────────────────────────────────────────────────┐
│ CONNECTIVITY CHECKS                                                                   │
│ /health endpoint:     ● REACHABLE                                                     │
│ /v1/models endpoint:  ● REACHABLE                                                     │
│ config.json:          ● READABLE                                                      │
│ router-events.jsonl:  ● READABLE                                                      │
│ router process:       ● RUNNING                                                       │
│   PID: 12345                                                                          │
│   Identity: PRODUCTION                                                                │
│   Binary: /home/user/router/router                                                    │
│   CWD: /home/user/router                                                              │
│   Uptime: 2h 15m                                                                      │
│                                                                                       │
│ ROUTER HEALTH ENDPOINT RESPONSE                                                       │
│ nvidia:       HEALTHY  (failures=0, models=80)                                        │
│ zydit-v1:     COOLDOWN  (failures=2, models=81)                                       │
│ zydit-v2:     HEALTHY  (failures=0, models=81)                                        │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

### Tab 2: Latency Drill-down
```
DIAGNOSTICS  [Connectivity]  [Latency]  [Errors]  [Binary]  [1-4=tabs]  [h/Esc=back]
────────────────────────────────────────────────────────────────────────────────────────
┌ Latency Drill-down ───────────────────────────────────────────────────────────────────┐
│ LATENCY DRILL-DOWN (from JSONL route_result events)                                   │
│                                                                                       │
│ Total requests: 1,247                                                                 │
│ Min: 45ms                                                                             │
│ P50: 234ms                                                                            │
│ P90: 892ms                                                                            │
│ P99: 2,145ms                                                                          │
│ Max: 8,432ms                                                                          │
│                                                                                       │
│ PER-PROVIDER LATENCY                                                                  │
│ nvidia:                                                                               │
│   Count: 523  Min: 45ms  P50: 198ms  P90: 712ms  Max: 4,211ms                        │
│ zydit-v1:                                                                             │
│   Count: 412  Min: 67ms  P50: 289ms  P90: 1,034ms  Max: 5,123ms                      │
│ zydit-v2:                                                                             │
│   Count: 312  Min: 89ms  P50: 312ms  P90: 1,201ms  Max: 8,432ms                      │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

### Tab 3: Errors
```
DIAGNOSTICS  [Connectivity]  [Latency]  [Errors]  [Binary]  [1-4=tabs]  [h/Esc=back]
────────────────────────────────────────────────────────────────────────────────────────
┌ Errors ───────────────────────────────────────────────────────────────────────────────┐
│ ERROR ANALYSIS (from JSONL events)                                                    │
│                                                                                       │
│ Total errors: 47                                                                      │
│ Total fallbacks: 23                                                                   │
│ Total cooldowns: 12                                                                   │
│                                                                                       │
│ TOP ERRORS                                                                            │
│ [12x] upstream SSE application error: Service temporarily overloaded                  │
│ [8x]  connection timeout after 120s                                                   │
│ [7x]  rate limit exceeded (429)                                                       │
│ [5x]  model not found in catalog                                                      │
│ [4x]  invalid API key                                                                 │
│                                                                                       │
│ RECENT FALLBACKS                                                                      │
│ 18:25:10  nemotron → glm-5.3  via zydit-v1                                            │
│ 18:18:45  deepseek-v4.1-flash → nemotron  via nvidia                                  │
│ 18:12:30  glm-5.3 → kimi-k3  via zydit-v2                                             │
│                                                                                       │
│ RECENT COOLDOWNS                                                                      │
│ 18:28:00  nvidia  60s                                                                 │
│ 18:20:15  zydit-v1  480s                                                              │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

### Tab 4: Binary Verification
```
DIAGNOSTICS  [Connectivity]  [Latency]  [Errors]  [Binary]  [1-4=tabs]  [h/Esc=back]
────────────────────────────────────────────────────────────────────────────────────────
┌ Binary Verification ──────────────────────────────────────────────────────────────────┐
│ PID: 12345                                                                             │
│ Identity: PRODUCTION                                                                   │
│ Executable: /home/user/router/router                                                   │
│ Working Dir: /home/user/router                                                         │
│ Uptime: 2h 15m                                                                         │
│                                                                                       │
│ SIZE CHECK                                                                            │
│ Binary size: 14.5MB  ✓ MATCHES ~10MB (production)                                     │
│                                                                                       │
│ PATH VERIFICATION                                                                     │
│ Exe path:  ✓ /home/user/router/router                                                 │
│ CWD:       ✓ /home/user/router                                                        │
│                                                                                       │
│ RESULT: PRODUCTION ROUTER VERIFIED ✓                                                  │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 6. SETTINGS

### Tab 1: Monitoring
```
SETTINGS  [Monitoring]  [Display]  [Thresholds]  [Advanced]  [1-4=tabs]  [↑/↓=edit]  [Enter=save]  [Esc=back]
────────────────────────────────────────────────────────────────────────────────────────────────────────────────
┌ Monitoring ──────────────────────────────────────────────────────────────────────────┐
│ MONITORING SETTINGS                                                                   │
│ Auto-refresh: ON  (true/false)                                                        │
│ Refresh interval: 2s  (seconds)                                                       │
│                                                                                       │
│ WORKER POLL INTERVALS (read-only)                                                     │
│ JSONL tail:     1.5s                                                                  │
│ /health:        2.5s                                                                  │
│ /v1/models:     30s                                                                   │
│ config.json:    60s                                                                   │
│ Process:        10s                                                                   │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

### Tab 2: Display
```
SETTINGS  [Monitoring]  [Display]  [Thresholds]  [Advanced]  [1-4=tabs]  [↑/↓=edit]  [Enter=save]  [Esc=back]
────────────────────────────────────────────────────────────────────────────────────────────────────────────────
┌ Display ──────────────────────────────────────────────────────────────────────────────┐
│ DISPLAY SETTINGS                                                                      │
│ Event density: normal  (compact/normal/verbose)                                       │
│ Show disclaimers: ON  (true/false)                                                    │
│                                                                                       │
│ COLOR THEME: Boxed (fixed)                                                            │
│ HEALTHY:    Green                                                                      │
│ COOLDOWN:   Yellow                                                                     │
│ HARD-FAILED: Red                                                                       │
│ WARNING:    Yellow                                                                     │
│ INFO:       Cyan                                                                       │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

### Tab 3: Thresholds
```
SETTINGS  [Monitoring]  [Display]  [Thresholds]  [Advanced]  [1-4=tabs]  [↑/↓=edit]  [Enter=save]  [Esc=back]
────────────────────────────────────────────────────────────────────────────────────────────────────────────────
┌ Thresholds ───────────────────────────────────────────────────────────────────────────┐
│ THRESHOLDS                                                                            │
│ Not yet configurable in v0.1                                                          │
│                                                                                       │
│ FUTURE:                                                                               │
│ Cooldown warning threshold                                                            │
│ Failure count alert threshold                                                         │
│ Latency P99 alert threshold                                                           │
│ Error rate alert threshold                                                            │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

### Tab 4: Advanced
```
SETTINGS  [Monitoring]  [Display]  [Thresholds]  [Advanced]  [1-4=tabs]  [↑/↓=edit]  [Enter=save]  [Esc=back]
────────────────────────────────────────────────────────────────────────────────────────────────────────────────
┌ Advanced ─────────────────────────────────────────────────────────────────────────────┐
│ ADVANCED SETTINGS                                                                      │
│ Config path: /home/user/CraftedWorkflowsOS/router/config.json                         │
│ Log level: INFO  (DEBUG/INFO/WARNING/ERROR)                                           │
│ Persist settings: ON  (true/false)                                                    │
│                                                                                       │
│ READ-ONLY PATHS                                                                       │
│ Router events:  /home/user/CraftedWorkflowsOS/router/router-events.jsonl              │
│ Router config:  /home/user/CraftedWorkflowsOS/router/config.json                      │
│ Router log:     /home/user/CraftedWorkflowsOS/router/router.log                       │
│                                                                                       │
│ Router binary:  /home/user/CraftedWorkflowsOS/router/router                           │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

### Navigation
| Key | Action |
|-----|--------|
| `1-4` | Switch tabs |
| `↑/↓` | Navigate editable fields |
| `Enter` | Enter edit mode / save |
| `Esc` | Exit edit mode / back |
| `h` / `Esc` | Back to overview |

---

## 7. API KEYS

### List View
```
● API KEYS  [3 providers]  [Enter=detail]  [h/Esc=back]
────────────────────────────────────────────────────────────────────────────────────────
▶ nvidia        ████████████  HEALTHY       fails:0          —  models:80  keys:3
  zydit-v1      ████████░░░░  COOLDOWN      fails:2    expired  models:81  keys:3
  zydit-v2      ████████████  HARD-FAILED   fails:5    45s     models:81  keys:3
  ↑↓ 1-3 of 3
```

### Detail View (Enter)
```
┌ KEY DETAIL: nvidia ─────────────────────────────────────────────────────────────────┐
│ KEY POOL CONFIGURATION (from config.json)                                            │
│                                                                                       │
│   [0] NVIDIA_API_KEY_1                                                                │
│   [1] NVIDIA_API_KEY_2                                                                │
│   [2] NVIDIA_API_KEY_3                                                                │
│                                                                                       │
│ KEY POOL HEALTH (from /health + config)                                               │
│                                                                                       │
│   [0] NVIDIA_API_KEY_1                    ████████████  HEALTHY                      │
│   [1] NVIDIA_API_KEY_2                    ████████████  HEALTHY                      │
│   [2] NVIDIA_API_KEY_3                    ████████████  HEALTHY                      │
│                                                                                       │
│ ACTIONS (Tier 2 - requires CEO approval)                                              │
│   [r] Rotate key (create new, retire old)                                             │
│   [d] Deactivate key                                                                   │
│   [a] Activate key                                                                     │
│                                                                                       │
│ COOLDOWNS                                                                             │
│   No active cooldowns                                                                  │
│                                                                                       │
│ RECENT EVENTS                                                                         │
│   18:30:10  route_attempt     ATTEMPT                                                 │
│   18:30:11  route_result      OK                                                      │
└────────────────────────────────────────────────────────────────────────────────────────┘
  ↑↓ 1-12 of 12
```

---

## Global Keybindings

| Key | Context | Action |
|-----|---------|--------|
| `p` | Any | Providers screen |
| `r` | Any | Routes screen |
| `y` | Any | History screen |
| `d` | Any | Diagnostics screen |
| `s` | Any | Settings screen |
| `k` | Any | API Keys screen |
| `q` | Overview | Quit |
| `Esc` | Subscreen | Back to Overview |
| `Esc` | Overview | Quit |
| `Space` | Any | Pause/Resume live updates |
| `Tab` | Routes | Next preference |
| `←/→` | Routes | Horizontal scroll |
| `f` | History | Cycle filter |
| `1-4` | Diagnostics/Settings | Switch tabs |
| `↑/↓` | Lists/editable | Navigate |
| `Enter` | Detail/editable | Open detail / save edit |

---

## Color Palette

| Pair | FG | BG | Usage |
|------|-----|-----|-------|
| 1 | White | Default | Borders, titles |
| 2 | Green | Default | Healthy, success, online |
| 3 | Yellow | Default | Cooldown, warning |
| 4 | Red | Default | Hard-failed, error, offline |
| 5 | Cyan | Default | Info, timestamps |
| 6 | Magenta | Default | Highlight |
| 7 | Blue | Default | Debug |
| 8 | White | Blue | Status bar, disclaimer |
| 9 | Black | White | Selected row |

---

## Data Sources

| Screen | Primary Sources |
|--------|-----------------|
| Overview | /health, /v1/models, config.json, JSONL, /proc |
| Providers | /health, config.json, JSONL |
| Routes | config.json, /health, /v1/models |
| History | JSONL (router-events.jsonl) |
| Diagnostics | All endpoints + /proc |
| Settings | In-memory (persisted to config) |
| API Keys | config.json, /health, JSONL |

---

## Implementation Notes

1. **No curses init in demo** — Demo uses mock terminal; real TUI requires TTY
2. **Read-only by default** — No write operations to router
3. **No credentials exposed** — key_env names only, never values
4. **Provider-agnostic** — No hardcoded provider names in code
5. **5 background workers** — JSONL(1.5s), Health(2.5s), Models(30s), Config(60s), Process(10s)
6. **Wakeup pipe** — Thread-to-main notification for instant updates
7. **Double-buffered curses** — Smooth 60fps rendering on Termux

---

## File Structure
```
routeops/
├── __main__.py              # Entry point
├── __init__.py              # Package exports
├── config.py                # Constants, paths, intervals
├── demo_overview.py         # Non-interactive demo
├── adapters/
│   ├── jsonl_tail.py        # Incremental JSONL tail
│   ├── health_poll.py       # /health polling
│   ├── models_poll.py       # /v1/models polling
│   ├── config_read.py       # config.json parsing
│   └── process_inspect.py   # /proc inspection
├── models/
│   ├── provider.py          # ProviderHealth, ModelAvailability, ProviderConfig
│   ├── request.py           # EventModel, ReconstructedRequest
│   ├── route.py             # RouteConfig, PreferenceConfig, RouterConfig
│   └── process.py           # ProcessInfo, ProcessIdentity
├── runtime/
│   ├── __init__.py          # Main event loop, worker manager
│   ├── workers.py           # BaseWorker, WorkerManager
│   ├── worker_impl.py       # Concrete workers
│   └── messages.py          # Typed update messages
├── reconstruction.py        # Request reconstruction from events
├── screens/
│   ├── overview_curses.py   # Overview screen
│   ├── providers_curses.py  # Providers screen
│   ├── routes_curses.py     # Routes screen
│   ├── history_curses.py    # History screen
│   ├── diagnostics_curses.py# Diagnostics screen
│   ├── settings_curses.py   # Settings screen
│   └── api_keys_curses.py   # API Keys screen
├── state/
│   └── __init__.py          # AppState, SettingsModel, NavigationStack
├── tui/
│   ├── curses_terminal.py   # Curses wrapper, double-buffering
│   └── nav_shell.py         # Navigation shell, key routing
```

---

## Run Commands

```bash
# Full TUI (requires TTY - run in Termux tab)
routerops

# Non-interactive demo (works anywhere)
python3 -m routeops.demo_overview
```

---

*Generated for RouteOps v0.1 — Production Router Operations TUI*