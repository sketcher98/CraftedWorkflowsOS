# Hermes Router Fallback Visibility Headers

## Overview
The CraftedWorkflowsOS router emits detailed routing headers on every response to enable Hermes (and other clients) to observe and act on provider/model fallback behavior.

---

## Headers Sent by Router

### Primary Routing Headers (Always Present)
| Header | Description | Example |
|--------|-------------|---------|
| `X-Router-Provider` | Provider that ultimately served the request | `nvidia`, `zydit-v1`, `zydit-v2` |
| `X-Router-Model` | Actual model used by the provider | `moonshotai/kimi-k3`, `z-ai/glm-5.3` |
| `X-Router-Preference` | Preference bucket that was used | `kimi-k3`, `glm-5.3`, `nemotron`, `deepseek-v4.1-flash` |
| `X-Request-ID` | Unique request identifier for correlation | `req-1791633555091324167-9749` |

### Fallback Headers (Present When Fallback Occurred)
| Header | Description | Example |
|--------|-------------|---------|
| `X-Router-Fallback` | `true` if any provider fallback occurred | `true` |
| `X-Router-Fallback-Count` | Number of provider fallbacks within preference | `1`, `2` |
| `X-Router-Fallback-From` | Provider that failed, triggering fallback | `nvidia` |
| `X-Router-Preference-Fallback` | `true` if preference ladder fallback occurred | `true` |
| `X-Router-Preference-From` | Preference that was exhausted | `kimi-k3` |
| `X-Router-Preference-To` | Preference that was tried next | `glm-5.3` |
| `X-Router-Skipped-Hard-Failed` | `true` if any hard-failed routes were skipped | `true` |

---

## Fallback Scenarios & Header Patterns

### 1. Provider Fallback Within Same Preference
**Scenario**: NVIDIA fails → Zydit V1 succeeds for same preference (kimi-k3)

```
X-Router-Provider: zydit-v1
X-Router-Model: moonshotai/kimi-k3
X-Router-Preference: kimi-k3
X-Router-Fallback: true
X-Router-Fallback-Count: 1
X-Router-Fallback-From: nvidia
X-Router-Preference-Fallback: (absent)
```

### 2. Preference Fallback (All Providers Exhausted)
**Scenario**: All providers for kimi-k3 fail → fallback to glm-5.3 preference

```
X-Router-Provider: nvidia
X-Router-Model: z-ai/glm-5.3
X-Router-Preference: glm-5.3
X-Router-Fallback: true
X-Router-Fallback-Count: 3
X-Router-Fallback-From: zydit-v2
X-Router-Preference-Fallback: true
X-Router-Preference-From: kimi-k3
X-Router-Preference-To: glm-5.3
```

### 3. Hard-Failed Routes Skipped
**Scenario**: NVIDIA × Kimi hard-failed (403/404) → skipped, Zydit V1 tried

```
X-Router-Provider: zydit-v1
X-Router-Model: moonshotai/kimi-k3
X-Router-Preference: kimi-k3
X-Router-Skipped-Hard-Failed: true
X-Router-Fallback: true
X-Router-Fallback-From: nvidia
```

### 4. No Fallback (Success on First Try)
**Scenario**: NVIDIA succeeds on first attempt

```
X-Router-Provider: nvidia
X-Router-Model: moonshotai/kimi-k3
X-Router-Preference: kimi-k3
# No fallback headers present
```

---

## Integration Guide for Hermes

### 1. Parse Headers on Every Response
```python
# In Hermes HTTP client response handler
router_provider = response.headers.get("X-Router-Provider")
router_model = response.headers.get("X-Router-Model")
router_preference = response.headers.get("X-Router-Preference")
request_id = response.headers.get("X-Request-ID")

is_fallback = response.headers.get("X-Router-Fallback") == "true"
fallback_count = int(response.headers.get("X-Router-Fallback-Count", "0"))
fallback_from = response.headers.get("X-Router-Fallback-From")
preference_fallback = response.headers.get("X-Router-Preference-Fallback") == "true"
preference_from = response.headers.get("X-Router-Preference-From")
preference_to = response.headers.get("X-Router-Preference-To")
skipped_hard_failed = response.headers.get("X-Router-Skipped-Hard-Failed") == "true"
```

### 2. Log Fallback Events
```python
if is_fallback:
    logger.info(
        "router_fallback",
        request_id=request_id,
        from_provider=fallback_from,
        to_provider=router_provider,
        model=router_model,
        preference=router_preference,
        fallback_count=fallback_count,
        preference_fallback=preference_fallback,
        preference_from=preference_from,
        preference_to=preference_to,
    )
```

### 3. Optional: Act on Fallback
- **Adjust retry logic**: Reduce retries if preference fallback occurred (already tried multiple providers)
- **Alert on preference fallback**: Indicates all providers for primary model failed
- **Track provider health**: Aggregate fallback counts per provider for dashboards
- **Skip hard-failed awareness**: If `X-Router-Skipped-Hard-Failed` present, provider has hard-failed routes

---

## Error Response Headers

On router exhaustion (all providers/preferences failed):

```
Status: 503 Service Unavailable
Retry-After: 15
Content-Type: application/json

{
  "error": "router_exhausted",
  "retry_after_secs": "15"
}
```

Headers included:
- `Retry-After`: Seconds until next retry recommended
- `X-Request-ID`: For correlation with router logs

---

## Testing

Test router available at: `http://127.0.0.1:8644` (isolated test environment)

Mock provider behaviors controllable via:
```
POST http://127.0.0.1:19990/control
{"port": 19990, "behavior": "503|403|404|401|stream_commit_then_drop|success", "model_behaviors": {...}}
```

---

## Contact

Router implementation: `~/CraftedWorkflowsOS/router/main.go`
Test environment: `~/CraftedWorkflowsOS/router/test-env/`
Production endpoint: `http://127.0.0.1:8643/v1`