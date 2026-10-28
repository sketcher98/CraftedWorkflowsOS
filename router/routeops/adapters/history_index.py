"""
History Index Adapter — full-file JSONL index for the History screen.

The History screen claims to show "full JSONL" but previously rendered only
app_state.recent_events, which is capped at 500 events. This adapter builds a
real index over the WHOLE router-events.jsonl file:

- Initial pass: streaming scan of the entire file (never loads it all into a
  single string — line by line, so a 100 MB file does not blow the heap).
- Steady state: incremental appends from the last known byte offset.
- Rotation/truncation: detected and triggers a full rescan.
- Memory bound: keeps at most MAX_EVENTS newest events in memory (default
  20000) but counts EVERY event in the file so the footer can report the
  true totals instead of a truncated lie.

Read-only. Never writes to the JSONL file.
"""

import json
import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from routeops.models.request import EventModel


# Event type → filter bucket. Mirrors the History screen's filter names.
# Includes the B-3..B-7 + GAP-001..007 telemetry types.
FILTER_ATTEMPTS = {"route_attempt", "request_started", "route_committed"}
FILTER_RESULTS = {"route_result", "stream_result", "client_abort",
                 "request_exhausted", "catalog_refreshed"}
FILTER_FALLBACKS = {"preference_fallback", "route_cooldown",
                    "route_quarantined", "route_recovered"}
FILTER_COOLDOWNS = {"route_cooldown"}
FILTER_HEALTH = {"provider_health", "route_recovered", "route_quarantined",
                 "route_skipped", "health_transition",
                 "credential_rotation_signal", "catalog_skip"}
FILTER_ERRORS = {"route_result", "stream_result", "client_abort",
                 "catalog_skip", "credential_rotation_signal"}


class HistoryIndex:
    """Full-file index over router-events.jsonl."""

    MAX_EVENTS = 20000

    def __init__(self, path: str, max_events: int = MAX_EVENTS):
        self.path = Path(path)
        self.max_events = max_events
        # Newest-first list of parsed events (bounded)
        self._events: List[EventModel] = []
        # Counts per event type across the WHOLE file (not bounded)
        self._counts: Dict[str, int] = {}
        self._total = 0
        self._offset = 0        # byte offset of the last fully-read line
        self._inode: Optional[int] = None
        self._full_scan_done = False
        self._last_error: str = ""

    # ------------------------------------------------------------------ state

    @property
    def total_events(self) -> int:
        return self._total

    @property
    def counts(self) -> Dict[str, int]:
        return dict(self._counts)

    @property
    def last_error(self) -> str:
        return self._last_error

    @property
    def fully_indexed(self) -> bool:
        return self._full_scan_done

    # ------------------------------------------------------------------ scan

    def refresh(self) -> int:
        """Index new content. Returns the number of events added."""
        try:
            if not self.path.exists():
                self._last_error = f"missing: {self.path}"
                return 0
            stat = os.stat(self.path)
        except OSError as e:
            self._last_error = str(e)
            return 0

        # Rotation / truncation detection
        if self._inode is not None:
            if stat.st_ino != self._inode or stat.st_size < self._offset:
                self._reset()
                return self._full_scan()

        self._inode = stat.st_ino

        if not self._full_scan_done:
            return self._full_scan()

        return self._incremental(stat.st_size)

    def _reset(self) -> None:
        self._events = []
        self._counts = {}
        self._total = 0
        self._offset = 0
        self._inode = None
        self._full_scan_done = False

    def _full_scan(self) -> int:
        """Stream the whole file, line by line. Never read it all at once."""
        added = 0
        self._events = []
        self._counts = {}
        self._total = 0
        self._offset = 0
        try:
            with open(self.path, "r", errors="replace") as f:
                for line in f:
                    if not line.endswith("\n"):
                        # Partial last line — stop, will be re-read next pass
                        break
                    self._offset += len(line.encode("utf-8", errors="replace"))
                    ev = self._parse(line)
                    if ev is None:
                        continue
                    self._append(ev)
                    added += 1
            self._full_scan_done = True
            self._last_error = ""
        except OSError as e:
            self._last_error = str(e)
        return added

    def _incremental(self, size: int) -> int:
        """Read only the bytes appended since the last pass."""
        if size <= self._offset:
            return 0
        added = 0
        try:
            with open(self.path, "rb") as f:
                f.seek(self._offset)
                chunk = f.read()
                # Only process complete lines
                last_nl = chunk.rfind(b"\n")
                if last_nl < 0:
                    return 0
                complete = chunk[:last_nl + 1]
                consumed = len(complete)
                for raw in complete.split(b"\n"):
                    if not raw.strip():
                        continue
                    try:
                        line = raw.decode("utf-8", errors="replace")
                    except Exception:
                        continue
                    ev = self._parse(line)
                    if ev is None:
                        continue
                    self._append(ev)
                    added += 1
                self._offset += consumed
        except OSError as e:
            self._last_error = str(e)
        return added

    def _parse(self, line: str) -> Optional[EventModel]:
        line = line.strip()
        if not line:
            return None
        try:
            data = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        return EventModel.from_json(data)

    def _append(self, ev: EventModel) -> None:
        self._total += 1
        self._counts[ev.event_type] = self._counts.get(ev.event_type, 0) + 1
        self._events.append(ev)
        # Bound memory: keep the NEWEST max_events
        if len(self._events) > self.max_events:
            del self._events[:len(self._events) - self.max_events]

    # ---------------------------------------------------------------- query

    def query(self, filter_mode: str) -> List[EventModel]:
        """Filtered events, NEWEST FIRST."""
        events = self._events
        if filter_mode == "attempts":
            events = [e for e in events if e.event_type in FILTER_ATTEMPTS]
        elif filter_mode == "results":
            events = [e for e in events if e.event_type in FILTER_RESULTS]
        elif filter_mode == "fallbacks":
            events = [e for e in events if e.event_type in FILTER_FALLBACKS]
        elif filter_mode == "cooldowns":
            events = [e for e in events if e.event_type in FILTER_COOLDOWNS]
        elif filter_mode == "health":
            events = [e for e in events if e.event_type in FILTER_HEALTH]
        elif filter_mode == "errors":
            events = [e for e in events
                      if e.event_type in FILTER_ERRORS and e.error]
        return list(reversed(events))

    def count_for(self, filter_mode: str) -> int:
        """Count matching the filter across the WHOLE file (not just the
        in-memory window) — uses the type histogram where possible."""
        if filter_mode == "all":
            return self._total
        if filter_mode == "attempts":
            return sum(self._counts.get(t, 0) for t in FILTER_ATTEMPTS)
        if filter_mode == "results":
            return sum(self._counts.get(t, 0) for t in FILTER_RESULTS)
        if filter_mode == "fallbacks":
            return sum(self._counts.get(t, 0) for t in FILTER_FALLBACKS)
        if filter_mode == "cooldowns":
            return sum(self._counts.get(t, 0) for t in FILTER_COOLDOWNS)
        if filter_mode == "health":
            return sum(self._counts.get(t, 0) for t in FILTER_HEALTH)
        if filter_mode == "errors":
            # Errors need the error field — count from the in-memory window
            return len(self.query("errors"))
        return self._total
