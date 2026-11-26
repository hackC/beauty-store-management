import json
from pathlib import Path
import shutil
import subprocess

from fastapi.testclient import TestClient
import pytest

from test_v160_dev9_catalog_inventory_permissions import _close_project, _load_project

ROOT = Path(__file__).resolve().parents[1]


def test_same_employee_companion_edit_saves_through_schedule_api(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for the appointment modal simulation")
    day = "2026-11-15"
    primary = {
        "id": "primary", "date": day, "staffId": "s1", "serviceId": "p1", "customer": "林女士",
        "groupId": "group-1", "groupCustomer": "林女士", "groupPhoneTail": "0123", "phoneTail": "0123",
        "duration": 60, "buffer": 0, "extend": 0, "start": "10:00", "status": "pending", "note": "整组备注",
    }
    payload = {
        "staff": [{"id": "s1", "name": "美容师"}],
        "services": [{"id": "p1", "name": "护理", "duration": 60},
                     {"id": "p2", "name": "次要护理", "duration": 30}],
        "appointments": [
            {**primary, "projectIds": ["p1", "p2"], "projectsTogether": True},
            {**primary, "id": "companion", "start": "11:00", "customer": "同行", "status": "done",
             "phoneTail": "9999", "note": "同行专属备注", "segmentOf": "old-segment", "projectName": "旧项目名"},
            {**primary, "id": "cancelled", "start": "12:00", "status": "cancelled"},
        ],
        "bookingGroups": [{
            "id": "group-1", "contact": "林女士", "phoneTail": "0123", "note": "整组备注",
            "date": day, "appointmentIds": ["primary", "companion", "cancelled"], "color": "#3355aa",
        }],
    }
    result = subprocess.run(
        [node, str(ROOT / "tests/rc4_companion_case.cjs"), str(ROOT / "static/index_V2_5_5.html")],
        input=json.dumps(payload), capture_output=True, encoding="utf-8", timeout=15,
    )
    assert result.returncode == 0, result.stderr
    draft = json.loads(result.stdout)
    project, old_cwd, main = _load_project(tmp_path)
    try:
        admin = TestClient(main.app, base_url="https://testserver")
        assert admin.post("/api/auth/bootstrap", json={
            "username": "manager", "display_name": "店长", "password": "ManagerPass1234",
        }).is_success
        assert admin.post("/api/schedule/migrate", json={"payload": payload}).is_success
        saved = admin.put("/api/schedule/state", json=draft)
        assert saved.status_code == 200, saved.text
        state = admin.get("/api/schedule/state").json()
        assert state["revision"] == 2
        assert state["payload"] == draft["payload"]
    finally:
        _close_project(project, old_cwd)


def test_project_picker_abbreviation_and_single_wait():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for the project picker simulation")
    subprocess.run([node, str(ROOT / "tests/rc4_project_case.cjs"),
                    str(ROOT / "static/schedule_projects.js")], check=True, timeout=15)


def test_desktop_import_uses_the_same_restore_validation(tmp_path):
    from fastapi import HTTPException
    from contextlib import closing
    import sqlite3

    project, old_cwd, main = _load_project(tmp_path)
    try:
        source = tmp_path / "before.db"
        with closing(main.db()) as live, closing(sqlite3.connect(source)) as copy:
            live.backup(copy)
        with closing(main.db()) as live, live:
            live.execute("INSERT INTO settings(key,value) VALUES('desktop-restore-test','1')")
        config_before = main.CONFIG_PATH.read_bytes() if main.CONFIG_PATH.exists() else None
        app = main.DesktopManagementService(main.create_backup, lambda _: None, main.DATA_DIR,
                                            main.LOG_DIR / "server.log", restore_action=main.restore_local_backup)
        result = app.restore_backup(source)
        assert result["ok"] and result["login_required"]
        assert not main._restoring
        assert (main.CONFIG_PATH.read_bytes() if main.CONFIG_PATH.exists() else None) == config_before
        with closing(main.db()) as live:
            assert live.execute("SELECT value FROM settings WHERE key='desktop-restore-test'").fetchone() is None
        with main._reserve_restore(), pytest.raises(HTTPException) as failure:
            main.restore_local_backup(source)
        assert failure.value.status_code == 409
    finally:
        _close_project(project, old_cwd)
