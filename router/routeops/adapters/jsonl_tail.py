"""
JSONL Tail Adapter - Incremental tail of router-events.jsonl

Implements incremental reading with:
- Inode tracking for rotation detection
- Byte position tracking
- Truncation detection without inode change
- Partial line preservation
- Malformed JSON handling
- UTF-8 decoding error handling
- File absence/appearance handling
- Bounded in-memory event buffer
"""

import json
import os
from pathlib import Path
from typing import List, Optional, Tuple, Any
from dataclasses import dataclass


@dataclass
class JSONLEvent:
    """Parsed JSONL event with metadata."""
    raw_line: str
    parsed: Optional[dict]
    line_number: int
    byte_offset: int
    parse_error: Optional[str] = None


class JSONLTailAdapter:
    """
    Incremental tail of router-events.jsonl with line buffering.
    
    Features:
    - Tracks inode and byte position for incremental reads
    - Detects inode rotation (log rotation)
    - Detects truncation without inode change
    - Preserves partial final line across polls
    - Handles malformed JSON and UTF-8 errors safely
    - Bounded in-memory event buffer
    """
    
    def __init__(self, path: str, max_buffer: int = 1000, initial_lines: int = 500):
        self.path = Path(path)
        self.max_buffer = max_buffer
        self._buffer: List[JSONLEvent] = []
        self._inode: Optional[int] = None
        self._position: int = 0
        self._partial_line: str = ""
        self._file_missing_count: int = 0
        self._initialized: bool = False  # Track if we've done initial seek to end
        self._initial_lines = initial_lines  # Lines to read on first init for historical context
        
    def _get_file_stat(self) -> Optional[os.stat_result]:
        """Get file stat, return None if file doesn't exist."""
        try:
            return self.path.stat()
        except (FileNotFoundError, OSError):
            return None
    
    def _detect_rotation_or_truncation(self, stat: os.stat_result) -> Tuple[bool, bool]:
        """
        Detect if file was rotated or truncated.
        Returns (rotated, truncated).
        """
        if self._inode is None:
            return False, False
        
        rotated = stat.st_ino != self._inode
        truncated = (not rotated) and (stat.st_size < self._position)
        
        return rotated, truncated
    
    def _read_new_content(self, stat: os.stat_result) -> str:
        """Read new content from file since last position."""
        if self._position >= stat.st_size:
            return ""
        
        try:
            with open(self.path, 'rb') as f:
                f.seek(self._position)
                content = f.read(stat.st_size - self._position)
                self._position = stat.st_size
                return content.decode('utf-8', errors='replace')
        except (OSError, UnicodeDecodeError) as e:
            # On read error, reset position to end to avoid re-reading bad data
            self._position = stat.st_size
            return ""
    
    def _parse_lines(self, content: str, line_number_start: int) -> Tuple[List[JSONLEvent], str, int]:
        """
        Parse lines from content, handling partial lines.
        Returns (events, remaining_partial, new_line_count).
        """
        lines = (self._partial_line + content).split('\n')
        events = []
        line_number = line_number_start
        
        # All but the last element are complete lines
        for i, line in enumerate(lines[:-1]):
            byte_offset = self._position - len(content) + len('\n'.join(lines[:i+1]))
            event = self._parse_line(line, line_number, byte_offset)
            events.append(event)
            line_number += 1
        
        # Last element is the partial line (may be empty)
        partial = lines[-1]
        return events, partial, line_number - line_number_start
    
    def _parse_line(self, line: str, line_number: int, byte_offset: int) -> JSONLEvent:
        """Parse a single JSONL line."""
        if not line.strip():
            return JSONLEvent(
                raw_line=line,
                parsed=None,
                line_number=line_number,
                byte_offset=byte_offset,
                parse_error="empty line"
            )
        
        try:
            parsed = json.loads(line)
            return JSONLEvent(
                raw_line=line,
                parsed=parsed,
                line_number=line_number,
                byte_offset=byte_offset,
                parse_error=None
            )
        except json.JSONDecodeError as e:
            return JSONLEvent(
                raw_line=line,
                parsed=None,
                line_number=line_number,
                byte_offset=byte_offset,
                parse_error=str(e)
            )
    
    def poll(self) -> List[JSONLEvent]:
        """
        Poll for new events.
        Returns list of new JSONLEvent objects.
        """
        stat = self._get_file_stat()
        
        if stat is None:
            # File doesn't exist
            self._file_missing_count += 1
            if self._file_missing_count == 1:
                # First time noticing it's missing - reset state
                self._inode = None
                self._position = 0
                self._partial_line = ""
            return []
        
        # File exists
        self._file_missing_count = 0
        
        rotated, truncated = self._detect_rotation_or_truncation(stat)
        
        if rotated:
            # Log rotation - reset position and inode
            self._inode = stat.st_ino
            self._position = 0
            self._partial_line = ""
            self._buffer.clear()
        elif truncated:
            # Truncation without rotation - reset position
            self._position = 0
            self._partial_line = ""
            self._buffer.clear()
        elif self._inode is None:
            # First time seeing the file - read last N lines for context, then seek to end
            self._inode = stat.st_ino
            
            # Read last N lines for historical context
            try:
                with open(self.path, 'rb') as f:
                    # Seek near the end
                    f.seek(0, 2)  # Seek to end
                    file_size = f.tell()
                    
                    # Read backwards to find last N lines
                    chunk_size = 8192
                    lines_found = 0
                    position = file_size
                    data = b""
                    
                    while position > 0 and lines_found < self._initial_lines:
                        read_size = min(chunk_size, position)
                        position -= read_size
                        f.seek(position)
                        chunk = f.read(read_size)
                        data = chunk + data
                        lines_found = data.count(b'\n')
                    
                    # Split and get last N lines
                    lines = data.split(b'\n')
                    # Remove empty first/last if needed
                    if lines and not lines[-1]:
                        lines.pop()
                    if lines and not lines[0]:
                        lines.pop(0)
                    
                    # Keep only last N lines
                    if len(lines) > self._initial_lines:
                        lines = lines[-self._initial_lines:]
                    
                    # Parse lines
                    for i, line_bytes in enumerate(lines):
                        try:
                            line = line_bytes.decode('utf-8', errors='replace')
                            byte_offset = position + sum(len(l) + 1 for l in lines[:i])  # approximate
                            event = self._parse_line(line, i + 1, byte_offset)
                            if event.parsed:
                                self._buffer.append(event)
                        except Exception:
                            pass
            except Exception:
                # If anything fails, just seek to end
                pass
            
            self._position = stat.st_size
            self._partial_line = ""
            self._initialized = True
            # Return the initial historical events so callers see them
            initial = list(self._buffer)
            return initial
        
        # Read new content
        content = self._read_new_content(stat)
        if not content and not self._partial_line:
            return []
        
        # Parse lines
        line_number_start = len(self._buffer) + 1
        events, self._partial_line, _ = self._parse_lines(content, line_number_start)
        
        # Add to buffer with bounding
        self._buffer.extend(events)
        if len(self._buffer) > self.max_buffer:
            # Remove oldest events, keep most recent
            self._buffer = self._buffer[-self.max_buffer:]
        
        return events
    
    def get_recent(self, n: int = 100) -> List[JSONLEvent]:
        """Get most recent n events from buffer."""
        return self._buffer[-n:] if self._buffer else []
    
    def get_all(self) -> List[JSONLEvent]:
        """Get all buffered events."""
        return list(self._buffer)
    
    def get_last_completed_request(self) -> Optional[dict]:
        """
        Get the last completed request from buffered events.
        A completed request has a terminal route_result.
        """
        # Group events by request_id
        requests = {}
        for event in self._buffer:
            if event.parsed and 'request_id' in event.parsed:
                req_id = event.parsed['request_id']
                if req_id not in requests:
                    requests[req_id] = []
                requests[req_id].append(event)
        
        # Find requests with terminal route_result
        completed = []
        for req_id, events in requests.items():
            has_terminal_result = any(
                e.parsed and e.parsed.get('event') == 'route_result' 
                for e in events
            )
            if has_terminal_result:
                # Get the latest event timestamp for this request
                latest_time = max(
                    e.parsed.get('timestamp', 0) 
                    for e in events if e.parsed
                )
                completed.append((latest_time, req_id, events))
        
        if not completed:
            return None
        
        # Return the most recent completed request
        completed.sort(reverse=True)
        _, req_id, events = completed[0]
        
        return {
            'request_id': req_id,
            'events': [e.parsed for e in events if e.parsed],
            'completed': True
        }
    
    def reset(self) -> None:
        """Reset adapter state."""
        self._buffer.clear()
        self._inode = None
        self._position = 0
        self._partial_line = ""
        self._file_missing_count = 0
