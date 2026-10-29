"""
Diagnostics Screen for RouteOps Curses TUI - 4 tabs with drill-down.
"""

import curses
from typing import List, Dict
from datetime import datetime

from routeops.state import AppState
from routeops.models.provider import ProviderHealth


class DiagnosticsScreen:
    """Diagnostics screen with 6 tabs: Connectivity, Latency, Errors, Binary, Dangling Models, Metrics."""

    def __init__(self, app_state: AppState):
        self.app_state = app_state
        self.current_tab = 0  # 0=connectivity, 1=latency, 2=errors, 3=binary, 4=dangling, 5=metrics, 6=telemetry
        self.tab_names = ["Connectivity", "Latency", "Errors", "Binary", "Dangling Models", "Metrics", "Telemetry"]
        self.scroll_offset = 0
        self._dangling_cache = None
        self._dangling_checked_at = None
        self._metrics_cache = None
        self._metrics_checked_at = None
        self._full_index = None
        self._full_last = 0

    def set_size(self, width: int, height: int) -> None:
        self.width = max(60, width)
        self.height = max(15, height)

    def handle_key(self, key: int) -> bool:
        """Handle key press. Returns True if handled."""
        if key in (ord('1'), ord('2'), ord('3'), ord('4'), ord('5'), ord('6'), ord('7')):
            self.current_tab = int(chr(key)) - 1
            self.scroll_offset = 0
            if self.current_tab == 4:
                self._dangling_cache = None  # force rescan on tab entry
            elif self.current_tab == 5:
                self._metrics_cache = None  # force refresh on tab entry
            return True

        if key in (curses.KEY_LEFT, ord('h')):
            self.current_tab = max(0, self.current_tab - 1)
            self.scroll_offset = 0
            if self.current_tab == 4:
                self._dangling_cache = None
            elif self.current_tab == 5:
                self._metrics_cache = None
            return True

        if key in (curses.KEY_RIGHT, ord('l')):
            self.current_tab = min(6, self.current_tab + 1)
            self.scroll_offset = 0
            if self.current_tab == 4:
                self._dangling_cache = None
            elif self.current_tab == 5:
                self._metrics_cache = None
            return True

        if key in (curses.KEY_UP, ord('k')):
            self.scroll_offset = max(0, self.scroll_offset - 1)
            return True

        if key in (curses.KEY_DOWN, ord('j')):
            self.scroll_offset += 1
            return True

        if key in (27, ord('q')):  # Esc, q
            return False  # Let parent handle

        return False

    def render(self, term) -> None:
        """Render diagnostics screen."""
        rows, cols = term.size.rows, term.size.cols
        y = 0

        # Overview-standard header line (router LIVE / pid / uptime / port + clock)
        from routeops.tui.render_std import render_header, render_box
        y = render_header(term.write, self.app_state, y, cols, rows)

        # Title line: "DIAGNOSTICS" aligned with the tabs below (col 3),
        # hint right-aligned; ruled below. The header rule above serves
        # as the top border.
        hint = "[1-7=tabs]  [h/Esc=back]"
        term.write(y, 3, "DIAGNOSTICS", curses.A_BOLD | curses.color_pair(1))
        term.write(y, cols - len(hint) - 1, hint[:cols - 2],
                   curses.color_pair(5) | curses.A_DIM)
        y += 1
        term.write(y, 0, "─" * (cols - 2), curses.color_pair(1))
        y += 1

        # Tab line: names only, directly under DIAGNOSTICS
        tx = 3
        full_names = self.tab_names
        compact_names = ["Conn", "Lat", "Err", "Bin", "Dang", "Metr", "Tele"]
        names = full_names
        est = sum(len(n) + 2 for n in names) + 3 + 4
        if est > cols:
            names = compact_names

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

        # Render current tab
        if self.current_tab == 0:
            lines = self._build_connectivity_lines(cols - 4)
        elif self.current_tab == 1:
            lines = self._build_latency_lines(cols - 4)
        elif self.current_tab == 2:
            lines = self._build_errors_lines(cols - 4)
        elif self.current_tab == 4:
            lines = self._build_dangling_lines(cols - 4)
        elif self.current_tab == 5:
            lines = self._build_metrics_lines(cols - 4)
        elif self.current_tab == 6:
            lines = self._build_telemetry_lines(cols - 4)
        else:
            lines = self._build_binary_lines(cols - 4)

        # Style each line into box segments (same coloring as before)
        for i, raw in enumerate(lines):
            stripped = raw.lstrip()
            if stripped.startswith(("✗", "⚠")) or "HARD-FAILED" in raw:
                attr = curses.color_pair(4) | curses.A_BOLD
            elif "ALL CLEAR" in raw or ("REACHABLE" in stripped and "UN" not in stripped):
                attr = curses.color_pair(2) | curses.A_BOLD
            elif "UNREACHABLE" in stripped or "UNREADABLE" in stripped or "NOT FOUND" in stripped:
                attr = curses.color_pair(4) | curses.A_BOLD
            elif stripped.startswith("○"):
                attr = curses.color_pair(3)
            elif stripped.startswith(("REACHABLE", "●")):
                attr = curses.color_pair(2)
            elif raw.endswith("─") or raw.isupper():
                attr = curses.A_BOLD | curses.color_pair(5)
            else:
                attr = curses.A_NORMAL
            lines[i] = [(raw, attr)]

        visible_rows = rows - y - 3
        body = lines[self.scroll_offset:self.scroll_offset + visible_rows]
        y = render_box(term.write, y, cols, self.tab_names[self.current_tab].upper(), body, rows)

        # Scroll indicator
        if len(lines) > visible_rows:
            indicator = f"  ↑↓ {self.scroll_offset + 1}-{min(self.scroll_offset + visible_rows, len(lines))} of {len(lines)}"
            term.write(y, 0, indicator[:cols], curses.color_pair(5))

    # ------------------------------------------------- dangling models

    _HERMES_CONFIG_REFS = [
        # (yaml path regex-ish keys, label)
        (r"^model:\n  default: (\S+)", "model.default"),
    ]

    def _scan_hermes_model_refs(self) -> Dict[str, List[str]]:
        """Collect every model reference in hermes main + profile configs.
        Returns {model_id: [source labels]}. Read-only."""
        import re as _re
        from pathlib import Path as _Path
        home = _Path.home()
        files = [home / ".hermes" / "config.yaml"]
        files += sorted((home / ".hermes" / "profiles").glob("*/config.yaml"))
        refs: Dict[str, List[str]] = {}
        for f in files:
            if not f.exists() or ".bak" in f.name:
                continue
            label_base = "main" if f.parent.name == ".hermes" else f.parent.name
            try:
                text = f.read_text()
            except Exception:
                continue
            # section-aware scan: model refs inside custom_providers (provider
            # definitions, not requests) and image_gen (different API surface,
            # never in chat catalogs) are not dangling candidates
            section = ""
            for ln in text.splitlines():
                sec = _re.match(r"^([a-z_]+):\s*$", ln)
                if sec:
                    section = sec.group(1)
                    continue
                m = _re.match(r"^\s+model:\s*'?([\w/.-]+)'?\s*$", ln)
                if m:
                    mid = m.group(1)
                    if section in ("custom_providers", "image_gen"):
                        continue
                    if mid and ("-" in mid or "/" in mid) and "http" not in mid:
                        refs.setdefault(mid, []).append(f"{label_base}:model_ref")
            # default model (first 30 lines cover the model: block)
            m = _re.search(r"^model:\n  default: (\S+)", text, _re.M)
            if m:
                refs.setdefault(m.group(1), []).append(f"{label_base}:model.default")
            # reasoning_overrides keys
            for rm in _re.finditer(r"^\s{4}([\w/.-]+):\s*(?:high|medium|low)\s*$",
                                   text, _re.M):
                if "/" in rm.group(1) or rm.group(1).startswith("moonshot"):
                    refs.setdefault(rm.group(1), []).append(
                        f"{label_base}:reasoning_override")
            # auxiliary model refs are already collected by the section-aware
            # scan above (which excludes custom_providers + image_gen)
        return refs

    def _build_dangling_lines(self, width: int) -> List[str]:
        """Dangling Models tab: hermes refs vs live router catalogs."""
        from datetime import datetime as _dt
        import threading as _threading
        lines = []
        w = width - 4
        lines.append("DANGLING MODEL SCAN — hermes config refs vs live catalogs")
        lines.append("─" * w)
        lines.append("")
        # cache for 5 min to avoid hammering on every redraw.
        # D5: the scan probes every provider (up to ~72s worst case) — run it
        # on a daemon thread so the TUI never freezes; show 'scanning' while
        # it runs.
        now = _dt.now()
        if (self._dangling_cache is None
                or self._dangling_checked_at is None
                or (now - self._dangling_checked_at).total_seconds() > 300):
            if not getattr(self, "_dangling_scanning", False):
                self._dangling_scanning = True
                def _do_scan():
                    try:
                        self._dangling_cache = self._run_dangling_scan()
                        self._dangling_checked_at = _dt.now()
                    finally:
                        self._dangling_scanning = False
                _threading.Thread(target=_do_scan, daemon=True).start()
            lines.append("  ○ SCANNING (provider probes running in background)…")
            return lines
        result = self._dangling_cache
        checked = self._dangling_checked_at.strftime("%H:%M:%S")
        lines.append(f"  scanned {checked} · {result['n_refs']} refs · "
                     f"{result['n_models']} unique models")
        lines.append("")
        if not result["dangling"]:
            lines.append("  ● ALL CLEAR — every referenced model exists in a live catalog")
            return lines
        lines.append(f"  ⚠ {len(result['dangling'])} DANGLING MODEL(S):")
        lines.append("")
        for mid, sources, status in result["dangling"]:
            lines.append(f"  ✗ {mid}")
            lines.append(f"      status: {status}")
            for s in sources[:4]:
                lines.append(f"      ref:    {s}")
            lines.append("")
        if result["unverifiable"]:
            lines.append(f"  ○ {len(result['unverifiable'])} unverifiable "
                         "(provider catalog empty — models:0):")
            for mid, sources in result["unverifiable"][:5]:
                lines.append(f"    ? {mid}  ({sources[0]})")
        return lines

    def _run_dangling_scan(self) -> Dict:
        """Full scan: gather refs, probe catalogs, classify."""
        from routeops.adapters.config_editor import ConfigEditor
        refs = self._scan_hermes_model_refs()
        ce = ConfigEditor()
        cfg = ce.load()
        # union of live catalogs (probe) — but also include router preferences
        # as valid targets (they're routable by definition)
        live: Dict[str, List[str]] = {}
        probe_issues = []
        for prov in cfg.get("providers", []):
            r = ce.probe_catalog(prov["base_url"], prov.get("key_env", ""),
                                 timeout=12)
            if r.get("ok"):
                for m in r["models"]:
                    live.setdefault(m, []).append(prov["name"])
            else:
                probe_issues.append(f"{prov['name']}: {r.get('error','')[:40]}")
        pref_models = {p["name"] for p in cfg.get("preferences", [])}
        for pr in cfg.get("preferences", []):
            for rt in pr.get("routes", []):
                pref_models.add(rt["model"])
        dangling = []
        unverifiable = []
        empty_catalogs = all(not v for v in live.values())
        for mid, sources in sorted(refs.items()):
            if mid in live:
                continue
            if mid in pref_models:
                continue
            if empty_catalogs:
                unverifiable.append((mid, sources))
                continue
            # probe the model directly for a definitive status
            status = "not found in any provider catalog"
            for prov in cfg.get("providers", []):
                r = ce.test_key(prov["base_url"], prov.get("key_env", ""), mid,
                                timeout=20)
                if r.get("ok"):
                    status = f"actually live on {prov['name']} (catalog lag)"
                    break
                if "410" in r.get("detail", ""):
                    status = "410 Gone — model reached end of life"
                    break
            dangling.append((mid, sources, status))
        return {"n_refs": sum(len(v) for v in refs.values()),
                "n_models": len(refs),
                "dangling": dangling,
                "unverifiable": unverifiable,
                "probe_issues": probe_issues}

    def _build_connectivity_lines(self, width: int) -> List[str]:
        lines = []
        w = width - 4
        
        lines.append("CONNECTIVITY CHECKS")
        lines.append("─" * w)
        lines.append("")

        # Check /health endpoint
        health_ok = len(self.app_state.providers) > 0 and any(p.health for p in self.app_state.providers.values())
        lines.append(f"  /health endpoint:     {'● REACHABLE' if health_ok else '● UNREACHABLE'}")

        # Check /v1/models endpoint
        models_ok = len(self.app_state._models_cache) > 0
        # D10: flag stale-cache reads instead of reporting REACHABLE blindly.
        models_note = ""
        if models_ok:
            models_note = "  (from cache — may be stale)"
        lines.append(f"  /v1/models endpoint:  {'● REACHABLE' if models_ok else '● UNREACHABLE'}{models_note}")

        # Check config.json
        config_ok = self.app_state.config is not None
        lines.append(f"  config.json:          {'● READABLE' if config_ok else '● UNREADABLE'}")

        # Check JSONL
        jsonl_ok = len(self.app_state.recent_events) > 0
        lines.append(f"  router-events.jsonl:  {'● READABLE' if jsonl_ok else '● UNREADABLE'}")

        # Check process
        proc = self.app_state.process_info
        proc_ok = proc and proc.identity == "PRODUCTION"
        lines.append(f"  router process:       {'● RUNNING' if proc_ok else '● NOT FOUND'}")
        if proc:
            lines.append(f"    PID: {proc.pid}")
            lines.append(f"    Identity: {proc.identity}")
            lines.append(f"    Binary: {proc.exe}")
            lines.append(f"    CWD: {proc.cwd}")
            if proc.uptime_seconds:
                hours = proc.uptime_seconds // 3600
                mins = (proc.uptime_seconds % 3600) // 60
                lines.append(f"    Uptime: {hours}h {mins}m")

        lines.append("")
        lines.append("ROUTER HEALTH ENDPOINT RESPONSE")
        lines.append("─" * w)
        lines.append("")

        # Show provider health from /health
        for name, pm in self.app_state.providers.items():
            health = pm.health
            status = "HEALTHY" if health.healthy else "DEGRADED"
            if health.hard_failed:
                status = "HARD-FAILED"
            lines.append(f"  {name:<12}: {status}  (failures={health.failures}, models={health.catalog_models})")
            # D11: surface per-cell routes[] from the router's B-7 health.
            cells = getattr(health, "cells", ()) or ()
            for cell in cells:
                c_short = cell.model.split("/")[-1][:28]
                c_stat = "OK" if cell.healthy else ("HARD-FAILED" if cell.hard_failed else "COOLDOWN/UNHEALTHY")
                lines.append(f"      {c_short:<30} {c_stat}  fail={cell.failures}")

        return [line[:w] for line in lines]

    # ------------------------------------------------- full-file index

    def _ensure_full_index(self):
        """Build/refresh the full-file HistoryIndex. The FIRST build runs
        on a daemon thread — a full scan of 40k+ lines must never freeze
        the TUI. The (empty) index shell is always created synchronously,
        so this NEVER returns None; only the scan is async."""
        import time as _time
        from routeops.adapters.history_index import HistoryIndex
        from routeops.config import ROUTER_EVENTS_JSONL
        now = _time.time()
        if getattr(self, "_full_index", None) is None:
            self._full_index = HistoryIndex(str(ROUTER_EVENTS_JSONL))
            self._full_last = now
            if not getattr(self, "_full_indexing", False):
                self._full_indexing = True
                import threading

                def build():
                    try:
                        self._full_index.refresh()
                    finally:
                        self._full_indexing = False

                threading.Thread(target=build, daemon=True).start()
        elif not getattr(self, "_full_indexing", False) and \
                now - getattr(self, "_full_last", 0) > 5:
            self._full_index.refresh()
            self._full_last = now
        return self._full_index

    def _full_results(self) -> List:
        """Body route_result events from the FULL-FILE index (D2).

        Replaces the 500-event memory window so diagnostics totals are true
        totals, not a shrinking snapshot. First build is async — returns
        [] until the scan lands."""
        idx = self._ensure_full_index()
        if idx is None or getattr(self, "_full_indexing", False):
            return []
        return [e for e in idx.query("results")
                if e.event_type == "route_result"]

    def _build_latency_lines(self, width: int) -> List[str]:
        """Build latency drill-down lines.

        D2/D3: sourced from the FULL-FILE index (not the 500-event memory
        window) and only SUCCESSFUL route_results — failures/timeouts pollute
        latency otherwise."""
        lines = []
        w = width - 4

        lines.append("LATENCY DRILL-DOWN (from JSONL route_result events)")
        lines.append("─" * w)
        lines.append("")

        # Collect latency data from the full-file index
        latencies = []
        for event in self._full_results():
            if event.outcome == "success" and event.latency_ms:
                latencies.append({
                    'provider': event.provider,
                    'model': event.model,
                    'latency_ms': event.latency_ms,
                    'status': event.status,
                    'timestamp': event.timestamp
                })

        if not latencies:
            lines.append("  No latency data available")
            return [line[:w] for line in lines]

        # Overall stats
        all_lat = [l['latency_ms'] for l in latencies]
        all_lat.sort()
        n = len(all_lat)
        
        def percentile(arr, p):
            if not arr:
                return 0
            idx = int(len(arr) * p / 100)
            return arr[min(idx, len(arr) - 1)]
        
        lines.append(f"  Total requests: {n}")
        lines.append(f"  Min: {min(all_lat)}ms")
        lines.append(f"  P50: {percentile(all_lat, 50)}ms")
        lines.append(f"  P90: {percentile(all_lat, 90)}ms")
        lines.append(f"  P99: {percentile(all_lat, 99)}ms")
        lines.append(f"  Max: {max(all_lat)}ms")
        lines.append("")

        # Per-provider breakdown
        from collections import defaultdict
        by_provider = defaultdict(list)
        for l in latencies:
            by_provider[l['provider']].append(l['latency_ms'])
        
        lines.append("PER-PROVIDER LATENCY")
        lines.append("─" * w)
        lines.append("")
        
        for provider, lats in sorted(by_provider.items()):
            lats.sort()
            lines.append(f"  {provider}:")
            lines.append(f"    Count: {len(lats)}  Min: {min(lats)}ms  P50: {percentile(lats, 50)}ms  P90: {percentile(lats, 90)}ms  Max: {max(lats)}ms")

        return [line[:w] for line in lines]

    def _build_errors_lines(self, width: int) -> List[str]:
        """Build error analysis lines.

        D2/D4: full-file index (not the 500-event window); client_abort is
        explicitly excluded — it is neutral, not an error."""
        lines = []
        w = width - 4

        lines.append("ERROR ANALYSIS (from JSONL events)")
        lines.append("─" * w)
        lines.append("")

        # Count errors by type
        errors = []
        fallback_events = []
        cooldown_events = []
        
        for event in self._full_results():
            if event.error and event.outcome not in ("client_abort",):
                errors.append({
                    'provider': event.provider,
                    'model': event.model,
                    'error': event.error,
                    'status': event.status,
                    'timestamp': event.timestamp
                })
            elif event.event_type == "route_cooldown":
                cooldown_events.append({
                    'provider': event.provider,
                    'cooldown': event.cooldown_seconds,
                    'timestamp': event.timestamp
                })
        for event in self._ensure_full_index().query("fallbacks"):
            if event.event_type == "preference_fallback":
                fallback_events.append({
                    'provider': event.provider,
                    'from_pref': event.from_preference,
                    'to_pref': event.to_preference,
                    'timestamp': event.timestamp
                })

        lines.append(f"Total errors: {len(errors)}")
        lines.append(f"Total fallbacks: {len(fallback_events)}")
        lines.append(f"Total cooldowns: {len(cooldown_events)}")
        lines.append("")

        # Top errors
        if errors:
            from collections import Counter
            error_counts = Counter(e['error'] for e in errors)
            lines.append("TOP ERRORS")
            lines.append("─" * w)
            lines.append("")
            for error_msg, count in error_counts.most_common(5):
                lines.append(f"  [{count}x] {error_msg[:w-10]}")
            lines.append("")

        # Recent fallbacks
        if fallback_events:
            lines.append("RECENT FALLBACKS")
            lines.append("─" * w)
            lines.append("")
            for fb in fallback_events[-5:]:
                ts = fb['timestamp'].strftime("%H:%M:%S") if fb['timestamp'] else "--:--:--"
                # D7: preference_fallback has no provider — show request-era info.
                lines.append(f"  {ts}  {fb['from_pref'] or '?'} → {fb['to_pref'] or '?'}")

        # Recent cooldowns
        if cooldown_events:
            lines.append("")
            lines.append("RECENT COOLDOWNS")
            lines.append("─" * w)
            lines.append("")
            for cd in cooldown_events[-5:]:
                ts = cd['timestamp'].strftime("%H:%M:%S") if cd['timestamp'] else "--:--:--"
                lines.append(f"  {ts}  {cd['provider']}  {cd['cooldown']}s")

        return [line[:w] for line in lines]

    def _build_binary_lines(self, width: int) -> List[str]:
        """Build binary verification lines."""
        lines = []
        w = width - 4

        lines.append("BINARY VERIFICATION")
        lines.append("─" * w)
        lines.append("")

        proc = self.app_state.process_info
        
        if not proc:
            lines.append("  No router process found")
            return [line[:w] for line in lines]

        lines.append(f"PID: {proc.pid}")
        lines.append(f"Identity: {proc.identity}")
        lines.append(f"Executable: {proc.exe}")
        lines.append(f"Working Dir: {proc.cwd}")
        
        if proc.uptime_seconds:
            hours = proc.uptime_seconds // 3600
            mins = (proc.uptime_seconds % 3600) // 60
            lines.append(f"Uptime: {hours}h {mins}m")

        lines.append("")
        lines.append("SIZE CHECK")
        lines.append("─" * w)
        lines.append("")

        if proc.binary_size:
            size_mb = proc.binary_size / (1024 * 1024)
            expected = "~10MB (production)"
            if proc.binary_size >= 8_000_000:
                lines.append(f"  Binary size: {size_mb:.1f}MB  ✓ MATCHES {expected}")
            else:
                lines.append(f"  Binary size: {size_mb:.1f}MB  ✗ BELOW {expected}")
        else:
            lines.append("  Binary size: UNKNOWN")

        lines.append("")
        lines.append("PATH VERIFICATION")
        lines.append("─" * w)
        lines.append("")

        from routeops.adapters.process_inspect import ProcessInspectAdapter
        expected_exe = str(ProcessInspectAdapter.PRODUCTION_EXE)
        expected_cwd = str(ProcessInspectAdapter.PRODUCTION_CWD)

        exe_match = proc.exe == expected_exe
        cwd_match = proc.cwd == expected_cwd

        lines.append(f"  Exe path:  {'✓' if exe_match else '✗'} {proc.exe}")
        if not exe_match:
            lines.append(f"    Expected: {expected_exe}")
        
        lines.append(f"  CWD:       {'✓' if cwd_match else '✗'} {proc.cwd}")
        if not cwd_match:
            lines.append(f"    Expected: {expected_cwd}")

        if exe_match and cwd_match and proc.binary_size >= 8_000_000:
            lines.append("")
            lines.append("  RESULT: PRODUCTION ROUTER VERIFIED ✓")
        elif exe_match and not cwd_match:
            lines.append("")
            lines.append("  RESULT: TEST ROUTER (same binary, different CWD)")
        elif not exe_match and "router" in proc.exe.lower():
            lines.append("")
            lines.append("  RESULT: WRONG ROUTER BINARY")
        else:
            lines.append("")
            lines.append("  RESULT: NOT RUNNING")

        return [line[:w] for line in lines]

    def _build_metrics_lines(self, width: int) -> List[str]:
        """Build Prometheus metrics visualization lines."""
        lines = []
        w = width - 4

        lines.append("PROMETHEUS METRICS (from /metrics endpoint)")
        lines.append("─" * w)
        lines.append("")

        # Fetch metrics if cache is stale
        import time
        now = time.time()
        if (self._metrics_cache is None
                or self._metrics_checked_at is None
                or (now - self._metrics_checked_at) > 10):  # 10s cache
            self._metrics_cache = self._fetch_prometheus_metrics()
            self._metrics_checked_at = now

        metrics = self._metrics_cache

        if "error" in metrics:
            lines.append(f"  ✗ Fetch failed: {metrics['error']}")
            return [line[:w] for line in lines]

        # Summary view - key router metrics
        lines.append("SUMMARY — KEY ROUTER METRICS")
        lines.append("─" * w)
        lines.append("")

        # router_requests_total
        if "router_requests_total" in metrics:
            lines.append("  router_requests_total:")
            for labels, value in metrics["router_requests_total"]:
                lines.append(f"    {labels} = {value}")
            lines.append("")

        # router_keys_healthy
        if "router_keys_healthy" in metrics:
            lines.append("  router_keys_healthy:")
            for labels, value in metrics["router_keys_healthy"]:
                lines.append(f"    {labels} = {value}")
            lines.append("")

        # router_routes_healthy
        if "router_routes_healthy" in metrics:
            lines.append("  router_routes_healthy:")
            for labels, value in metrics["router_routes_healthy"]:
                lines.append(f"    {labels} = {value}")
            lines.append("")

        # router_catalog_models
        if "router_catalog_models" in metrics:
            lines.append("  router_catalog_models:")
            for labels, value in metrics["router_catalog_models"]:
                lines.append(f"    {labels} = {value}")
            lines.append("")

        # router_routes_hard_failed
        if "router_routes_hard_failed" in metrics:
            lines.append("  router_routes_hard_failed:")
            for labels, value in metrics["router_routes_hard_failed"]:
                lines.append(f"    {labels} = {value}")
            lines.append("")

        # router_routes_in_cooldown
        if "router_routes_in_cooldown" in metrics:
            lines.append("  router_routes_in_cooldown:")
            for labels, value in metrics["router_routes_in_cooldown"]:
                lines.append(f"    {labels} = {value}")
            lines.append("")

        # Latency histogram — D9: derive real p50/p90/p99 from the cumulative
        # buckets instead of just pointing at the Latency tab.
        if "router_request_duration_seconds_bucket" in metrics:
            # histogram buckets: router_request_duration_seconds_bucket{provider=...,model=...,le="0.1"} N
            buckets = []
            for labels, value in metrics["router_request_duration_seconds_bucket"]:
                le = None
                for part in labels.split(","):
                    if part.strip().startswith('le="'):
                        le = part.strip()[4:-1]
                if le is not None and le != "+Inf":
                    try:
                        buckets.append((float(le), float(value)))
                    except ValueError:
                        pass
            count_val = 0.0
            for labels, value in metrics.get("router_request_duration_seconds_count", []):
                try:
                    count_val += float(value)
                except ValueError:
                    pass
            if buckets and count_val > 0:
                buckets.sort()
                lines.append(f"  histogram count: {count_val:.0f}")
                pct = {}
                for p in (50, 90, 99):
                    target = count_val * p / 100.0
                    pct[p] = next((le for le, cum in buckets if cum >= target), buckets[-1][0])
                lines.append(f"  histogram p50={pct[50]*1000:.0f}ms  p90={pct[90]*1000:.0f}ms  p99={pct[99]*1000:.0f}ms")
                lines.append("")
        else:
            lines.append("  router_request_duration_seconds (histogram): no bucket series found")
            lines.append("")

        # System metrics
        lines.append("SYSTEM METRICS")
        lines.append("─" * w)
        lines.append("")

        sys_metrics = [
            "go_goroutines",
            "go_memstats_heap_alloc_bytes",
            "go_memstats_heap_inuse_bytes",
            "process_resident_memory_bytes",
            "process_cpu_seconds_total",
        ]
        for m in sys_metrics:
            if m in metrics:
                for labels, value in metrics[m]:
                    lines.append(f"  {m}{labels} = {value}")
                lines.append("")

        # All router metrics list
        lines.append("ALL ROUTER METRICS")
        lines.append("─" * w)
        lines.append("")
        router_metrics = [m for m in metrics.keys() if m.startswith("router_")]
        for m in sorted(router_metrics):
            count = len(metrics[m])
            lines.append(f"  {m} ({count} series)")

        return [line[:w] for line in lines]

    def _fetch_prometheus_metrics(self) -> Dict:
        """Fetch and parse Prometheus metrics from /metrics endpoint."""
        import urllib.request
        import urllib.error

        metrics = {}

        try:
            base_url = self.app_state.settings.router_base_url
            if not base_url:
                base_url = "http://127.0.0.1:8643"
            url = f"{base_url}/metrics"
            req = urllib.request.Request(url, headers={'Accept': 'text/plain'})
            with urllib.request.urlopen(req, timeout=5) as resp:
                text = resp.read().decode('utf-8')

            # Parse Prometheus text format
            current_metric = None
            for line in text.split('\n'):
                line = line.strip()
                if not line or line.startswith('#'):
                    if line.startswith('# TYPE'):
                        parts = line.split(' ', 2)
                        if len(parts) >= 3:
                            current_metric = parts[2].split('{')[0]
                            if current_metric not in metrics:
                                metrics[current_metric] = []
                    continue

                # Parse metric line
                if '{' in line:
                    metric_part, rest = line.split('{', 1)
                    labels_part, value_part = rest.split('}', 1)
                    labels = labels_part
                    value = value_part.strip().split(' ')[0]
                else:
                    parts = line.split(' ')
                    metric_part = parts[0]
                    labels = ""
                    value = parts[1] if len(parts) > 1 else ""

                if metric_part not in metrics:
                    metrics[metric_part] = []
                metrics[metric_part].append((labels, value))

        except urllib.error.URLError as e:
            metrics = {"error": f"Connection failed: {e}"}
        except Exception as e:
            metrics = {"error": f"Parse error: {e}"}

        return metrics

    def _build_telemetry_lines(self, width: int) -> List[str]:
        """GAP telemetry home: full-file per-event-type counts + newest
        timestamp for every event type the router emits. Covers the GAP
        event set (request_started/exhausted, client_abort, catalog_*,
        credential_rotation_signal, route_committed, health_transition,
        route_skipped) alongside the core route events."""
        lines = []
        w = width - 4

        idx = self._ensure_full_index()
        lines.append("EVENT TYPES — full router-events.jsonl (all events)")
        lines.append("─" * w)
        lines.append("")
        if getattr(self, "_full_indexing", False) or not idx.counts:
            if getattr(self, "_full_indexing", False):
                lines.append("  Indexing full event log (first open) …")
                lines.append("  This happens once per routerops start — screen stays live.")
            else:
                lines.append("  no events indexed yet")
            return [line[:w] for line in lines]

        lines.append(f"  TOTAL events indexed: {idx.total_events}")
        lines.append("")

        # GAP event types surface first (they are the ones the TUI now
        # surfaces; core route events follow below).
        gap_order = [
            "request_started", "request_exhausted", "client_abort",
            "catalog_refreshed", "catalog_skip", "credential_rotation_signal",
            "route_committed", "health_transition", "route_skipped",
        ]
        counts = idx.counts
        last_ts = idx.last_ts
        shown_gap = 0
        for t in gap_order:
            if t in counts:
                ts = last_ts.get(t, "")
                if ts:
                    ts = ts[11:19]  # HH:MM:SS from ISO datetime
                lines.append(f"  {t:<28} {counts[t]:>7}   latest {ts}")
                shown_gap += 1
        if shown_gap:
            lines.append("")
        lines.append("  core events:")
        core = sorted((t for t in counts if t not in gap_order), key=lambda t: -counts[t])
        for t in core:
            ts = last_ts.get(t, "")
            if ts:
                ts = ts[11:19]
            lines.append(f"  {t:<28} {counts[t]:>7}   latest {ts}")
        return [line[:w] for line in lines]
