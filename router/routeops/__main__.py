"""
RouteOps Main Entry Point
"""

import sys
import os
import curses

# Add routeops parent to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main():
    """Main entry point for RouteOps TUI."""
    from routeops.tui.curses_terminal import curses_session
    from routeops.tui.nav_shell import NavigationShell
    from routeops.runtime import create_runtime
    from routeops.config import (
        ROUTER_EVENTS_JSONL, ROUTER_BASE_URL, ROUTER_CONFIG_JSON,
        POLL_INTERVALS
    )
    
    # Initialize AppState - will be replaced by runtime's AppState after create_runtime()
    app_state = None
    
    # Create runtime
    runtime = create_runtime()
    # Use the AppState created inside runtime (has _tui_start_time set)
    app_state = runtime.app_state
    
    # Set initial paths on the shared AppState
    app_state.settings.router_events_path = str(ROUTER_EVENTS_JSONL)
    app_state.settings.router_config_path = str(ROUTER_CONFIG_JSON)
    app_state.settings.router_log_path = str(ROUTER_BASE_URL.replace("http://", "").replace(":8643", "") + "/router.log")
    
    # Connect runtime tick to app state refresh
    def tick_callback():
        pass
    runtime.set_tick_callback(tick_callback)
    
    # Start runtime workers (non-blocking)
    runtime.start()
    
    # Run TUI with integrated update processing
    with curses_session() as term:
        shell = NavigationShell(app_state)
        # Patch shell to process runtime updates
        original_run = shell.run
        def run_with_updates(term):
            shell.term = term
            shell.running = True
            shell._init_screens()
            
            while shell.running and not runtime.shutdown_event.is_set():
                # Process runtime updates
                runtime._drain_wakeup()
                runtime._process_updates()
                
                # Resize screens
                for screen in shell.screens.values():
                    if hasattr(screen, 'set_size'):
                        screen.set_size(term.size.cols, term.size.rows)
                
                # Render current screen
                shell._render_current()
                
                # LIVE REFRESH (procmon pattern): halfdelay(7) = 700ms window
                # for a keypress; on timeout getch returns ERR(-1) and the
                # loop re-renders with fresh worker data. Every screen stays
                # live without per-screen timers.
                term.halfdelay(7)
                key = term.getch()
                
                if key != -1:
                    if not shell._handle_key(key):
                        break
            
            runtime.shutdown()
        
        run_with_updates(term)


if __name__ == "__main__":
    main()
