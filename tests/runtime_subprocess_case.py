"""Independent process, fresh temporary database, real listener and optional Tk."""
from pathlib import Path
import asyncio
import json
import shutil
import socket
import sqlite3
import ssl
import sys
import tempfile
import threading
import time
import urllib.request


source, case = Path(sys.argv[1]), sys.argv[2]
from contextlib import nullcontext

# The parent owns this directory for the forced-exit case because os._exit
# bypasses TemporaryDirectory cleanup.
test_dir = nullcontext(sys.argv[3]) if len(sys.argv) > 3 else tempfile.TemporaryDirectory(prefix="inventory-hotfix-test-")
with test_dir as temp:
    root = Path(temp)
    for path in source.glob("*.py"):
        shutil.copy2(path, root/path.name)
    shutil.copytree(source/"store_inventory", root/"store_inventory")
    shutil.copytree(source/"static", root/"static")
    sys.path.insert(0, str(root))
    import main
    main.CONFIG.update(auto_backup=False, https=case in {"https_close", "auth_https"})
    main.init_db()
    if case == "stalled_request":
        main.REQUEST_DRAIN_SECONDS = 1
    if case == "tk_force_close":
        main.REQUEST_DRAIN_SECONDS = 1
        main.APPLICATION_SHUTDOWN_SECONDS = 2
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    main.CONFIG["port"] = port
    scheme = "https" if main.CONFIG["https"] else "http"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=ssl._create_unverified_context()))

    def read(path):
        with opener.open(f"{scheme}://127.0.0.1:{port}{path}", timeout=5) as response:
            return json.load(response)

    def wait_started():
        deadline = time.monotonic()+8
        while time.monotonic() < deadline:
            if main.server_obj and main.server_obj.started:
                return
            if main.server_error:
                raise AssertionError(main.server_error)
            time.sleep(0.02)
        raise AssertionError("listener did not start")

    main.start_background_tasks()
    held = None
    if case == "startup_failure":
        held = socket.socket()
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            held.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        held.bind(("0.0.0.0", port))
        held.listen()
    if case == "close_during_start":
        entered, proceed = threading.Event(), threading.Event()
        original_run = main.run_server
        def delayed_start():
            entered.set(); assert proceed.wait(5); original_run()
        main.run_server = delayed_start
    main.start_server_thread()

    if case == "close_during_start":
        assert entered.wait(2)
        main.request_application_shutdown("close_before_listener")
        proceed.set()
    elif case == "startup_failure":
        main.server_thread.join(5)
        startup_failed = not main.server_thread.is_alive() and main.server_error
        main.request_application_shutdown("failed_start_close")
        held.close()
        assert startup_failed
    else:
        wait_started()
        assert read("/api/health/live")["version"] == main.APP_VERSION
        assert read("/api/health/ready")["ok"] is True
        if case in {"auth_http", "auth_https"}:
            import httpx
            # Independent cookie jars simulate host and mobile sessions. Flip the
            # saved setting to exercise a listener waiting for a settings restart.
            main.CONFIG["https"] = scheme == "http"
            with httpx.Client(base_url=f"{scheme}://127.0.0.1:{port}", verify=False, trust_env=False, timeout=5) as admin, httpx.Client(base_url=f"{scheme}://127.0.0.1:{port}", verify=False, trust_env=False, timeout=5) as phone:
                response = admin.post("/api/auth/bootstrap", json={"username":"testadmin","display_name":"管理员","password":"TestPassword123"})
                assert response.status_code == 200, response.text
                assert ("; Secure" in response.headers["set-cookie"]) == (scheme == "https")
                assert admin.get("/api/auth/status").json()["authenticated"]
                default_path = "/api/users/activation-default"
                assert admin.put(default_path, json={"activation_code": "123"}).status_code == 400
                assert admin.put(default_path, json={"activation_code": "01234567"}).json() == {"configured": True}
                assert admin.get(default_path).json() == {"configured": True}
                response = admin.post("/api/users", json={
                    "username":"staff01", "display_name":"员工", "use_default_activation_code":True,
                })
                assert response.is_success, response.text
                assert response.json()["used_default_activation_code"]
                assert "activation_code" not in response.json()
                assert admin.put(default_path, json={"activation_code": None}).json() == {"configured": False}
                assert not admin.post("/api/users", json={
                    "username":"nodefault", "display_name":"员工", "use_default_activation_code":True,
                }).is_success
                custom = admin.post("/api/users", json={
                    "username":"customstaff", "display_name":"员工", "activation_code":"09876543",
                })
                assert custom.json()["activation_code"] == "09876543"
                reset = admin.post(f'/api/users/{custom.json()["id"]}/activation-code', json={"activation_code":"00012345"})
                assert reset.json()["activation_code"] == "00012345"
                for invalid in ("09876543", "00000000"):
                    assert phone.post("/api/auth/login", json={"username":"customstaff","password":invalid}).status_code == 401
                first = phone.post("/api/auth/login", json={"username":"staff01","password":"01234567"})
                assert first.json()["must_set_password"] is True
                assert phone.get("/api/auth/status").json()["user"]["must_set_password"] is True
                assert phone.get("/api/products").status_code == 403
                assert admin.post("/api/auth/login", json={"username":"staff01","password":"01234567"}).status_code == 401
                response = phone.post("/api/auth/set-first-password", json={"password":"StaffPassword123"})
                assert response.is_success, response.text
                assert phone.get("/api/auth/status").json()["user"]["must_set_password"] is False
                assert phone.post("/api/auth/logout").is_success
                assert phone.post("/api/auth/login", json={"username":"staff01","password":"StaffPassword123"}).is_success
                assert phone.get("/api/auth/status").json()["authenticated"]
                assert phone.put(default_path, json={"activation_code":"11111111"}).status_code == 403
                assert admin.get("/").headers["cache-control"] == "no-store"
            main.request_application_shutdown("test_auth")
        elif case == "locked_inventory_backup":
            import httpx
            from store_inventory.platform import backup_archive
            import os

            backup_archive.SQLITE_SNAPSHOT_TIMEOUT = 1.5
            entered = threading.Event()
            original_guard = main.INVENTORY_SERVICE.backup_guard

            def guard():
                con = main.db()
                try:
                    con.execute("BEGIN EXCLUSIVE")
                    entered.set()
                    original_guard()
                finally:
                    con.rollback()
                    con.close()

            main.INVENTORY_SERVICE.backup_guard = guard
            host = f"{scheme}://127.0.0.1:{port}"
            with httpx.Client(base_url=host, trust_env=False) as admin:
                assert admin.post("/api/auth/bootstrap", json={
                    "username":"testadmin", "display_name":"管理员", "password":"TestPassword123",
                }).is_success
                fixture = main.db()
                try:
                    fixture.execute("""
                        INSERT INTO products (id,name,brand,spec,unit,category,location,opening,threshold,created_at,updated_at)
                        VALUES ('LOCK-ITEM','库存测试','','','件','','',0,5,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)
                    """)
                    fixture.commit()
                finally:
                    fixture.close()

                replies = []
                def submit_write():
                    try:
                        replies.append(admin.post("/api/submit", json={
                            "type":"入库", "request_id":"lock-regression",
                            "items":[{"product_id":"LOCK-ITEM","quantity":1}],
                        }, timeout=8))
                    except httpx.HTTPError as exc:
                        replies.append(exc)

                client = threading.Thread(target=submit_write, daemon=True)
                client.start()
                assert entered.wait(3), replies
                try:
                    with opener.open(f"{host}/api/health/live", timeout=0.8) as response:
                        assert response.status == 200
                except TimeoutError:
                    # The baseline comparison intentionally runs the old broken
                    # process and exits once listener starvation is observed.
                    if os.environ.get("INVENTORY_EXPECT_BASELINE_STALL") == "1":
                        print("Baseline blocked /api/health/live during an inventory backup", flush=True)
                        os._exit(0)
                    raise
                assert not replies, "health must respond while the backup is still waiting"
                client.join(5)
                assert len(replies) == 1 and replies[0].status_code == 503, replies
                assert "SQLite 快照等待超时" in replies[0].json()["detail"]
                assert not list((main.DATA_DIR/"backups").glob("*.db"))
                assert admin.get("/api/transactions").json() == []
            main.request_application_shutdown("test_locked_inventory_backup")
        elif case == "stalled_request":
            entered, cancelled = threading.Event(), threading.Event()

            @main.app.get("/test/stalled")
            async def stalled():
                entered.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.set()

            def request():
                try:
                    read("/test/stalled")
                except (OSError, ValueError):
                    pass

            client = threading.Thread(target=request, daemon=True)
            client.start()
            assert entered.wait(3)
            main.request_application_shutdown("test_stalled_request")
            assert cancelled.wait(4)
            client.join(3)
        elif case == "slow_write":
            started, allow_finish, finished = threading.Event(), threading.Event(), threading.Event()
            @main.app.get("/test/slow-write")
            def slow_write():
                con = main.db()
                try:
                    con.execute("CREATE TABLE IF NOT EXISTS close_test(value TEXT)")
                    con.execute("INSERT INTO close_test VALUES ('committed')")
                    started.set()
                    assert allow_finish.wait(8)
                    con.commit()
                    return {"ok": True}
                finally:
                    con.close(); finished.set()
            failures=[]
            def request():
                try: assert read("/test/slow-write")["ok"]
                except BaseException as exc: failures.append(str(exc))
            client=threading.Thread(target=request);client.start()
            assert started.wait(3)
            main.request_application_shutdown("close_with_write")
            assert main.finish_application_shutdown() is False
            allow_finish.set();client.join(5)
            assert finished.is_set() and not failures
        elif case == "restart_then_close":
            main.SERVER_STOP.request_stop(); main.server_thread.join(5)
            assert not main.server_thread.is_alive()
            main.start_server_thread();wait_started()
            assert read("/api/health/ready")["ok"] is True
            main.request_application_shutdown("close_after_restart")
        elif case == "websocket_close":
            # Use a dedicated test route with the real WS manager, without altering
            # production authentication or requiring a production user account.
            from fastapi import WebSocket
            @main.app.websocket("/test/ws")
            async def ws_endpoint(ws: WebSocket):
                await main.wsmanager.connect(ws)
                try:
                    await ws.receive_text()
                except Exception:
                    pass
                finally:
                    main.wsmanager.disconnect(ws)
            import websockets
            async def exercise():
                async with websockets.connect(f"ws://127.0.0.1:{port}/test/ws") as ws:
                    main.request_application_shutdown("close_with_websocket")
                    try:
                        await asyncio.wait_for(ws.recv(), 5)
                    except websockets.exceptions.ConnectionClosed:
                        return
                    raise AssertionError("WebSocket was not closed")
            asyncio.run(exercise())
        elif case in {"tk_close", "tk_force_close"}:
            if case == "tk_force_close":
                entered = threading.Event()
                with main.db() as setup:
                    setup.execute("CREATE TABLE close_rollback(value TEXT)")
                setup.close()

                @main.app.get("/test/blocked")
                async def blocked():
                    # Leave an uncommitted write open while blocking the server loop.
                    con = main.db()
                    try:
                        con.execute("INSERT INTO close_rollback VALUES ('uncommitted')")
                        entered.set()
                        threading.Event().wait(60)
                    finally:
                        con.close()

                def request():
                    try:
                        read("/test/blocked")
                    except (OSError, ValueError):
                        pass

                threading.Thread(target=request, daemon=True).start()
                assert entered.wait(3)
                print(json.dumps({"blocked_listener_port": port}), flush=True)

            # Execute the real Manager wiring/callbacks through a headless widget
            # adapter. A native Windows/Tk visual test is still required.
            from unittest.mock import patch
            from types import SimpleNamespace
            class Widget:
                def __init__(self, *a, **k): pass
                def pack(self, *a, **k): pass
                def winfo_children(self): return []
                def state(self, *a): pass
                def configure(self, **kwargs): pass
            class Variable:
                def __init__(self, value=""): self.value=value
                def set(self, value): self.value=value
            class Window(Widget):
                def __init__(self):
                    self.jobs={};self.protocols={};self.next=0;self.destroyed=False
                def title(self, *a): pass
                def geometry(self, *a): pass
                def minsize(self, *a): pass
                def protocol(self, name, callback=None):
                    if callback is not None: self.protocols[name]=callback
                    return self.protocols[name]
                def after(self, delay, callback):
                    self.next+=1;self.jobs[self.next]=callback;return self.next
                def after_cancel(self, token): self.jobs.pop(token,None)
                def destroy(self): self.destroyed=True;self.jobs.clear()
                def update(self):
                    jobs=list(self.jobs.items());self.jobs.clear()
                    for _, callback in jobs:
                        if not self.destroyed: callback()
            window=Window()
            with patch.object(main, "configure_console"), patch.object(main.tk, "StringVar", Variable), patch.multiple(main.ttk, Frame=Widget, Label=Widget, LabelFrame=Widget, Button=Widget):
                manager=main.Manager(window)
                callback=window.protocol("WM_DELETE_WINDOW")
                callback()
                manager.quit()
                deadline=time.monotonic()+8
                while not window.destroyed and time.monotonic()<deadline:
                    window.update();time.sleep(.02)
                assert case == "tk_close", "blocked server should have exited the process"
                assert main._shutdown_complete and window.destroyed
                assert not manager._after_ids and not window.jobs
        else:
            main.request_application_shutdown("test_window_close")

    deadline=time.monotonic()+10
    while not main.finish_application_shutdown() and time.monotonic()<deadline:
        main.APP_LIFECYCLE.join(.05)
    assert main._shutdown_complete, main.APP_LIFECYCLE.pending()
    assert main.APP_LIFECYCLE.pending() == []
    assert not main.wsmanager.clients
    assert main._server_log_handler.stream is None
    assert main.finish_application_shutdown() is True
    with socket.socket() as rebound:
        rebound.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        rebound.bind(("0.0.0.0", port));rebound.listen()
    with sqlite3.connect(main.DB_PATH) as con:
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        if case == "slow_write":
            assert con.execute("SELECT value FROM close_test").fetchone()[0] == "committed"
    con.close()
    log=(root/"logs/server.log").read_text(encoding="utf-8")
    assert "Application resources released" in log
    print(json.dumps({"case":case,"success":True}))
