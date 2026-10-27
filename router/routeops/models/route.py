"""
Route Models

RouteConfig -- from config.json (static configuration)
ConfiguredRoute -- route with health info for display
"""

from dataclasses import dataclass
from typing import Optional, Tuple
from enum import Enum


class RouteProtocol(Enum):
    OPENAI_COMPATIBLE = "openai-compatible"
    ANTHROPIC = "anthropic"


@dataclass(frozen=True)
class RouteConfig:
    """Single route configuration from config.json"""
    provider: str
    model: str
    preference: str  # The preference name this route belongs to


@dataclass(frozen=True)
class PreferenceConfig:
    """Preference configuration from config.json"""
    name: str
    routes: Tuple[RouteConfig, ...]


@dataclass(frozen=True)
class RouterConfig:
    """Complete router configuration"""
    providers: Tuple['ProviderConfig', ...]
    preferences: Tuple['PreferenceConfig', ...]
    total_routes: int

    @property
    def provider_count(self) -> int:
        return len(self.providers)

    @property
    def preference_count(self) -> int:
        return len(self.preferences)

    @property
    def configured_route_count(self) -> int:
        return self.total_routes

    def get_configured_routes(self):
        """Get all configured routes with preference context"""
        routes = []
        for pref in self.preferences:
            for i, route in enumerate(pref.routes):
                routes.append({
                    'preference': pref.name,
                    'provider': route.provider,
                    'model': route.model,
                    'order': i
                })
        return routes

    @property
    def unique_model_count(self) -> int:
        models = set()
        for pref in self.preferences:
            for route in pref.routes:
                models.add(route.model)
        return len(models)
