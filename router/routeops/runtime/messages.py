"""
Runtime Messages - Typed messages for inter-thread communication

Defines all message types that workers send to the main thread.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List, Any
from enum import Enum

from routeops.models.provider import ProviderHealth, ModelAvailability
from routeops.models.route import RouterConfig
from routeops.models.process import ProcessCandidate
from routeops.models.request import EventModel, ReconstructedRequest
from routeops.adapters.jsonl_tail import JSONLEvent


class DataFreshness(Enum):
    FRESH = "fresh"
    STALE = "stale"
    ERROR = "error"


@dataclass(frozen=True)
class Update:
    """Base update message"""
    source: str
    timestamp: datetime
    freshness: DataFreshness = DataFreshness.FRESH
    error: Optional[str] = None


@dataclass(frozen=True)
class HealthUpdate(Update):
    """Provider health update from /health endpoint"""
    providers: List[ProviderHealth] = field(default_factory=list)


@dataclass(frozen=True)
class ModelsUpdate(Update):
    """Models update from /v1/models endpoint"""
    models: List[ModelAvailability] = field(default_factory=list)


@dataclass(frozen=True)
class ConfigUpdate(Update):
    """Config update from config.json"""
    config: Optional[Any] = None  # RouterConfig


@dataclass(frozen=True)
class ProcessUpdate(Update):
    """Process inspection update"""
    candidates: List[Any] = field(default_factory=list)  # ProcessCandidate


@dataclass(frozen=True)
class TelemetryUpdate(Update):
    """Telemetry events from JSONL"""
    events: List[Any] = field(default_factory=list)  # JSONLEvent
    last_completed_request: Optional[dict] = None


@dataclass(frozen=True)
class RequestStateUpdate(Update):
    """Reconstructed request state update"""
    last_completed_request: Optional[dict] = None


@dataclass(frozen=True)
class ErrorUpdate(Update):
    """Worker error"""
    worker: str = ""


@dataclass(frozen=True)
class ShutdownUpdate(Update):
    """Shutdown signal"""
    pass


@dataclass(frozen=True)
class SchedulerTickUpdate(Update):
    """Periodic scheduler tick"""
    pass
