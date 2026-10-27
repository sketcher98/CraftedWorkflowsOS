#!/usr/bin/env python3
"""
RouteOps Demo - Non-interactive overview render.
Tests the full data pipeline and renders overview to stdout.
"""

import sys
import os
import time

# Add routeops to path
sys.path.insert(0, '/data/data/com.termux/files/home/CraftedWorkflowsOS/router')

from routeops.tui.curses_terminal import curses_session, CursesTerminal
from routeops.state import AppState
from routeops.screens.overview_curses import OverviewScreen
from routeops.runtime import create_runtime
from routeops.config import (
    ROUTER_EVENTS_JSONL, ROUTER_BASE_URL, ROUTER_CONFIG_JSON,
    POLL_INTERVALS
)


class MockTerm:
    """Mock terminal for demo output."""
    def __init__(self, width=120, height=40):
        self._size = type('Size', (), {'rows': height, 'cols': width})()
        self.output = []
    
    @property
    def size(self):
        return self._size
    
    def write(self, y, x, text, attr=0):
        while len(self.output) <= y:
            self.output.append("")
        if len(self.output[y]) < x + len(text):
            self.output[y] = self.output[y].ljust(x)
        self.output[y] = self.output[y][:x] + text + self.output[y][x + len(text):]
    
    def clear(self):
        self.output = []
    
    def refresh(self):
        for line in self.output:
            print(line)


def main():
    print("Initializing RouteOps demo...")
    
    # Initialize AppState
    app_state = AppState()
    app_state.settings.router_events_path = str(ROUTER_EVENTS_JSONL)
    app_state.settings.router_config_path = str(ROUTER_CONFIG_JSON)
    app_state.settings.router_log_path = "/data/data/com.termux/files/home/CraftedWorkflowsOS/router/router.log"
    
    # Create runtime
    runtime = create_runtime()
    runtime.app_state = app_state
    
    # Start workers
    print("Starting workers...")
    runtime.start()
    
    # Wait for initial data
    print("Waiting for initial data (5 seconds)...")
    time.sleep(5)
    
    # Render overview
    print("\n" + "=" * 80)
    print("ROUTEOPS OVERVIEW DEMO")
    print("=" * 80 + "\n")
    
    # Create mock terminal and screen
    term = MockTerm(width=130, height=50)
    screen = OverviewScreen(app_state)
    screen.set_size(130, 50)
    screen.render(term)
    term.refresh()
    
    print("\n" + "=" * 80)
    print("Demo complete. Press Ctrl+C to exit.")
    print("=" * 80)
    
    # Keep running for a bit to show updates
    try:
        for i in range(10):
            time.sleep(2)
            # Re-render
            term = MockTerm(width=130, height=50)
            screen.render(term)
            print("\n\n--- UPDATE ---\n")
            term.refresh()
    except KeyboardInterrupt:
        pass
    
    # Shutdown
    print("\nShutting down...")
    runtime.shutdown()


if __name__ == "__main__":
    main()
