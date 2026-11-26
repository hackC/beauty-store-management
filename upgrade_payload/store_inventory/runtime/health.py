from __future__ import annotations

from dataclasses import dataclass
import json
import ssl
import threading
import urllib.error
import urllib.request
from typing import Callable, Protocol


@dataclass(frozen=True)
class Endpoint:
    port: int
    https: bool
    version: str
    instance: str


@dataclass(frozen=True)
class HealthResult:
    endpoint: Endpoint
    live: bool
    ready: bool
    detail: str


class HealthProbe(Protocol):
    def check(self, endpoint: Endpoint) -> HealthResult: ...


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class LoopbackProbe:
    def __init__(self, timeout=2):
        self.timeout = timeout

    def check(self, endpoint):
        # Retains legacy local self-signed support; fixed IP, no proxies/redirects.
        # This context is private to the loopback probe, never business traffic.
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), NoRedirect(),
            urllib.request.HTTPSHandler(context=ssl._create_unverified_context()),
        )
        scheme = "https" if endpoint.https else "http"
        base = f"{scheme}://127.0.0.1:{endpoint.port}"
        live = False
        stage = "live"
        try:
            for stage in ("live", "ready"):
                request = urllib.request.Request(f"{base}/api/health/{stage}", headers={"Cache-Control": "no-cache"})
                with opener.open(request, timeout=self.timeout) as response:
                    body = response.read(8193)
                    if len(body) > 8192:
                        raise ValueError("health response exceeds limit")
                    value = json.loads(body)
                    if (response.status != 200 or not isinstance(value, dict)
                            or value.get("ok") is not True
                            or value.get("version") != endpoint.version
                            or value.get("instance_id") != endpoint.instance):
                        raise ValueError("health identity or status mismatch")
                if stage == "live":
                    live = True
            return HealthResult(endpoint, True, True, "ready")
        except urllib.error.HTTPError as exc:
            return HealthResult(endpoint, live, False, f"{stage}: HTTP {exc.code}")
        except Exception as exc:
            return HealthResult(endpoint, live, False, f"{stage}: {type(exc).__name__}: {exc}"[:240])


class HealthMonitor:
    def __init__(self, probe: HealthProbe, endpoint: Callable[[], Endpoint],
                 stop_event: threading.Event, on_change: Callable[[HealthResult], None]):
        self._probe, self._endpoint, self._stop, self._on_change = probe, endpoint, stop_event, on_change
        self._lock = threading.Lock()
        self._result = None

    @property
    def result(self):
        with self._lock:
            return self._result

    def run(self):
        previous = None
        while not self._stop.is_set():
            endpoint = self._endpoint()
            result = self._probe.check(endpoint)
            if self._stop.is_set():
                break
            if endpoint == self._endpoint():
                with self._lock:
                    self._result = result
                if result != previous:
                    self._on_change(result)
                    previous = result
            self._stop.wait(1.5)
