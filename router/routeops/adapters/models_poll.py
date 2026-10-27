"""
Models Poll Adapter - Polls /v1/models endpoint

Read-only GET against the production router's existing models endpoint.
Parses logical model IDs from the catalog.
"""

import json
import urllib.request
import urllib.error
from typing import List, Optional, Set
from routeops.models.provider import ModelAvailability


class ModelsPollAdapter:
    """
    Polls /v1/models endpoint on the router.
    
    Features:
    - Hard timeout on requests
    - Safe handling of unavailable router, timeout, malformed JSON
    - Returns ModelAvailability objects
    - Does NOT interpret model count as configured route count
    - Does NOT infer provider health
    """
    
    def __init__(self, base_url: str, timeout: float = 5.0):
        self.base_url = base_url.rstrip('/')
        self.models_url = f"{self.base_url}/v1/models"
        self.timeout = timeout
        self._cached_models: List[ModelAvailability] = []
        self._last_poll_time: float = 0.0
        self._last_error: Optional[str] = None
    
    def poll(self) -> List[ModelAvailability]:
        """
        Poll the models endpoint.
        Returns list of ModelAvailability objects.
        On error, returns cached models (or empty list).
        """
        import time
        
        try:
            req = urllib.request.Request(
                self.models_url,
                headers={'Accept': 'application/json'}
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                if response.status != 200:
                    self._last_error = f"HTTP {response.status}"
                    return self._cached_models
                
                data = response.read().decode('utf-8')
                models_list = self._parse_models(data)
                self._cached_models = models_list
                self._last_error = None
                self._last_poll_time = time.time()
                return models_list
                
        except urllib.error.URLError as e:
            self._last_error = f"URL error: {e.reason}"
            return self._cached_models
        except json.JSONDecodeError as e:
            self._last_error = f"JSON decode error: {e}"
            return self._cached_models
        except Exception as e:
            self._last_error = f"Unexpected error: {e}"
            return self._cached_models
    
    def _parse_models(self, json_data: str) -> List[ModelAvailability]:
        """Parse models JSON into ModelAvailability objects."""
        try:
            data = json.loads(json_data)
        except json.JSONDecodeError:
            return []
        
        model_ids: Set[str] = set()
        
        # Handle OpenAI-compatible format: {"data": [{"id": "model-name"}, ...]}
        if isinstance(data, dict) and 'data' in data:
            items = data['data']
            if isinstance(items, list):
                for item in items:
                    if isinstance(item, dict) and 'id' in item:
                        model_ids.add(str(item['id']))
        
        # Handle simple list format: [{"id": "model-name"}, ...] or ["model-name", ...]
        elif isinstance(data, list):
            for item in data:
                if isinstance(item, dict) and 'id' in item:
                    model_ids.add(str(item['id']))
                elif isinstance(item, str):
                    model_ids.add(item)
        
        # Handle dict format: {"model-name": {...}, ...}
        elif isinstance(data, dict):
            for key in data.keys():
                model_ids.add(str(key))
        
        # Convert to ModelAvailability objects (catalog-only, no provider info)
        models = []
        for model_id in sorted(model_ids):
            models.append(ModelAvailability(
                model_id=model_id,
                available_in_catalog=True,
                providers_offering=()  # Empty - will be populated from config separately
            ))
        
        return models
    
    def get_cached(self) -> List[ModelAvailability]:
        """Get cached models data."""
        return list(self._cached_models)
    
    def get_last_error(self) -> Optional[str]:
        """Get last error message."""
        return self._last_error
    
    def get_last_poll_time(self) -> float:
        """Get timestamp of last successful poll."""
        return self._last_poll_time
