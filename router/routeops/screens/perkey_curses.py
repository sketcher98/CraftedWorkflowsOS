"""
Per-Key Health Screen for RouteOps Curses TUI - Shows key-level health from router.
"""

import curses
import json
import time
from datetime import datetime
from typing import Optional, Dict, List
from routeops.state import AppState
from routeops.tui.bars import (
    pct_bar, tier_color, tier_icon, BAR_CELLS, classify_outcome,
    health_display,
)


class PerKeyHealthScreen:
    """Per-key health visualization screen."""

    def __init__(self, app_state: AppState):
        self.app_state = app_state
        self.scroll_offset = 0
        self.selected_provider = 0
        self.selected_key = 0
        self.show_key_detail = False
        self.last_fetch = 0
        self.fetch_interval = 5  # seconds

    def set_size(self, width: int, height: int) -> None:
        self.width = max(60, width)
        self.height = max(15, height)

    def handle_key(self, key: int) -> bool:
        """Handle key press. Returns True if handled."""
        providers = self._get_sorted_providers()

        if self.show_key_detail:
            if key in (curses.KEY_UP, ord('k')):
                self.selected_key = max(0, self.selected_key - 1)
                return True
            elif key in (curses.KEY_DOWN, ord('j')):
                provider = providers[self.selected_provider] if providers else None
                if provider:
                    keys = self._get_provider_keys(provider)
                    self.selected_key = min(len(keys) - 1, self.selected_key + 1)
                return True
            elif key in (27, ord('h'), ord('q'), 10, curses.KEY_ENTER):  # Esc, h, q, Enter
                self.show_key_detail = False
                return True
        else:
            max_index = len(providers) - 1 if providers else 0
            if key in (curses.KEY_UP, ord('k')):
                self.selected_provider = max(0, self.selected_provider - 1)
                return True
            elif key in (curses.KEY_DOWN, ord('j')):
                self.selected_provider = min(max_index, self.selected_provider + 1)
                return True
            elif key in (10, curses.KEY_ENTER):  # Enter
                if providers:
                    self.show_key_detail = True
                    self.selected_key = 0
                return True
            elif key == ord('t'):  # Test keys
                self._test_selected_provider(providers)
                return True
            elif key == ord('r'):  # Refresh
                self._fetch_health()
                return True
        return False

    def _get_sorted_providers(self) -> List[str]:
        """Get providers sorted by routed count."""
        if not self.app_state.config or not self.app_state.config.preferences:
            return list(self.app_state.providers.keys())
        routed_count = {}
        for pref in self.app_state.config.preferences:
            for route in pref.routes:
                routed_count[route.provider] = routed_count.get(route.provider, 0) + 1
        providers = list(self.app_state.providers.keys())
        providers.sort(key=lambda n: (-routed_count.get(n, 0), n))
        return providers

    def _get_provider_keys(self, provider_name: str) -> List[Dict]:
        """Get key health data for a provider.

        HONESTY RULES:
        - A key with NO observations is "NO DATA" — never "unhealthy".
        - ok/fail counts are REAL (derived from route_result key_env).
        - Cooldown / hard-failed per key are NOT observable from the router
          (no per-key state is exposed by /health or JSONL). We never fake
          them; the router-side change is tracked in P3.3.
        """
        pm = self.app_state.providers.get(provider_name)
        if not pm:
            return []

        keys = []
        key_envs = []
        if pm.config:
            if pm.config.key_envs:
                key_envs = list(pm.config.key_envs)
            elif pm.config.key_env:
                key_envs = [pm.config.key_env]

        # Get key health from JSONL events if available
        key_stats = self._compute_key_stats(provider_name, key_envs)

        for i, kenv in enumerate(key_envs):
            stat = key_stats.get(kenv, {"ok": 0, "fail": 0, "abort": 0, "last": None})
            n_ok = stat["ok"]
            n_fail = stat["fail"]
            n_abort = stat.get("abort", 0)
            total = n_ok + n_fail
            # Per-key verdict using the SAME tier scale as every other screen.
            pct = (n_ok / total * 100) if total else None
            hv = health_display(pct, total, n_fail)
            keys.append({
                "index": i,
                "key_env": kenv,
                "state": hv.status,
                "icon": hv.icon,
                "icon_color": hv.text_color,
                "observed": total > 0,
                "ok": n_ok,
                "fail": n_fail,
                "abort": n_abort,
                "last_used": stat["last"],
                "in_cooldown": False,  # NOT observable — never fabricated
            })
        return keys

    def _compute_key_stats(self, provider: str, key_envs: List[str]) -> Dict[str, Dict]:
        """Compute per-key stats from JSONL events."""
        stats = {}
        # Use recent events from app_state
        for event in self.app_state.recent_events[-500:]:
            if event.provider == provider and event.key_env:
                kenv = event.key_env
                if kenv not in stats:
                    stats[kenv] = {"ok": 0, "fail": 0, "abort": 0, "last": None}
                cls = classify_outcome(event)
                if cls == "ok":
                    stats[kenv]["ok"] += 1
                elif cls == "fail":
                    stats[kenv]["fail"] += 1
                else:
                    stats[kenv]["abort"] += 1
                if event.timestamp:
                    stats[kenv]["last"] = event.timestamp
        return stats

    def _fetch_health(self) -> None:
        """Fetch health from router /health endpoint."""
        try:
            import urllib.request
            base_url = self.app_state.settings.router_base_url
            if not base_url:
                base_url = "http://127.0.0.1:8643"
            url = f"{base_url}/health"
            req = urllib.request.Request(url, headers={'Accept': 'application/json'})
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode('utf-8'))
            # Update provider health in app_state
            for item in data:
                name = item.get('name')
                if name in self.app_state.providers:
                    self.app_state.providers[name].health = type('Health', (), {
                        'healthy': item.get('healthy', False),
                        'failures': item.get('failures', 0),
                        'hard_failed': item.get('hard_failed', False),
                        'cooldown_until': item.get('cooldown_until'),
                        'catalog_models': item.get('catalog_models', 0),
                    })()
            self.last_fetch = time.time()
        except Exception:
            pass

    def _test_selected_provider(self, providers: List[str]) -> None:
        """Live test all keys of selected provider."""
        if not providers:
            return
        provider_name = providers[self.selected_provider]
        pm = self.app_state.providers.get(provider_name)
        if not pm:
            return
        
        from routeops.adapters.config_editor import ConfigEditor
        ce = ConfigEditor()
        key_envs = []
        if pm.config:
            if pm.config.key_envs:
                key_envs = list(pm.config.key_envs)
            elif pm.config.key_env:
                key_envs = [pm.config.key_env]
        
        self.test_results = []
        any_ok = False
        for i, kenv in enumerate(key_envs):
            r = ce.probe_catalog(pm.config.base_url, kenv, timeout=15)
            if r.get("ok"):
                self.test_results.append((i, kenv, True, f"{len(r['models'])} models"))
                any_ok = True
            else:
                self.test_results.append((i, kenv, False, r.get('error', '')[:40]))
        
        self.test_verdict = "OK" if any_ok else "ALL KEYS DEAD"
        self.test_provider = provider_name

    test_results = []
    test_verdict = ""
    test_provider = ""

    def render(self, term) -> None:
        """Render per-key health screen."""
        rows, cols = term.size.rows, term.size.cols

        # Auto-fetch
        if time.time() - self.last_fetch > self.fetch_interval:
            self._fetch_health()

        providers = self._get_sorted_providers()

        # Title
        if self.show_key_detail:
            provider = providers[self.selected_provider] if providers else "Unknown"
            title = f"● PER-KEY HEALTH: {provider}  [↑/↓]=key  [Enter/Esc]=back  [t]=test  [r]=refresh"
        else:
            title = f"● PER-KEY HEALTH  [{len(providers)} providers]  [↑/↓]=provider  [Enter]=detail  [t]=test  [r]=refresh  [q/Esc]=Back"
        term.write(0, 0, title[:cols], curses.A_BOLD | curses.color_pair(1))
        term.write(1, 0, "─" * (cols - 2), curses.color_pair(1))

        y = 2

        if self.show_key_detail and providers:
            # Detail view for selected provider
            provider_name = providers[self.selected_provider]
            pm = self.app_state.providers.get(provider_name)
            keys = self._get_provider_keys(provider_name)
            
            # Provider summary
            ok_count = sum(1 for k in keys if k["state"] == "HEALTHY")
            degraded_count = sum(1 for k in keys if k["state"] in ("DEGRADING", "DEGRADED"))
            failing_count = sum(1 for k in keys if k["state"] == "UNHEALTHY")
            nodata_count = sum(1 for k in keys if k["state"] == "NO DATA YET")
            total_count = len(keys)
            term.write(y, 2, f"Provider: {provider_name}", curses.A_BOLD | curses.color_pair(1))
            if total_count:
                summary = (f"Keys: {ok_count} ok / {degraded_count} degraded / "
                           f"{failing_count} failing / {nodata_count} no-data")
            else:
                summary = "Keys: none configured"
            term.write(y, 30, summary,
                       curses.color_pair(2) if (ok_count == total_count and total_count)
                       else curses.color_pair(3))
            if pm and pm.config:
                term.write(y, 55, f"URL: {pm.config.base_url}", curses.color_pair(5))
            y += 1
            term.write(y, 0, "─" * (cols - 2), curses.color_pair(1))
            y += 1

            # Test results banner
            if hasattr(self, 'test_results') and self.test_results and self.test_provider == provider_name:
                ok = self.test_verdict == "OK"
                term.write(y, 2, f"KEY TEST {provider_name}: {self.test_verdict}", 
                           (curses.color_pair(2) if ok else curses.color_pair(4)) | curses.A_BOLD)
                y += 1
                for idx, kenv, success, detail in self.test_results:
                    color = curses.color_pair(2) if success else curses.color_pair(4)
                    term.write(y, 4, f"  [{idx}] {kenv:<44} {'●' if success else '✗'} {detail}", color)
                    y += 1

            # Key table
            for i, key in enumerate(keys):
                if y >= rows - 2:
                    break
                is_selected = i == self.selected_key
                
                # Health indicator — driven by the shared verdict
                if key["state"] == "HEALTHY":
                    health_icon = "●"
                    health_color = curses.color_pair(2)
                elif key["state"] in ("DEGRADING", "DEGRADED"):
                    health_icon = "◐"
                    health_color = curses.color_pair(3) if key["state"] == "DEGRADING" else curses.color_pair(4)
                elif key["state"] == "UNHEALTHY":
                    health_icon = "○"
                    health_color = curses.color_pair(4)
                else:
                    health_icon = "●"
                    health_color = curses.color_pair(2)

                attr = curses.A_REVERSE if is_selected else 0

                # Key env (truncated)
                kenv_short = key["key_env"][-40:] if len(key["key_env"]) > 40 else key["key_env"]

                counts = f"ok:{key['ok']}  fail:{key['fail']}"
                if key.get("abort"):
                    counts += f"  abort:{key['abort']}"
                line = (f"  [{key['index']}] {kenv_short:<44} {health_icon}  "
                        f"{counts}  {key['state']}")
                term.write(y, 2, line[:cols-4], attr | health_color)
                y += 1
        else:
            # Provider list view
            for i, provider_name in enumerate(providers):
                if y >= rows - 2:
                    break
                pm = self.app_state.providers.get(provider_name)
                keys = self._get_provider_keys(provider_name)
                
                ok_count = sum(1 for k in keys if k["state"] == "HEALTHY")
                degraded_count = sum(1 for k in keys if k["state"] in ("DEGRADING", "DEGRADED"))
                failing_count = sum(1 for k in keys if k["state"] == "UNHEALTHY")
                nodata_count = sum(1 for k in keys if k["state"] == "NO DATA YET")
                total_count = len(keys)
                
                # Provider health
                health = pm.health if pm else None
                if health and health.hard_failed:
                    provider_icon = "✗"
                    provider_color = curses.color_pair(4)
                elif health and not health.healthy:
                    provider_icon = "◐"
                    provider_color = curses.color_pair(3)
                elif health and health.healthy:
                    provider_icon = "●"
                    provider_color = curses.color_pair(2)
                else:
                    provider_icon = "◎"
                    provider_color = curses.color_pair(8)
                
                is_selected = i == self.selected_provider
                attr = curses.A_REVERSE | curses.color_pair(1) if is_selected else provider_color
                
                # Catalog models
                models = health.catalog_models if health else 0

                line = (f"  {provider_icon} {provider_name:<20}  "
                        f"keys: {ok_count} ok / {degraded_count} degraded / "
                        f"{failing_count} failing / {nodata_count} no-data  "
                        f"models: {models}  fail:{health.failures if health else 0}")
                term.write(y, 2, line[:cols-4], attr)
                y += 1

        # Footer
        footer_y = rows - 1
        if self.show_key_detail:
            term.write(footer_y, 0, "[↑/↓]=select key  [Enter/Esc]=back to providers  [t]=test keys", curses.color_pair(8))
        else:
            term.write(footer_y, 0, "[↑/↓]=select provider  [Enter]=key detail  [t]=test keys  [r]=refresh  [q]=Back", curses.color_pair(8))