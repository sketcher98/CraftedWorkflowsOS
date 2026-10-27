"""
Settings Screen for RouteOps Curses TUI.
Honest design: NO fake editable fields (old screen edited an in-memory
model nothing reads, and its Advanced tab crashed on phantom fields).
Three read-only tabs: Monitor, Router, Paths & Security.
"""

import curses
import json
from typing import List

from routeops.state import AppState
from routeops.config import (
    POLL_INTERVALS, HTTP_CONNECT_TIMEOUT, HTTP_READ_TIMEOUT,
    JSONL_MAX_BUFFER, FORBIDDEN_PATHS, FORBIDDEN_ENV_VARS,
    ROUTER_CONFIG_JSON, ROUTER_EVENTS_JSONL, ROUTER_LOG,
    MIN_PRODUCTION_BINARY_SIZE,
)


class SettingsScreen:
    """Settings screen: 3 read-only tabs (Monitor / Router / Paths)."""

    def __init__(self, app_state: AppState):
        self.app_state = app_state
        self.current_tab = 0
        self.tab_names = ["Monitor", "Router", "Paths & Security"]
        self.scroll_offset = 0

    def set_size(self, width: int, height: int) -> None:
        self.width = max(60, width)
        self.height = max(15, height)

    def handle_key(self, key: int) -> bool:
        """Handle key press. Returns True if handled."""
        if key in (ord('1'), ord('2'), ord('3')):
            self.current_tab = int(chr(key)) - 1
            self.scroll_offset = 0
            return True

        if key in (curses.KEY_LEFT, ord('h')):
            self.current_tab = max(0, self.current_tab - 1)
            self.scroll_offset = 0
            return True

        if key in (curses.KEY_RIGHT, ord('l')):
            self.current_tab = min(2, self.current_tab + 1)
            self.scroll_offset = 0
            return True

        if key in (curses.KEY_UP, ord('k')):
            self.scroll_offset = max(0, self.scroll_offset - 1)
            return True

        if key in (curses.KEY_DOWN, ord('j')):
            self.scroll_offset += 1
            return True

        if key in (27, ord('q')):
            return False  # Let parent handle

        return False

    # ---------------------------------------------------------------- render

    def render(self, term) -> None:
        rows, cols = term.size.rows, term.size.cols
        y = 0

        # Tab bar — active tab highlighted (house style)
        term.write(y, 0, "SETTINGS  ", curses.A_BOLD | curses.color_pair(1))
        tx = 10
        for i, name in enumerate(self.tab_names):
            marker = f"[{name}]" if i == self.current_tab else f" {name} "
            attr = (curses.color_pair(2) | curses.A_BOLD if i == self.current_tab
                    else curses.A_DIM)
            if tx + len(marker) > cols - 24:
                break
            term.write(y, tx, marker, attr)
            tx += len(marker) + 1
        hint = "[1-3=tabs]  [h/Esc=back]"
        term.write(y, cols - len(hint) - 1, hint, curses.color_pair(5) | curses.A_DIM)
        y += 1
        term.write(y, 0, "─" * (cols - 2), curses.color_pair(1))
        y += 1

        if self.current_tab == 0:
            lines = self._build_monitor_lines(cols - 4)
        elif self.current_tab == 1:
            lines = self._build_router_lines(cols - 4)
        else:
            lines = self._build_paths_lines(cols - 4)

        visible_rows = rows - y - 1
        for i in range(self.scroll_offset,
                       min(self.scroll_offset + visible_rows, len(lines))):
            raw = lines[i]
            line = raw.ljust(cols - 6)[:cols - 6]
            if raw.rstrip().endswith("─") or raw.isupper():
                attr = curses.A_BOLD | curses.color_pair(5)
            else:
                attr = curses.A_NORMAL
            term.write(y, 2, f"│ {line} │", attr)
            y += 1

        if len(lines) > visible_rows:
            ind = (f"  ↑↓ {self.scroll_offset + 1}-"
                   f"{min(self.scroll_offset + visible_rows, len(lines))} of {len(lines)}")
            term.write(rows - 1, 0, ind[:cols], curses.color_pair(5))

    # ---------------------------------------------------------------- tabs

    def _build_monitor_lines(self, width: int) -> List[str]:
        lines = []
        w = width - 4
        lines.append("MONITOR — live worker facts (read-only)")
        lines.append("─" * w)
        lines.append("")
        lines.append("POLL INTERVALS (compiled into routeops.config)")
        for name, secs in POLL_INTERVALS.items():
            lines.append(f"  {name:<14} {secs}s")
        lines.append("")
        lines.append(f"  HTTP connect timeout:    {HTTP_CONNECT_TIMEOUT}s")
        lines.append(f"  HTTP read timeout:       {HTTP_READ_TIMEOUT}s")
        lines.append(f"  Event buffer (in mem):   {JSONL_MAX_BUFFER} events")
        n = len(self.app_state.recent_events)
        lines.append(f"  Events currently held:   {n}")
        lines.append("")
        lines.append("DISPLAY — effective values (display-only, not persisted)")
        s = self.app_state.settings
        lines.append(f"  Event density:    {s.event_density}")
        lines.append(f"  Disclaimers:       {'ON' if s.show_disclaimers else 'OFF'}")
        lines.append(f"  Auto-refresh:      {'ON' if s.auto_refresh else 'OFF'}  (workers always run)")
        lines.append("")
        lines.append("Nothing here changes behavior; these are the real values")
        lines.append("the workers use. Adjusting requires editing config.py +")
        lines.append("restarting routerops — by design.")
        return [line[:w] for line in lines]

    def _build_router_lines(self, width: int) -> List[str]:
        lines = []
        w = width - 4
        lines.append("ROUTER — config.json as the router sees it (read-only)")
        lines.append("─" * w)
        lines.append("")
        try:
            cfg = json.loads(ROUTER_CONFIG_JSON.read_text())
        except Exception as e:
            lines.append(f"  (cannot read config.json: {e})")
            return [line[:w] for line in lines]

        r = cfg.get("retry", {})
        lines.append("RETRY & TIMEOUTS (edit on Routes screen — live)")
        lines.append(f"  failure_limit:          {r.get('failure_limit')}")
        lines.append(f"  cooldown_seconds:       {r.get('cooldown_seconds')}")
        lines.append(f"  max_cooldown_seconds:   {r.get('max_cooldown_seconds')}")
        lines.append(f"  all_failed_wait_seconds:{r.get('all_failed_wait_seconds')}")
        lines.append(f"  route_timeout_seconds:  {r.get('route_timeout_seconds')}")
        lines.append(f"  stream_idle_timeout:    {r.get('stream_idle_timeout_seconds')}")
        lines.append(f"  first_event_timeout:    {r.get('first_event_timeout_seconds')}")
        lines.append(f"  hard_failed_wait:       {r.get('hard_failed_wait_seconds')}")
        hold = r.get('max_hold_seconds')
        lines.append(f"  max_hold_seconds:       {hold}  "
                     f"({'hold-and-serve ON' if (hold or 0) > 0 else 'hold-and-serve OFF (legacy 503)'})")
        lines.append(f"  continuation_max:       {r.get('continuation_max')}  (0 = stitching banned)")
        lines.append(f"  catalog_refresh:        {cfg.get('catalog_refresh_seconds')}s")
        lines.append("")
        provs = cfg.get("providers", [])
        dis = cfg.get("disabled_providers", [])
        prefs = cfg.get("preferences", [])
        lines.append("PROVIDERS & PREFERENCES")
        lines.append(f"  Active providers:   {len(provs)}  "
                     f"({', '.join(p['name'] for p in provs)})")
        if dis:
            lines.append(f"  Disabled:           {len(dis)}  "
                         f"({', '.join(p['name'] for p in dis)})")
        lines.append(f"  Preferences:         {len(prefs)}  "
                     f"({', '.join(p['name'] for p in prefs)})")
        lines.append("")
        lines.append("Editing lives on the Routes screen (e) / provider")
        lines.append("manager (o). Restart router after any change.")
        return [line[:w] for line in lines]

    def _build_paths_lines(self, width: int) -> List[str]:
        lines = []
        w = width - 4
        lines.append("PATHS & SECURITY")
        lines.append("─" * w)
        lines.append("")
        lines.append("FILES")
        lines.append(f"  Router config:   {ROUTER_CONFIG_JSON}")
        lines.append(f"  Events JSONL:    {ROUTER_EVENTS_JSONL}")
        lines.append(f"  Router log:      {ROUTER_LOG}")
        lines.append(f"  Binary min size: {MIN_PRODUCTION_BINARY_SIZE // 1_000_000}MB (production identity check)")
        lines.append("")
        lines.append("SECURITY (enforced in routeops.config)")
        lines.append(f"  Forbidden paths:     {', '.join(sorted(FORBIDDEN_PATHS))}")
        lines.append("  Forbidden env vars:  key names only — values are NEVER")
        lines.append("                       read, displayed, or logged")
        lines.append("")
        lines.append("EDITOR SAFETY")
        lines.append("  Every config write: timestamped .bak backup -> validate")
        lines.append("  -> atomic rename. Router never auto-restarts from TUI.")
        lines.append("")
        lines.append("RouteOps v0.2 — Phase 1B (editor + merged key tests)")
        return [line[:w] for line in lines]
