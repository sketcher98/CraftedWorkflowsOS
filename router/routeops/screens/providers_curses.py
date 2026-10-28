"""
Providers Screen for RouteOps Curses TUI - Scrollable list with detail drawer.
"""

import curses
from datetime import datetime
from typing import Optional, List

from routeops.state import AppState
from routeops.models.provider import ProviderHealth
from routeops.tui.bars import (
    pct_bar, pct_subbar, pct_label, tier_color, tier_icon, hardfail_icon,
    provider_rollup, provider_routes, cooldown_text, BAR_CELLS,
    verdict, status_from_verdict, health_display,
)


class ProvidersScreen:
    """Providers health screen with scrollable list and detail drawer."""

    def __init__(self, app_state: AppState):
        self.app_state = app_state
        self.scroll_offset = 0
        self.selected_index = 0
        self.show_detail = False
        self.detail_scroll = 0

    def set_size(self, width: int, height: int) -> None:
        self.width = max(40, width)
        self.height = max(10, height)

    def _sorted_providers(self) -> List[str]:
        """Providers with active routes first (most routes first), unrouted last.
        Shared by handle_key and render so indices always match."""
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

        if key == ord('t'):  # live-test selected provider
            if providers and not self.show_detail:
                self._test_selected(providers)
            return True

        if key in (27, ord('h'), ord('q')):  # Esc, h, q
            if self.show_detail:
                self.show_detail = False
                self.detail_scroll = 0
            else:
                return False  # Let parent handle
            return True
        
        if key == ord('m'):  # Key Management (Termux-safe; was Shift+K, unreachable on phone)
            if providers:
                self.app_state.detail_target = providers[self.selected_index]
                # Signal to parent to switch to keymgmt screen
                return "keymgmt"
            return True

        if key == ord('i'):  # Per-Key Health (inspect keys of selected provider)
            if providers:
                self.app_state.detail_target = providers[self.selected_index]
                return "perkey"
            return True

        return False

    def _test_selected(self, providers: List[str]) -> None:
        """Live-test ALL key pools of the selected provider, each individually.
        Result: per-key status lines shown in the detail-style inline banner."""
        from routeops.adapters.config_editor import ConfigEditor
        name = providers[self.selected_index]
        pm = self.app_state.providers[name]
        ce = ConfigEditor()
        key_envs = list(getattr(pm.config, 'key_envs', None) or
                        ([pm.config.key_env] if getattr(pm.config, 'key_env', '') else []))
        if not key_envs:
            self.test_result = f"TEST {name}: no keys configured"
            self.test_lines = None
            return
        lines = []
        any_ok = False
        for i, kenv in enumerate(key_envs):
            r = ce.probe_catalog(pm.config.base_url, kenv, timeout=15)
            if r.get("ok"):
                lines.append(f"  [{i}] {kenv:<44} ● live ({len(r['models'])} models)")
                any_ok = True
            else:
                lines.append(f"  [{i}] {kenv:<44} ✗ FAIL {r.get('error','')[:30]}")
        verdict = "OK" if any_ok else "ALL KEYS DEAD"
        self.test_result = f"KEY TEST {name}: {verdict} — {len(key_envs)} pool(s):"
        self.test_lines = lines

    test_result = None
    test_lines = None

    def _adjust_scroll(self, item_count: int) -> None:
        """Adjust scroll offset to keep selected item visible."""
        visible_rows = self.height - 6  # Approximate visible items
        if self.selected_index < self.scroll_offset:
            self.scroll_offset = self.selected_index
        elif self.selected_index >= self.scroll_offset + visible_rows:
            self.scroll_offset = self.selected_index - visible_rows + 1
        self.scroll_offset = max(0, min(self.scroll_offset, max(0, item_count - visible_rows)))

    def render(self, term) -> None:
        """Render providers screen."""
        rows, cols = term.size.rows, term.size.cols
        y = 0

        # Title bar
        title = f"● PROVIDERS  [{len(self.app_state.providers)} providers]  [Enter=detail]  [t]=test  [m]=keys  [i]=per-key  [h/Esc] Back"
        term.write(y, 0, title[:cols], curses.A_BOLD | curses.color_pair(1))
        y += 1
        term.write(y, 0, "─" * (cols - 2), curses.color_pair(1))
        y += 1

        # Inline test result banner (multi-line per-key results)
        if self.test_result and not self.show_detail:
            ok = "OK" in self.test_result
            term.write(y, 0, self.test_result[:cols],
                       (curses.color_pair(2) if ok else curses.color_pair(4)) | curses.A_BOLD)
            y += 1
            if self.test_lines:
                for tl in self.test_lines:
                    color = curses.color_pair(2) if "● live" in tl else curses.color_pair(4)
                    term.write(y, 0, tl[:cols], color)
                    y += 1

        if self.show_detail and self.app_state.providers:
            # Detail drawer
            provider_name = self._sorted_providers()[self.selected_index]
            pm = self.app_state.providers[provider_name]
            detail_lines, seg_lines = self._build_detail_lines(pm, cols - 4)
            self._render_detail_box(term, y, cols, f"DETAIL: {provider_name}",
                                    detail_lines, seg_lines)
        else:
            # Provider list — routed-first ordering, rows as colored segments
            providers = self._sorted_providers()
            visible_rows = rows - y - 3

            shown = 0
            for i in range(self.scroll_offset, min(self.scroll_offset + visible_rows, len(providers))):
                name = providers[i]
                pm = self.app_state.providers[name]
                segs = self._format_provider_segs(name, pm, cols - 2)

                if i == self.selected_index:
                    # Selected: keep segment colors, invert the row background
                    x = 0
                    for text, attr in segs:
                        term.write(y, x, text[:cols - x], attr | curses.color_pair(9) | curses.A_BOLD)
                        x += len(text)
                    for x in range(x, cols):
                        term.write(y, x, " ", curses.color_pair(9))
                else:
                    x = 0
                    for text, attr in segs:
                        term.write(y, x, text[:cols - x], attr)
                        x += len(text)
                y += 1
                shown += 1

            # Summary line — SAME accounting as the rendered rows. Counting by
            # /health alone made the summary claim 'OK: 3' while two rows
            # rendered DEGRADED — the summary must match what is on screen.
            n_total = len(providers)
            if n_total:
                n_ok = n_cool = n_hard = n_noroute = n_deg = 0
                for name in providers:
                    pm = self.app_state.providers[name]
                    h = pm.health
                    if provider_routes(self.app_state, name) == 0:
                        n_noroute += 1
                        continue
                    if h.hard_failed:
                        n_hard += 1
                        continue
                    if not h.healthy:
                        n_cool += 1
                        continue
                    pct, ok_t, fail_t, _ab = provider_rollup(self.app_state, name)
                    v = verdict(pct, ok_t + fail_t)
                    if v == "healthy":
                        n_ok += 1
                    else:
                        n_deg += 1
                routed_total = n_total - n_noroute
                summary = f"Total: {n_total}  Routed: {routed_total}  OK: {n_ok}"
                if n_deg:
                    summary += f"  Degraded: {n_deg}"
                if n_cool:
                    summary += f"  Cool: {n_cool}"
                if n_hard:
                    summary += f"  Hard-failed: {n_hard}"
                if n_noroute:
                    summary += f"  No-route: {n_noroute}"
                if shown < n_total:
                    summary += f"  (showing {shown} of {n_total})"
                term.write(y, 0, summary[:cols], curses.color_pair(5) | curses.A_DIM)
                y += 1

            # Scroll indicator
            if len(providers) > visible_rows:
                indicator = f"  ↑↓ {self.scroll_offset + 1}-{min(self.scroll_offset + visible_rows, len(providers))} of {len(providers)}"
                term.write(y, 0, indicator[:cols], curses.color_pair(5))

    def _format_provider_segs(self, name: str, pm, cols: int) -> List:
        """Provider row as colored segments: bar (JSONL rollup %) + icon + status
        + fails + cooldown + models + keys. One visual language with Overview."""
        health = pm.health
        failures = health.failures if health.failures else 0
        models = str(health.catalog_models) if health.catalog_models else "0"
        if getattr(pm.config, 'key_envs', None):
            keys = len(pm.config.key_envs)
        elif getattr(pm.config, 'key_env', '') or (hasattr(pm.config, 'key_envs') and getattr(pm.config, 'key_envs') == ()):
            keys = 1
        else:
            keys = 0

        pct, _ok, _fail, _abort = provider_rollup(self.app_state, name)
        n_routes = provider_routes(self.app_state, name)

        # Unrouted provider: configured but no preference routes to it.
        if n_routes == 0:
            segs = [
                (f"{name:<12}  ", curses.A_DIM),
                (pct_bar(None), curses.A_DIM),
                (f" {pct_label(None)} ", curses.A_DIM),
                ("◎ ", curses.A_DIM),
                ("NO ROUTE     ", curses.A_DIM),
                (f" 0 routes in any preference", curses.A_DIM),
            ]
            return segs

        # ONE shared verdict drives the bar, the icon AND the status word —
        # '0.0% ○ HEALTHY' is impossible by construction.
        in_cd = not health.healthy and not health.hard_failed
        hv = health_display(pct, _ok + _fail, _fail, in_cd)

        # Status word + icon: hard-failed wins, then /health state
        if health.hard_failed:
            icon, ic = hardfail_icon()
            status = "HARD-FAILED"
            st_color = curses.color_pair(4) | curses.A_BOLD
            bar_c = curses.color_pair(4)
        elif in_cd:
            icon, ic = ("◐", curses.color_pair(3))
            status = "COOLDOWN"
            st_color = curses.color_pair(3)
            bar_c = hv.bar_color
        else:
            icon, ic = hv.icon, hv.text_color
            status = hv.status
            st_color = hv.text_color
            bar_c = hv.bar_color

        cd = cooldown_text(health)

        segs = [
            (f"{name:<12}  ", curses.A_BOLD),
            (pct_bar(hv.bar_pct), bar_c),
            (f" {pct_label(hv.bar_pct)} ", bar_c | curses.A_BOLD),
            (f"{icon} ", ic),
            (f"{status:<12} ", st_color),
            (f" fails:{failures:<2} ", 0),
            (f"{cd:>7}  ", 0),
            (f"models:{models:<3} ", 0),
            (f"keys:{keys}", 0),
        ]
        return segs

    def _build_detail_lines(self, pm, width: int):
        """Build detail lines for provider. Model-health and recent-event rows
        come back as colored segments via seg_rows: {line_idx: [(text, attr)]}."""
        lines = []
        w = width - 4
        health = pm.health

        seg_rows = {}  # line idx -> colored segments

        # Unrouted provider: no preference routes here — say so plainly
        if provider_routes(self.app_state, pm.config.name) == 0:
            lines.append("Status: NO ROUTE")
            lines.append("This provider serves no traffic — no preference")
            lines.append("routes to it. Health/catalog below are what the")
            lines.append("provider WOULD offer if routed.")
            lines.append("")
            lines.append(f"Catalog models: {health.catalog_models if health.catalog_models else 0}")
            lines.append("")
            lines.append("KEY POOLS:")
            if hasattr(pm.config, 'key_envs') and pm.config.key_envs:
                for i, key_env in enumerate(pm.config.key_envs):
                    lines.append(f"  [{i}] {key_env}")
            elif pm.config.key_env:
                lines.append(f"  [0] {pm.config.key_env}")
            else:
                lines.append("  (no key_env configured)")
            lines.append("")
            lines.append(f"Base URL: {pm.config.base_url}")
            lines.append("")
            lines.append("To route it: Routes screen → [e] edit a preference,")
            lines.append("or [o] providers → [m] browse this provider's models.")
            return [line[:w] for line in lines], seg_rows

        # Health status
        if health.hard_failed:
            lines.append(f"Status: HARD-FAILED")
        elif not health.healthy:
            lines.append(f"Status: COOLDOWN")
        else:
            lines.append(f"Status: HEALTHY")

        lines.append(f"Failures: {health.failures if health.failures else 0}")
        lines.append(cooldown_text(health).replace("cd:", "Cooldown: "))

        lines.append(f"Catalog models: {health.catalog_models if health.catalog_models else 0}")
        lines.append("")

        # Key pools
        lines.append("KEY POOLS:")
        if hasattr(pm.config, 'key_envs') and pm.config.key_envs:
            for i, key_env in enumerate(pm.config.key_envs):
                lines.append(f"  [{i}] {key_env}")
        elif pm.config.key_env:
            lines.append(f"  [0] {pm.config.key_env}")
        else:
            lines.append("  (no key_env configured)")

        lines.append("")
        lines.append(f"Base URL: {pm.config.base_url}")

        # Rollup header: real success % across this provider's active models
        pct, ok_t, fail_t, abort_t = provider_rollup(self.app_state, pm.config.name)
        hv = health_display(pct, ok_t + fail_t, fail_t)
        seg_rows[len(lines)] = [
            ("  ", 0),
            (pct_bar(hv.bar_pct), hv.bar_color),
            (f" {pct_label(hv.bar_pct)} ", hv.bar_color | curses.A_BOLD),
            (hv.icon, hv.text_color),
            (f"  {hv.status}  rollup across active models  ok:{ok_t}  fail:{fail_t}", 0),
        ]
        if abort_t:
            seg_rows[len(lines)].append((f"  aborted:{abort_t}", curses.A_DIM))
        lines.append("")  # placeholder
        lines.append("")

        # Per-model health derived from JSONL (route_result success/fail counts)
        # Config is source of truth: only models in active preferences shown
        mh = getattr(self.app_state, '_model_health', {})
        # Build the set of models actively routed for THIS provider
        active_models_for_prov = set()
        if self.app_state.config:
            for pref in self.app_state.config.preferences:
                for route in pref.routes:
                    if route.provider == pm.config.name:
                        active_models_for_prov.add(route.model)
        prov_models = {k: v for k, v in mh.items()
                       if k[0] == pm.config.name and k[1] in active_models_for_prov}
        lines.append("")
        if prov_models or active_models_for_prov:
            lines.append("MODEL HEALTH (active routes, from JSONL results):")
            # Show every active model, even with no data yet
            for model in sorted(active_models_for_prov):
                st = prov_models.get((pm.config.name, model))
                m_short = model.split("/")[-1][:24]
                if st:
                    m_ok = st.get("ok", 0)
                    m_fail = st.get("fail", 0)
                    m_abort = st.get("abort", 0)
                    total = m_ok + m_fail
                    # ONE shared verdict — zero-traffic reads as NO DATA (empty bar),
                    # a single failure reads DEGRADED, never a fake 0% red bar.
                    mpct = (m_ok / total * 100) if total else None
                    hv = health_display(mpct, total, m_fail)
                    last = st["last_outcome"] or "—"
                    lc = (curses.color_pair(2) if last == "success"
                          else curses.color_pair(4) if last in ("failure", "timeout")
                          else curses.A_DIM)
                    counts = f" ok:{m_ok:<4} fail:{m_fail:<4}"
                    if m_abort:
                        counts += f" abort:{m_abort:<4}"
                    seg_rows[len(lines)] = [
                        ("  ", 0),
                        (pct_subbar(hv.bar_pct), hv.bar_color),
                        (f" {pct_label(hv.bar_pct)} ", hv.bar_color | curses.A_BOLD),
                        (hv.icon, hv.text_color),
                        (f" {m_short:<24}", 0),
                        (counts, 0),
                        (f"  last: {last}", lc),
                    ]
                    lines.append("")  # placeholder
                else:
                    # Zero traffic for this model — configured but not yet
                    # exercised: full green bar + NO DATA YET.
                    hv = health_display(None, 0, 0)
                    seg_rows[len(lines)] = [
                        ("  ", 0),
                        (pct_subbar(hv.bar_pct), hv.bar_color),
                        (f" {pct_label(hv.bar_pct)} ", hv.bar_color),
                        (hv.icon, hv.text_color),
                        (f" {m_short:<24}", curses.A_DIM),
                        ("  no results yet", curses.A_DIM),
                    ]
                    lines.append("")  # placeholder
        else:
            lines.append("MODEL HEALTH: no active routes for this provider")

        # Recent events for this provider — NEWEST FIRST, colored segments
        lines.append("")
        lines.append("RECENT EVENTS (newest first):")
        from datetime import datetime as _dt
        _now = _dt.now().astimezone()
        prov_events = [e for e in self.app_state.recent_events
                       if e.provider == pm.config.name]
        CYAN = curses.color_pair(5)
        GREEN = curses.color_pair(2)
        RED = curses.color_pair(4)
        YELLOW = curses.color_pair(3)
        WHITE = 0
        for event in reversed(prov_events[-7:]):
            ts = event.timestamp.strftime("%H:%M:%S") if event.timestamp else "--:--:--"
            model = event.model or ""
            outcome = event.outcome or ""
            status = event.status if event.status else ""
            error = event.error or ""
            # age tag
            try:
                age = (_now - event.timestamp).total_seconds() if event.timestamp else 1e9
            except (TypeError, ValueError):
                age = 1e9
            age_str = ("live" if age < 90 else
                       f"{int(age // 60)}m" if age < 5400 else
                       f"{age / 3600:.1f}h" if age < 129600 else
                       f"{age / 86400:.0f}d")
            if event.event_type == "route_attempt":
                seg = [(f"{ts}  ", CYAN), (f"{model or '—':<38}", WHITE),
                       ("ATTEMPT", YELLOW)]
            elif status and error:
                seg = [(f"{ts}  ", CYAN), (f"{model or '—':<38}", WHITE),
                       (f"{status} {error[:28]}", RED)]
            elif outcome == "success" or status == 200:
                seg = [(f"{ts}  ", CYAN), (f"{model or '—':<38}", WHITE),
                       (f"{status or 'OK'}", GREEN)]
            else:
                seg = [(f"{ts}  ", CYAN), (f"{model or '—':<38}", WHITE),
                       (f"{outcome or event.event_type}", YELLOW)]
            seg.append((f"  [{age_str}]", CYAN if age_str == "live" else curses.color_pair(3)))
            seg_rows[len(lines)] = seg
            lines.append("")  # placeholder
        if not prov_events:
            seg_rows[len(lines)] = [(f"  (no events for {pm.config.name})", WHITE)]
            lines.append("")  # placeholder
        return [line[:w] for line in lines], seg_rows

    def _render_detail_box(self, term, y: int, cols: int, title: str,
                           lines: List[str],
                           seg_rows: Optional[dict] = None) -> None:
        """Render detail box with scroll support. Any line idx present in
        seg_rows renders as colored segments; others render as plain text."""
        w = cols - 4
        max_visible = term.size.rows - y - 2

        # Top border
        term.write(y, 0, f"┌ {title} {'─' * max(0, w - len(title) - 2)}┐", curses.color_pair(1))

        # Content
        for i in range(self.detail_scroll, min(self.detail_scroll + max_visible, len(lines))):
            row = y + 1 + (i - self.detail_scroll)
            segs = (seg_rows or {}).get(i)
            if segs:
                term.write(row, 2, "│ ", curses.color_pair(1))
                x = 4
                room = w - 2
                for text, attr in segs:
                    if room <= 0:
                        break
                    term.write(row, x, text[:room], attr)
                    x += len(text[:room])
                    room -= len(text[:room])
                term.write(row, w + 2, " │", curses.color_pair(1))
            else:
                term.write(row, 2, f"│ {lines[i].ljust(w - 2)} │")

        # Bottom border
        term.write(y + 1 + min(max_visible, len(lines) - self.detail_scroll), 0,
                   "└" + "─" * w + "┘", curses.color_pair(1))

        # Scroll indicator
        if len(lines) > max_visible:
            indicator = f"  ↑↓ {self.detail_scroll + 1}-{min(self.detail_scroll + max_visible, len(lines))} of {len(lines)}"
            term.write(y + 1 + max_visible, 2, indicator[:w], curses.color_pair(5))
