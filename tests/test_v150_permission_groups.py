# -*- coding: utf-8 -*-
"""Rules are enforced by the API and become effective in already-issued sessions."""
import importlib
import os
from pathlib import Path
import shutil
import sys
import tempfile
from fastapi.testclient import TestClient

from application_test_support import unload_test_application


def test_group_crud_membership_and_immediate_revocation():
    source = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="store_permission_groups_") as td:
        project = Path(td) / "project"
        shutil.copytree(source, project, ignore=shutil.ignore_patterns(
            "__pycache__", ".pytest_cache", "*.pyc", "release", "upgrade_payload", "logs"
        ))
        old_cwd = os.getcwd()
        sys.path.insert(0, str(project))
        os.chdir(project)
        sys.modules.pop("main", None)
        try:
            main = importlib.import_module("main")
            main.init_db()  # all DB migrations happen in the copied tree
            admin = TestClient(main.app, base_url="https://testserver")
            staff = TestClient(main.app, base_url="https://testserver")
            bootstrap = admin.post("/api/auth/bootstrap", json={
                "username": "manager", "display_name": "店长", "password": "ManagerPass1234"
            })
            assert bootstrap.status_code == 200, bootstrap.text
            member = admin.post("/api/users", json={
                "username": "assistant1", "display_name": "前台", "password": "StaffPass1234",
                "permissions": {k: False for k in main.PERMISSION_LABELS},
            })
            assert member.status_code == 200, member.text
            user_id = member.json()["id"]
            login = staff.post("/api/auth/login", json={"username": "assistant1", "password": "StaffPass1234"})
            assert login.status_code == 200, login.text
            assert staff.get("/api/reports/operations").status_code == 403
            assert staff.get("/api/permission-groups").status_code == 403
            forbidden = admin.post("/api/permission-groups", json={
                "name": "危险规则", "permissions": {"backup_restore": True}
            })
            assert forbidden.status_code == 400
            created = admin.post("/api/permission-groups", json={
                "name": "前台查询", "permissions": {"reports_view": True}
            })
            assert created.status_code == 201, created.text
            group_id = created.json()["id"]
            assigned = admin.put(f"/api/permission-groups/{group_id}/members/{user_id}")
            assert assigned.status_code == 200, assigned.text
            current = staff.get("/api/auth/me").json()
            assert current["permissions"]["reports_view"] is True
            assert current["direct_permissions"]["reports_view"] is False
            assert group_id in current["group_ids"]
            assert staff.get("/api/reports/operations").status_code == 200
            # The logged-in employee sees group edits without re-login.
            assert admin.patch(f"/api/permission-groups/{group_id}", json={
                "name": "前台查询", "permissions": {"products_view": True}
            }).status_code == 200
            assert staff.get("/api/reports/operations").status_code == 403
            assert staff.get("/api/products").status_code == 200
            assert admin.delete(f"/api/permission-groups/{group_id}/members/{user_id}").status_code == 200
            assert staff.get("/api/products").status_code == 403
            assert admin.delete(f"/api/permission-groups/{group_id}").status_code == 200
            assert admin.get("/api/permission-groups").json() == []
        finally:
            os.chdir(old_cwd)
            sys.path.remove(str(project))
            unload_test_application()


def test_second_round_mobile_layout_contracts():
    root = Path(__file__).resolve().parents[1]
    inventory = (root / "static" / "index.html").read_text("utf-8")
    schedule = (root / "static" / "index_V2_5_5.html").read_text("utf-8")
    main = (root / "main.py").read_text("utf-8")
    assert 'class="field op-location readonly"' in inventory
    assert 'row.querySelector(".op-location").value = product.location' in inventory
    assert 'row.querySelector(".op-location").value = ""' in inventory
    assert 'id="permissionGroupCards"' in inventory
    assert 'data-group-permission=' in inventory
    assert '.mobile-card-table tr { grid-auto-rows:minmax(68px,auto)' in inventory
    assert 'height:46px' in inventory
    assert '/static/schedule_display.css' in schedule
    assert 'ScheduleDisplay.pixelsPerMinute(wrap.clientHeight, headerHeight, prefs.visibleHours)' in schedule
    assert "new ResizeObserver(fitScheduleTimeline)" in schedule
    assert 'class="schedule-nav-menu"' in schedule
    assert 'class="store-mobile-nav app-bottom-nav"' in schedule
    assert 'data-store-page="schedule"' in schedule
    assert 'class="schedule-mobile-bar"' not in schedule
    assert 'DB_SCHEMA_VERSION = 8' in main
    assert '"https": True' in main
