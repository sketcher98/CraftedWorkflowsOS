"""
Shared bar + iconography module — ONE visual language across all screens.

Bar spec:  [██████░░░░] 61.1%   — always 10 cells, always success % from JSONL
Icon spec: ● green >=80%  ·  ◐ yellow 40-79%  ·  ○ red <40%  ·  ◎ dim no data  ·  ✗ hard-failed
Cooldown:  text column (cd:12s) — bars never animate countdowns (one bar, one meaning).

All functions are pure: no AppState mutation, no I/O except provider_rollup
reading the already-in-memory _model_health dict.
"""

import curses
from typing import Dict, Any, Optional, Tuple

BAR_CELLS = 10
TIER_GREEN_PCT = 80.0
TIER_YELLOW_PCT = 40.0

GREEN = lambda: curses.color_pair(2)
YELLOW = lambda: curses.color_pair(3)
RED = lambda: curses.color_pair(4)
DIM = lambda: curses.A_DIM
BOLD = lambda: curses.A_BOLD


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


def provider_rollup(app_state, provider_name: str) -> Tuple[Optional[float], int, int]:
        """Real success % for a provider across ALL its actively-routed models,
        from the in-memory JSONL-derived _model_health.
        Returns (pct, ok, fail) — pct None ONLY for unrouted providers.
        Zero-traffic but healthy providers show 100% (green) with 0 req,
        matching /health status until real traffic arrives."""
        mh = getattr(app_state, "_model_health", {})
        active_models = set()
        if app_state.config:
            for pref in app_state.config.preferences:
                for route in pref.routes:
                    if route.provider == provider_name:
                        active_models.add(route.model)
        ok = fail = 0
        for (prov, model), st in mh.items():
            if prov == provider_name and (not active_models or model in active_models):
                ok += st["ok"]
                fail += st["fail"]
        total = ok + fail
        
        # Get provider health from /health endpoint
        provider_healthy = True
        pm = app_state.providers.get(provider_name)
        if pm and pm.health:
            provider_healthy = pm.health.healthy and not pm.health.hard_failed
        
        if total == 0:
            # Zero observed traffic: if /health says healthy, show 100% (green, 0 req)
            # If unhealthy/unrouted, return None (empty bar, ◎)
            if provider_healthy and active_models:
                return 100.0, 0, 0
            return None, 0, 0
        return (ok / total * 100), ok, fail


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
