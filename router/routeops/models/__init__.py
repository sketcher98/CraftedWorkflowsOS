"""
RouteOps Models Package

Exports all model classes for the RouteOps TUI.
"""

from routeops.models.provider import (
    ProviderHealth,
    ModelAvailability,
    ProviderConfig,
    ProviderModel,
    render_provider_health,
    render_provider_health_with_details,
)

from routeops.models.request import (
    EventModel,
    ReconstructedAttempt,
    ReconstructedRequest,
    RequestStatus,
    EventType,
)

from routeops.models.route import (
    RouteConfig,
    PreferenceConfig,
    RouterConfig,
)

# Alias for backward compatibility
ConfigModel = RouterConfig

from routeops.models.process import (
    ProcessInfo,
    ProcessCandidate,
    ProcessIdentity,
    get_process_uptime,
)

__all__ = [
    "ProviderHealth",
    "ModelAvailability",
    "ProviderConfig",
    "ProviderModel",
    "render_provider_health",
    "render_provider_health_with_details",
    "EventModel",
    "ReconstructedAttempt",
    "ReconstructedRequest",
    "RequestStatus",
    "EventType",
    "RouteConfig",
    "PreferenceConfig",
    "RouterConfig",
    "ConfigModel",
    "ProcessInfo",
    "ProcessCandidate",
    "ProcessIdentity",
    "get_process_uptime",
]
