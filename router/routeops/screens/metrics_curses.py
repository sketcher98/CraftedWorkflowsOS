"""
Metrics Screen for RouteOps Curses TUI - Prometheus metrics visualization.
"""

import curses
import json
import time
from datetime import datetime
from typing import Optional, Dict, List, Tuple
from routeops.state import AppState
from routeops.tui.bars import pct_bar, tier_color, tier_icon, BAR_CELLS


class MetricsScreen:
    """Prometheus metrics visualization screen."""

    def __init__(self, app_state: AppState):
        self.app_state = app_state
        self.scroll_offset = 0
        self.selected_metric = 0
        self.metrics_data: Dict[str, List[Tuple[str, str]]] = {}  # metric_name -> [(labels, value)]
        self.metric_names: List[str] = []
        self.last_fetch = 0
        self.fetch_interval = 5  # seconds
        self.selected_view = 0  # 0=summary, 1=latency, 2=counts, 3=all

    def set_size(self, width: int, height: int) -> None:
        self.width = max(60, width)
        self.height = max(15, height)

    def handle_key(self, key: int) -> bool:
        """Handle key press. Returns True if handled."""
        max_index = len(self.metric_names) - 1 if self.metric_names else 0

        if key in (curses.KEY_UP, ord('k')):
            self.selected_metric = max(0, self.selected_metric - 1)
            return True
        elif key in (curses.KEY_DOWN, ord('j')):
            self.selected_metric = min(max_index, self.selected_metric + 1)
            return True
        elif key == ord('1'):
            self.selected_view = 0
            return True
        elif key == ord('2'):
            self.selected_view = 1
            return True
        elif key == ord('3'):
            self.selected_view = 2
            return True
        elif key == ord('4'):
            self.selected_view = 3
            return True
        elif key == ord('r'):  # Refresh now
            self._fetch_metrics()
            return True
        return False

    def _fetch_metrics(self) -> None:
        """Fetch metrics from Prometheus /metrics endpoint."""
        try:
            import urllib.request
            base_url = self.app_state.settings.router_base_url
            if not base_url:
                base_url = "http://127.0.0.1:8643"
            url = f"{base_url}/metrics"
            req = urllib.request.Request(url, headers={'Accept': 'text/plain'})
            with urllib.request.urlopen(req, timeout=5) as resp:
                text = resp.read().decode('utf-8')
            self._parse_prometheus(text)
            self.last_fetch = time.time()
        except Exception as e:
            self.metrics_data = {"error": [("", str(e))]}
            self.metric_names = ["error"]

    def _parse_prometheus(self, text: str) -> None:
        """Parse Prometheus text format into structured data."""
        self.metrics_data = {}
        self.metric_names = []
        current_metric = None
        current_help = ""
        current_type = ""

        for line in text.split('\n'):
            line = line.strip()
            if not line or line.startswith('#'):
                if line.startswith('# HELP'):
                    parts = line.split(' ', 2)
                    if len(parts) >= 3:
                        current_help = parts[2]
                elif line.startswith('# TYPE'):
                    parts = line.split(' ', 2)
                    if len(parts) >= 3:
                        current_metric = parts[2].split('{')[0]
                        current_type = parts[2].split(' ')[-1] if len(parts) > 2 else ""
                        if current_metric not in self.metrics_data:
                            self.metrics_data[current_metric] = []
                            self.metric_names.append(current_metric)
                continue

            # Parse metric line: metric_name{labels} value [timestamp]
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

            if metric_part not in self.metrics_data:
                self.metrics_data[metric_part] = []
                self.metric_names.append(metric_part)
            self.metrics_data[metric_part].append((labels, value))

    def render(self, term) -> None:
        """Render metrics screen."""
        rows, cols = term.size.rows, term.size.cols

        # Auto-fetch
        if time.time() - self.last_fetch > self.fetch_interval:
            self._fetch_metrics()

        # Title
        view_names = ["Summary", "Latency", "Counts", "All Router Metrics"]
        title = f"● METRICS — {view_names[self.selected_view]}  [1-4]=view  [r]=refresh  [q/Esc]=Back"
        term.write(0, 0, title[:cols], curses.A_BOLD | curses.color_pair(1))
        term.write(1, 0, "─" * (cols - 2), curses.color_pair(1))

        y = 2

        # Filter metrics based on view
        filtered_names = self._get_filtered_metrics()

        if not filtered_names:
            term.write(y, 2, "No metrics data. Press [r] to fetch.", curses.color_pair(3))
            return

        # Render selected metric detail or list
        if self.selected_view == 3:
            # All metrics list
            visible = rows - 5
            for i, name in enumerate(filtered_names[self.scroll_offset:self.scroll_offset + visible]):
                is_selected = (i + self.scroll_offset) == self.selected_metric
                attr = curses.A_REVERSE | curses.color_pair(1) if is_selected else curses.color_pair(5)
                term.write(y, 2, f"{name} ({len(self.metrics_data.get(name, []))} series)", attr)
                y += 1
        else:
            # Show selected metric detail
            if self.selected_metric < len(filtered_names):
                name = filtered_names[self.selected_metric]
                term.write(y, 2, f"Metric: {name}", curses.A_BOLD | curses.color_pair(1))
                term.write(y, 20, f"Series: {len(self.metrics_data.get(name, []))}", curses.color_pair(5))
                y += 1
                term.write(y, 0, "─" * (cols - 2), curses.color_pair(1))
                y += 1

                for labels, value in self.metrics_data.get(name, [])[:rows - y - 2]:
                    term.write(y, 4, f"{labels} = {value}", curses.color_pair(6))
                    y += 1

        # Footer
        footer_y = rows - 1
        term.write(footer_y, 0, "[1]=Summary  [2]=Latency  [3]=Counts  [4]=All  [r]=Refresh  [q]=Back", curses.color_pair(8))

    def _get_filtered_metrics(self) -> List[str]:
        """Get filtered metric names based on selected view."""
        if self.selected_view == 0:  # Summary - key router metrics
            keywords = ['router_requests_total', 'router_keys_healthy', 'router_routes_healthy', 'router_catalog_models', 'router_routes_hard_failed', 'router_routes_in_cooldown']
        elif self.selected_view == 1:  # Latency
            keywords = ['router_request_duration', 'go_gc_duration']
        elif self.selected_view == 2:  # Counts
            keywords = ['router_requests_total', 'process_cpu', 'go_memstats', 'go_goroutines']
        else:
            return [n for n in self.metric_names if n.startswith('router_')] + [n for n in self.metric_names if not n.startswith('router_')]

        result = []
        for kw in keywords:
            for name in self.metric_names:
                if kw in name:
                    result.append(name)
        return result