"""
History Screen for RouteOps Curses TUI - Filterable event log from full JSONL.
"""

import curses
from typing import List, Optional
from datetime import datetime

from routeops.state import AppState
from routeops.models.request import EventModel


class HistoryScreen:
    """History screen showing filterable event log from JSONL."""

    def __init__(self, app_state: AppState):
        self.app_state = app_state
        self.scroll_offset = 0
        self.selected_index = 0
        self.filter_mode = "all"  # all, attempts, results, fallbacks, cooldowns, health, errors
        self.show_detail = False
        self.detail_scroll = 0
        self.filter_names = ["all", "attempts", "results", "fallbacks", "cooldowns", "health", "errors"]

    def set_size(self, width: int, height: int) -> None:
        self.width = max(60, width)
        self.height = max(15, height)

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

        if key in (10, curses.KEY_ENTER):  # Enter
            if not self.show_detail and events:
                self.show_detail = True
                self.detail_scroll = 0
            return True

        if key in (27, ord('h'), ord('q')):  # Esc, h, q
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
        """Get events filtered by current filter mode — NEWEST FIRST."""
        events = list(self.app_state.recent_events)

        if self.filter_mode == "attempts":
            events = [e for e in events if e.event_type == "route_attempt"]
        elif self.filter_mode == "results":
            events = [e for e in events if e.event_type == "route_result"]
        elif self.filter_mode == "fallbacks":
            events = [e for e in events if e.event_type in ("route_fallback", "route_cooldown", "route_quarantined", "route_recovered")]
        elif self.filter_mode == "cooldowns":
            events = [e for e in events if e.event_type == "route_cooldown"]
        elif self.filter_mode == "health":
            events = [e for e in events if e.event_type == "provider_health"]
        elif self.filter_mode == "errors":
            events = [e for e in events if e.event_type == "route_result" and e.error]
        # newest first — the JSONL tail is ascending, reverse for display
        return list(reversed(events))

    def render(self, term) -> None:
        """Render history screen."""
        rows, cols = term.size.rows, term.size.cols
        y = 0

        # Title bar with filter
        filter_display = f"[{self.filter_mode.upper()}]"
        title = f"● HISTORY  {filter_display}  [f=filter]  [Enter=detail]  [h/Esc=back]"
        term.write(y, 0, title[:cols], curses.A_BOLD | curses.color_pair(1))
        y += 1
        term.write(y, 0, "─" * (cols - 2), curses.color_pair(1))
        y += 1

        events = self._get_filtered_events()

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
            term.write(y, 2, header[:cols-4], curses.color_pair(1) | curses.A_BOLD)
            y += 1
            term.write(y, 2, "─" * (cols - 4), curses.color_pair(1))
            y += 1

            # Events — colored rows, newest first
            visible_rows = rows - y - 3
            _now = datetime.now().astimezone()
            for i in range(self.scroll_offset, min(self.scroll_offset + visible_rows, len(events))):
                event = events[i]
                line, color = self._format_event_line(event, cols - 4, _now)

                if i == self.selected_index:
                    term.write(y, 2, f"▶ {line[:cols-6]}", curses.color_pair(9) | curses.A_BOLD)
                else:
                    term.write(y, 2, f"  {line[:cols-6]}", color)
                y += 1

            # Scroll indicator
            if len(events) > visible_rows:
                indicator = f"  ↑↓ {self.scroll_offset + 1}-{min(self.scroll_offset + visible_rows, len(events))} of {len(events)}"
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
