"""
API Keys Screen for RouteOps Curses TUI - Key pools with health bars.
"""

import curses
from datetime import datetime
from typing import List, Optional

from routeops.state import AppState
from routeops.models.provider import ProviderHealth


class APIKeysScreen:
    """API Keys screen showing key pools per provider with detail view."""

    def __init__(self, app_state: AppState):
        self.app_state = app_state
        self.scroll_offset = 0
        self.selected_index = 0
        self.show_detail = False
        self.detail_scroll = 0

    def set_size(self, width: int, height: int) -> None:
        self.width = max(60, width)
        self.height = max(15, height)

    def _sorted_providers(self) -> List[str]:
        """Routed-first ordering, shared by handle_key/render so indices match."""
        providers = list(self.app_state.providers.keys())
        routed_count = {}
        if self.app_state.config and self.app_state.config.preferences:
            for pref in self.app_state.config.preferences:
                for route in pref.routes:
                    routed_count[route.provider] = (
                        routed_count.get(route.provider, 0) + 1)
        providers.sort(key=lambda n: (-routed_count.get(n, 0), n))
        return providers

    def handle_key(self, key: int) -> bool:
        """Handle key press. Returns True if handled."""
        providers = self._sorted_providers()
        max_index = len(providers) - 1

        if key in (curses.KEY_UP, ord('k')):
            if self.show_detail:
                self.detail_scroll = max(0, self.detail_scroll - 1)
            else:
                self.selected_index = max(0, self.selected_index - 1)
                self._adjust_scroll(len(providers))
            return True

        if key in (curses.KEY_DOWN, ord('j')):
            if self.show_detail:
                self.detail_scroll += 1
            else:
                self.selected_index = min(max_index, self.selected_index + 1)
                self._adjust_scroll(len(providers))
            return True

        if key in (10, curses.KEY_ENTER):  # Enter
            if not self.show_detail and providers:
                self.show_detail = True
                self.detail_scroll = 0
            return True

        if key == ord('t'):  # live-test selected provider's keys
            if providers and not self.show_detail:
                self._test_selected(providers)
            return True

        if key in (27, ord('h'), ord('q')):  # Esc, h, q
            if self.show_detail:
                self.show_detail = False
                self.detail_scroll = 0
            else:
                return False
            return True

        return False

    def _adjust_scroll(self, item_count: int) -> None:
        visible_rows = self.height - 6
        if self.selected_index < self.scroll_offset:
            self.scroll_offset = self.selected_index
        elif self.selected_index >= self.scroll_offset + visible_rows:
            self.scroll_offset = self.selected_index - visible_rows + 1
        self.scroll_offset = max(0, min(self.scroll_offset, max(0, item_count - visible_rows)))

    def _test_selected(self, providers: List[str]) -> None:
        """Live-probe selected provider's key (catalog fetch). Result inline."""
        from routeops.adapters.config_editor import ConfigEditor
        name = providers[self.selected_index]
        pm = self.app_state.providers[name]
        ce = ConfigEditor()
        r = ce.probe_catalog(pm.config.base_url,
                              getattr(pm.config, 'key_env', ''),
                              timeout=15)
        if r.get("ok"):
            self.test_result = (f"TEST {name}: OK — key valid, "
                                f"{len(r['models'])} models reachable")
        else:
            self.test_result = f"TEST {name}: FAIL — {r.get('error','')[:60]}"

    test_result = None

    def render(self, term) -> None:
        """Render API keys screen."""
        rows, cols = term.size.rows, term.size.cols
        y = 0

        # Title bar
        title = f"● API KEYS  [{len(self.app_state.providers)} providers]  [Enter=detail]  [t]=test  [h/Esc=back]"
        term.write(y, 0, title[:cols], curses.A_BOLD | curses.color_pair(1))
        y += 1
        term.write(y, 0, "─" * (cols - 2), curses.color_pair(1))
        y += 1

        # Inline test result banner
        if self.test_result and not self.show_detail:
            color = curses.color_pair(2) if "OK" in self.test_result else curses.color_pair(4)
            term.write(y, 0, self.test_result[:cols], color | curses.A_BOLD)
            y += 1

        if self.show_detail and self.app_state.providers:
            provider_name = self._sorted_providers()[self.selected_index]
            pm = self.app_state.providers[provider_name]
            detail_lines, seg_lines = self._build_detail_lines(pm, cols - 4)
            self._render_detail_box(term, y, cols, f"KEY DETAIL: {provider_name}",
                                    detail_lines, seg_lines)
        else:
            # Provider list — routed-first (same order as handle_key)
            providers = self._sorted_providers()
            visible_rows = rows - y - 3

            for i in range(self.scroll_offset, min(self.scroll_offset + visible_rows, len(providers))):
                name = providers[i]
                pm = self.app_state.providers[name]
                health = pm.health
                line = self._format_provider_line(name, pm, health, cols - 2)
                
                if i == self.selected_index:
                    term.write(y, 0, f"▶ {line[:cols-4]}", curses.color_pair(9) | curses.A_BOLD)
                else:
                    term.write(y, 0, f"  {line[:cols-4]}")
                y += 1

            if len(providers) > visible_rows:
                indicator = f"  ↑↓ {self.scroll_offset + 1}-{min(self.scroll_offset + visible_rows, len(providers))} of {len(providers)}"
                term.write(y, 0, indicator[:cols], curses.color_pair(5))

    def _format_provider_line(self, name: str, pm, health: ProviderHealth, width: int) -> str:
        """Format single provider line for list view."""
        failures = health.failures if health.failures else 0
        models = str(health.catalog_models) if health.catalog_models else "0"
        # key count from provider CONFIG (pm.config), not health
        if getattr(pm.config, 'key_envs', None):
            keys = len(pm.config.key_envs)
        elif getattr(pm.config, 'key_env', ''):
            keys = 1
        else:
            keys = 0
        
        if health.hard_failed:
            status = "HARD-FAILED"
        elif not health.healthy:
            status = "COOLDOWN"
        else:
            status = "HEALTHY"
        
        bar_width = 10
        if health.hard_failed:
            bar = "█" * bar_width
        elif not health.healthy:
            if health.cooldown_until:
                total_cooldown = getattr(health, 'cooldown_total', 60)
                remaining = max(0, int((health.cooldown_until - datetime.now()).total_seconds()))
                progress = max(0, min(bar_width, int((1 - remaining / total_cooldown) * bar_width)))
                bar = "█" * progress + "░" * (bar_width - progress)
            else:
                bar = "░" * bar_width
        else:
            bar = "█" * bar_width
        
        return f"{name:<12}  {bar}  {status:<12}  fails:{failures:<2}  models:{models:<2}  keys:{keys}"

    def _build_detail_lines(self, pm, width: int) -> List[str]:
        """Build detail lines for key pool."""
        lines = []
        w = width - 4
        health = pm.health

        # Config keys
        lines.append("KEY POOL CONFIGURATION (from config.json)")
        lines.append("─" * w)
        lines.append("")
        
        if hasattr(pm.config, 'key_envs') and pm.config.key_envs:
            for i, key_env in enumerate(pm.config.key_envs):
                lines.append(f"  [{i}] {key_env}")
        elif pm.config.key_env:
            lines.append(f"  [0] {pm.config.key_env}")
        else:
            lines.append("  (no key_env configured)")
        
        lines.append("")
        lines.append("KEY POOL HEALTH (from /health + config)")
        lines.append("─" * w)
        lines.append("")

        # Per-key health (derived from provider health since key-level not observable)
        if hasattr(pm.config, 'key_envs') and pm.config.key_envs:
            for i, key_env in enumerate(pm.config.key_envs):
                # All keys share provider health currently
                if health.hard_failed:
                    status = "HARD-FAILED"
                    bar = "█" * 10
                elif not health.healthy:
                    status = "COOLDOWN"
                    if health.cooldown_until:
                        total = getattr(health, 'cooldown_total', 60)
                        remaining = max(0, int((health.cooldown_until - datetime.now()).total_seconds()))
                        progress = max(0, min(10, int((1 - remaining / total) * 10)))
                        bar = "█" * progress + "░" * (10 - progress)
                    else:
                        bar = "░" * 10
                else:
                    status = "HEALTHY"
                    bar = "█" * 10
                
                lines.append(f"  [{i}] {key_env:<30} {bar}  {status}")
        elif pm.config.key_env:
            if health.hard_failed:
                status = "HARD-FAILED"
                bar = "█" * 10
            elif not health.healthy:
                status = "COOLDOWN"
                bar = "░" * 10
            else:
                status = "HEALTHY"
                bar = "█" * 10
            lines.append(f"  [0] {pm.config.key_env:<30} {bar}  {status}")

        lines.append("")
        lines.append("ACTIONS (Tier 2 - requires CEO approval)")
        lines.append("─" * w)
        lines.append("")
        lines.append("  [r] Rotate key (create new, retire old)")
        lines.append("  [d] Deactivate key")
        lines.append("  [a] Activate key")
        lines.append("")
        lines.append("COOLDOWNS")
        lines.append("─" * w)
        lines.append("")
        
        if health.cooldown_until:
            now = datetime.now()
            cooldown_dt = health.cooldown_until
            if cooldown_dt.tzinfo is not None and now.tzinfo is None:
                now = now.replace(tzinfo=cooldown_dt.tzinfo)
            elif cooldown_dt.tzinfo is None and now.tzinfo is not None:
                cooldown_dt = cooldown_dt.replace(tzinfo=None)
            remaining = max(0, int((cooldown_dt - now).total_seconds()))
            lines.append(f"  Provider cooldown: {remaining}s remaining")
        else:
            lines.append("  No active cooldowns")
        
        # Recent events — colored segments, newest first
        lines.append("")
        lines.append("RECENT EVENTS (newest first)")
        lines.append("─" * w)
        lines.append("")
        CYAN = curses.color_pair(5)
        GREEN = curses.color_pair(2)
        RED = curses.color_pair(4)
        YELLOW = curses.color_pair(3)
        WHITE = 0
        seg_lines = []
        prov_events = [e for e in self.app_state.recent_events
                       if e.provider == pm.config.name]
        _now = datetime.now().astimezone()
        for event in reversed(prov_events[-7:]):
            ts = event.timestamp.strftime("%H:%M:%S") if event.timestamp else "--:--:--"
            model = event.model or ""
            outcome = event.outcome or ""
            status = event.status if event.status else ""
            error = event.error or ""
            try:
                age = (_now - event.timestamp).total_seconds() if event.timestamp else 1e9
            except (TypeError, ValueError):
                age = 1e9
            age_str = ("live" if age < 90 else f"{int(age // 60)}m" if age < 5400
                       else f"{age / 3600:.1f}h")
            if event.event_type == "route_attempt":
                seg = [(f"{ts}  ", CYAN), (f"{model or '—':<30}", WHITE),
                       ("ATTEMPT", YELLOW)]
            elif error:
                seg = [(f"{ts}  ", CYAN), (f"{model or '—':<30}", WHITE),
                       (f"{status} {error[:24]}", RED)]
            elif outcome == "success" or status == 200:
                seg = [(f"{ts}  ", CYAN), (f"{model or '—':<30}", WHITE),
                       (f"{status or 'OK'}", GREEN)]
            else:
                seg = [(f"{ts}  ", CYAN), (f"{model or '—':<30}", WHITE),
                       (f"{outcome or event.event_type}", YELLOW)]
            seg.append((f"  [{age_str}]", CYAN if age_str == "live" else curses.color_pair(3)))
            seg_lines.append(seg)
        if not seg_lines:
            seg_lines.append([(f"  (no events for {pm.config.name})", WHITE)])
        lines.extend([""] * len(seg_lines))
        return [line[:w] for line in lines], seg_lines

    def _render_detail_box(self, term, y: int, cols: int, title: str,
                           lines: List[str],
                           seg_lines: Optional[List[List]] = None) -> None:
        """Render detail box with scroll. Lines after 'RECENT EVENTS' heading
        render as colored segments when seg_lines provided."""
        w = cols - 4
        max_visible = term.size.rows - y - 2
        events_start = next((i for i, l in enumerate(lines)
                             if "RECENT EVENTS" in l), len(lines))

        term.write(y, 0, f"┌ {title} {'─' * max(0, w - len(title) - 2)}┐", curses.color_pair(1))

        seg_idx = 0
        for i in range(self.detail_scroll, min(self.detail_scroll + max_visible, len(lines))):
            row = y + 1 + (i - self.detail_scroll)
            if seg_lines and i >= events_start + 1 and seg_idx < len(seg_lines):
                term.write(row, 2, "│ ", curses.color_pair(1))
                x = 4
                room = w - 2
                for text, attr in seg_lines[seg_idx]:
                    if room <= 0:
                        break
                    term.write(row, x, text[:room], attr)
                    x += len(text[:room])
                    room -= len(text[:room])
                term.write(row, w + 2, " │", curses.color_pair(1))
                seg_idx += 1
            else:
                term.write(row, 2, f"│ {lines[i].ljust(w - 2)} │")

        term.write(y + 1 + min(max_visible, len(lines) - self.detail_scroll), 0,
                   "└" + "─" * w + "┘", curses.color_pair(1))

        if len(lines) > max_visible:
            indicator = f"  ↑↓ {self.detail_scroll + 1}-{min(self.detail_scroll + max_visible, len(lines))} of {len(lines)}"
            term.write(y + 1 + max_visible, 2, indicator[:w], curses.color_pair(5))
