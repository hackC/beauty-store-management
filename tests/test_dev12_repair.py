from __future__ import annotations

import json
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from store_inventory.runtime.health import Endpoint, LoopbackProbe
from store_inventory.runtime.lifecycle import Lifecycle, ServerStop
from store_inventory.runtime import network


def test_shutdown_wakes_workers_and_refuses_new_work():
    entered = threading.Event()
    completed = threading.Event()
    lifecycle = Lifecycle(lambda name: pytest.fail(name))
    def work():
        entered.set()
        lifecycle.stop_event.wait(60)
        completed.set()
    worker = lifecycle.start("owned", work)
    assert entered.wait(2)
    assert worker.daemon is False
    assert lifecycle.start("owned", work) is worker
    assert lifecycle.request_stop("window_close") is True
    assert lifecycle.request_stop("again") is False
    assert lifecycle.start("late", lambda: None) is None
    assert lifecycle.join(2) == []
    assert completed.is_set()


def test_close_before_uvicorn_exists_is_latched():
    stop = ServerStop()
    stop.request_stop()
    server = SimpleNamespace(should_exit=False)
    stop.attach(server)
    assert server.should_exit is True


def test_lan_address_fallback_does_not_resolve_dns():
    with patch.object(socket.socket, "connect", side_effect=OSError("offline")), patch.object(socket, "getaddrinfo", side_effect=AssertionError("DNS must not run")), patch.object(network, "interface_addresses", return_value=["127.0.0.1", "192.168.50.8"]):
        assert network.discover_lan_ip() == "192.168.50.8"


@pytest.mark.parametrize("mode,live,ready", [("ok", True, True), ("wrong_instance", False, False), ("redirect", False, False), ("db_down", True, False), ("oversize", False, False)])
def test_loopback_probe_direct_identity_and_error_modes(mode, live, ready):
    visited = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def do_GET(self):
            visited.append(self.path)
            status = 200
            body = json.dumps({"ok": True, "version": "test", "instance_id": "different" if mode == "wrong_instance" else "this-instance"}).encode()
            if mode == "redirect": status = 302
            if mode == "db_down" and self.path.endswith("ready"): status = 503
            if mode == "oversize": body = b"x" * 9000
            self.send_response(status)
            if status == 302: self.send_header("Location", "/should-not-follow")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers(); self.wfile.write(body)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever)
    worker.start()
    try:
        # Auto proxy discovery would fail if the production probe tried to use it.
        with patch("urllib.request.getproxies", side_effect=AssertionError("must bypass proxy discovery")):
            result = LoopbackProbe().check(Endpoint(server.server_port, False, "test", "this-instance"))
        assert (result.live, result.ready) == (live, ready)
        assert "/should-not-follow" not in visited
    finally:
        server.shutdown(); server.server_close(); worker.join(2)


@pytest.mark.parametrize("case", ["http_close", "https_close", "slow_write", "locked_inventory_backup", "stalled_request", "startup_failure", "close_during_start", "restart_then_close", "websocket_close", "tk_close", "auth_http", "auth_https"])
def test_real_application_resource_lifecycle(case):
    root = Path(__file__).resolve().parents[1]
    timeout = 90 if case in {"auth_http", "auth_https"} else 35
    result = subprocess.run(
        [sys.executable, "-X", "utf8", str(root/"tests/runtime_subprocess_case.py"), str(root), case],
        encoding="utf-8", capture_output=True, timeout=timeout,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert '"success": true' in result.stdout, result.stdout


def test_layout_observer_terminates_after_a_dynamic_table_update():
    import shutil
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js unavailable")
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([node, str(root/"tests/dev12_layout_case.cjs"), str(root/"static/dev12_ui.js")], capture_output=True, text=True, timeout=8)
    assert result.returncode == 0, result.stderr



def test_window_close_forces_process_exit_if_listener_is_blocked(tmp_path):
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "-X", "utf8", str(root/"tests/runtime_subprocess_case.py"), str(root), "tk_force_close", str(tmp_path)],
        encoding="utf-8", capture_output=True, timeout=35,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    port = json.loads(result.stdout.strip())["blocked_listener_port"]
    log = (tmp_path/"logs/server.log").read_text(encoding="utf-8")
    assert "Shutdown deadline exceeded; forcing process exit: reason=window_close" in log
    forced_line = next(line for line in log.splitlines() if "forcing process exit" in line)
    assert "InventoryServer" in forced_line.split("pending=", 1)[1]
    assert "Application resources released" not in log
    stacks = (tmp_path/"logs/shutdown-stacks.log").read_text(encoding="utf-8")
    assert "InventoryServer:" in stacks and "in blocked" in stacks
    with socket.socket() as rebound:
        rebound.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        rebound.bind(("0.0.0.0", port))
        rebound.listen()
    with sqlite3.connect(tmp_path/"data/inventory.db") as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert connection.execute("SELECT COUNT(*) FROM close_rollback").fetchone() == (0,)
    connection.close()
