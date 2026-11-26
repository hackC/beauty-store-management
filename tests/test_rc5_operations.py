from threading import Event, Thread
from types import SimpleNamespace

from fastapi.testclient import TestClient

from store_inventory.runtime.local_control import LocalControl
from test_v160_dev9_catalog_inventory_permissions import _close_project, _load_project


def test_local_stop_uses_the_active_instance_marker_and_acknowledges(tmp_path):
    control = LocalControl(tmp_path)
    worker_stop = Event()
    ready = Event()
    endpoint = SimpleNamespace(instance="current-instance", version="test", port=8123, https=False)

    def run():
        with control.lease(endpoint) as marker:
            ready.set()
            control.watch(marker, worker_stop, worker_stop.set)
            control.confirm_stopped(marker)

    thread = Thread(target=run)
    thread.start()
    try:
        assert ready.wait(3)
        assert control.observe()["instance"] == endpoint.instance
        assert control.request_stop(timeout=3)
    finally:
        worker_stop.set()
        thread.join(3)
    assert not thread.is_alive()
    assert not control.observe()


def test_keep_login_policy_and_inventory_replay(tmp_path):
    project, old_cwd, main = _load_project(tmp_path)
    try:
        admin = TestClient(main.app, base_url="https://testserver")
        assert admin.post("/api/auth/bootstrap", json={"username":"manager","display_name":"店长","password":"ManagerPass1234"}).is_success
        assert admin.get("/api/users/session-policy").json() == {"days": None}
        with TestClient(main.app, base_url="https://testserver") as phone:
            payload = {"username":"manager","password":"ManagerPass1234"}
            assert "Max-Age" not in phone.post("/api/auth/login", json=payload).headers["set-cookie"]
            remembered = phone.post("/api/auth/login", json={**payload,"remember_browser":True})
            assert remembered.json()["remembered"]
            assert "Max-Age=" in remembered.headers["set-cookie"]
            assert "set-cookie" not in phone.get("/api/auth/status").headers
            assert phone.post("/api/auth/logout").is_success
            assert not phone.get("/api/auth/status").json()["authenticated"]
            assert admin.put("/api/users/session-policy", json={"days":7}).json() == {"days":7}
            limited = phone.post("/api/auth/login", json={**payload,"remember_browser":True})
            assert "Max-Age=604800" in limited.headers["set-cookie"]
        assert admin.post("/api/products", json={"id":"RC5-ONE","name":"测试入库"}).is_success
        payload = {"type":"入库","request_id":"same-receipt","items":[{"product_id":"RC5-ONE","quantity":2}]}
        first = admin.post("/api/submit", json=payload)
        assert first.is_success, first.text
        duplicate = admin.post("/api/submit", json=payload)
        assert duplicate.json() == {**first.json(),"duplicate":True}
        payload["items"][0]["quantity"] = 3
        assert admin.post("/api/submit", json=payload).status_code == 409
        rows = admin.get("/api/transactions",params={"q":"RC5-ONE"}).json()
        assert len(rows) == 1 and rows[0]["qty_in"] == 2
    finally:
        _close_project(project, old_cwd)


def test_interrupted_restore_blocks_startup_before_schema_writes(tmp_path, monkeypatch):
    project, old_cwd, main = _load_project(tmp_path)
    calls = []
    try:
        main._restore_journal_path().write_text('{"phase":"swapping"}', encoding="utf-8")
        monkeypatch.setattr(main, "init_db", lambda: calls.append("init"))
        main.start_server_thread(prepare=True)
        main.server_thread.join(3)
        assert not main.server_thread.is_alive()
        assert not calls
        assert "上次恢复未确认完成" in main.server_error
        assert main._restore_journal_path().is_file()
    finally:
        _close_project(project, old_cwd)
