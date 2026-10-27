"""
Process Model

Process information for router identity verification.
"""

import os
from dataclasses import dataclass
from typing import Optional
from pathlib import Path


def get_process_uptime(pid: int) -> int:
    """Get process uptime in seconds.

    Priority:
    1. /proc/uptime - (starttime / clk_tck)   [blocked on Android]
    2. /proc/<pid>/ dir ctime as start-time proxy  [works on Android]
    """
    import time

    # Method 1: classic /proc/uptime
    try:
        with open(f"/proc/{pid}/stat", "r") as f:
            parts = f.read().split()
        if len(parts) >= 22:
            starttime = int(parts[21])
            clk_tck = os.sysconf(os.sysconf_names['SC_CLK_TCK'])
            try:
                with open("/proc/uptime", "r") as f:
                    uptime = float(f.read().split()[0])
                process_uptime = uptime - (starttime / clk_tck)
                return max(0, int(process_uptime))
            except (FileNotFoundError, PermissionError, OSError):
                pass
    except (FileNotFoundError, PermissionError, ValueError, OSError):
        pass

    # Method 2 (Android): /proc/<pid>/ directory ctime ≈ process start time
    try:
        stat_result = os.stat(f"/proc/{pid}")
        return max(0, int(time.time() - stat_result.st_ctime))
    except (FileNotFoundError, PermissionError, OSError):
        pass

    return 0


@dataclass(frozen=True)
class ProcessInfo:
    """Process information for router identity verification"""
    pid: int
    exe: str
    cwd: str
    uptime_seconds: int
    identity: str  # PRODUCTION, TEST, WRONG, NOT_RUNNING
    binary_size: int = 0  # Binary size in bytes
    
    @property
    def uptime_display(self) -> str:
        """Format uptime as human-readable string"""
        if self.uptime_seconds <= 0:
            return "—"
        hours = self.uptime_seconds // 3600
        minutes = (self.uptime_seconds % 3600) // 60
        if hours > 0:
            return f"{hours}h {minutes}m"
        return f"{minutes}m"
    
    @property
    def is_production(self) -> bool:
        return self.identity == "PRODUCTION"
    
    @property
    def is_test(self) -> bool:
        return self.identity == "TEST"


@dataclass(frozen=True)
class ProcessCandidate:
    """A candidate router process"""
    pid: int
    exe_path: str
    cwd: str
    cmdline: list
    identity: str  # PRODUCTION, TEST, WRONG, NOT_RUNNING
    binary_size: int = 0
    uptime_seconds: int = 0
    
    @property
    def uptime_display(self) -> str:
        """Format uptime as human-readable string"""
        hours = self.uptime_seconds // 3600
        minutes = (self.uptime_seconds % 3600) // 60
        if hours > 0:
            return f"{hours}h {minutes}m"
        return f"{minutes}m"


@dataclass(frozen=True)
class ProcessIdentity:
    """Router process classification"""
    PRODUCTION = "PRODUCTION"
    TEST = "TEST"
    WRONG = "WRONG"
    NOT_RUNNING = "NOT_RUNNING"
