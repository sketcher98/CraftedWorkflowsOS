"""
Worker Base Classes - Abstract base classes for all workers

Provides the worker framework with polling, error handling, and update emission.
"""

import threading
import time
from queue import Queue
from typing import Optional, TypeVar, Generic
from abc import ABC, abstractmethod
from threading import Event


T = TypeVar('T')


class BaseWorker(ABC, Generic[T]):
    """Abstract base worker that polls an adapter and emits typed updates."""
    
    def __init__(
        self,
        name: str,
        update_queue: Queue,
        shutdown_event: Event,
        interval: float = 1.0,
        timeout: float = 2.0
    ):
        self.name = name
        self.update_queue = update_queue
        self.shutdown_event = shutdown_event
        self.interval = interval
        self.timeout = timeout
        self._thread: Optional[threading.Thread] = None
        self._running = False
    
    def start(self) -> None:
        """Start the worker thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
    
    def stop(self, timeout: float = 3.0) -> None:
        """Stop the worker thread."""
        if not self._running:
            return
        self._running = False
        if self._thread:
            self._thread.join(timeout=timeout)
    
    def _run(self) -> None:
        """Main worker loop."""
        while self._running and not self.shutdown_event.is_set():
            start = time.time()
            
            try:
                update = self.poll()
                if update is not None:
                    self.update_queue.put(update)
            except Exception as e:
                # Put error update
                from routeops.runtime.messages import ErrorUpdate, DataFreshness
                from datetime import datetime
                self.update_queue.put(ErrorUpdate(
                    source=self.name,
                    timestamp=datetime.now(),
                    freshness=DataFreshness.ERROR,
                    error=str(e)
                ))
            
            # Sleep for remaining interval
            elapsed = time.time() - start
            sleep_time = max(0, self.interval - elapsed)
            if sleep_time > 0:
                time.sleep(sleep_time)
    
    @abstractmethod
    def poll(self) -> Optional[T]:
        """
        Poll the data source.
        Returns an update object or None.
        """
        pass


class WorkerManager:
    """Manages multiple workers."""
    
    def __init__(self, update_queue: Queue, shutdown_event: Event):
        self.update_queue = update_queue
        self.shutdown_event = shutdown_event
        self.workers: dict[str, BaseWorker] = {}
    
    def add_worker(self, worker: BaseWorker) -> None:
        """Add a worker to the manager."""
        self.workers[worker.name] = worker
    
    def start_all(self) -> None:
        """Start all workers."""
        for worker in self.workers.values():
            worker.start()
    
    def stop_all(self, timeout: float = 3.0) -> None:
        """Stop all workers."""
        for worker in self.workers.values():
            worker.stop(timeout=timeout)
    
    def get_worker(self, name: str):
        """Get a worker by name."""
        return self.workers.get(name)
