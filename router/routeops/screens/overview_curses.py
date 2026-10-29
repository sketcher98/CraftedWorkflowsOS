"""
Overview Screen for RouteOps Curses TUI - Boxed theme only.
"""

import curses
from datetime import datetime
from typing import Optional

from routeops.state import AppState
from routeops.models.provider import ProviderHealth
from routeops.reconstruction import ReconstructedRequest, RequestStatus
from routeops.tui.bars import (
    pct_bar, pct_subbar, pct_label, tier_color, tier_icon, hardfail_icon,
    provider_rollup, provider_routes, cooldown_text, BAR_CELLS,
    verdict, status_from_verdict, health_display,
)


class OverviewScreen:
    """Main Overview screen for RouteOps Curses TUI - Boxed theme only."""

    def __init__(self, app_state: AppState):
        self.app_state = app_state
        self.width = 80
        self.height = 24
        self.scroll_y = 0  # viewport scroll offset

    def set_size(self, width: int, height: int) -> None:
        """Update terminal dimensions."""
        self.width = max(40, width)
        self.height = max(10, height)
        # Clamp scroll to valid range after resize
        self._clamp_scroll()

    def _clamp_scroll(self) -> None:
        """Clamp scroll offset to valid range based on content height."""
        content_height = self._compute_content_height(self.width)
        max_scroll = max(0, content_height - self.height + 2)  # leave room for key help
        if self.scroll_y > max_scroll:
            self.scroll_y = max_scroll

    def _compute_content_height(self, cols: int) -> int:
        """Compute total content height in rows (without rendering)."""
        # Header: 1
        # LAST COMPLETED box: title + border(2) + content lines + 1 gap
        lc_lines = len(self._build_last_completed_lines(cols - 4))
        lc_h = lc_lines + 3
        # PROVIDER HEALTH box: title + border(2) + providers + 1 gap
        ph_lines = len(self._build_provider_health_lines(cols - 4))
        ph_h = ph_lines + 3
        # MODEL HEALTH box (if present)
        mh_lines = self._build_model_health_lines(cols - 4)
        mh_h = (len(mh_lines) + 3) if mh_lines else 0
        # LIVE EVENTS box: title + border(2) + events (max 8) + 1 gap
        le_lines = len(self._build_live_events_lines(cols - 4))
        le_h = le_lines + 3
        # SYSTEM box: title + border(2) + lines
        sys_lines = len(self._build_system_lines(cols - 4))
        sys_h = sys_lines + 3
        # +2 for key help + disclaimer
        return 1 + lc_h + 1 + ph_h + 1 + mh_h + 1 + le_h + 1 + sys_h + 2

    def handle_key(self, key: int) -> bool:
        """Handle scroll + page-tab keys. Returns True if handled, or a
        "goto:<page>" sentinel that nav_shell converts into a screen switch.
        NOTE: arrows ←/→ + 1-6 select the page tabs (like Settings/
        Diagnostics); ↑/↓ scroll the overview content."""
        page_order = ["overview", "providers", "routes", "history",
                      "diagnostics", "settings"]
        if key in (curses.KEY_LEFT,):
            cur = 0
            return f"goto:{page_order[(cur - 1) % len(page_order)]}"
        if key in (curses.KEY_RIGHT,):
            cur = 0
            return f"goto:{page_order[(cur + 1) % len(page_order)]}"
        if key in (ord('1'), ord('2'), ord('3'), ord('4'), ord('5'), ord('6')):
            idx = int(chr(key)) - 1
            if idx < len(page_order):
                return f"goto:{page_order[idx]}"
            return True
        if key in (curses.KEY_UP, ord('k'), ord('K')):
            self.scroll_y = max(0, self.scroll_y - 1)
            return True
        elif key in (curses.KEY_DOWN, ord('j'), ord('J')):
            self.scroll_y += 1
            self._clamp_scroll()
            return True
        elif key in (curses.KEY_PPAGE,):  # Page Up
            self.scroll_y = max(0, self.scroll_y - (self.height // 2))
            return True
        elif key in (curses.KEY_NPAGE,):  # Page Down
            self.scroll_y += self.height // 2
            self._clamp_scroll()
            return True
        elif key in (curses.KEY_HOME,):
            self.scroll_y = 0
            return True
        elif key in (curses.KEY_END,):
            content_height = self._compute_content_height(self.width)
            self.scroll_y = max(0, content_height - self.height + 2)
            return True
        return False

    def render(self, term) -> None:
        """Render complete overview directly to curses terminal with scroll viewport."""
        rows, cols = term.size.rows, term.size.cols
        y = 0 - self.scroll_y  # Start rendering at negative offset for scroll

        # Clip-aware write
        def write_vis(row, col, text, attr=0):
            if 0 <= row < rows:
                term.write(row, col, text, attr)

        # Header
        y = self._render_header_clipped(write_vis, y, cols, rows)

        # Title line + page tabs (same grammar as Settings/Diagnostics)
        title = "OVERVIEW"
        hint = "[↑/↓] scroll  [←/→] page  [q] quit"
        write_vis(y, 3, title, curses.A_BOLD | curses.color_pair(1))
        write_vis(y, cols - len(hint) - 1, hint, curses.color_pair(5) | curses.A_DIM)
        y += 1
        write_vis(y, 0, "─" * (cols - 2), curses.color_pair(1))
        y += 1
        page_names = ["overview", "providers", "routes", "history",
                      "diagnostics", "settings"]
        tx = 3
        for pi, pn in enumerate(page_names):
            marker = f"[{pn.upper()}]" if pi == 0 else f" {pn} "
            attr = (curses.color_pair(2) | curses.A_BOLD if pi == 0
                    else curses.A_DIM)
            if tx + len(marker) > cols - 2:
                break
            write_vis(y, tx, marker, attr)
            tx += len(marker) + 1
        y += 1
        write_vis(y, 0, "─" * (cols - 2), curses.color_pair(1))
        y += 1

        # Last completed route (full width)
        y = self._render_box_clipped(write_vis, y, cols, "LAST COMPLETED REQUEST", 
                                    self._build_last_completed_lines(cols - 4), rows)
        y += 1

        # Provider health
        y = self._render_box_clipped(write_vis, y, cols, "PROVIDER HEALTH", 
                                    self._build_provider_health_lines(cols - 4), rows)
        y += 1

        # Per-model health (JSONL-derived)
        mh_lines = self._build_model_health_lines(cols - 4)
        if mh_lines:
            y = self._render_box_clipped(write_vis, y, cols, "MODEL HEALTH (JSONL)", mh_lines, rows)
            y += 1

        # Live events
        y = self._render_box_clipped(write_vis, y, cols, "LIVE EVENTS", 
                                    self._build_live_events_lines(cols - 4), rows)
        y += 1

        # System
        y = self._render_box_clipped(write_vis, y, cols, "SYSTEM", 
                                    self._build_system_lines(cols - 4), rows)

        # Disclaimer (at bottom) — key help moved to the top tab row
        self._render_disclaimer_clipped(write_vis, rows - 1, cols)

    def _render_header_clipped(self, write, y: int, cols: int, rows: int) -> int:
        """Overview-standard header (shared module)."""
        from routeops.tui.render_std import render_header
        return render_header(write, self.app_state, y, cols, rows)

    def _render_box_clipped(self, write, y: int, cols: int, title: str, lines: list, rows: int) -> int:
        """Overview-standard boxed section (shared module)."""
        from routeops.tui.render_std import render_box
        return render_box(write, y, cols, title, lines, rows)

    def _render_disclaimer_clipped(self, write, y: int, cols: int) -> None:
        """Render disclaimer as a full-width colored bar (background fills the line).
        Honored by the show_disclaimers setting — a settings row that nothing
        reads is a fake control."""
        enabled = getattr(self.app_state.settings, "show_disclaimers", True)
        if not enabled:
            return
        if 0 <= y < cols:
            disc = "ROUTEOPS — external observer of the router. Source: /health · /v1/models · config.json · router-events.jsonl"
            # Truncate to fit, then pad with spaces so the color fills the WHOLE row
            write(y, 0, disc[:cols].ljust(cols), curses.color_pair(8))

    def _build_model_health_lines(self, width: int) -> list:
        """Per-(model, provider) health derived from JSONL route_result events.
        Shows ONLY models in active preferences (config.json) — config is the
        source of truth; add/remove/rearrange reflects here immediately.
        Layout mirrors PROVIDER HEALTH: name | bar | pct | counts | last."""
        mh = getattr(self.app_state, '_model_health', {})
        if not mh:
            # No post-baseline telemetry yet — say so instead of hiding the box.
            # Baseline = router process start (or TUI start if unknown)
            baseline_str = "—"
            st = getattr(self.app_state, '_tui_start_time', None)
            proc = getattr(self.app_state, 'process_info', None)
            if proc and proc.uptime_seconds > 0:
                from datetime import datetime, timedelta
                rt = datetime.now().astimezone() - timedelta(seconds=proc.uptime_seconds)
                baseline_str = rt.strftime("%H:%M:%S") + " (router)"
            elif st:
                baseline_str = st.strftime("%H:%M:%S") + " (TUI)"
            return [f"No route results since {baseline_str} — bars accumulate live traffic only"]

        # Active models = union of models across all configured preferences,
        # in preference order (config order, not history order)
        active_models = []
        active_providers = {}
        if self.app_state.config:
            for pref in self.app_state.config.preferences:
                for route in pref.routes:
                    if route.model not in active_models:
                        active_models.append(route.model)
                    active_providers.setdefault(route.model, [])
                    if route.provider not in active_providers[route.model]:
                        active_providers[route.model].append(route.provider)

        GREEN = curses.color_pair(2)
        YELLOW = curses.color_pair(3)
        RED = curses.color_pair(4)
        WHITE = 0
        DIM = curses.A_DIM

        NAME_W = 20  # model/provider name column — bars align at this width

        lines = []
        for model in active_models:
            providers = active_providers.get(model, [])
            if not providers:
                continue

            # --- Model header: name + aggregate bar + pct + total requests
            total_ok = total_fail = total_abort = 0
            any_prov_healthy = False
            for prov in providers:
                st = mh.get((prov, model))
                if st:
                    total_ok += st.get("ok", 0)
                    total_fail += st.get("fail", 0)
                    total_abort += st.get("abort", 0)
            total = total_ok + total_fail + total_abort
            n_obs = total_ok + total_fail
            # Zero-traffic model: fall back to the router's own verdict
            if n_obs == 0:
                for prov in providers:
                    pm = self.app_state.providers.get(prov)
                    if pm and pm.health and pm.health.healthy and not pm.health.hard_failed:
                        any_prov_healthy = True
                        break
                pct = 100.0 if any_prov_healthy else None
            else:
                pct = (total_ok / n_obs * 100)
            # ONE shared verdict for the model header — bar, icon and status
            # all come from the same evidence.
            hv = health_display(pct, n_obs, total_fail)
            m_short = model.split("/")[-1][:NAME_W - 1]
            req_note = f"  ·  {total} req"
            if total_abort:
                req_note += f" ({total_abort} aborted)"
            lines.append([
                (f"{m_short:<{NAME_W}}", curses.A_BOLD),
                (pct_bar(hv.bar_pct), hv.bar_color),
                (f" {pct_label(hv.bar_pct)}", hv.bar_color | curses.A_BOLD),
                (f" {hv.icon}", hv.text_color),
                (req_note, DIM),
            ])

            # --- Provider rows: aligned under the model header
            for prov in providers:
                st = mh.get((prov, model))
                # Check provider health from /health
                pm = self.app_state.providers.get(prov)
                prov_healthy = True
                if pm and pm.health:
                    prov_healthy = pm.health.healthy and not pm.health.hard_failed
                
                if st:
                    p_ok = st.get("ok", 0)
                    p_fail = st.get("fail", 0)
                    p_abort = st.get("abort", 0)
                    p_total = p_ok + p_fail
                    p_pct = (p_ok / p_total * 100) if p_total else None
                    # ONE shared verdict — the same tier scale as the parent.
                    hv = health_display(p_pct, p_total, p_fail)
                    last = st["last_outcome"] or "—"
                    lc = GREEN if last == "success" else (RED if last in ("failure", "timeout") else DIM)
                    counts = f"  {p_ok:>3} ok  {p_fail:>3} fail"
                    if p_abort:
                        counts += f"  {p_abort:>3} abort"
                    lines.append([
                        (f"  {prov:<{NAME_W - 2}}", WHITE),
                        (pct_subbar(hv.bar_pct), hv.bar_color),
                        (f" {pct_label(hv.bar_pct)}", hv.bar_color),
                        (f" {hv.icon}", hv.text_color),
                        (counts, WHITE),
                        (f"  {last}", lc),
                    ])
                else:
                    # Zero traffic for this provider/model — configured and
                    # routed but not yet exercised. Full green bar + NO DATA
                    # YET, never an empty/red bar that reads as dead.
                    hv = health_display(None, 0, 0)
                    lines.append([
                        (f"  {prov:<{NAME_W - 2}}", WHITE),
                        (pct_subbar(hv.bar_pct), hv.bar_color),
                        (f" {pct_label(hv.bar_pct)}", hv.bar_color),
                        (f" {hv.icon}", hv.text_color),
                        (f"  0 ok  0 fail", DIM),
                        (f"  —", DIM),
                    ])
            lines.append("")  # gap between models

        if lines and lines[-1] == "":
            lines.pop()
        return lines

    def _build_last_completed_lines(self, width: int) -> list:
        """Build last completed route box content lines with fallback chain."""
        lines = []
        w = width - 4
        req = self.app_state.last_request

        if not req:
            lines.append("No completed route observed")
            return [line[:w] for line in lines]

        if isinstance(req, dict):
            preference = req.get('preference', 'unknown')
            fallback = req.get('fallback_chain', [])
            model = req.get('final_model', req.get('model', 'unknown'))
            outcome = req.get('outcome', 'unknown')
            status_code = req.get('status_code')
            latency = req.get('total_latency_ms', req.get('latency_ms'))
            ts = req.get('last_timestamp') or req.get('first_timestamp')
        else:
            preference = getattr(req, 'preference', 'unknown') or 'unknown'
            fallback = getattr(req, 'fallback_chain', [])
            model = getattr(req, 'final_model', None) or getattr(req, 'model', 'unknown')
            outcome = getattr(req, 'outcome', 'unknown')
            status_code = getattr(req, 'status_code', None)
            latency = getattr(req, 'total_latency_ms', None)
            ts = getattr(req, 'last_timestamp', None) or getattr(req, 'first_timestamp', None)

        # EXACT time of the completed request — the JSONL timestamp, not a
        # generic "now". A last-completed box without a clock is useless.
        if ts:
            lines.append(f"Time: {self._format_event_ts(ts)}")

        lines.append(f"Preference: {preference}")

        # Render fallback chain with arrows and status codes
        if fallback and len(fallback) > 1:
            chain_parts = []
            for i, prov in enumerate(fallback):
                chain_parts.append(prov)
            # Audit catch: the literal '── 429 ──→' was a fabricated status —
            # do NOT claim a status we don't have. Neutral arrow when real
            # per-attempt statuses are unavailable.
            chain = " ──→ ".join(chain_parts) if len(fallback) > 1 else fallback[0]
            # Build from real per-attempt statuses when they exist.
            attempts = req.get('attempts') if isinstance(req, dict) else getattr(req, 'attempts', None)
            if attempts:
                chain = self._build_chain_from_attempts(attempts)
            lines.append(chain)
        elif fallback:
            lines.append(f"Provider: {fallback[0]}")

        lines.append(f"Model: {model}")

        outcome_str = outcome
        if status_code:
            outcome_str += f" ({status_code})"
        lines.append(f"Status: {outcome_str}")

        if latency:
            lat_str = f"{latency/1000:.1f}s" if latency >= 1000 else f"{latency}ms"
            lines.append(f"Latency: {lat_str}")

        return [line[:w] for line in lines]

    def _build_chain_from_attempts(self, attempts) -> str:
        """Build fallback chain string from reconstructed attempts."""
        if not attempts:
            return ""

        parts = []
        prev_provider = None

        for i, attempt in enumerate(attempts):
            provider = getattr(attempt, 'provider', 'unknown')
            event_type = getattr(attempt, 'event_type', '')
            status_code = getattr(attempt, 'status_code', None)

            if i == 0:
                parts.append(provider)
                prev_provider = provider
            else:
                # Only show arrow if provider changed (actual fallback)
                if provider != prev_provider:
                    prev_status = getattr(attempts[i-1], 'status_code', None)
                    if prev_status:
                        parts.append(f"── {prev_status} ──→ {provider}")
                    else:
                        parts.append(f"──→ {provider}")
                    prev_provider = provider
                # Same provider = retry, don't add to chain

        # Add final status if available
        if attempts and getattr(attempts[-1], 'status_code', None):
            final_status = getattr(attempts[-1], 'status_code')
            parts.append(f"── {final_status}")

        return " ".join(parts)

    def _build_provider_health_lines(self, width: int) -> list:
        lines = []
        w = width - 4

        if not self.app_state.providers:
            lines.append("No providers configured")
            return [line[:w] for line in lines]

        lines.append("Health: /health (provider-level) + JSONL results (per-model, below)")
        lines.append("")

        healthy = degraded = hard_failed = unrouted = 0

        # Alphabetical by provider name — /health response order is arbitrary
        # (map iteration), which made the box read as shuffled.
        for name in sorted(self.app_state.providers.keys()):
            pm = self.app_state.providers[name]
            health = pm.health
            failures = health.failures if health.failures else 0
            cd = cooldown_text(health)

            models = str(health.catalog_models) if health.catalog_models else "0"
            keys = len(pm.config.key_envs) if hasattr(pm.config, 'key_envs') else 1

            n_routes = provider_routes(self.app_state, name)

            # Unrouted provider: configured but no preference references it.
            # /health saying "healthy" means nothing — it serves no traffic.
            # HIDE from overview — stays visible on Providers screen
            if n_routes == 0:
                unrouted += 1
                continue

            # Real success % from JSONL rollup — same meaning as MODEL HEALTH bars
            pct, _ok, _fail, _abort = provider_rollup(self.app_state, name)
            n_obs = (_ok + _fail)
            router_healthy = health.healthy and not health.hard_failed

            # ONE shared verdict drives the bar, the icon AND the status word,
            # so a row can never read '0.0% ○ HEALTHY'.
            in_cd = not health.healthy and not health.hard_failed
            hv = health_display(pct, n_obs, _fail, in_cd)

            if health.hard_failed:
                hard_failed += 1
                status = "HARD-FAILED"
                icon, icon_c = hardfail_icon()
                status_color = curses.color_pair(4)
                bar_c = curses.color_pair(4)
            elif not health.healthy:
                degraded += 1
                status = "COOLDOWN"
                icon, icon_c = ("◐", curses.color_pair(3))
                status_color = curses.color_pair(3)
                bar_c = hv.bar_color
            else:
                healthy += 1
                status = hv.status
                icon, icon_c = hv.icon, hv.text_color
                status_color = hv.text_color
                bar_c = hv.bar_color

            lines.append([
                (f"{name:<12}  ", curses.A_BOLD),
                (pct_bar(hv.bar_pct), bar_c),
                (f" {pct_label(hv.bar_pct)} ", bar_c | curses.A_BOLD),
                (f"{icon} ", icon_c),
                (f"{status:<12} ", status_color),
                (f" fails:{failures:<2}  {cd:>7}  models:{models:<2}  keys:{keys}", 0),
            ])

        total = healthy + degraded + hard_failed
        # The summary must count what the ROWS show. A row rendered DEGRADED
        # (or FAILING) cannot be counted as OK, or the summary contradicts
        # the rows directly above it.
        n_healthy = n_degrading = n_degraded = n_unhealthy = 0
        for name in sorted(self.app_state.providers.keys()):
            if provider_routes(self.app_state, name) == 0:
                continue
            pm = self.app_state.providers[name]
            h = pm.health
            if h.hard_failed or not h.healthy:
                continue
            pct, ok_t, fail_t, _ab = provider_rollup(self.app_state, name)
            v = verdict(pct, ok_t + fail_t)
            if v == "healthy":
                n_healthy += 1
            elif v == "degrading":
                n_degrading += 1
            elif v == "degraded":
                n_degraded += 1
            else:
                n_unhealthy += 1

        summary = f"Total: {total}  OK: {n_healthy}"
        if n_degrading:
            summary += f"  Degrading: {n_degrading}"
        if n_degraded:
            summary += f"  Degraded: {n_degraded}"
        if n_unhealthy:
            summary += f"  Unhealthy: {n_unhealthy}"
        if degraded:
            summary += f"  Cooldown: {degraded}"
        if hard_failed:
            summary += f"  Hard-failed: {hard_failed}"
        if unrouted:
            summary += f"  (No-route hidden: {unrouted})"
        lines.append("")
        lines.append(summary)

        # Truncate segment lists to width; strings truncated as before
        out = []
        for item in lines:
            if isinstance(item, list):
                # Trim segments to width w-4
                used = 0
                trimmed = []
                for text, attr in item:
                    if used >= w - 4:
                        break
                    room = (w - 4) - used
                    trimmed.append((text[:room], attr))
                    used += len(text[:room])
                out.append(trimmed)
            else:
                out.append(item[:w])
        return out

    @staticmethod
    def _format_event_ts(ts) -> str:
        """Format a JSONL timestamp as 'HH:MM:SS' in LOCAL time.

        Accepts either a datetime (EventModel.timestamp) or the raw ISO
        string the router writes ('2026-10-28T07:39:12.472406611Z').
        """
        if ts is None:
            return "—"
        if hasattr(ts, "strftime"):
            return ts.strftime("%H:%M:%S")
        try:
            from datetime import datetime
            dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
            return dt.astimezone().strftime("%H:%M:%S")
        except (ValueError, AttributeError, TypeError):
            return str(ts)

    def _build_live_events_lines(self, width: int) -> list:
        """Build live events lines showing full fallback chain (colored)."""
        lines = []
        w = width - 4
        events = self.app_state.recent_events

        if not events:
            lines.append("No telemetry events")
            return [line[:w] for line in lines]

        CYAN = curses.color_pair(5)
        GREEN = curses.color_pair(2)
        RED = curses.color_pair(4)
        YELLOW = curses.color_pair(3)
        WHITE = 0

        # Show last 5 events with full detail
        # Per-event age helper (tags every line, not just the newest)
        now_dt = datetime.now().astimezone()

        def _age_of(ev) -> str:
            if not ev.timestamp:
                return "—"
            try:
                age = (now_dt - ev.timestamp).total_seconds()
            except (TypeError, ValueError):
                return "—"
            if age < 90:
                return "live"
            if age < 5400:
                return f"{int(age // 60)}m"
            if age < 129600:
                return f"{age / 3600:.1f}h"
            return f"{age / 86400:.0f}d"

        # NEWEST-FIRST display (newest at top), each line age-tagged
        shown = list(reversed(events[-5:]))
        for idx, event in enumerate(shown):
            ts = event.timestamp.strftime("%H:%M:%S") if event.timestamp else "--:--:--"
            provider = event.provider or ""
            model = event.model or ""
            outcome = event.outcome or ""
            status = event.status if event.status else ""
            error = event.error or ""
            age_str = _age_of(event)
            age_tag = (f"  [{age_str}]", CYAN if age_str == "live" else curses.color_pair(3))

            if event.event_type == "route_attempt":
                seg = [
                    (f"{ts}  ", CYAN),
                    (f"{provider} / {model}     ", WHITE),
                    (status or "ATTEMPT", YELLOW),
                ]
            elif event.event_type == "route_result":
                if error:
                    seg = [
                        (f"{ts}  ", CYAN),
                        (f"{provider} / {model}     ", WHITE),
                        (f"{status} {error}", RED),
                    ]
                else:
                    seg = [
                        (f"{ts}  ", CYAN),
                        (f"{provider} / {model}     ", WHITE),
                        (f"{status} OK", GREEN),
                    ]
            elif event.event_type == "route_cooldown":
                seg = [
                    (f"{ts}  ", CYAN),
                    (f"{provider} / {model or '—'}     ", WHITE),
                    (f"→ cooldown {event.cooldown_seconds or '?'}s", YELLOW),
                ]
            elif event.event_type == "route_quarantined":
                seg = [
                    (f"{ts}  ", CYAN),
                    (f"{provider} / {model or '—'}     ", WHITE),
                    ("✗ QUARANTINED (permanent)", RED),
                ]
            elif event.event_type == "route_recovered":
                seg = [
                    (f"{ts}  ", CYAN),
                    (f"{provider} / {model or '—'}     ", WHITE),
                    ("✓ RECOVERED", GREEN),
                ]
            elif event.event_type == "preference_fallback":
                seg = [
                    (f"{ts}  ", CYAN),
                    (f"→ pref {event.from_preference or '?'} → {event.to_preference or '?'}     ", WHITE),
                    ("PREFERENCE FALLBACK", YELLOW),
                ]
            elif event.event_type == "stream_result":
                if error:
                    seg = [
                        (f"{ts}  ", CYAN),
                        (f"{provider or '—'} / {model or '—'}     ", WHITE),
                        (f"STREAM ERROR {error}", RED),
                    ]
                else:
                    seg = [
                        (f"{ts}  ", CYAN),
                        (f"{provider or '—'} / {model or '—'}     ", WHITE),
                        (f"STREAM {status or outcome or 'OK'}", GREEN),
                    ]
            elif event.event_type == "truncation_detected":
                seg = [
                    (f"{ts}  ", CYAN),
                    (f"{provider or '—'} / {model or '—'}     ", WHITE),
                    ("⚠ TRUNCATION DETECTED", YELLOW),
                ]
            elif event.event_type == "route_skipped":
                reason = outcome or "?"
                rcolor = YELLOW if reason in ("cooldown", "unhealthy") else RED
                seg = [
                    (f"{ts}  ", CYAN),
                    (f"{provider or '—'} / {model or '—'}     ", WHITE),
                    (f"SKIP ({reason})", rcolor),
                ]
            elif event.event_type == "request_started":
                seg = [
                    (f"{ts}  ", CYAN),
                    (f"req {event.request_id or '?'} → {event.preference or '?'}    ", WHITE),
                    ("STARTED", GREEN),
                ]
            elif event.event_type == "request_exhausted":
                seg = [
                    (f"{ts}  ", CYAN),
                    (f"req {event.request_id or '?'} → {event.preference or '?'}    ", WHITE),
                    ("EXHAUSTED (503)", RED),
                ]
            elif event.event_type == "client_abort":
                seg = [
                    (f"{ts}  ", CYAN),
                    (f"{provider or '—'} / {model or '—'}     ", WHITE),
                    ("CLIENT ABORT (no penalty)", YELLOW),
                ]
            elif event.event_type == "route_committed":
                ttfb = getattr(event, "first_chunk_latency_ms", None) or ""
                ttfb_s = f" ttfb={ttfb}ms" if ttfb else ""
                seg = [
                    (f"{ts}  ", CYAN),
                    (f"{provider or '—'} / {model or '—'}     ", WHITE),
                    (f"COMMITTED{ttfb_s}", GREEN),
                ]
            elif event.event_type == "catalog_refreshed":
                seg = [
                    (f"{ts}  ", CYAN),
                    (f"{provider or '—'} catalog: ", WHITE),
                    (f"{model or '?'} models", GREEN),
                ]
            elif event.event_type == "catalog_skip":
                seg = [
                    (f"{ts}  ", CYAN),
                    (f"{provider or '—'} catalog: ", WHITE),
                    (f"SKIP ({outcome or '?'})", RED),
                ]
            elif event.event_type == "credential_rotation_signal":
                seg = [
                    (f"{ts}  ", CYAN),
                    (f"{provider or '—'} key {event.key_env or '?'}: ", WHITE),
                    ("ROTATION SIGNAL", RED),
                ]
            elif event.event_type == "health_transition":
                tcolor = RED if outcome in ("healthy_to_cooldown", "unhealthy_to_hardfailed") else GREEN
                seg = [
                    (f"{ts}  ", CYAN),
                    (f"{provider or '—'} / {model or '—'}: ", WHITE),
                    (outcome, tcolor),
                ]
            elif event.event_type == "provider_health":
                color = GREEN if outcome in ("healthy", "recovered") else RED
                seg = [
                    (f"{ts}  ", CYAN),
                    (f"{provider} health: ", WHITE),
                    (outcome, color),
                ]
            else:
                seg = [(f"{ts}  {event.event_type}", WHITE)]

            if age_tag:
                seg.append(age_tag)
            lines.append(seg)

        # Truncate segments to width
        out = []
        for item in lines:
            if isinstance(item, list):
                used = 0
                trimmed = []
                for text, attr in item:
                    if used >= w:
                        break
                    room = w - used
                    trimmed.append((text[:room], attr))
                    used += len(text[:room])
                out.append(trimmed)
            else:
                out.append(item[:w])
        return out

    def _build_system_lines(self, width: int) -> list:
        """Build system info lines — REAL readings from /proc + df, with bars."""
        lines = []
        w = width - 4

        proc = self.app_state.process_info
        # Uptime indicator is derived from the observed process identity.
        if proc and proc.identity == "PRODUCTION":
            uptime_indicator = "✓ UP"
        else:
            uptime_indicator = "✗ DOWN"

        # Real memory from /proc/meminfo (cheap, no subprocess)
        mem_pct = None
        try:
            with open("/proc/meminfo") as f:
                mi = {}
                for _ in range(5):
                    k, v = f.readline().split(":")
                    mi[k] = int(v.strip().split()[0])
                total = mi.get("MemTotal", 1)
                avail = mi.get("MemAvailable", 0)
                mem_pct = round(100 * (total - avail) / total)
        except Exception:
            pass

        # Real disk from statvfs
        disk_pct = None
        try:
            import os
            st = os.statvfs("/data")
            disk_pct = round(100 * (1 - st.f_bavail / st.f_blocks))
        except Exception:
            pass

        def usage_bar(pct, cells=BAR_CELLS):
            """Usage bar for system resources: fill = USED, green<60/yellow<90/red>=90."""
            if pct is None:
                return ("[" + "░" * cells + "]", DIMC)
            filled = round(pct / 100 * cells)
            if pct >= 90:
                c = curses.color_pair(4)
            elif pct >= 60:
                c = curses.color_pair(3)
            else:
                c = curses.color_pair(2)
            return ("[" + "█" * filled + "░" * (cells - filled) + "]", c)

        DIMC = curses.A_DIM
        mem_bar, mem_c = usage_bar(mem_pct)
        mem_lbl = f"{mem_pct}%" if mem_pct is not None else "—"
        disk_bar, disk_c = usage_bar(disk_pct)
        disk_lbl = f"{disk_pct}%" if disk_pct is not None else "—"

        # Line 1 as segments: Hermes + Memory bar + Disk bar
        # The Router status is NOT repeated here — the header already reports
        # it (LIVE / PID / uptime / port) from the observed process. A second
        # copy in the SYSTEM box was a hardcoded literal that could disagree
        # with the header.
        # Hermes status is PROBED (process worker collects live Hermes PIDs) —
        # it is never a hardcoded "RUNNING" literal.
        hermes_pids = getattr(self.app_state, "_hermes_pids", []) or []
        if hermes_pids:
            hermes_str = f"Hermes ● RUNNING ({len(hermes_pids)} proc)"
            hermes_attr = curses.color_pair(2)
        else:
            hermes_str = "Hermes ○ NOT OBSERVED"
            hermes_attr = curses.color_pair(3)

        lines.append([
            (hermes_str, hermes_attr),
            ("    ", 0),
            ("Memory ", 0), (mem_bar, mem_c), (f" {mem_lbl}", mem_c),
            ("   Disk ", 0), (disk_bar, disk_c), (f" {disk_lbl}", disk_c),
        ])

        if proc and proc.uptime_seconds > 0:
            hours = proc.uptime_seconds // 3600
            mins = (proc.uptime_seconds % 3600) // 60
            if hours > 0:
                uptime_str = f"{hours}h {mins}m"
            else:
                uptime_str = f"{mins}m"
            lines.append(f"Uptime: {uptime_str}   {uptime_indicator}")
        elif proc and proc.identity == "PRODUCTION":
            lines.append(f"Uptime: starting...   {uptime_indicator}")

        # Routing strategy (live config read)
        try:
            from routeops.adapters.config_editor import ConfigEditor as _CE
            strat = _CE().current_strategy()
        except Exception:
            strat = ""
        strat_attr = curses.color_pair(5) | curses.A_BOLD
        lines.append([(f"Strategy: {'LKGP' if strat else 'configured order'}", strat_attr)])

        return [line[:w] for line in lines]

    def _render_disclaimer(self, term, y: int, cols: int) -> None:
        """Render mandatory disclaimer as a full-width colored bar."""
        disclaimer = "ROUTEOPS — external observer of the router. Source: /health · /v1/models · config.json · router-events.jsonl"
        term.write(y, 0, disclaimer[:cols].ljust(cols), curses.color_pair(8))

    def _render_key_help(self, term, y: int, cols: int) -> None:
        """Render key help at bottom."""
        help_text = "[p] Providers  [r] Routes  [h] History  [d] Diagnostics  [s] Settings  [q] Quit  [↑/↓] Scroll  [←/→] Screen"
        term.write(y, 0, help_text[:cols], curses.color_pair(5) | curses.A_DIM)


from datetime import datetime
