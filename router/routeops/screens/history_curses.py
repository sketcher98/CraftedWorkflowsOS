"""
History Screen for RouteOps Curses TUI - Filterable event log from full JSONL.
"""

import curses
from typing import List, Optional
from datetime import datetime

from routeops.state import AppState
from routeops.models.request import EventModel
from routeops.adapters.history_index import HistoryIndex


class HistoryScreen:
    """History screen showing filterable event log from the FULL JSONL file.

    The index is a real full-file scan (see adapters/history_index.py) — the
    footer reports true file-wide totals, not the 500-event in-memory tail.
    """

    def __init__(self, app_state: AppState):
        self.app_state = app_state
        self.scroll_offset = 0
        self.selected_index = 0
        self.filter_mode = "all"  # all, attempts, results, fallbacks, cooldowns, health, errors
        self.show_detail = False
        self.detail_scroll = 0
        self.filter_names = ["all", "attempts", "results", "fallbacks", "cooldowns", "health", "errors"]
        # Full-file index — built lazily on first render, ASYNC so the
        # first open never freezes the TUI on the full-file scan.
        self._index: Optional[HistoryIndex] = None
        self._index_path: str = ""
        self._indexing = False

    def set_size(self, width: int, height: int) -> None:
        self.width = max(60, width)
        self.height = max(15, height)

    def _ensure_index(self) -> HistoryIndex:
        """Build/refresh the full-file index (throttled to every 3s).
        The FIRST build runs on a daemon thread; until it finishes, an empty
        index is returned and render() shows an 'indexing' state."""
        import time
        from routeops.config import ROUTER_EVENTS_JSONL
        path = str(ROUTER_EVENTS_JSONL)
        now = time.time()
        if self._index is None or path != self._index_path:
            if self._index is None and not self._indexing:
                # kick off async full scan
                self._indexing = True
                self._index = HistoryIndex(path)  # empty until scanned
                self._index_path = path
                self._last_index = now  # suppress UI-thread refresh while scanning
                import threading

                def build():
                    try:
                        self._index.refresh()
                    finally:
                        self._indexing = False

                threading.Thread(target=build, daemon=True).start()
                return self._index
            if self._index is not None:
                self._index_path = path
                self._index.refresh()
                self._last_index = now
            return self._index
        elif not self._indexing and now - getattr(self, "_last_index", 0) > 3.0:
            self._index.refresh()
            self._last_index = now
        return self._index

    def handle_key(self, key: int) -> bool:
        """Handle key press. Returns True if handled."""
        events = self._get_filtered_events()
        max_index = len(events) - 1

        if key in (curses.KEY_UP, ord('k')):
            if self.show_detail:
                self.detail_scroll = max(0, self.detail_scroll - 1)
            else:
                self.selected_index = max(0, self.selected_index - 1)
                self._adjust_scroll(len(events))
            return True

        if key in (curses.KEY_DOWN, ord('j')):
            if self.show_detail:
                self.detail_scroll += 1
            else:
                self.selected_index = min(max_index, self.selected_index + 1)
                self._adjust_scroll(len(events))
            return True

        if key == ord('f'):  # Cycle filter
            current_idx = self.filter_names.index(self.filter_mode)
            self.filter_mode = self.filter_names[(current_idx + 1) % len(self.filter_names)]
            self.selected_index = 0
            self.scroll_offset = 0
            return True

        # Filter tabs: 1-7 only (arrows are reserved for global page nav)
        if key in (ord('1'), ord('2'), ord('3'), ord('4'), ord('5'), ord('6'), ord('7')):
            idx = int(chr(key)) - 1
            if idx < len(self.filter_names):
                self.filter_mode = self.filter_names[idx]
                self.selected_index = 0
                self.scroll_offset = 0
            return True

        if key in (10, curses.KEY_ENTER):  # Enter
            if not self.show_detail and events:
                self.show_detail = True
                self.detail_scroll = 0
            return True

        if key in (27, ord('q')):  # Esc, q — h is now the global History key
            if self.show_detail:
                self.show_detail = False
                self.detail_scroll = 0
            else:
                return False  # Let parent handle
            return True

        return False

    def _adjust_scroll(self, item_count: int) -> None:
        visible_rows = self.height - 6
        if self.selected_index < self.scroll_offset:
            self.scroll_offset = self.selected_index
        elif self.selected_index >= self.scroll_offset + visible_rows:
            self.scroll_offset = self.selected_index - visible_rows + 1
        self.scroll_offset = max(0, min(self.scroll_offset, max(0, item_count - visible_rows)))

    def _get_filtered_events(self) -> List[EventModel]:
        """Get events filtered by current filter mode — NEWEST FIRST.

        Sourced from the FULL-FILE index, not the 500-event in-memory tail.
        """
        index = self._ensure_index()
        return index.query(self.filter_mode)

    def render(self, term) -> None:
        """Render history screen."""
        rows, cols = term.size.rows, term.size.cols
        y = 0

        # Overview-standard header line
        from routeops.tui.render_std import render_header, render_box
        y = render_header(term.write, self.app_state, y, cols, rows)

        # Title line: HISTORY @3, hint right-aligned, ruled below
        hint = "[←/→] page •  [1-7]=filter  [Enter]=detail  [Esc]=back"
        term.write(y, 3, "HISTORY", curses.A_BOLD | curses.color_pair(1))
        term.write(y, cols - len(hint) - 1, hint[:cols - 2],
                   curses.color_pair(5) | curses.A_DIM)
        y += 1
        term.write(y, 0, "─" * (cols - 2), curses.color_pair(1))
        y += 1

        # Filter row — selectable like Settings/Diagnostics tabs (@3)
        tx = 3
        for name in self.filter_names:
            marker = f"[{name.upper()}]" if name == self.filter_mode else f" {name} "
            attr = (curses.color_pair(2) | curses.A_BOLD if name == self.filter_mode
                    else curses.A_DIM)
            if tx + len(marker) > cols - 2:
                break
            term.write(y, tx, marker, attr)
            tx += len(marker) + 1
        y += 1
        term.write(y, 0, "─" * (cols - 2), curses.color_pair(1))
        y += 1

        events = self._get_filtered_events()

        # First-open full-file scan is async — show progress until ready
        if self._indexing:
            body = [[("Indexing full event log (first open) …", curses.color_pair(3) | curses.A_BOLD)],
                    [("This happens once per routerops start — the screen stays live.", curses.A_DIM)]]
            y = render_box(term.write, y, cols, "EVENTS", body, rows)
            return

        if self.show_detail and events and self.selected_index < len(events):
            event = events[self.selected_index]
            detail_lines = self._build_detail_lines(event, cols - 4)
            self._render_detail_box(term, y, cols, f"EVENT DETAIL", detail_lines)
        else:
            # Table header — model column width matches row formatting
            w_total = cols - 4
            fixed = 8 + 5 + 14 + 11 + 7 + 12
            model_w = max(12, w_total - fixed - 10)
            header = (f"{'Time':<8}  {'Age':>5}  {'Type':<14}  {'Provider':<11}  "
                      f"{'Model':<{model_w}}  {'Status':<7}  {'Outcome'}")
            body = [[(header, curses.color_pair(1) | curses.A_BOLD)]]

            # Events — colored rows, newest first
            visible_rows = rows - y - 3
            _now = datetime.now().astimezone()
            for i in range(self.scroll_offset, min(self.scroll_offset + visible_rows, len(events))):
                event = events[i]
                line, color = self._format_event_line(event, cols - 4, _now)
                if i == self.selected_index:
                    color = color | curses.color_pair(9) | curses.A_BOLD
                    body.append([("▶ ", color), (line[:-2], color)])
                else:
                    body.append([("  ", 0), (line, color)])

            y = render_box(term.write, y, cols, "EVENTS", body, rows)

            # Scroll indicator — reports the TRUE file-wide total, not the
            # in-memory window size.
            index = self._ensure_index()
            true_total = index.count_for(self.filter_mode)
            shown_from = self.scroll_offset + 1 if events else 0
            shown_to = min(self.scroll_offset + visible_rows, len(events))
            if true_total:
                indicator = (f"  ↑↓ {shown_from}-{shown_to} of {true_total} "
                             f"(file)  ·  {len(events)} in memory")
            else:
                indicator = "  (no events)"
            term.write(y, 0, indicator[:cols], curses.color_pair(5))

    def _age_tag(self, event: EventModel, now) -> str:
        if not event.timestamp:
            return "  —"
        try:
            age = (now - event.timestamp).total_seconds()
        except (TypeError, ValueError):
            return "  —"
        if age < 90:
            return " live"
        if age < 5400:
            return f"{int(age // 60)}m".rjust(4)
        if age < 129600:
            return f"{age / 3600:.1f}h".rjust(4)
        return f"{age / 86400:.0f}d".rjust(4)

    def _format_event_line(self, event: EventModel, width: int,
                           now=None) -> tuple:
        """Format event for table view. Returns (line, curses_color).
        Width-aware: model column flexes so Status/Outcome never truncate."""
        # Date-aware timestamp: today shows HH:MM:SS, older days show MM-DD HH:MM
        if event.timestamp:
            now_dt = now if now else None
            try:
                ev_local = event.timestamp.astimezone() if event.timestamp.tzinfo else event.timestamp
                today = (now_dt or ev_local).date() if now_dt else ev_local.date()
                if ev_local.date() == today:
                    ts = ev_local.strftime("%H:%M:%S")
                else:
                    ts = ev_local.strftime("%m-%d %H:%M")
            except (ValueError, OSError):
                ts = event.timestamp.strftime("%H:%M:%S")
        else:
            ts = "--:--:--"
        age = self._age_tag(event, now) if now else "    "
        event_type = event.event_type or ""
        provider = event.provider or ""
        model = event.model or ""
        status = str(event.status) if event.status else ""
        outcome = event.outcome or ""

        # width math at usable width w_total = cols-4:
        # ts8 + age5 + type14 + prov11 + status7 = 45; 6 separators x2 = 12
        # remaining = w_total - 57 - 12 splits model (flex, min 12) vs
        # outcome (rest, min 8). Truncate model with ellipsis; outcome
        # ellipsized only if truly tiny terminal.
        usable = width if width > 0 else 80
        fixed_sep = 45 + 12
        rem = usable - fixed_sep
        model_w = max(12, rem - 14)
        outcome_w = max(8, rem - model_w)
        if len(model) > model_w:
            model = model[:model_w - 1] + "…"
        if len(outcome) > outcome_w:
            outcome = outcome[:outcome_w - 1] + "…"

        line = (f"{ts:<8}  {age:>5}  {event_type:<14}  {provider:<11}  "
                f"{model:<{model_w}}  {status:<7}  {outcome}")

        # row color by severity
        if event.error:
            color = curses.color_pair(4)   # red — error
        elif outcome == "success" or status == "200":
            color = curses.color_pair(2)   # green — success
        elif outcome in ("transient", "timeout", "stream_error") or event_type == "route_attempt":
            color = curses.color_pair(3)   # yellow — attempt/soft-fail
        else:
            color = curses.color_pair(5)   # cyan — info
        return line, color

    def _build_detail_lines(self, event: EventModel, width: int) -> List[str]:
        """Build detail lines for event."""
        lines = []
        w = width - 4

        lines.append(f"Timestamp: {event.timestamp.isoformat() if event.timestamp else 'unknown'}")
        lines.append(f"Event Type: {event.event_type or 'unknown'}")
        lines.append(f"Request ID: {event.request_id or 'unknown'}")
        lines.append(f"Provider: {event.provider or 'unknown'}")
        lines.append(f"Model: {event.model or 'unknown'}")
        lines.append(f"Preference: {event.preference or 'unknown'}")
        lines.append(f"Context: {event.context or 'request'}")
        
        if event.status:
            lines.append(f"Status Code: {event.status}")
        if event.latency_ms:
            lat_str = f"{event.latency_ms/1000:.1f}s" if event.latency_ms >= 1000 else f"{event.latency_ms}ms"
            lines.append(f"Latency: {lat_str}")
        if event.outcome:
            lines.append(f"Outcome: {event.outcome}")
        if event.error:
            lines.append(f"Error: {event.error}")
        if event.cooldown_seconds:
            lines.append(f"Cooldown: {event.cooldown_seconds}s")
        
        # Preference fallback info
        if event.from_preference and event.to_preference:
            lines.append(f"Preference Fallback: {event.from_preference} → {event.to_preference}")

        # H1: surface the GAP telemetry fields the router now emits.
        if event.requested_model:
            lines.append(f"Requested Model: {event.requested_model}")
        if event.error_kind:
            lines.append(f"Error Kind: {event.error_kind}")
        if event.build_id:
            lines.append(f"Build: {event.build_id}")
        if event.first_chunk_latency_ms:
            lines.append(f"First-chunk (TTFB): {event.first_chunk_latency_ms}ms")
        if event.fallback_depth is not None:
            lines.append(f"Fallback Depth: {event.fallback_depth}")
        if event.fallback_chain:
            lines.append(f"Fallback Chain: {' → '.join(event.fallback_chain)}")
        if event.retry_after_seconds:
            lines.append(f"Retry-After (429): {event.retry_after_seconds}s")
        if event.key_env:
            lines.append(f"Key Env: {event.key_env}")
        if event.key_id:
            lines.append(f"Key ID: {event.key_id}")

        return [line[:w] for line in lines]

    def _render_detail_box(self, term, y: int, cols: int, title: str, lines: List[str]) -> None:
        """Render detail box."""
        w = cols - 4
        max_visible = term.size.rows - y - 2
        
        term.write(y, 0, f"┌ {title} {'─' * (w - len(title) - 2)}┐", curses.color_pair(1))
        
        for i in range(self.detail_scroll, min(self.detail_scroll + max_visible, len(lines))):
            term.write(y + 1 + (i - self.detail_scroll), 2, f"│ {lines[i].ljust(w - 2)} │")
        
        term.write(y + 1 + min(max_visible, len(lines) - self.detail_scroll), 0, 
                   "└" + "─" * w + "┘", curses.color_pair(1))
        
        if len(lines) > max_visible:
            indicator = f"  ↑↓ {self.detail_scroll + 1}-{min(self.detail_scroll + max_visible, len(lines))} of {len(lines)}"
            term.write(y + 1 + max_visible, 2, indicator[:w], curses.color_pair(5))
