"""Shared overview-standard render helpers for every RouteOps screen.

Single source of truth for the header line and boxed sections so all
screens hold the SAME visual grammar as Overview: LIVE header with
router identity + clock, then full-width titled boxes with segment
support. Nothing here knows screen specifics — only app_state + a
write callback (curried for the CursesTerminal or MockTerm).
"""

import curses


def render_header(write, app_state, y, cols, rows):
    """Overview-standard header line + rule. Returns next y."""
    if app_state.last_refresh:
        time_str = app_state.last_refresh.strftime("%H:%M:%S")
    else:
        time_str = "--:--:--"

    proc = getattr(app_state, "process_info", None)
    router_online = bool(proc and getattr(proc, "identity", "") == "PRODUCTION")
    if router_online:
        left = "● LIVE   Router: ONLINE"
    elif proc:
        left = f"● LIVE   Router: {proc.identity}"
    else:
        left = "● LIVE   Router: NOT OBSERVED"

    if proc:
        left += f"   PID: {proc.pid}"
        up = getattr(proc, "uptime_display", "--")
        left += f"   Up: {up if up != '--' else '--'}"
    left += f"   Port: {getattr(app_state, 'router_port', '?')}"

    if getattr(app_state, "paused", False):
        left += "   ⏸ PAUSED"

    if 0 <= y < rows:
        write(y, 0, left, curses.color_pair(2) | curses.A_BOLD)
    if 0 <= y < rows:
        write(y, cols - len(time_str) - 2, time_str, curses.color_pair(5))
    if 0 <= y + 1 < rows:
        write(y + 1, 0, "─" * (cols - 2), curses.color_pair(1))
    return y + 2


def render_box(write, y, cols, title, lines, rows):
    """Overview-standard boxed section. lines: str OR (text, attr) list.
    Returns next y."""
    w = cols - 4
    if 0 <= y < rows:
        write(y, 0, f"┌ {title} {'─' * max(0, w - len(title) - 2)}┐",
              curses.color_pair(1))

    for i, line in enumerate(lines):
        row = y + 1 + i
        if not (0 <= row < rows):
            continue
        if isinstance(line, list):
            write(row, 2, "│ ", curses.color_pair(1))
            x = 4
            room = w - 2
            for text, attr in line:
                if room <= 0 or not text:
                    break
                write(row, x, text[:room], attr)
                x += len(text[:room])
                room -= len(text[:room])
            write(row, w + 2, " │", curses.color_pair(1))
        else:
            write(row, 2, f"│ {line[:w - 2].ljust(w - 2)} │")

    bottom_row = y + 1 + len(lines)
    if 0 <= bottom_row < rows:
        write(bottom_row, 0, "└" + "─" * w + "┘", curses.color_pair(1))
    return y + 2 + len(lines)
