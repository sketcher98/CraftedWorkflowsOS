"""
Application State Management

Global application state and navigation.
"""

from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from datetime import datetime
from enum import Enum
from routeops.models.provider import ProviderHealth, ProviderModel
from routeops.models.request import RequestModel, EventModel
from routeops.models.route import RouteConfig
from routeops.models.process import ProcessInfo, ProcessIdentity


class ScreenType(str, Enum):
    """Screen type enumeration."""
    OVERVIEW = "overview"
    PROVIDERS = "providers"
    ROUTES = "routes"
    HISTORY = "history"
    DIAGNOSTICS = "diagnostics"
    SETTINGS = "settings"
    API_KEYS = "api_keys"
    PERKEY = "perkey"
    KEYMGMT = "keymgmt"


class NavigationStack:
    """Simple navigation stack for screen history."""
    def __init__(self):
        self._stack = [ScreenType.OVERVIEW]
    
    @property
    def current(self) -> ScreenType:
        return self._stack[-1]
    
    def push(self, screen: ScreenType) -> None:
        self._stack.append(screen)
    
    def pop(self) -> ScreenType:
        if len(self._stack) > 1:
            return self._stack.pop()
        return self._stack[-1]


@dataclass
class SettingsModel:
    """In-memory settings for current TUI session only."""
    refresh_interval: int = 2
    auto_refresh: bool = True
    event_density: str = "normal"  # compact, normal, verbose
    show_disclaimers: bool = True
    # Monitoring
    event_retention: int = 500
    parallel_workers: int = 5
    # Display
    theme: str = "boxed"
    color_scheme: str = "auto"
    compact_mode: bool = False
    time_format: str = "24h"
    show_keys_column: bool = True
    # Thresholds
    health_timeout: float = 5.0
    models_timeout: float = 10.0
    jsonl_stall_alert: int = 30
    cooldown_warning_pct: int = 60
    failure_rate_alert: int = 10
    latency_p99_alert: float = 5.0
    # Advanced
    config_file: str = "~/CraftedWorkflowsOS/router/config.json"
    telemetry_file: str = "~/CraftedWorkflowsOS/router/router-events.jsonl"
    router_binary: str = "~/CraftedWorkflowsOS/router/router"
    router_base_url: str = "http://127.0.0.1:8643"
    persist_settings: bool = True
    auto_save: bool = True

    def to_dict(self) -> dict:
        return {
            "refresh_interval": self.refresh_interval,
            "auto_refresh": self.auto_refresh,
            "event_density": self.event_density,
            "show_disclaimers": self.show_disclaimers,
            "event_retention": self.event_retention,
            "parallel_workers": self.parallel_workers,
            "theme": self.theme,
            "color_scheme": self.color_scheme,
            "compact_mode": self.compact_mode,
            "time_format": self.time_format,
            "show_keys_column": self.show_keys_column,
            "health_timeout": self.health_timeout,
            "models_timeout": self.models_timeout,
            "jsonl_stall_alert": self.jsonl_stall_alert,
            "cooldown_warning_pct": self.cooldown_warning_pct,
            "failure_rate_alert": self.failure_rate_alert,
            "latency_p99_alert": self.latency_p99_alert,
            "config_file": self.config_file,
            "telemetry_file": self.telemetry_file,
            "router_binary": self.router_binary,
            "router_base_url": self.router_base_url,
            "persist_settings": self.persist_settings,
            "auto_save": self.auto_save,
        }


@dataclass
class AppState:
    # runtime back-reference (set by __main__ after create_runtime; workers
    # read their interval each loop so live edits take effect immediately)
    runtime = None
    """Global application state - single source of truth."""
    
    # Data models
    router: Optional['RouterModel'] = None
    providers: Dict[str, ProviderModel] = field(default_factory=dict)
    last_request: Optional['ReconstructedRequest'] = None
    recent_events: List[EventModel] = field(default_factory=list)
    config: Optional['RouterConfig'] = None
    process_info: Optional[ProcessInfo] = None
    
    # UI state
    current_screen: 'ScreenType' = field(default_factory=lambda: ScreenType.OVERVIEW)
    navigation: 'NavigationStack' = field(default_factory=lambda: NavigationStack())
    paused: bool = False
    selected_index: int = 0
    detail_target: Optional[Any] = None
    
    # Settings
    settings: SettingsModel = field(default_factory=SettingsModel)
    
    # Runtime
    last_refresh: datetime = field(default_factory=datetime.now)
    adapter_errors: Dict[str, str] = field(default_factory=dict)

    # Live Hermes PIDs observed by the process worker (empty = not observed)
    _hermes_pids: List[int] = field(default_factory=list)
    # Router endpoint actually in use (host, port) — from config.json
    router_host: str = "127.0.0.1"
    router_port: int = 8643

    # Adapter data caches
    _health_cache: Dict[str, Any] = field(default_factory=dict)
    _models_cache: List[str] = field(default_factory=list)
    _config_cache: Optional['RouterConfig'] = None
    _process_cache: Optional[ProcessInfo] = None
    # Per-model health derived from JSONL: {(provider, model): {"ok": n, "fail": n, "last_ts": dt}}
    _model_health: Dict[tuple, Dict[str, Any]] = field(default_factory=dict)

    # TUI start time for filtering stale telemetry
    _tui_start_time: Optional[datetime] = None

    def get_provider(self, name: str) -> Optional[ProviderModel]:
        return self.providers.get(name)
    
    def get_all_providers(self) -> List[ProviderModel]:
        return list(self.providers.values())
    
    def get_healthy_providers(self) -> List[ProviderModel]:
        return [p for p in self.providers.values() if p.health.healthy]
    
    def get_cooldown_providers(self) -> List[ProviderModel]:
        return [p for p in self.providers.values() if not p.health.healthy and not p.health.hard_failed]
    
    def get_hard_failed_providers(self) -> List[ProviderModel]:
        return [p for p in self.providers.values() if p.health.hard_failed]
    
    def get_recent_events(self, limit: int = 100) -> List[EventModel]:
        return self.recent_events[-limit:]
    
    def add_event(self, event: EventModel) -> None:
        self.recent_events.append(event)
        # Keep only last 500 events
        if len(self.recent_events) > 500:
            self.recent_events = self.recent_events[-500:]
    
    def set_last_request(self, request: 'ReconstructedRequest') -> None:
        self.last_request = request
    
    def get_display_settings(self) -> dict:
        return {
            "refresh_interval": self.settings.refresh_interval,
            "auto_refresh": self.settings.auto_refresh,
            "event_density": self.settings.event_density,
            "show_disclaimers": self.settings.show_disclaimers,
        }


# Forward references
from routeops.models.router import RouterModel
from routeops.models.provider import ProviderModel
from routeops.models.request import RequestModel, EventModel
from routeops.models.route import RouterConfig
from routeops.models.process import ProcessInfo
from typing import Any, Optional, Dict, List
from datetime import datetime
