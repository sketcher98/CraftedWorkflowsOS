"""
Router Model
"""

from dataclasses import dataclass
from typing import Optional, Tuple
from routeops.models.route import RouterConfig


@dataclass(frozen=True)
class RouterModel:
    """Router model combining identity and config."""
    config: Optional[RouterConfig] = None
    identity: str = "UNKNOWN"
    uptime_seconds: int = 0
    binary_size: int = 0
    last_seen: float = 0.0
