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
        # Staged-diff gate for Router tab (S1/S8): no write happens on +/-
        # — values accumulate here, Enter shows a diff, Enter again commits.
        self.pending_cfg = None
        self.pending_diff = []
        self.confirm_diff = False

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

        # ---- diff-confirm mode (Router tab staged changes) ----
        if self.confirm_diff:
            if key in (curses.KEY_ENTER, 10, 13):
                self._commit_pending()
                return True
            if key in (27, ord('q'), ord('h')):
                self.confirm_diff = False
                self.pending_cfg = None
                self.pending_diff = []
                self._flash("Staged changes discarded")
                return True
            return True

        # ---- text editing mode (routeops test_prompt) ----
        if self.editing:
            if key in (curses.KEY_ENTER, 10, 13):
                self._stage_router_text("test_prompt", self.edit_buffer)
                self.editing = False
                return True
            if key in (27, ord('q')):
                self.editing = False
                self._flash("prompt edit cancelled")
                return True
            if key in (8, 127, curses.KEY_BACKSPACE):
                self.edit_buffer = self.edit_buffer[:-1]
                return True
            if 32 <= key <= 126:
                self.edit_buffer += chr(key)
                return True
            return True

        # Tab switching: 1-3 only (arrows are reserved for global page nav)
        if key in (ord("1"), ord("2"), ord("3")):
            self.current_tab = int(chr(key)) - 1
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

        # Editing — Router tab uses the staged-diff gate (S8): +/- only
        # STAGES; Enter opens the diff; Enter again commits.
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
            # Use the staged config when present so repeated Enters cycle
            # forward from the STAGED value, not from disk every time.
            if self.pending_cfg is not None:
                return self._router_editables(self.pending_cfg)
            return self._router_editables()
        return []  # Paths tab is read-only

    def _monitor_editables(self) -> List[dict]:
        """Poll intervals + display settings."""
        rows = []
        # Poll intervals (live-edit workers)
        for name in ("jsonl", "health", "models", "config", "process"):
            rows.append({"type": "interval", "key": name,
                         "label": f"Poll: {name}", "unit": "s"})
        # Display — only controls that something actually reads.
        # (event_density / compact_mode / time_format were fake controls:
        #  nothing in rendering consumed them — removed.)
        s = self.app_state.settings
        rows.append({"type": "bool", "key": "show_disclaimers",
                     "label": "Disclaimers", "value": s.show_disclaimers})
        return rows

    # knob -> risk note shown when toggling/stepping and while cursor sits on it
    KNOB_RISKS = {
        "strategy": "Changes provider order. lkgp pins last-good; p2c/priority may route to slower or costlier providers.",
        "cache.enabled": "Serves cached completions for TTL seconds. Only safe for deterministic requests; stale answers possible.",
        "cache.max_size": "Higher size uses more memory; too low evicts entries early.",
        "cache.ttl_seconds": "Higher TTL increases staleness; lower TTL reduces cache benefit.",
        "budget.enabled": "Rejects requests over the sliding window. Set RPM deliberately or clients see extra 503s.",
        "budget.requests_per_minute": "Too low throttles legitimate traffic; too high defeats the guard.",
        "budget.window_seconds": "Shorter window forgets past usage faster; longer smooths bursts less.",
        "test_timeout_seconds": "Long timeouts stall tests; short timeouts false-fail slow providers.",
        "max_hold_seconds": "Hold parks requests before 503; too long delays the client.",
        "cooldown_seconds": "Low values can fail providers too fast; high values starve fallbacks.",
        "failure_limit": "Low limits quarantine routes quickly, reducing fallback availability.",
        "hard_failed_wait_seconds": "Short waits re-probe too often; long waits leave routes down longer.",
    }

    def _knob_risk(self, key: str) -> str:
        return self.KNOB_RISKS.get(key, "")

    def _router_editables(self, cfg: dict = None) -> List[dict]:
        """Router retry/timeout settings. cfg may be the STAGED config (so
        rows reflect pending values); otherwise the on-disk config is read."""
        if cfg is None:
            try:
                cfg = json.loads(ROUTER_CONFIG_JSON.read_text())
            except Exception:
                return []
        r = cfg.get("retry", {})
        ro = cfg.get("routeops", {})
        c = cfg.get("cache", {})
        b = cfg.get("budget", {})
        rows = []
        # S2: continuation_max was removed from config (B-15) — no dead row.
        # opt = recommended value shown dimmed next to the current value.
        opt = {
            "failure_limit": 2, "cooldown_seconds": 15,
            "max_cooldown_seconds": 120, "all_failed_wait_seconds": 10,
            "route_timeout_seconds": 120, "stream_idle_timeout_seconds": 300,
            "first_event_timeout_seconds": 180, "hard_failed_wait_seconds": 10,
            "max_hold_seconds": 30, "test_timeout_seconds": 45,
            "test_prompt": "Just Reply OK",
            "cache.enabled": False, "cache.max_size": 512,
            "cache.ttl_seconds": 60,
            "budget.enabled": False, "budget.requests_per_minute": 60,
            "budget.window_seconds": 60,
            "strategy": "configured",
        }
        for key in ("failure_limit", "cooldown_seconds", "max_cooldown_seconds",
                    "all_failed_wait_seconds", "route_timeout_seconds",
                    "stream_idle_timeout_seconds", "first_event_timeout_seconds",
                    "hard_failed_wait_seconds", "max_hold_seconds"):
            rows.append({"type": "int", "key": key,
                         "label": key, "value": r.get(key, 0),
                         "opt": opt[key]})
        # routeops test knobs (used by route/key tests; default 45s prompt)
        rows.append({"type": "int", "key": "test_timeout_seconds",
                     "label": "Test timeout (s)", "value": ro.get("test_timeout_seconds", 45),
                     "opt": 45})
        rows.append({"type": "text", "key": "test_prompt",
                     "label": "Test prompt",
                     "value": ro.get("test_prompt", "Just Reply OK"),
                     "opt": "Just Reply OK"})
        # exact-match cache + sliding-window budget knobs (S13/S11-S12)
        rows.append({"type": "bool", "key": "cache.enabled",
                     "label": "Cache: enabled", "value": bool(c.get("enabled")),
                     "opt": False})
        rows.append({"type": "int", "key": "cache.max_size",
                     "label": "Cache: max size", "value": c.get("max_size", 512),
                     "opt": 512})
        rows.append({"type": "int", "key": "cache.ttl_seconds",
                     "label": "Cache: TTL (s)", "value": c.get("ttl_seconds", 60),
                     "opt": 60})
        rows.append({"type": "bool", "key": "budget.enabled",
                     "label": "Budget: enabled", "value": bool(b.get("enabled")),
                     "opt": False})
        rows.append({"type": "int", "key": "budget.requests_per_minute",
                     "label": "Budget: RPM limit", "value": b.get("requests_per_minute", 0),
                     "opt": 60})
        rows.append({"type": "int", "key": "budget.window_seconds",
                     "label": "Budget: window (s)", "value": b.get("window_seconds", 60),
                     "opt": 60})
        # routing strategy cycle (config.json top-level "strategy")
        cur_strat = cfg.get("strategy", "")
        rows.append({"type": "cycle", "key": "strategy",
                     "label": "Routing strategy",
                     "options": ["configured", "lkgp", "priority", "p2c"],
                     "value": cur_strat or "configured",
                     "opt": "configured"})
        return rows

    def _router_section(self, key: str):
        """Map a knob key to its config section + leaf name."""
        if key.startswith("cache."):
            return "cache", key.split(".", 1)[1]
        if key.startswith("budget."):
            return "budget", key.split(".", 1)[1]
        if key in ("test_timeout_seconds", "test_prompt"):
            return "routeops", key
        return "retry", key

    def _adjust(self, delta: int) -> None:
        """Adjust the currently selected editable value."""
        editables = self._get_editable_rows()
        if self.cursor >= len(editables):
            return
        item = editables[self.cursor]

        if item["type"] == "interval":
            self._adjust_interval(item["key"], delta)
        elif item["type"] == "int":
            # S1/S8: stage only, never write on a keystroke.
            self._stage_router_int(item["key"], delta)
        elif item["type"] == "bool":
            # +/- also toggles bools on the Router tab (Enter does too)
            if self.current_tab == 1:
                self._stage_router_bool(item["key"], not bool(item.get("value")))
            else:
                key = item["key"]
                s = self.app_state.settings
                setattr(s, key, not getattr(s, key, False))
                self._flash(f"{item['label']}: {'ON' if getattr(s, key) else 'OFF'}")
        elif item["type"] == "cycle":
            if self.current_tab == 1:
                # +/- cycles the strategy mode (forward/backward), staged.
                opts = item["options"]
                current = item.get("value", opts[0])
                idx = opts.index(current) if current in opts else 0
                next_val = opts[(idx + delta) % len(opts)]
                self._stage_router_strategy(next_val)
            else:
                key = item["key"]
                s = self.app_state.settings
                opts = item["options"]
                current = getattr(s, key, opts[0])
                idx = opts.index(current) if current in opts else 0
                next_val = opts[(idx + delta) % len(opts)]
                setattr(s, key, next_val)
                self._flash(f"{item['label']}: {next_val}")

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
        # S5: do NOT mutate the global POLL_INTERVALS dict — the worker is
        # the source of truth; keep config in sync for display
        self._flash(f"{name}: {old:.1f}s -> {new:.1f}s (live)")

    def _effective_cfg(self) -> dict:
        """Current working config: staged pending if present, else disk."""
        if self.pending_cfg is not None:
            return self.pending_cfg
        return json.loads(ROUTER_CONFIG_JSON.read_text())

    def _stage_router_int(self, key: str, delta: int) -> None:
        """Stage a router config change (commit only through the diff gate).
        Mutates the PENDING config when present, so repeated +/- adjust the
        staged value instead of always applying to the disk baseline."""
        try:
            cfg = self._effective_cfg()
        except Exception:
            self._flash("Cannot read config")
            return
        section, sub = self._router_section(key)
        target = cfg.setdefault(section, {})
        old = target.get(sub, 0)
        step = 1 if old < 10 else (5 if old < 60 else 10)
        new = max(0, old + delta * step)
        # S4: enforce sane minimums for timeout/limit knobs. Cache TTL and
        # budget window have NO floor — 0 is a legal value and users must be
        # able to step back down to it.
        mins = {"failure_limit": 1, "cooldown_seconds": 1,
                "hard_failed_wait_seconds": 1, "all_failed_wait_seconds": 1,
                "route_timeout_seconds": 1, "stream_idle_timeout_seconds": 1,
                "first_event_timeout_seconds": 1, "test_timeout_seconds": 1}
        if key in mins and new < mins[key]:
            new = mins[key]
            self._flash(f"{key}: minimum is {mins[key]} (staged)")
        target[sub] = new
        self.pending_cfg = cfg
        self._flash(f"{key}: {old} -> {new} (staged — Enter to review)"
                    + (f" · RISK: {self._knob_risk(key)}" if self._knob_risk(key) else ""))

    def _stage_router_bool(self, key: str, value: bool) -> None:
        """Stage a router bool knob (cache/budget enabled) via the diff gate."""
        try:
            cfg = self._effective_cfg()
        except Exception:
            self._flash("Cannot read config")
            return
        section, sub = self._router_section(key)
        target = cfg.setdefault(section, {})
        target[sub] = value
        self.pending_cfg = cfg
        self._flash(f"{key}: {'ON' if value else 'OFF'} staged — Enter to review"
                    + (f" · RISK: {self._knob_risk(key)}" if self._knob_risk(key) else ""))

    def _stage_router_strategy(self, mode: str) -> None:
        """Stage a routing-strategy change (config top-level "strategy")."""
        try:
            cfg = self._effective_cfg()
        except Exception:
            self._flash("Cannot read config")
            return
        cfg["strategy"] = "" if mode == "configured" else mode
        self.pending_cfg = cfg
        self._flash(f"strategy: {mode} staged — Enter to review"
                    + (f" · RISK: {self._knob_risk('strategy')}" if self._knob_risk('strategy') else ""))

    def _stage_router_text(self, key: str, value: str) -> None:
        """Stage a routeops text knob (test_prompt) via the diff gate."""
        try:
            cfg = self._effective_cfg()
        except Exception:
            self._flash("Cannot read config")
            return
        ro = cfg.setdefault("routeops", {})
        ro[key] = value
        self.pending_cfg = cfg
        self._flash(f"{key}: staged — Enter to review")

    def _activate(self) -> None:
        """Enter key: toggle bools, cycle enums, or open the staged diff."""
        editables = self._get_editable_rows()
        if self.cursor >= len(editables):
            # S7: a pending staged change can be reviewed even if the cursor
            # is beyond the current list (e.g. after a tab switch).
            if self.pending_cfg is not None:
                self._open_diff_preview()
            return
        item = editables[self.cursor]
        s = self.app_state.settings

        if item["type"] == "bool":
            key = item["key"]
            if self.current_tab == 1:
                # Router tab: stage the toggle through the diff gate.
                self._stage_router_bool(key, not bool(item.get("value")))
            else:
                current = getattr(s, key, False)
                setattr(s, key, not current)
                self._flash(f"{item['label']}: {'ON' if not current else 'OFF'}")
        elif item["type"] == "cycle":
            key = item["key"]
            opts = item["options"]
            if self.current_tab == 1:
                # Router tab: Enter cycles while nothing staged; once staged,
                # Enter reviews the diff (commit path). +/- also cycles.
                if self.pending_cfg is not None:
                    self._open_diff_preview()
                else:
                    current = item.get("value", opts[0])
                    idx = opts.index(current) if current in opts else 0
                    next_val = opts[(idx + 1) % len(opts)]
                    self._stage_router_strategy(next_val)
            else:
                current = getattr(s, key, opts[0])
                idx = opts.index(current) if current in opts else 0
                next_val = opts[(idx + 1) % len(opts)]
                setattr(s, key, next_val)
                self._flash(f"{item['label']}: {next_val}")
        elif item["type"] == "int":
            if self.pending_cfg is None:
                self._flash("nothing staged yet — use +/- to change this knob")
            else:
                self._open_diff_preview()
        elif item["type"] == "text":
            # Enter on a text row starts inline editing; Enter again stages.
            self.editing = True
            self.edit_buffer = str(item.get("value", ""))
            self._flash("type new value, Enter=stage, Esc=cancel")

    def _open_diff_preview(self) -> None:
        """Build diff lines from staged cfg and enter confirm mode."""
        if self.pending_cfg is None:
            return
        try:
            from routeops.adapters.config_editor import ConfigEditor
            editor = ConfigEditor()
            old = editor.load()
            self.pending_diff = editor.diff_summary(old, self.pending_cfg)
        except Exception as e:
            self.pending_diff = [f"diff error: {e}"]
        self.confirm_diff = True
        if not any(ln != "no changes" for ln in self.pending_diff):
            self._flash("No real changes staged")
            self.confirm_diff = False
            self.pending_cfg = None
            self.pending_diff = []

    def _commit_pending(self) -> None:
        """Commit staged cfg through ConfigEditor (backup+validate+atomic)."""
        if self.pending_cfg is None:
            self.confirm_diff = False
            return
        try:
            from routeops.adapters.config_editor import ConfigEditor
            editor = ConfigEditor()
            bak, _ = editor.commit(self.pending_cfg, reason="settings router tab")
            self._flash(f"Saved (bak: {bak.name}) — Restarting router now")
            # User-mandated: a confirmed config commit restarts the router
            # immediately so the change goes live without a manual window.
            try:
                from routeops.adapters.config_editor import restart_router
                result = restart_router()
                if not result.get("ok"):
                    self._flash(f"Router restart FAILED: {result.get('detail','')[:80]}")
                else:
                    self.status_msg = "Router restarted — config live."
                    self.status_timer = 40
            except Exception as e:
                self._flash(f"Router restart errored: {str(e)[:60]}")
        except Exception as e:
            self._flash(f"SAVE FAILED: {e}")
        self.confirm_diff = False
        self.pending_cfg = None
        self.pending_diff = []

    # ---------------------------------------------------------------- render

    def render(self, term) -> None:
        rows, cols = term.size.rows, term.size.cols
        y = 0

        # S8: diff-confirm overlay takes the whole screen.
        if self.confirm_diff:
            self._render_diff_confirm(term, y, rows, cols)
            return

        # Overview-standard header line
        from routeops.tui.render_std import render_header, render_box
        y = render_header(term.write, self.app_state, y, cols, rows)

        # Title line: "SETTINGS" aligned with the tabs below (col 3),
        # hint right-aligned; ruled below. The header rule above serves
        # as the top border.
        hint = "[←/→] page •  [+/- adjust] [Enter review] [1-3 tabs] [Esc back]"
        term.write(y, 3, "SETTINGS", curses.A_BOLD | curses.color_pair(1))
        term.write(y, cols - len(hint) - 1, hint[:cols - 2],
                   curses.color_pair(5) | curses.A_DIM)
        y += 1
        term.write(y, 0, "─" * (cols - 2), curses.color_pair(1))
        y += 1

        # Tab line: names only, directly under SETTINGS
        tx = 3
        full_names = self.tab_names
        compact_names = ["Mon", "Rtr", "Pth"]
        names = full_names if sum(len(n) + 2 for n in full_names) + 3 + 4 <= cols else compact_names
        for i, name in enumerate(names):
            marker = f"[{name}]" if i == self.current_tab else f" {name} "
            attr = (curses.color_pair(2) | curses.A_BOLD if i == self.current_tab
                    else curses.A_DIM)
            if tx + len(marker) > cols - 2:
                break
            term.write(y, tx, marker, attr)
            tx += len(marker) + 1
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
            body = self._monitor_body(rows, cols)
            y = render_box(term.write, y, cols, "MONITOR", body, rows)
        elif self.current_tab == 1:
            body = self._router_body(rows, cols)
            y = render_box(term.write, y, cols, "ROUTER", body, rows)
        else:
            body = self._paths_body(rows, cols)
            y = render_box(term.write, y, cols, "PATHS", body, rows)

    def _render_diff_confirm(self, term, y: int, rows: int, cols: int) -> None:
        """Full-screen diff preview for staged router config changes."""
        w = cols - 4
        term.write(y, 0, "┌ STAGED CONFIG DIFF ──────────────────────────────────────────────┐"[:cols], curses.color_pair(1))
        y += 1
        term.write(y, 2, "Enter=commit · Esc=discard", curses.color_pair(5) | curses.A_DIM)
        y += 1
        for ln in self.pending_diff[:rows - y - 2]:
            term.write(y, 2, f"│ {ln[:w - 4]}", curses.color_pair(2) if ln.startswith("+") else curses.color_pair(4) if ln.startswith("-") else 0)
            y += 1
        term.write(y, 0, "└" + "─" * (w - 2) + "┘"[:cols])

    def _monitor_body(self, rows: int, cols: int) -> list:
        """Monitor tab body: live-editable poll intervals + display toggles."""
        runtime = getattr(self.app_state, "runtime", None)
        s = self.app_state.settings

        lines = []  # (text, is_editable)
        lines.append(("POLL INTERVALS (live — +/- to adjust, takes effect immediately)", False))
        lines.append(("", False))

        editables = self._monitor_editables()
        row_idx = 0
        for name in ("jsonl", "health", "models", "config", "process"):
            interval = POLL_INTERVALS.get(name, 0)
            if runtime and runtime.worker_manager:
                wk = runtime.worker_manager.get_worker(name)
                if wk:
                    interval = wk.interval
            marker = "▶ " if self.cursor == row_idx else "  "
            lines.append((f"{marker}{name:<12} {interval:>6.1f}s", True))
            row_idx += 1

        lines.append(("", False))
        lines.append(("DISPLAY (live — Enter to toggle/cycle)", False))
        lines.append(("", False))

        marker = "▶ " if self.cursor == row_idx else "  "
        lines.append((f"{marker}Auto-refresh    {'ON' if s.auto_refresh else 'OFF'}", True))
        row_idx += 1
        marker = "▶ " if self.cursor == row_idx else "  "
        lines.append((f"{marker}Disclaimers     {'ON' if s.show_disclaimers else 'OFF'}", True))
        row_idx += 1
        marker = "▶ " if self.cursor == row_idx else "  "
        lines.append((f"{marker}Event density   {s.event_density}", True))
        row_idx += 1

        lines.append(("", False))
        lines.append(("Changes to poll intervals apply to the running workers", False))
        lines.append(("immediately. Display settings affect this TUI session.", False))

        n_editable = row_idx
        if self.cursor >= n_editable:
            self.cursor = max(0, n_editable - 1)

        body = []
        for text, is_ed in lines:
            if is_ed and "▶" in text:
                body.append([(text, curses.color_pair(2) | curses.A_BOLD)])
            elif text.isupper() or text.startswith(("POLL", "DISPLAY")):
                body.append([(text, curses.A_BOLD | curses.color_pair(5))])
            else:
                body.append(text)
        return body

    def _router_body(self, rows: int, cols: int) -> list:
        """Router tab body: retry/timeout settings, editable via +/-."""
        try:
            cfg = json.loads(ROUTER_CONFIG_JSON.read_text())
        except Exception as e:
            return [f"Cannot read config.json: {e}"]
        # If something is staged, reflect the STAGED values so toggles and
        # steps appear immediately (commit writes them for real).
        if self.pending_cfg is not None:
            cfg = self.pending_cfg

        r = cfg.get("retry", {})
        ro = cfg.get("routeops", {})
        c = cfg.get("cache", {})
        b = cfg.get("budget", {})
        lines = []
        lines.append(("RETRY & TIMEOUTS (+/- stages, Enter reviews diff, Enter commits)", False))
        lines.append(("Commit auto-restarts the router — changes apply immediately", False))
        lines.append(("", False))

        editables = self._router_editables(cfg)
        for i, item in enumerate(editables):
            marker = "▶ " if self.cursor == i else "  "
            rec = item.get("opt")
            rec_note = ""
            if rec is not None:
                rec_str = "ON" if rec is True else ("OFF" if rec is False else str(rec))
                rec_note = f"  (recommended {rec_str})"
            if item["type"] == "text":
                val = self.edit_buffer + "_" if (self.editing and i == self.cursor) else str(item["value"])
                lines.append((f"{marker}{item['label']:<28} {val}", True))
            elif item["type"] == "cycle":
                val = item.get("value", "configured")
                lines.append((f"{marker}{item['label']:<28} {val}{rec_note}", True))
            else:
                section, sub = self._router_section(item["key"])
                cfg_sec = {"retry": r, "routeops": ro, "cache": c, "budget": b}.get(section, {})
                if item["type"] == "bool":
                    val = "ON" if cfg_sec.get(sub, False) else "OFF"
                else:
                    val = cfg_sec.get(sub, 0)
                    hold_note = ""
                    if item["key"] == "max_hold_seconds":
                        hold_note = " (hold-and-serve ON)" if (val or 0) > 0 else " (OFF)"
                    val = f"{val}{hold_note}"
                lines.append((f"{marker}{item['label']:<28} {val}{rec_note}", True))

        lines.append(("", False))
        lines.append(("On confirmed save: the router is restarted automatically.", False))
        lines.append(("Manual restart: bash ~/CraftedWorkflowsOS/router/scripts/startr", False))

        # Risk note for the knob under the cursor (dim, under the list)
        if 0 <= self.cursor < len(editables):
            item = editables[self.cursor]
            risk = self._knob_risk(item["key"])
            bits = []
            if risk:
                bits.append(risk)
            if "opt" in item and item["opt"] is not None:
                bits.append(f"Recommended: {item['opt']}")
            if bits:
                lines.append(("", False))
                lines.append(("⚠ " + " | ".join(bits), False))

        n_editable = len(editables)
        if self.cursor >= n_editable:
            self.cursor = max(0, n_editable - 1)

        body = []
        for text, is_ed in lines:
            if is_ed and "▶" in text:
                body.append([(text, curses.color_pair(2) | curses.A_BOLD)])
            elif text.isupper() or text.startswith("RETRY"):
                body.append([(text, curses.A_BOLD | curses.color_pair(5))])
            else:
                body.append(text)
        return body

    def _paths_body(self, rows: int, cols: int) -> list:
        """Paths tab body: read-only info."""
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
            "  Confirmed commits auto-restart the router (user-mandated).",
            "",
            "RouteOps v0.2 — Phase 1B (editor + merged key tests)",
        ]
        body = []
        for text in lines:
            if text.isupper():
                body.append([(text, curses.A_BOLD | curses.color_pair(5))])
            else:
                body.append(text)
        return body

    # (_render_paths removed — _paths_body builds the PATHS box body)
