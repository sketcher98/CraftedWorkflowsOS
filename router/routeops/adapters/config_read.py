"""
Config Read Adapter - Reads config.json

Read-only parsing of the production router configuration.
Extracts providers, preferences, routes, and key_env names (opaque only).
"""

import json
import os
from pathlib import Path
from typing import List, Optional, Dict, Any, Tuple
from dataclasses import dataclass

from routeops.models.provider import ProviderConfig
from routeops.models.route import RouteConfig, PreferenceConfig, RouterConfig


class ConfigAdapter:
    """
    Reads config.json for routing configuration.
    
    Features:
    - Read-only parsing
    - Extracts providers, preferences, routes
    - Treats key_env as opaque metadata name ONLY
    - NEVER reads actual credential values from environment
    - Computes configured route count from config structure only
    """
    
    def __init__(self, path: str, interval: float = 60.0):
        self.path = Path(path)
        self.interval = interval
        self._cached_config: Optional[RouterConfig] = None
        self._last_read_time: float = 0.0
        self._last_error: Optional[str] = None
        self._last_mtime: Optional[float] = None
    
    def poll(self) -> Optional[RouterConfig]:
        """
        Poll config.json for changes.
        Returns RouterConfig or cached config.
        """
        import time
        
        try:
            stat = self.path.stat()
            if self._last_mtime is not None and stat.st_mtime <= self._last_mtime:
                return self._cached_config
            
            with open(self.path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            config = self._parse_config(data)
            self._cached_config = config
            self._last_read_time = time.time()
            self._last_mtime = stat.st_mtime
            self._last_error = None
            return config
            
        except FileNotFoundError:
            self._last_error = f"Config file not found: {self.path}"
            return self._cached_config
        except json.JSONDecodeError as e:
            self._last_error = f"JSON decode error: {e}"
            return self._cached_config
        except Exception as e:
            self._last_error = f"Unexpected error: {e}"
            return self._cached_config
    
    def _parse_config(self, data: dict) -> RouterConfig:
        """Parse config JSON into structured config objects."""
        
        # Parse providers
        providers = []
        providers_data = data.get('providers', [])
        if isinstance(providers_data, list):
            for provider_data in providers_data:
                if isinstance(provider_data, dict):
                    # Handle both formats: {name: ..., ...} and {key: {name: ..., ...}}
                    name = provider_data.get('name', '')
                    if not name and len(provider_data) == 1:
                        # Might be {provider_name: {...}} format
                        key = list(provider_data.keys())[0]
                        if isinstance(provider_data[key], dict):
                            name = key
                            provider_data = provider_data[key]
                    providers.append(ProviderConfig(
                        name=name,
                        base_url=provider_data.get('base_url', ''),
                        key_env=provider_data.get('key_env', ''),
                        key_envs=tuple(provider_data.get('key_envs', []))
                    ))
        elif isinstance(providers_data, dict):
            for name, provider_data in providers_data.items():
                if isinstance(provider_data, dict):
                    providers.append(ProviderConfig(
                        name=name,
                        base_url=provider_data.get('base_url', ''),
                        key_env=provider_data.get('key_env', ''),
                        key_envs=tuple(provider_data.get('key_envs', []))
                    ))
        
        # Parse preferences and routes
        preferences = []
        total_routes = 0
        preferences_data = data.get('preferences', [])
        if isinstance(preferences_data, list):
            for pref_data in preferences_data:
                if isinstance(pref_data, dict):
                    pref_name = pref_data.get('name', '')
                    routes_data = pref_data.get('routes', [])
                    routes = []
                    if isinstance(routes_data, list):
                        for route_data in routes_data:
                            if isinstance(route_data, dict):
                                routes.append(RouteConfig(
                                    provider=route_data.get('provider', ''),
                                    model=route_data.get('model', ''),
                                    preference=pref_name
                                ))
                                total_routes += 1
                    preferences.append(PreferenceConfig(
                        name=pref_name,
                        routes=tuple(routes)
                    ))
        
        return RouterConfig(
            providers=tuple(providers),
            preferences=tuple(preferences),
            total_routes=total_routes
        )
    
    def get_cached(self) -> Optional[RouterConfig]:
        """Get cached config data."""
        return self._cached_config
    
    def get_last_error(self) -> Optional[str]:
        """Get last error message."""
        return self._last_error
    
    def get_last_read_time(self) -> float:
        """Get timestamp of last successful read."""
        return self._last_read_time
    
    def force_reload(self) -> Optional[RouterConfig]:
        """Force reload ignoring mtime."""
        self._last_mtime = None
        return self.poll()
