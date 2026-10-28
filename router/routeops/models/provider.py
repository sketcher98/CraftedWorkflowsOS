"""
Provider Models

ProviderHealth  -- from /health endpoint (provider-level only)
ModelAvailability -- from /v1/models + config (catalog availability)
"""

from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Tuple


@dataclass(frozen=True)
class CellHealth:
    """Per-cell (provider×model) health from the router's /health routes[].

    Added when the router gained B-7 per-cell health — /health now reports
    every route cell individually, which the TUI previously had no way to
    see (provider-level only).
    """
    model: str
    healthy: bool
    failures: int = 0
    hard_failed: bool = False
    cooldown_until: Optional[datetime] = None
    warm: bool = False

    @property
    def status_label(self) -> str:
        if self.hard_failed:
            return "HARD-FAILED"
        if not self.healthy:
            if self.cooldown_until:
                return "COOLDOWN"
            return "UNHEALTHY"
        if self.warm:
            return "HEALTHY (warm)"
        return "HEALTHY"


@dataclass(frozen=True)
class ProviderHealth:
    """Provider health from /health endpoint.
    Includes per-cell routes[] when the router provides them (B-7)."""
    name: str
    healthy: bool
    failures: int
    hard_failed: bool
    catalog_models: int
    cooldown_until: Optional[datetime] = None
    cells: Tuple[CellHealth, ...] = ()

    @property
    def status_label(self) -> str:
        if self.hard_failed:
            return "HARD-FAILED"
        if not self.healthy:
            if self.cooldown_until:
                return f"COOLDOWN"
            return "UNHEALTHY"
        return "HEALTHY"

    @property
    def cooldown_remaining_seconds(self) -> Optional[int]:
        if self.cooldown_until:
            delta = self.cooldown_until - datetime.now()
            return max(0, int(delta.total_seconds()))
        return None


@dataclass(frozen=True)
class ModelAvailability:
    """
    Model availability from /v1/models catalog + config.json.
    This is CATALOG-LEVEL, not route health.
    """
    model_id: str                    # From /v1/models catalog
    available_in_catalog: bool       # True if listed in /v1/models
    providers_offering: Tuple[str, ...]  # Providers configured for this model (from config.json)

    # NEVER include: provider_health, route_health, provider_x_model_health


@dataclass(frozen=True)
class ProviderConfig:
    """Provider configuration from config.json"""
    name: str
    base_url: str
    key_env: str  # Opaque metadata name only (e.g., "NVIDIA_API_KEY"), NEVER the actual value
    key_envs: Tuple[str, ...] = ()  # Additional key env names

    def __post_init__(self):
        # If key_envs is empty but key_env is set, use key_env as single key
        if not self.key_envs and self.key_env:
            object.__setattr__(self, 'key_envs', (self.key_env,))

    @property
    def display_name(self) -> str:
        return self.name


@dataclass(frozen=True)
class ProviderModel:
    """Complete provider model combining config and health"""
    config: ProviderConfig
    health: ProviderHealth
    routes: Tuple = ()  # ConfiguredRoute objects for this provider

    @property
    def name(self) -> str:
        return self.config.name

    @property
    def base_url(self) -> str:
        return self.config.base_url

    @property
    def key_env(self) -> str:
        return self.config.key_env


def render_provider_health(health: ProviderHealth) -> str:
    """Render provider health for display. Never implies route health."""
    label = health.status_label
    if health.cooldown_until and not health.hard_failed:
        remaining = health.cooldown_remaining_seconds
        if remaining is not None:
            return f"{health.name}: {label} ({remaining}s)"
    return f"{health.name}: {label}"


def render_provider_health_with_details(health: ProviderHealth) -> str:
    """Render provider health with full details."""
    parts = [health.name, health.status_label]
    if health.failures:
        parts.append(f"failures: {health.failures}")
    if health.catalog_models:
        parts.append(f"catalog: {health.catalog_models}")
    if health.cooldown_until and not health.hard_failed:
        remaining = health.cooldown_remaining_seconds
        if remaining is not None:
            parts.append(f"cooldown: {remaining}s")
    return " | ".join(parts)
