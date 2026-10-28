#!/usr/bin/env python3
"""Headless render check for RouteOps screens (no TTY needed).

Usage: python3 routeops/tools/render_check.py [screen ...]
Screens: overview providers routes history diagnostics settings perkey keymgmt

Renders each screen against the LIVE runtime (real /health, config.json,
router-events.jsonl) into a grid-dict MockTerm and prints the result.
"""
import os
import sys
import time
import curses

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

# curses.color_pair is the only initscr-requiring call in render paths
curses.color_pair = lambda n: 0


class MockTerm:
    """Grid-dict terminal: write(y,x,text) records into grid[(y,x)]."""

    def __init__(self, width=130, height=50):
        self._size = type('Size', (), {'rows': height, 'cols': width})()
        self.grid = {}

    @property
    def size(self):
        return self._size

    def write(self, y, x, text, attr=0):
        if not text:
            return
        for i, ch in enumerate(str(text)):
            self.grid[(y, x + i)] = ch

    def clear(self):
        self.grid = {}

    def refresh(self):
        pass

    def dump(self):
        rows = []
        for y in range(self.size.rows):
            line = "".join(self.grid.get((y, x), " ")
                            for x in range(self.size.cols)).rstrip()
            rows.append(line)
        while rows and not rows[-1]:
            rows.pop()
        return "\n".join(rows)


SCREENS = {
    'overview': ('routeops.screens.overview_curses', 'OverviewScreen'),
    'providers': ('routeops.screens.providers_curses', 'ProvidersScreen'),
    'routes': ('routeops.screens.routes_curses', 'RoutesScreen'),
    'history': ('routeops.screens.history_curses', 'HistoryScreen'),
    'diagnostics': ('routeops.screens.diagnostics_curses', 'DiagnosticsScreen'),
    'settings': ('routeops.screens.settings_curses', 'SettingsScreen'),
    'perkey': ('routeops.screens.perkey_curses', 'PerKeyHealthScreen'),
    'keymgmt': ('routeops.screens.keymgmt_curses', 'KeyManagementScreen'),
}


def main():
    names = sys.argv[1:] or ['overview']
    from routeops.runtime import create_runtime

    runtime = create_runtime()
    app_state = runtime.app_state
    app_state.runtime = runtime
    runtime.start()
    print("warming up runtime (6s)...", flush=True)
    time.sleep(6)
    runtime._drain_wakeup()
    runtime._process_updates()

    for name in names:
        if name not in SCREENS:
            print(f"unknown screen: {name}")
            continue
        mod, cls = SCREENS[name]
        module = __import__(mod, fromlist=[cls])
        screen = getattr(module, cls)(app_state)
        term = MockTerm(130, 50)
        screen.set_size(130, 50)
        screen.render(term)
        print("=" * 130)
        print(f"SCREEN: {name}  ({cls})")
        print("=" * 130)
        print(term.dump())
        print()

    runtime.shutdown()


if __name__ == '__main__':
    main()
