"""
Shared bar + iconography module — ONE visual language across all screens.

Bar spec:  [██████░░░░] 61.1%   — always 10 cells, always success % from JSONL
Icon spec: ● green >=80%  ·  ◐ yellow 40-79%  ·  ○ red <40%  ·  ◎ dim no data  ·  ✗ hard-failed
Cooldown:  text column (cd:12s) — bars never animate countdowns (one bar, one meaning).

All functions are pure: no AppState mutation, no I/O except provider_rollup
reading the already-in-memory _model_health dict.
"""

import curses
from typing import Dict, Any, Optional, Tuple, NamedTuple

BAR_CELLS = 10

# Tier scale (the user's spec — applied identically on every screen):
#   100-80  HEALTHY    bar green   text green
#    79-60  DEGRADING  bar yellow text yellow
#    59-40  DEGRADED   bar yellow text RED
#      39-  UNHEALTHY  bar red    text red
TIER_GREEN_PCT = 80.0
TIER_DEGRADING_PCT = 60.0
TIER_DEGRADED_PCT = 40.0
TIER_YELLOW_PCT = 40.0  # kept: bar turns yellow at/above this

GREEN = lambda: curses.color_pair(2)
YELLOW = lambda: curses.color_pair(3)
RED = lambda: curses.color_pair(4)
DIM = lambda: curses.A_DIM
BOLD = lambda: curses.A_BOLD

# Outcomes that mean "the CLIENT hung up", not "the provider failed".
# Hermes cancelling a slow stream says nothing about provider quality, so
# these must NOT count against a provider's success rate.
CLIENT_ABORT_ERRORS = (
    "context canceled",
    "context cancelled",
    "client disconnected",
    "broken pipe",
)


class HealthVerdict(NamedTuple):
    """One health verdict — bar, word, icon and their colours.

    bar_color and text_color are SEPARATE because the 59-40 band is
    deliberately mixed: yellow bar, RED text.
    """
    bar_pct: Optional[float]
    status: str
    icon: str
    bar_color: int
    text_color: int


def is_client_abort(event) -> bool:
    """True when a route_result was aborted by the CLIENT, not the provider.

    Checks the error string, not just the outcome, because the router emits
    stream_error for both a real upstream stall and a client disconnect.
    """
    err = (getattr(event, "error", None) or "").strip().lower()
    if not err:
        return False
    return any(marker in err for marker in CLIENT_ABORT_ERRORS)


def classify_outcome(event) -> str:
    """Classify a route_result event as 'ok' | 'fail' | 'abort'."""
    if is_client_abort(event):
        return "abort"
    return "ok" if getattr(event, "outcome", None) == "success" else "fail"


def verdict(pct: Optional[float], n: int = 0) -> str:
    """Success rate -> 'healthy' | 'degrading' | 'degraded' | 'unhealthy'.

    The bands are the user's spec; see the constants above. `n` is accepted
    for call compatibility but does NOT gate the band: a real rate is always
    banded, because hiding it behind 'NO RATE' told the user nothing.
    """
    if pct is None:
        return "no_data"
    if pct >= TIER_GREEN_PCT:
        return "healthy"
    if pct >= TIER_DEGRADING_PCT:
        return "degrading"
    if pct >= TIER_DEGRADED_PCT:
        return "degraded"
    return "unhealthy"


def status_from_verdict(v: str) -> str:
    """Map a verdict to the status WORD shown next to the bar.

    The word and the bar MUST come from the same verdict — a row reading
    '0.0% ○ HEALTHY' is a self-contradiction and is impossible by construction.
    """
    return {
        "healthy": "HEALTHY",
        "degrading": "DEGRADING",
        "degraded": "DEGRADED",
        "unhealthy": "UNHEALTHY",
        "no_data": "NO DATA YET",
    }.get(v, "NO DATA YET")


def health_display(pct: Optional[float], n_obs: int, n_fail: int,
                   in_cooldown: bool = False) -> HealthVerdict:
    """THE shared verdict for every health row in every screen.

    Returns a HealthVerdict(bar_pct, status, icon, bar_color, text_color).

    Rules:
      * Zero observations -> full GREEN bar + 'NO DATA YET'. The provider is
        configured and routed but has not served yet: that is not a fault,
        and painting it red/empty reads as a dead provider.
      * With observations -> the real rate, banded by the tier scale.
      * The 59-40 band is yellow BAR + RED TEXT.
      * in_cooldown: a provider the router has parked shows 'COOLDOWN' with a
        YELLOW word and a GREEN bar (it is resting, not broken) — but only
        while its rate is still healthy. A genuinely bad rate stays red.
      * Client aborts are excluded upstream (they are the client's doing).
    """
    if not n_obs:
        # No traffic yet — configured and routed, but unproven.
        return HealthVerdict(100.0, "NO DATA YET", "●",
                             curses.color_pair(2), curses.color_pair(2))

    v = verdict(pct, n_obs)
    if v == "healthy":
        bar_c, text_c = curses.color_pair(2), curses.color_pair(2)
        icon = "●"
    elif v == "degrading":
        bar_c, text_c = curses.color_pair(3), curses.color_pair(3)
        icon = "◐"
    elif v == "degraded":
        # yellow bar, RED text — the deliberately mixed band
        bar_c, text_c = curses.color_pair(3), curses.color_pair(4)
        icon = "◐"
    else:
        bar_c, text_c = curses.color_pair(4), curses.color_pair(4)
        icon = "○"

    if in_cooldown and v == "healthy":
        return HealthVerdict(pct, "COOLDOWN", "◐",
                             curses.color_pair(2), curses.color_pair(3))

    return HealthVerdict(pct, status_from_verdict(v), icon, bar_c, text_c)


def pct_bar(pct: Optional[float], cells: int = BAR_CELLS) -> str:
    """Bracketed bar: [██████░░░░]. pct=None -> empty bar (no data).
    Reserved for TOP-LEVEL rows (model headers, provider list rows)."""
    if pct is None:
        return "[" + "░" * cells + "]"
    filled = round(pct / 100 * cells)
    return "[" + "█" * filled + "░" * (cells - filled) + "]"


def pct_subbar(pct: Optional[float], cells: int = BAR_CELLS) -> str:
    """Child-row bar: ▓▓▓▓▓▓░░░░ — no brackets, lighter shade fill.
    Visually distinct from the parent [██████░░░░] so sub-rows (providers
    under a model, models under a provider rollup) read as breakdowns,
    not competing health statements."""
    if pct is None:
        return "░" * cells
    filled = round(pct / 100 * cells)
    return "▓" * filled + "░" * (cells - filled)


def pct_label(pct: Optional[float]) -> str:
    """Fixed-width pct label: ' 61.1%' right-aligned, '  —   ' when None."""
    if pct is None:
        return "   —  "
    return f"{pct:5.1f}%"


def tier_color(pct: Optional[float]) -> int:
    """Curses color for a success pct. None -> dim."""
    if pct is None:
        return DIM()
    if pct >= TIER_GREEN_PCT:
        return GREEN()
    if pct >= TIER_YELLOW_PCT:
        return YELLOW()
    return RED()


def tier_icon(pct: Optional[float]) -> Tuple[str, int]:
    """(icon, color) for a success pct."""
    if pct is None:
        return "◎", DIM()
    if pct >= TIER_GREEN_PCT:
        return "●", GREEN()
    if pct >= TIER_YELLOW_PCT:
        return "◐", YELLOW()
    return "○", RED()


def hardfail_icon() -> Tuple[str, int]:
    return "✗", RED() | BOLD()


def cell_display(cell) -> Tuple[str, str, int]:
    """One shared renderer for a router per-cell (B-7) health object.

    Returns (icon, status_label, color) — used by the Providers detail drawer
    and the Routes matrix so the two can never disagree (Q1).
    """
    if cell.hard_failed:
        return "✗", "HARD-FAILED", RED() | BOLD()
    if not cell.healthy and cell.cooldown_until:
        return "◐", f"COOLDOWN until {cell.cooldown_until.strftime('%H:%M:%S')}", YELLOW()
    if not cell.healthy:
        return "○", "UNHEALTHY", RED()
    if cell.warm:
        return "●", "HEALTHY (warm)", GREEN()
    return "●", "HEALTHY", GREEN()


def provider_routes(app_state, provider_name: str) -> int:
    """Number of routes (across all preferences) that reference this provider.
    Zero = provider is configured but unrouted — 'HEALTHY' would be a lie."""
    n = 0
    if app_state.config:
        for pref in app_state.config.preferences:
            for route in pref.routes:
                if route.provider == provider_name:
                    n += 1
    return n


def provider_rollup(app_state, provider_name: str) -> Tuple[Optional[float], int, int, int]:
    """Real success % for a provider across ALL its actively-routed models,
    from the in-memory JSONL-derived _model_health.
    Returns (pct, ok, fail, abort) — pct None ONLY for unrouted providers.
    Zero-traffic but healthy providers show 100% (green) with 0 req,
    matching /health status until real traffic arrives.
    Abort count is returned so screens can show 'n req (x aborted)' and
    the verdict logic can exclude aborted events from the success %."""
    mh = getattr(app_state, "_model_health", {})
    active_models = set()
    if app_state.config:
        for pref in app_state.config.preferences:
            for route in pref.routes:
                if route.provider == provider_name:
                    active_models.add(route.model)
    ok = fail = abort = 0
    for (prov, model), st in mh.items():
        if prov == provider_name and (not active_models or model in active_models):
            ok += st.get("ok", 0)
            fail += st.get("fail", 0)
            abort += st.get("abort", 0)
    total = ok + fail + abort

    # Get provider health from /health endpoint
    provider_healthy = True
    pm = app_state.providers.get(provider_name)
    if pm and pm.health:
        provider_healthy = pm.health.healthy and not pm.health.hard_failed

    if total == 0:
        # Zero observed traffic: if /health says healthy, show 100% (green, 0 req)
        # If unhealthy/unrouted, return None (empty bar, ◎)
        if provider_healthy and active_models:
            return 100.0, 0, 0, 0
        return None, 0, 0, 0
    # Success rate EXCLUDES aborts (client cancels)
    denom = ok + fail
    if denom == 0:
        return None, ok, fail, abort
    return (ok / denom * 100), ok, fail, abort


def cooldown_text(health) -> str:
    """Cooldown remaining as short text: 'cd:12s', 'cd:1m04s', or 'cd:—'."""
    from datetime import datetime
    if not health.cooldown_until:
        return "cd:—"
    now = datetime.now().astimezone()
    cd = health.cooldown_until
    if cd.tzinfo is None:
        cd = cd.replace(tzinfo=now.tzinfo)
    remaining = max(0, int((cd - now).total_seconds()))
    if remaining <= 0:
        return "cd:—"
    if remaining < 60:
        return f"cd:{remaining}s"
    return f"cd:{remaining // 60}m{remaining % 60:02d}s"
