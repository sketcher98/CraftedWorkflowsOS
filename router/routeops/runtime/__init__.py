"""
Main Runtime - Event loop, wakeup, state integration.

This is the core runtime that coordinates workers, manages the update queue,
and applies updates to AppState on the main thread.
"""

import os
import sys
import select
import threading
import time
import signal
from queue import Queue, Empty
from typing import Optional, Callable, Any
from datetime import datetime

from routeops.runtime.workers import BaseWorker, WorkerManager
from routeops.runtime.worker_impl import create_worker_manager
from routeops.runtime.messages import (
    Update, HealthUpdate, ModelsUpdate, ConfigUpdate, ProcessUpdate,
    TelemetryUpdate, RequestStateUpdate, ErrorUpdate, DataFreshness
)
from routeops.adapters.jsonl_tail import JSONLTailAdapter
from routeops.adapters.health_poll import HealthPollAdapter
from routeops.adapters.models_poll import ModelsPollAdapter
from routeops.adapters.config_read import ConfigAdapter, RouterConfig
from routeops.adapters.process_inspect import ProcessInspectAdapter, ProcessCandidate
from routeops.reconstruction import RequestReconstructor, ReconstructedRequest
from routeops.state import AppState, ScreenType
from routeops.tui.bars import is_client_abort
from routeops.config import (
    ROUTER_EVENTS_JSONL, ROUTER_BASE_URL, ROUTER_CONFIG_JSON,
    POLL_INTERVALS, DEFAULT_REFRESH_INTERVAL
)


class RouteOpsRuntime:
    """
    Main RouteOps runtime coordinating all workers and state.
    
    Architecture:
    - Main thread: event loop, state mutations, TUI rendering
    - Worker threads: blocking I/O (HTTP, filesystem)
    - Wakeup pipe: thread-safe main thread notification
    - Update queue: typed messages from workers to main thread
    """

    def __init__(self):
        self.shutdown_event = threading.Event()
        self.update_queue: Queue[Any] = Queue()
        self.wakeup_r: int = -1
        self.wakeup_w: int = -1
        self.worker_manager: Optional[WorkerManager] = None
        self.app_state = AppState()
        self._running = False
        self._tick_callback: Optional[Callable] = None

    def initialize(self) -> None:
        """Initialize runtime components."""
        # Create wakeup pipe for thread-to-main notification
        self.wakeup_r, self.wakeup_w = os.pipe()
        # Make non-blocking
        os.set_blocking(self.wakeup_r, False)
        os.set_blocking(self.wakeup_w, False)

        # TUI start time BEFORE workers start (blocks historical JSONL events from counting)
        self.app_state._tui_start_time = datetime.now()

        # Create worker manager with all workers
        self.worker_manager = create_worker_manager(
            update_queue=self.update_queue,
            shutdown_event=self.shutdown_event,
            jsonl_path=ROUTER_EVENTS_JSONL,
            router_base_url=ROUTER_BASE_URL,
            config_path=ROUTER_CONFIG_JSON,
            jsonl_interval=POLL_INTERVALS.get("jsonl", 1.5),
            health_interval=POLL_INTERVALS.get("health", 2.5),
            models_interval=POLL_INTERVALS.get("models", 30.0),
            config_interval=POLL_INTERVALS.get("config", 60.0),
            process_interval=POLL_INTERVALS.get("process", 10.0)
        )

        # Register signal handlers
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def _signal_handler(self, signum, frame) -> None:
        """Handle shutdown signals."""
        self.shutdown()

    def _wakeup(self) -> None:
        """Wake up the main thread via pipe."""
        try:
            os.write(self.wakeup_w, b'x')
        except OSError:
            # Pipe full or closed - ignore
            pass

    def _drain_wakeup(self) -> None:
        """Drain available wakeup bytes."""
        try:
            while True:
                os.read(self.wakeup_r, 1024)
        except OSError:
            pass

    def start(self) -> None:
        """Start all workers (non-blocking)."""
        if not self.worker_manager:
            raise RuntimeError("Runtime not initialized")

        self.worker_manager.start_all()
        self._running = True
        # Sync router endpoint from config to app_state for display
        from routeops.config import ROUTER_HOST, ROUTER_PORT
        self.app_state.router_host = ROUTER_HOST
        self.app_state.router_port = ROUTER_PORT

    def _run_main_loop(self) -> None:
        """Main event loop - runs on main thread only."""
        stdin_fd = sys.stdin.fileno()

        try:
            while self._running and not self.shutdown_event.is_set():
                # Wait for stdin or wakeup pipe
                readable, _, _ = select.select(
                    [stdin_fd, self.wakeup_r], [], [], 0.1
                )

                # Handle stdin input
                if stdin_fd in readable:
                    self._handle_input()

                # Handle wakeup (worker updates available)
                if self.wakeup_r in readable:
                    self._drain_wakeup()
                    self._process_updates()

                # Periodic tick
                self._tick()

        finally:
            self._cleanup()

    def _handle_input(self) -> None:
        """Handle terminal input - forward to NavigationShell."""
        try:
            data = os.read(sys.stdin.fileno(), 1024)
            if data:
                # Parse input - simple single-char for now
                self._process_input_data(data)
        except OSError:
            pass

    def _process_input_data(self, data: bytes) -> None:
        """Process raw input data and forward to navigation shell."""
        # This will be connected to navigation shell later
        pass

    def _process_updates(self) -> None:
        """Process all pending updates from workers."""
        while True:
            try:
                update = self.update_queue.get_nowait()
                self._apply_update(update)
            except Empty:
                break

    def _apply_update(self, update: Any) -> None:
        """Apply a single update to AppState (main thread only)."""
        from routeops.runtime.messages import (
            ShutdownUpdate, ErrorUpdate, HealthUpdate, ModelsUpdate,
            ConfigUpdate, ProcessUpdate, TelemetryUpdate,
            RequestStateUpdate, SchedulerTickUpdate
        )

        if isinstance(update, ShutdownUpdate):
            self.shutdown()
            return

        if isinstance(update, ErrorUpdate):
            self.app_state.adapter_errors[update.worker] = update.error
            return

        if isinstance(update, HealthUpdate):
            self._apply_health_update(update)
            return

        if isinstance(update, ModelsUpdate):
            self._apply_models_update(update)
            return

        if isinstance(update, ConfigUpdate):
            self._apply_config_update(update)
            return

        if isinstance(update, ProcessUpdate):
            self._apply_process_update(update)
            return

        if isinstance(update, TelemetryUpdate):
            self._apply_telemetry_update(update)
            return

        if isinstance(update, RequestStateUpdate):
            self._apply_request_state_update(update)
            return

        if isinstance(update, SchedulerTickUpdate):
            self.app_state.last_refresh = datetime.now()
            return

    def _apply_health_update(self, update) -> None:
        """Apply health update to provider models."""
        from routeops.models.provider import ProviderModel, ProviderConfig

        for health in update.providers:
            # Get or create provider model
            if health.name not in self.app_state.providers:
                # Merge config from loaded config if available, else minimal
                cfg = None
                if self.app_state.config is not None:
                    for p in self.app_state.config.providers:
                        if p.name == health.name:
                            cfg = p
                            break
                if cfg is None:
                    cfg = ProviderConfig(
                        name=health.name,
                        base_url="",
                        key_env=""
                    )
                self.app_state.providers[health.name] = ProviderModel(
                    config=cfg,
                    health=health,
                    routes=()
                )
            else:
                # Update health on existing provider
                provider_model = self.app_state.providers[health.name]
                self.app_state.providers[health.name] = ProviderModel(
                    config=provider_model.config,
                    health=health,
                    routes=provider_model.routes
                )

            # Track freshness
            self.app_state._health_cache[health.name] = {
                'freshness': update.freshness.value,
                'timestamp': update.timestamp.isoformat(),
                'error': update.error
            }

        self.app_state.last_refresh = datetime.now()

    def _apply_models_update(self, update) -> None:
        """Apply models update to AppState."""
        self.app_state._models_cache = [m.model_id for m in update.models]
        self.app_state.last_refresh = datetime.now()

    def _apply_config_update(self, update) -> None:
        """Apply config update to AppState."""
        if update.config:
            self.app_state.config = update.config
            self.app_state._config_cache = update.config

            # Build config lookup once
            provider_configs = {p.name: p for p in update.config.providers}
            routes_by_provider = {}
            for pref in update.config.preferences:
                for route in pref.routes:
                    routes_by_provider.setdefault(route.provider, []).append(route)

            # Update existing providers AND create ones not yet seen by health worker
            all_names = set(self.app_state.providers.keys()) | set(provider_configs.keys())
            for provider_name in all_names:
                provider_config = provider_configs.get(provider_name)
                existing = self.app_state.providers.get(provider_name)

                if existing is not None:
                    new_config = provider_config if provider_config else existing.config
                    self.app_state.providers[provider_name] = existing.__class__(
                        config=new_config,
                        health=existing.health,
                        routes=tuple(routes_by_provider.get(provider_name, []))
                    )
                else:
                    # Config arrived before first health poll — create with placeholder health
                    from routeops.models.provider import ProviderModel, ProviderHealth
                    from datetime import datetime as _dt
                    self.app_state.providers[provider_name] = ProviderModel(
                        config=provider_config,
                        health=ProviderHealth(
                            name=provider_name,
                            healthy=True,
                            failures=0,
                            hard_failed=False,
                            catalog_models=0,
                            cooldown_until=None
                        ),
                        routes=tuple(routes_by_provider.get(provider_name, []))
                    )

        self.app_state.last_refresh = datetime.now()

    def _apply_process_update(self, update) -> None:
        """Apply process update to AppState."""
        # Live Hermes PIDs (evidence for the SYSTEM box — never a literal)
        self.app_state._hermes_pids = list(getattr(update, "hermes_pids", []) or [])

        production = None
        for candidate in update.candidates:
            if candidate.identity == "PRODUCTION":
                production = candidate
                break

        if production:
            # Convert ProcessCandidate to ProcessInfo for app_state
            from routeops.models.process import ProcessInfo
            self.app_state.process_info = ProcessInfo(
                pid=production.pid,
                exe=production.exe_path,
                cwd=production.cwd,
                uptime_seconds=production.uptime_seconds,
                identity=production.identity,
                binary_size=production.binary_size or 0
            )
            self.app_state._process_cache = self.app_state.process_info
        elif update.candidates:
            # No production found but some process exists
            candidate = update.candidates[0]
            from routeops.models.process import ProcessInfo
            self.app_state.process_info = ProcessInfo(
                pid=candidate.pid,
                exe=candidate.exe_path,
                cwd=candidate.cwd,
                uptime_seconds=candidate.uptime_seconds,
                identity=candidate.identity,
                binary_size=candidate.binary_size or 0
            )
            self.app_state._process_cache = self.app_state.process_info

        # Router process start time now known — recompute model health with new baseline
        self._recompute_model_health()

        self.app_state.last_refresh = datetime.now()

    def _apply_telemetry_update(self, update) -> None:
        """Apply telemetry events to AppState."""
        from routeops.models.request import EventModel
        from datetime import datetime

        # Convert JSONLEvent to EventModel and add to recent events
        for event in update.events:
            if event.parsed:
                # EventModel.from_json is the single parser — it maps every
                # field the router emits (including key_env). The previous
                # hand-rolled constructor silently dropped key_env, which
                # made every per-key screen report zero observations.
                ev = EventModel.from_json(event.parsed)
                self.app_state.add_event(ev)

        # Recompute per-model health from the JSONL-derived events
        self._recompute_model_health()

        # Update last completed request
        if update.last_completed_request:
            self.app_state.last_request = update.last_completed_request

        self.app_state.last_refresh = datetime.now()

    def _recompute_model_health(self) -> None:
        """Derive per (provider, model) health from recent route_result events.
        Only counts events since router process start (not TUI start) so health
        reflects full process lifetime even if TUI restarts.

        Outcomes are classified three ways (F1/F2):
          success      -> ok
          client abort -> abort (NEUTRAL - the CLIENT hung up, not the provider)
          anything else-> fail (the provider's fault)

        A client abort must NOT count against a provider: Hermes cancelling a
        slow stream says nothing about provider quality, and charging it as a
        failure made a healthy provider render 0% after one disconnect.
        """
        # Use router process start time as baseline (survives TUI restarts)
        router_start = None
        if self.app_state.process_info and self.app_state.process_info.uptime_seconds > 0:
            from datetime import datetime, timedelta
            router_start = datetime.now().astimezone() - timedelta(seconds=self.app_state.process_info.uptime_seconds)
        
        # Fallback to TUI start time if router start unavailable
        tui_start = self.app_state._tui_start_time
        if tui_start is not None and tui_start.tzinfo is None:
            tui_start = tui_start.astimezone()
        
        # Use whichever is earlier (router start if available, else TUI start)
        baseline = router_start if router_start else tui_start
        
        stats: Dict[tuple, Dict[str, Any]] = {}
        for ev in self.app_state.recent_events:
            if ev.event_type != 'route_result' or not ev.provider or not ev.model:
                continue
            # Skip events that occurred before baseline
            if baseline and ev.timestamp and ev.timestamp < baseline:
                continue
            key = (ev.provider, ev.model)
            bucket = stats.setdefault(key, {"ok": 0, "fail": 0, "abort": 0, "last_ts": None, "last_outcome": None})
            bucket["abort" if is_client_abort(ev) else ("ok" if ev.outcome == 'success' else "fail")] += 1
            if ev.timestamp and (bucket["last_ts"] is None or ev.timestamp > bucket["last_ts"]):
                bucket["last_ts"] = ev.timestamp
                bucket["last_outcome"] = ev.outcome
        self.app_state._model_health = stats

    def _apply_request_state_update(self, update) -> None:
        """Apply reconstructed request state."""
        if update.last_completed_request:
            self.app_state.last_request = update.last_completed_request

        self.app_state.last_refresh = datetime.now()

    def _tick(self) -> None:
        """Periodic tick callback."""
        if self._tick_callback:
            self._tick_callback()

    def set_tick_callback(self, callback: Callable) -> None:
        """Set periodic tick callback."""
        self._tick_callback = callback

    def shutdown(self) -> None:
        """Initiate graceful shutdown."""
        if not self._running:
            return

        self._running = False
        self.shutdown_event.set()
        self._wakeup()  # Wake main loop

        if self.worker_manager:
            self.worker_manager.stop_all(timeout=3.0)

    def _cleanup(self) -> None:
        """Cleanup resources."""
        # Close wakeup pipe
        for fd in (self.wakeup_r, self.wakeup_w):
            if fd >= 0:
                try:
                    os.close(fd)
                except OSError:
                    pass


def create_runtime() -> RouteOpsRuntime:
    """Factory function to create and initialize runtime."""
    runtime = RouteOpsRuntime()
    runtime.initialize()
    return runtime
