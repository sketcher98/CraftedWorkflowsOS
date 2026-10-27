"""
Navigation Shell - Main TUI event loop with screen routing and input handling.
"""

import curses
from typing import Optional, Dict, Callable

from routeops.tui.curses_terminal import curses_session, CursesTerminal
from routeops.state import AppState, ScreenType
from routeops.screens.overview_curses import OverviewScreen
from routeops.screens.providers_curses import ProvidersScreen
from routeops.screens.routes_curses import RoutesScreen
from routeops.screens.history_curses import HistoryScreen
from routeops.screens.diagnostics_curses import DiagnosticsScreen
from routeops.screens.settings_curses import SettingsScreen


class NavigationShell:
    """Main navigation shell managing screen routing and input."""

    # Cycle order: Overview -> Providers -> Routes -> History -> Diag -> Settings
    SCREEN_CYCLE: list = [
        ScreenType.OVERVIEW, ScreenType.PROVIDERS, ScreenType.ROUTES,
        ScreenType.HISTORY, ScreenType.DIAGNOSTICS, ScreenType.SETTINGS,
    ]

    def __init__(self, app_state: AppState):
        self.app_state = app_state
        self.term: Optional[CursesTerminal] = None
        self.current_screen = ScreenType.OVERVIEW
        self.running = False
        
        # Screen instances
        self.screens: Dict[ScreenType, object] = {}
        
        # Key mappings to screens
        self.screen_keys = {
            # Direct navigation
            ord('p'): ScreenType.PROVIDERS,
            ord('r'): ScreenType.ROUTES,
            ord('y'): ScreenType.HISTORY,
            ord('d'): ScreenType.DIAGNOSTICS,
            ord('s'): ScreenType.SETTINGS,
            # Global
            ord('q'): ScreenType.OVERVIEW,  # q on overview = quit
        }

    def run(self, term: CursesTerminal) -> None:
        """Run main event loop."""
        self.term = term
        self.running = True
        
        # Initialize screens
        self._init_screens()
        
        # Main loop
        while self.running:
            # Resize screens
            for screen in self.screens.values():
                if hasattr(screen, 'set_size'):
                    screen.set_size(term.size.cols, term.size.rows)
            
            # Render current screen
            self._render_current()
            
            # Get input
            key = term.getch()
            
            if key != -1:
                if not self._handle_key(key):
                    break  # Quit
            
            # Small sleep to prevent busy loop
            curses.napms(50)

    def _init_screens(self) -> None:
        """Initialize all screen instances."""
        self.screens[ScreenType.OVERVIEW] = OverviewScreen(self.app_state)
        self.screens[ScreenType.PROVIDERS] = ProvidersScreen(self.app_state)
        self.screens[ScreenType.ROUTES] = RoutesScreen(self.app_state)
        self.screens[ScreenType.HISTORY] = HistoryScreen(self.app_state)
        self.screens[ScreenType.DIAGNOSTICS] = DiagnosticsScreen(self.app_state)
        self.screens[ScreenType.SETTINGS] = SettingsScreen(self.app_state)

    def _render_current(self) -> None:
        """Render current screen."""
        self.term.clear()
        screen = self.screens.get(self.current_screen)
        if screen and hasattr(screen, 'render'):
            screen.render(self.term)
        self.term.refresh()

    def _handle_key(self, key: int) -> bool:
        """Handle key press. Returns False to quit."""
        screen = self.screens.get(self.current_screen)
        
        # Let current screen handle key first
        if screen and hasattr(screen, 'handle_key'):
            handled = screen.handle_key(key)
            if handled:
                return True
        
        # Global keys
        if key == ord('q') and self.current_screen == ScreenType.OVERVIEW:
            return False  # Quit on 'q' from overview
        
        if key == 27:  # Esc
            if self.current_screen != ScreenType.OVERVIEW:
                self.current_screen = ScreenType.OVERVIEW
                return True
            return False  # Quit on Esc from overview
        
        # Screen cycling: Tab forward, Shift-Tab / Left / Right arrows.
        # Screens that consume these keys (Routes: Tab=pref cycle, Left/Right
        # = matrix scroll; editor overlays) return handled=True above and we
        # never reach here — so this only fires when the key is free.
        if key in (ord('\t'), curses.KEY_RIGHT):
            self._cycle_screen(+1)
            return True
        if key in (curses.KEY_BTAB, curses.KEY_LEFT):
            self._cycle_screen(-1)
            return True
        
        # Direct screen navigation
        if key in self.screen_keys:
            target = self.screen_keys[key]
            if target != self.current_screen:
                self.current_screen = target
                return True
        
        return True

    def _cycle_screen(self, delta: int) -> None:
        """Move to the next/previous screen in cycle order."""
        try:
            idx = self.SCREEN_CYCLE.index(self.current_screen)
        except ValueError:
            idx = 0
        nxt = (idx + delta) % len(self.SCREEN_CYCLE)
        self.current_screen = self.SCREEN_CYCLE[nxt]
