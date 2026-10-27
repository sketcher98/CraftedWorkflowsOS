"""
Worker Implementations - Concrete workers for each data source.
"""

import threading
import time
from queue import Queue
from threading import Event
from datetime import datetime
from typing import Optional, List

from routeops.runtime.workers import BaseWorker, WorkerManager
from routeops.adapters.jsonl_tail import JSONLTailAdapter
from routeops.adapters.health_poll import HealthPollAdapter
from routeops.adapters.models_poll import ModelsPollAdapter
from routeops.adapters.config_read import ConfigAdapter
from routeops.adapters.process_inspect import ProcessInspectAdapter
from routeops.reconstruction import RequestReconstructor
from routeops.runtime.messages import (
    HealthUpdate, ModelsUpdate, ConfigUpdate, ProcessUpdate,
    TelemetryUpdate, RequestStateUpdate, SchedulerTickUpdate,
    DataFreshness
)


class JSONLWorker(BaseWorker):
    """Worker for JSONL tail."""
    
    def __init__(self, update_queue: Queue, shutdown_event: Event, path: str, 
                 interval: float, initial_lines: int = 500):
        super().__init__("jsonl", update_queue, shutdown_event, interval)
        self.adapter = JSONLTailAdapter(path, max_buffer=1000, initial_lines=initial_lines)
        self.reconstructor = RequestReconstructor()
    
    def poll(self) -> Optional[TelemetryUpdate]:
        events = self.adapter.poll()
        if not events:
            return None
        
        # Get last completed request
        last_completed = self.reconstructor.get_last_completed_request(
            [e.parsed for e in self.adapter.get_all() if e.parsed]
        )
        
        return TelemetryUpdate(
            source="jsonl",
            timestamp=datetime.now(),
            freshness=DataFreshness.FRESH,
            events=events,
            last_completed_request=last_completed
        )


class HealthWorker(BaseWorker):
    """Worker for /health polling."""
    
    def __init__(self, update_queue: Queue, shutdown_event: Event, base_url: str,
                 interval: float, timeout: float = 2.0):
        super().__init__("health", update_queue, shutdown_event, interval)
        self.adapter = HealthPollAdapter(base_url, timeout=timeout)
    
    def poll(self) -> Optional[HealthUpdate]:
        providers = self.adapter.poll()
        if not providers and not self.adapter.get_cached():
            return None
        
        return HealthUpdate(
            source="health",
            timestamp=datetime.now(),
            freshness=DataFreshness.FRESH,
            providers=providers
        )


class ModelsWorker(BaseWorker):
    """Worker for /v1/models polling."""
    
    def __init__(self, update_queue: Queue, shutdown_event: Event, base_url: str,
                 interval: float, timeout: float = 5.0):
        super().__init__("models", update_queue, shutdown_event, interval)
        self.adapter = ModelsPollAdapter(base_url, timeout=timeout)
    
    def poll(self) -> Optional[ModelsUpdate]:
        models = self.adapter.poll()
        if not models and not self.adapter.get_cached():
            return None
        
        return ModelsUpdate(
            source="models",
            timestamp=datetime.now(),
            freshness=DataFreshness.FRESH,
            models=models
        )


class ConfigWorker(BaseWorker):
    """Worker for config.json polling."""
    
    def __init__(self, update_queue: Queue, shutdown_event: Event, path: str,
                 interval: float):
        super().__init__("config", update_queue, shutdown_event, interval)
        self.adapter = ConfigAdapter(path, interval=interval)
        self._first_poll = True
    
    def poll(self) -> Optional[ConfigUpdate]:
        # Force initial poll
        if self._first_poll:
            self._first_poll = False
            config = self.adapter.force_reload()
        else:
            config = self.adapter.poll()
        
        return ConfigUpdate(
            source="config",
            timestamp=datetime.now(),
            freshness=DataFreshness.FRESH,
            config=config
        )


class ProcessWorker(BaseWorker):
    """Worker for process inspection."""
    
    def __init__(self, update_queue: Queue, shutdown_event: Event, interval: float):
        super().__init__("process", update_queue, shutdown_event, interval)
        self.adapter = ProcessInspectAdapter(interval=interval)
    
    def poll(self) -> Optional[ProcessUpdate]:
        candidates = self.adapter.poll()
        if not candidates:
            return None
        
        return ProcessUpdate(
            source="process",
            timestamp=datetime.now(),
            freshness=DataFreshness.FRESH,
            candidates=candidates
        )


class SchedulerWorker(BaseWorker):
    """Worker for periodic scheduler tick."""
    
    def __init__(self, update_queue: Queue, shutdown_event: Event, interval: float = 1.0):
        super().__init__("scheduler", update_queue, shutdown_event, interval)
    
    def poll(self) -> Optional[SchedulerTickUpdate]:
        return SchedulerTickUpdate(
            source="scheduler",
            timestamp=datetime.now(),
            freshness=DataFreshness.FRESH
        )


def create_worker_manager(
    update_queue: Queue,
    shutdown_event: Event,
    jsonl_path: str,
    router_base_url: str,
    config_path: str,
    jsonl_interval: float = 1.5,
    health_interval: float = 2.5,
    models_interval: float = 30.0,
    config_interval: float = 60.0,
    process_interval: float = 10.0
) -> WorkerManager:
    """Create and configure worker manager with all workers."""
    manager = WorkerManager(update_queue, shutdown_event)
    
    # JSONL worker with initial lines for historical context
    manager.add_worker(JSONLWorker(
        update_queue, shutdown_event, jsonl_path, 
        jsonl_interval, initial_lines=500
    ))
    
    # Health worker
    manager.add_worker(HealthWorker(
        update_queue, shutdown_event, router_base_url,
        health_interval, timeout=2.0
    ))
    
    # Models worker
    manager.add_worker(ModelsWorker(
        update_queue, shutdown_event, router_base_url,
        models_interval, timeout=5.0
    ))
    
    # Config worker (immediate first poll)
    manager.add_worker(ConfigWorker(
        update_queue, shutdown_event, config_path,
        config_interval
    ))
    
    # Process worker
    manager.add_worker(ProcessWorker(
        update_queue, shutdown_event, process_interval
    ))
    
    # Scheduler worker (for periodic UI updates)
    manager.add_worker(SchedulerWorker(
        update_queue, shutdown_event, interval=1.0
    ))
    
    return manager
