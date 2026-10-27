"""
Health Poll Adapter - Polls /health endpoint

Read-only GET against the production router's existing health endpoint.
Parses provider health from the response.
"""

import json
import urllib.request
import urllib.error
from datetime import datetime
from typing import List, Optional
from routeops.models.provider import ProviderHealth


class HealthPollAdapter:
    """
    Polls /health endpoint on the router.
    
    Features:
    - Hard timeout on requests
    - Safe handling of unavailable router, timeout, malformed JSON
    - Returns ProviderHealth objects
    - Does NOT interpret provider health as route health
    """
    
    def __init__(self, base_url: str, timeout: float = 2.0):
        self.base_url = base_url.rstrip('/')
        self.health_url = f"{self.base_url}/health"
        self.timeout = timeout
        self._cached_health: List[ProviderHealth] = []
        self._last_poll_time: float = 0.0
        self._last_error: Optional[str] = None
    
    def poll(self) -> List[ProviderHealth]:
        """
        Poll the health endpoint.
        Returns list of ProviderHealth objects.
        On error, returns cached health (or empty list).
        """
        import time
        
        try:
            req = urllib.request.Request(
                self.health_url,
                headers={'Accept': 'application/json'}
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                if response.status != 200:
                    self._last_error = f"HTTP {response.status}"
                    return self._cached_health
                
                data = response.read().decode('utf-8')
                health_list = self._parse_health(data)
                self._cached_health = health_list
                self._last_error = None
                self._last_poll_time = time.time()
                return health_list
                
        except urllib.error.URLError as e:
            self._last_error = f"URL error: {e.reason}"
            return self._cached_health
        except json.JSONDecodeError as e:
            self._last_error = f"JSON decode error: {e}"
            return self._cached_health
        except Exception as e:
            self._last_error = f"Unexpected error: {e}"
            return self._cached_health
    
    def _parse_health(self, json_data: str) -> List[ProviderHealth]:
        """Parse health JSON into ProviderHealth objects."""
        try:
            data = json.loads(json_data)
        except json.JSONDecodeError:
            return []
        
        health_list = []
        
        # Handle different possible response formats
        if isinstance(data, dict):
            # Format: {"provider_name": {"status": "...", ...}, ...}
            for provider_name, provider_data in data.items():
                if isinstance(provider_data, dict):
                    health = self._parse_provider_health(provider_name, provider_data)
                    health_list.append(health)
        elif isinstance(data, list):
            # Format: [{"provider": "...", "status": "..."}, ...]
            for item in data:
                if isinstance(item, dict):
                    provider_name = item.get('provider') or item.get('name') or 'unknown'
                    health = self._parse_provider_health(provider_name, item)
                    health_list.append(health)
        
        return health_list
    
    def _parse_provider_health(self, provider_name: str, data: dict) -> ProviderHealth:
        """Parse individual provider health data."""
        
        # Extract healthy status
        healthy = False
        if 'healthy' in data:
            healthy = bool(data['healthy'])
        elif 'status' in data:
            status_str = str(data.get('status', 'unknown')).lower()
            if status_str in ('healthy', 'ok'):
                healthy = True
        
        # Extract failures count
        failures = 0
        if 'failures' in data:
            try:
                failures = int(data['failures'])
            except (ValueError, TypeError):
                pass
        elif 'failure_count' in data:
            try:
                failures = int(data['failure_count'])
            except (ValueError, TypeError):
                pass
        
        # Extract hard_failed
        hard_failed = False
        if 'hard_failed' in data:
            hard_failed = bool(data['hard_failed'])
        elif 'hard_failure' in data:
            hard_failed = bool(data['hard_failure'])
        
        # Extract catalog_models
        catalog_models = 0
        if 'catalog_models' in data:
            try:
                catalog_models = int(data['catalog_models'])
            except (ValueError, TypeError):
                pass
        elif 'models' in data and isinstance(data['models'], list):
            catalog_models = len(data['models'])
        
        # Extract cooldown_until - handle both "cooldown" and "cooldown_until"
        cooldown_until = None
        if 'cooldown_until' in data:
            try:
                cooldown_until = datetime.fromisoformat(str(data['cooldown_until']).replace('Z', '+00:00'))
            except (ValueError, TypeError):
                pass
        elif 'cooldown' in data:
            try:
                cooldown_until = datetime.fromisoformat(str(data['cooldown']).replace('Z', '+00:00'))
            except (ValueError, TypeError):
                pass
        elif 'cooldown_expires' in data:
            try:
                cooldown_until = datetime.fromisoformat(str(data['cooldown_expires']).replace('Z', '+00:00'))
            except (ValueError, TypeError):
                pass
        
        return ProviderHealth(
            name=provider_name,
            healthy=healthy,
            failures=failures,
            hard_failed=hard_failed,
            catalog_models=catalog_models,
            cooldown_until=cooldown_until
        )
    
    def get_cached(self) -> List[ProviderHealth]:
        """Get cached health data."""
        return list(self._cached_health)
    
    def get_last_error(self) -> Optional[str]:
        """Get last error message."""
        return self._last_error
    
    def get_last_poll_time(self) -> float:
        """Get timestamp of last successful poll."""
        return self._last_poll_time
