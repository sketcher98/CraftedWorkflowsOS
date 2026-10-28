"""
Process Inspect Adapter - Inspects router process

Read-only process inspection using /proc filesystem.
Identifies production router by exe path and cwd.
"""

import os
from pathlib import Path
from typing import Optional, List
from dataclasses import dataclass
from routeops.models.process import ProcessInfo, ProcessIdentity, get_process_uptime


@dataclass(frozen=True)
class ProcessCandidate:
    """A candidate router process"""
    pid: int
    exe_path: str
    cwd: str
    cmdline: List[str]
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


class ProcessInspectAdapter:
    """
    Inspects router process via /proc.
    
    Features:
    - Identifies production router by exe path and cwd
    - Does not rely on pgrep syntax
    - Classifies as PRODUCTION, TEST, WRONG, NOT_RUNNING
    - Does not inspect credentials or process environment
    - Binary size as supplementary evidence only
    """
    
    # Expected production paths
    PRODUCTION_EXE = Path("/data/data/com.termux/files/home/CraftedWorkflowsOS/router/router")
    PRODUCTION_CWD = Path("/data/data/com.termux/files/home/CraftedWorkflowsOS/router")
    
    def __init__(self, interval: float = 10.0):
        self.interval = interval
        self._cached_candidates: List[ProcessCandidate] = []
        self._last_poll_time: float = 0.0
        self._last_error: Optional[str] = None
    
    def poll(self) -> List[ProcessCandidate]:
        """
        Scan /proc for router processes.
        Returns list of ProcessCandidate objects.
        """
        import time
        
        candidates = []
        self._last_error = None
        
        try:
            proc_dir = Path("/proc")
            if not proc_dir.exists() or not proc_dir.is_dir():
                self._last_error = "/proc not accessible"
                self._cached_candidates = []
                return []
            
            for entry in proc_dir.iterdir():
                # Skip non-directories and handle permission errors
                try:
                    if not entry.is_dir():
                        continue
                except PermissionError:
                    continue
                except OSError:
                    continue
                
                # Try to parse PID
                try:
                    pid = int(entry.name)
                except ValueError:
                    continue
                
                candidate = self._inspect_process(pid)
                if candidate:
                    candidates.append(candidate)
            
            self._cached_candidates = candidates
            self._last_poll_time = time.time()
            return candidates
            
        except PermissionError:
            self._last_error = "Permission denied accessing /proc"
            self._cached_candidates = []
            return []
        except Exception as e:
            self._last_error = f"Unexpected error: {e}"
            self._cached_candidates = []
            return []
    
    def _inspect_process(self, pid: int) -> Optional[ProcessCandidate]:
        """Inspect a single process."""
        try:
            proc_path = Path(f"/proc/{pid}")
            
            # Read exe symlink
            exe_path = ""
            try:
                exe_path = os.readlink(proc_path / "exe")
            except (OSError, FileNotFoundError):
                return None
            
            # Read cwd symlink
            cwd = ""
            try:
                cwd = os.readlink(proc_path / "cwd")
            except (OSError, FileNotFoundError):
                return None
            
            # Read cmdline
            cmdline = []
            try:
                with open(proc_path / "cmdline", 'r') as f:
                    cmdline = f.read().split('\x00')
                    cmdline = [c for c in cmdline if c]
            except (OSError, FileNotFoundError):
                pass
            
            # Get binary size
            binary_size = 0
            try:
                binary_size = os.path.getsize(exe_path)
            except (OSError, FileNotFoundError):
                pass
            
            # Classify identity
            identity = self._classify_identity(exe_path, cwd)
            
            # Get uptime
            uptime = get_process_uptime(pid)
            
            return ProcessCandidate(
                pid=pid,
                exe_path=exe_path,
                cwd=cwd,
                cmdline=cmdline,
                identity=identity,
                binary_size=binary_size,
                uptime_seconds=uptime
            )
            
        except Exception:
            return None
    
    def _classify_identity(self, exe_path: str, cwd: str) -> str:
        """Classify process identity based on exe and cwd."""
        
        # Normalize paths
        try:
            exe_path_resolved = Path(exe_path).resolve() if exe_path else Path("")
        except Exception:
            exe_path_resolved = Path("")
        
        try:
            cwd_resolved = Path(cwd).resolve() if cwd else Path("")
        except Exception:
            cwd_resolved = Path("")
        
        # Check for production router
        try:
            prod_exe = self.PRODUCTION_EXE.resolve()
            prod_cwd = self.PRODUCTION_CWD.resolve()
            
            if exe_path_resolved == prod_exe and cwd_resolved == prod_cwd:
                return ProcessIdentity.PRODUCTION
        except Exception:
            pass
        
        # Check for test router (same binary, different cwd)
        try:
            prod_exe = self.PRODUCTION_EXE.resolve()
            if exe_path_resolved == prod_exe:
                return ProcessIdentity.TEST
        except Exception:
            pass
        
        # Check if it's a router binary but wrong location
        if "router" in str(exe_path_resolved).lower():
            return ProcessIdentity.WRONG
        
        return ProcessIdentity.NOT_RUNNING
    
    def get_cached(self) -> List[ProcessCandidate]:
        """Get cached candidates."""
        return list(self._cached_candidates)

    def detect_hermes_pids(self) -> List[int]:
        """Find live Hermes processes (agent / gateway / MCP servers).

        Read-only: reads /proc/<pid>/cmdline only. Never reads /proc/<pid>/environ
        (that would expose credentials). A Hermes "process" is any process whose
        cmdline references the hermes-agent tree or a known Hermes module.
        """
        pids = []
        agent_root = str(Path.home() / ".hermes" / "hermes-agent")
        markers = (agent_root, "tui_gateway.entry", "twitter_mcp.server")
        try:
            proc_dir = Path("/proc")
            for entry in proc_dir.iterdir():
                try:
                    if not entry.is_dir():
                        continue
                    pid = int(entry.name)
                except (ValueError, OSError):
                    continue
                try:
                    with open(entry / "cmdline", "r") as f:
                        cmdline = f.read()
                except (OSError, FileNotFoundError, PermissionError):
                    continue
                if any(m in cmdline for m in markers):
                    pids.append(pid)
        except (PermissionError, OSError):
            return []
        return sorted(pids)

    def get_production_process(self) -> Optional[ProcessCandidate]:
        """Get the production router process if found."""
        for candidate in self._cached_candidates:
            if candidate.identity == ProcessIdentity.PRODUCTION:
                return candidate
        return None
    
    def get_last_error(self) -> Optional[str]:
        """Get last error message."""
        return self._last_error
    
    def get_last_poll_time(self) -> float:
        """Get timestamp of last successful poll."""
        return self._last_poll_time


def get_production_router() -> Optional[ProcessInfo]:
    """
    Convenience function to get production router ProcessInfo.
    Uses a one-shot inspection.
    """
    adapter = ProcessInspectAdapter()
    candidates = adapter.poll()
    
    for candidate in candidates:
        if candidate.identity == ProcessIdentity.PRODUCTION:
            uptime = get_process_uptime(candidate.pid)
            return ProcessInfo(
                pid=candidate.pid,
                exe=candidate.exe_path,
                cwd=candidate.cwd,
                uptime_seconds=uptime,
                identity=candidate.identity,
                binary_size=candidate.binary_size
            )
    
    return None
