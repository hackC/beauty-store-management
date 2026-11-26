from __future__ import annotations

import threading
import time
from typing import Callable, Protocol


class StopTarget(Protocol):
    def request_stop(self) -> None: ...


class Lifecycle:
    """Owns cooperative workers. Shutdown is idempotent and never kills a writer."""

    def __init__(self, on_error: Callable[[str], None]):
        self.stop_event = threading.Event()
        self._lock = threading.RLock()
        self._threads: dict[str, threading.Thread] = {}
        self._targets: list[StopTarget] = []
        self._on_error = on_error
        self.reason: str | None = None
        self.stop_started_at: float | None = None

    def add_target(self, target: StopTarget) -> None:
        with self._lock:
            self._targets.append(target)
            if self.stop_event.is_set():
                target.request_stop()

    def start(self, name: str, action: Callable[[], None]) -> threading.Thread | None:
        with self._lock:
            if self.stop_event.is_set():
                return None
            old = self._threads.get(name)
            if old and old.is_alive():
                return old

            def run():
                try:
                    action()
                except BaseException:
                    self._on_error(name)

            thread = threading.Thread(target=run, name=name, daemon=False)
            self._threads[name] = thread
            thread.start()
            return thread

    def request_stop(self, reason: str) -> bool:
        with self._lock:
            if self.stop_event.is_set():
                return False
            self.reason = reason
            self.stop_started_at = time.monotonic()
            self.stop_event.set()
            for target in self._targets:
                target.request_stop()
            return True

    def pending(self) -> list[str]:
        with self._lock:
            return [name for name, thread in self._threads.items() if thread.is_alive()]

    def join(self, timeout: float) -> list[str]:
        deadline = time.monotonic() + timeout
        with self._lock:
            threads = list(self._threads.values())
        for thread in threads:
            if thread is not threading.current_thread():
                thread.join(max(0, deadline - time.monotonic()))
        return self.pending()


class ServerStop:
    """Latches stop requests even before the concrete server has been created."""

    def __init__(self):
        self._lock = threading.RLock()
        self._server = None
        self.requested = threading.Event()

    def reset(self):
        with self._lock:
            self._server = None
            self.requested.clear()

    def attach(self, server):
        with self._lock:
            self._server = server
            if self.requested.is_set():
                server.should_exit = True

    def request_stop(self):
        with self._lock:
            self.requested.set()
            if self._server is not None:
                self._server.should_exit = True
