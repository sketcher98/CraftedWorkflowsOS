"""
Curses Terminal Wrapper - Double-buffered curses terminal for smooth rendering.
"""

import curses
from dataclasses import dataclass
from typing import NamedTuple


class Size(NamedTuple):
    rows: int
    cols: int


class CursesTerminal:
    """Wrapper around curses window with double-buffering support."""
    
    def __init__(self):
        self.stdscr = None
        self._size = Size(24, 80)
        self._color_pairs_initialized = False
    
    def _init_colors(self) -> None:
        """Initialize color pairs."""
        if self._color_pairs_initialized:
            return
        
        curses.start_color()
        curses.use_default_colors()
        
        # Color pairs
        curses.init_pair(1, curses.COLOR_WHITE, -1)      # Default/title
        curses.init_pair(2, curses.COLOR_GREEN, -1)      # Healthy/success
        curses.init_pair(3, curses.COLOR_YELLOW, -1)     # Warning/cooldown
        curses.init_pair(4, curses.COLOR_RED, -1)        # Error/failed
        curses.init_pair(5, curses.COLOR_CYAN, -1)       # Info
        curses.init_pair(6, curses.COLOR_MAGENTA, -1)    # Highlight
        curses.init_pair(7, curses.COLOR_BLUE, -1)       # Debug
        curses.init_pair(8, curses.COLOR_WHITE, curses.COLOR_BLUE)  # Status bar
        curses.init_pair(9, curses.COLOR_BLACK, curses.COLOR_WHITE) # Selected
        
        self._color_pairs_initialized = True
    
    def _resize(self) -> None:
        """Update terminal size."""
        if self.stdscr:
            rows, cols = self.stdscr.getmaxyx()
            self._size = Size(rows, cols)
    
    @property
    def size(self) -> Size:
        """Live terminal size — re-query every access so Android keyboard
        show/hide resizes are picked up within one render cycle (~700ms)."""
        if self.stdscr:
            try:
                rows, cols = self.stdscr.getmaxyx()
                self._size = Size(rows, cols)
            except curses.error:
                pass
        return self._size
    
    def clear(self) -> None:
        """Clear the screen."""
        if self.stdscr:
            self.stdscr.clear()
    
    def write(self, y: int, x: int, text: str, attr: int = 0) -> None:
        """Write text at position."""
        if not self.stdscr:
            return
        rows, cols = self._size.rows, self._size.cols
        if y < 0 or y >= rows:
            return
        if x < 0 or x >= cols:
            return
        text = text[:cols - x]
        try:
            self.stdscr.addstr(y, x, text, attr)
        except curses.error:
            pass
    
    def nodelay(self, val: bool) -> None:
        """Set non-blocking input."""
        if self.stdscr:
            self.stdscr.nodelay(val)
    
    def getch(self) -> int:
        """Get a character (non-blocking if nodelay)."""
        if self.stdscr:
            try:
                return self.stdscr.getch()
            except:
                return -1
        return -1
    
    def refresh(self) -> None:
        """Refresh the screen."""
        if self.stdscr:
            self.stdscr.refresh()
    
    def halfdelay(self, tenths: int) -> None:
        """Set half-delay mode."""
        if self.stdscr:
            curses.halfdelay(tenths)


def curses_session():
    """Context manager for curses session with proper cleanup."""
    class CursesContext:
        def __enter__(self):
            stdscr = curses.initscr()
            curses.noecho()
            curses.cbreak()
            stdscr.keypad(True)
            curses.curs_set(0)
            
            term = CursesTerminal()
            term.stdscr = stdscr
            term._init_colors()
            term._resize()
            return term
        
        def __exit__(self, exc_type, exc_val, exc_tb):
            try:
                if curses:
                    curses.nocbreak()
                    curses.echo()
                    curses.endwin()
            except:
                pass
            return False
    
    return CursesContext()
