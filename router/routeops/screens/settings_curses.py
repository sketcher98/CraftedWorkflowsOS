"""
Settings Screen for RouteOps Curses TUI.
Fully editable: poll intervals (live), display toggles, router config (safe write).
Three tabs: Monitor, Router, Paths & Security.
Keys: Up/Down select, +/- adjust, Enter toggle/edit, 1-3 tabs, h/Esc back.
"""

import curses
import json
from typing import List, Optional, Tuple

from routeops.state import AppState
from routeops.config import (
    POLL_INTERVALS, HTTP_CONNECT_TIMEOUT, HTTP_READ_TIMEOUT,
    JSONL_MAX_BUFFER, FORBIDDEN_PATHS, FORBIDDEN_ENV_VARS,
    ROUTER_CONFIG_JSON, ROUTER_EVENTS_JSONL, ROUTER_LOG,
    MIN_PRODUCTION_BINARY_SIZE,
)


class SettingsScreen:
    """Settings screen: 3 tabs with live editing."""

    def __init__(self, app_state: AppState):
        self.app_state = app_state
        self.current_tab = 0
        self.tab_names = ["Monitor", "Router", "Paths"]
        self.scroll_offset = 0
        self.cursor = 0  # selected row index within current tab
        self.editing = False
        self.edit_buffer = ""
        self.status_msg = ""
        self.status_timer = 0

    def set_size(self, width: int, height: int) -> None:
        self.width = max(60, width)
        self.height = max(15, height)

    def _flash(self, msg: str) -> None:
        self.status_msg = msg
        self.status_timer = 40  # ~20s at 500ms tick

    # ---------------------------------------------------------------- keys

    def handle_key(self, key: int) -> bool:
        """Handle key press. Returns True if handled."""
        if self.status_timer > 0:
            self.status_timer -= 1

        # Tab switching
        if key in (ord("1"), ord("2"), ord("3")):
            self.current_tab = int(chr(key)) - 1
            self.scroll_offset = 0
            self.cursor = 0
            self.editing = False
            return True

        if key in (curses.KEY_LEFT,):
            self.current_tab = max(0, self.current_tab - 1)
            self.scroll_offset = 0
            self.cursor = 0
            self.editing = False
            return True

        if key in (curses.KEY_RIGHT,):
            self.current_tab = min(2, self.current_tab + 1)
            self.scroll_offset = 0
            self.cursor = 0
            self.editing = False
            return True

        # Navigation
        if key in (curses.KEY_UP, ord("k")):
            self.cursor = max(0, self.cursor - 1)
            return True

        if key in (curses.KEY_DOWN, ord("j")):
            self.cursor += 1
            return True

        # Editing
        if key == ord("+") or key == ord("="):
            self._adjust(+1)
            return True

        if key == ord("-") or key == ord("_"):
            self._adjust(-1)
            return True

        if key in (curses.KEY_ENTER, 10, 13):
            self._activate()
            return True

        if key in (27, ord("q")):
            return False  # Let parent handle

        return False

    # ---------------------------------------------------------------- edit logic

    def _get_editable_rows(self) -> List[dict]:
        """Return list of editable items for current tab."""
        if self.current_tab == 0:
            return self._monitor_editables()
        elif self.current_tab == 1:
            return self._router_editables()
        return []  # Paths tab is read-only

    def _monitor_editables(self) -> List[dict]:
        """Poll intervals + display settings."""
        rows = []
        # Poll intervals (live-edit workers)
        for name in ("jsonl", "health", "models", "config", "process"):
            rows.append({"type": "interval", "key": name,
                         "label": f"Poll: {name}", "unit": "s"})
        # Display
        s = self.app_state.settings
        rows.append({"type": "bool", "key": "auto_refresh",
                     "label": "Auto-refresh", "value": s.auto_refresh})
        rows.append({"type": "bool", "key": "show_disclaimers",
                     "label": "Disclaimers", "value": s.show_disclaimers})
        rows.append({"type": "cycle", "key": "event_density",
                     "label": "Event density",
                     "options": ["compact", "normal", "verbose"],
                     "value": s.event_density})
        return rows

    def _router_editables(self) -> List[dict]:
        """Router retry/timeout settings from config.json."""
        try:
            cfg = json.loads(ROUTER_CONFIG_JSON.read_text())
        except Exception:
            return []
        r = cfg.get("retry", {})
        rows = []
        for key in ("failure_limit", "cooldown_seconds", "max_cooldown_seconds",
                    "all_failed_wait_seconds", "route_timeout_seconds",
                    "stream_idle_timeout_seconds", "first_event_timeout_seconds",
                    "hard_failed_wait_seconds", "max_hold_seconds", "continuation_max"):
            rows.append({"type": "int", "key": key,
                         "label": key, "value": r.get(key, 0)})
        return rows

    def _adjust(self, delta: int) -> None:
        """Adjust the currently selected editable value."""
        editables = self._get_editable_rows()
        if self.cursor >= len(editables):
            return
        item = editables[self.cursor]

        if item["type"] == "interval":
            self._adjust_interval(item["key"], delta)
        elif item["type"] == "int":
            self._adjust_router_int(item["key"], delta)
        elif item["type"] == "bool":
            pass  # Enter toggles
        elif item["type"] == "cycle":
            pass  # Enter cycles

    def _adjust_interval(self, name: str, delta: int) -> None:
        """Live-adjust a worker poll interval."""
        runtime = getattr(self.app_state, "runtime", None)
        if not runtime or not runtime.worker_manager:
            self._flash("No runtime")
            return
        worker = runtime.worker_manager.get_worker(name)
        if not worker:
            self._flash(f"Worker '{name}' not found")
            return
        old = worker.interval
        new = max(0.5, old + delta * (0.5 if old < 5 else 1.0))
        new = min(new, 300.0)
        worker.interval = new
        POLL_INTERVALS[name] = new  # keep config in sync for display
        self._flash(f"{name}: {old:.1f}s -> {new:.1f}s (live)")

    def _adjust_router_int(self, key: str, delta: int) -> None:
        """Stage a router config change (commit on Enter)."""
        try:
            cfg = json.loads(ROUTER_CONFIG_JSON.read_text())
        except Exception:
            self._flash("Cannot read config")
            return
        r = cfg.get("retry", {})
        old = r.get(key, 0)
        step = 1 if old < 10 else (5 if old < 60 else 10)
        new = max(0, old + delta * step)
        r[key] = new
        cfg["retry"] = r
        # Write immediately via safe editor
        self._commit_router_config(cfg, f"{key}: {old} -> {new}")

    def _activate(self) -> None:
        """Enter key: toggle bools, cycle enums."""
        editables = self._get_editable_rows()
        if self.cursor >= len(editables):
            return
        item = editables[self.cursor]
        s = self.app_state.settings

        if item["type"] == "bool":
            key = item["key"]
            current = getattr(s, key, False)
            setattr(s, key, not current)
            self._flash(f"{item['label']}: {'ON' if not current else 'OFF'}")
        elif item["type"] == "cycle":
            key = item["key"]
            opts = item["options"]
            current = getattr(s, key, opts[0])
            idx = opts.index(current) if current in opts else 0
            next_val = opts[(idx + 1) % len(opts)]
            setattr(s, key, next_val)
            self._flash(f"{item['label']}: {next_val}")

    def _commit_router_config(self, cfg: dict, reason: str) -> None:
        """Write config via ConfigEditor (backup + validate + atomic)."""
        try:
            from routeops.adapters.config_editor import ConfigEditor
            editor = ConfigEditor()
            bak, path = editor.commit(cfg, reason)
            self._flash(f"Saved: {reason} (bak: {bak.name})")
        except Exception as e:
            self._flash(f"SAVE FAILED: {e}")

    # ---------------------------------------------------------------- render

    def render(self, term) -> None:
        rows, cols = term.size.rows, term.size.cols
        y = 0

        # Tab bar
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
        hint = "[+/- adjust] [Enter toggle] [1-3 tabs] [Esc back]"
        term.write(y, max(tx + 2, cols - len(hint) - 1), hint[:cols - tx - 3],
                   curses.color_pair(5) | curses.A_DIM)
        y += 1
        term.write(y, 0, "─" * (cols - 2), curses.color_pair(1))
        y += 1

        # Status message
        if self.status_msg and self.status_timer > 0:
            term.write(y, 2, f"⚡ {self.status_msg}", curses.color_pair(3) | curses.A_BOLD)
            y += 1
        else:
            y += 1

        if self.current_tab == 0:
            self._render_monitor(term, y, rows, cols)
        elif self.current_tab == 1:
            self._render_router(term, y, rows, cols)
        else:
            self._render_paths(term, y, rows, cols)

    def _render_monitor(self, term, y: int, rows: int, cols: int) -> None:
        """Monitor tab: live-editable poll intervals + display toggles."""
        w = cols - 6
        runtime = getattr(self.app_state, "runtime", None)
        s = self.app_state.settings

        lines = []  # (text, is_editable, current_value_str)
        lines.append(("POLL INTERVALS (live — +/- to adjust, takes effect immediately)", False, ""))
        lines.append(("", False, ""))

        editables = self._monitor_editables()
        row_idx = 0
        for name in ("jsonl", "health", "models", "config", "process"):
            interval = POLL_INTERVALS.get(name, 0)
            # Check live worker value
            if runtime and runtime.worker_manager:
                wk = runtime.worker_manager.get_worker(name)
                if wk:
                    interval = wk.interval
            marker = "▶ " if self.cursor == row_idx else "  "
            lines.append((f"{marker}{name:<12} {interval:>6.1f}s", True, ""))
            row_idx += 1

        lines.append(("", False, ""))
        lines.append(("DISPLAY (live — Enter to toggle/cycle)", False, ""))
        lines.append(("", False, ""))

        # auto_refresh
        marker = "▶ " if self.cursor == row_idx else "  "
        val = "ON" if s.auto_refresh else "OFF"
        lines.append((f"{marker}Auto-refresh    {val}", True, ""))
        row_idx += 1

        # show_disclaimers
        marker = "▶ " if self.cursor == row_idx else "  "
        val = "ON" if s.show_disclaimers else "OFF"
        lines.append((f"{marker}Disclaimers     {val}", True, ""))
        row_idx += 1

        # event_density
        marker = "▶ " if self.cursor == row_idx else "  "
        lines.append((f"{marker}Event density   {s.event_density}", True, ""))
        row_idx += 1

        lines.append(("", False, ""))
        lines.append(("Changes to poll intervals apply to the running workers", False, ""))
        lines.append(("immediately. Display settings affect this TUI session.", False, ""))

        # Clamp cursor
        n_editable = row_idx
        if self.cursor >= n_editable:
            self.cursor = max(0, n_editable - 1)

        visible = rows - y - 1
        for i, (text, is_ed, _) in enumerate(lines):
            if i < self.scroll_offset or i >= self.scroll_offset + visible:
                continue
            attr = curses.A_NORMAL
            if is_ed and i == self.scroll_offset + (self.cursor - self.scroll_offset):
                pass
            row_y = y + i - self.scroll_offset
            if row_y >= rows - 1:
                break
            if "▶" in text:
                attr = curses.color_pair(2) | curses.A_BOLD
            elif text.isupper() or text.startswith("POLL") or text.startswith("DISPLAY"):
                attr = curses.A_BOLD | curses.color_pair(5)
            term.write(row_y, 2, text[:w])

    def _render_router(self, term, y: int, rows: int, cols: int) -> None:
        """Router tab: retry/timeout settings, editable via +/-."""
        w = cols - 6
        try:
            cfg = json.loads(ROUTER_CONFIG_JSON.read_text())
        except Exception as e:
            term.write(y, 2, f"Cannot read config.json: {e}")
            return

        r = cfg.get("retry", {})
        lines = []
        lines.append(("RETRY & TIMEOUTS (+/- to adjust, saves immediately)", False))
        lines.append(("Each change: backup -> validate -> atomic write", False))
        lines.append(("", False))

        editables = self._router_editables()
        for i, item in enumerate(editables):
            marker = "▶ " if self.cursor == i else "  "
            val = r.get(item["key"], 0)
            hold_note = ""
            if item["key"] == "max_hold_seconds":
                hold_note = " (hold-and-serve ON)" if (val or 0) > 0 else " (OFF)"
            elif item["key"] == "continuation_max":
                hold_note = " (0 = stitching banned)"
            lines.append((f"{marker}{item['label']:<28} {val}{hold_note}", True))

        lines.append(("", False))
        lines.append(("After changes: router hot-reloads config on next request.", False))
        lines.append(("If not, restart with: ~/bin/startr", False))

        # Clamp cursor
        n_editable = len(editables)
        if self.cursor >= n_editable:
            self.cursor = max(0, n_editable - 1)

        visible = rows - y - 1
        for i, (text, is_ed) in enumerate(lines):
            row_y = y + i - self.scroll_offset
            if row_y < y or row_y >= rows - 1:
                continue
            attr = curses.A_NORMAL
            if "▶" in text:
                attr = curses.color_pair(2) | curses.A_BOLD
            elif text.isupper() or text.startswith("RETRY"):
                attr = curses.A_BOLD | curses.color_pair(5)
            term.write(row_y, 2, text[:w])

    def _render_paths(self, term, y: int, rows: int, cols: int) -> None:
        """Paths tab: read-only info."""
        w = cols - 6
        lines = [
            "FILES",
            f"  Router config:   {ROUTER_CONFIG_JSON}",
            f"  Events JSONL:    {ROUTER_EVENTS_JSONL}",
            f"  Router log:      {ROUTER_LOG}",
            f"  Binary min size: {MIN_PRODUCTION_BINARY_SIZE // 1_000_000}MB",
            "",
            "SECURITY (enforced in routeops.config)",
            f"  Forbidden paths:     {', '.join(sorted(FORBIDDEN_PATHS))}",
            "  Forbidden env vars:  key names only — values NEVER read/displayed",
            "",
            "EDITOR SAFETY",
            "  Every config write: timestamped .bak -> validate -> atomic rename",
            "  Router never auto-restarts from TUI.",
            "",
            "RouteOps v0.2 — Phase 1B (editor + merged key tests)",
        ]
        for i, text in enumerate(lines):
            row_y = y + i
            if row_y >= rows - 1:
                break
            attr = curses.A_BOLD | curses.color_pair(5) if text.isupper() else curses.A_NORMAL
            term.write(row_y, 2, text[:w], attr)
