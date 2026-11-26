from datetime import date
from pathlib import Path
import runpy
import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]


def test_network_recovers_without_refresh_or_repeating_writes():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for the browser transport simulation")
    subprocess.run([node, str(ROOT / "tests/dev126_network_case.cjs"),
                    str(ROOT / "static/store_network.js")], check=True, timeout=15)


def test_only_the_allowed_active_page_requests_business_data():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for the navigation simulation")
    subprocess.run([node, str(ROOT / "tests/dev126_navigation_case.cjs"),
                    str(ROOT / "static/app_shell.js"), str(ROOT / "static/index.html")],
                   check=True, timeout=15)


def test_staff_templates_and_canonical_schedule_route(tmp_path):
    support = runpy.run_path(str(Path(__file__).with_name("test_v160_dev9_catalog_inventory_permissions.py")))
    project, old_cwd, app = support["_load_project"](tmp_path)
    try:
        admin = TestClient(app.app, base_url="https://testserver")
        guest = TestClient(app.app, base_url="https://testserver")
        assert admin.post("/api/auth/bootstrap", json={
            "username": "storeadmin", "display_name": "管理员", "password": "StorePass1234",
        }).is_success
        assert "page-home" in guest.get("/?page=schedule").text
        redirect = guest.get("/schedule", follow_redirects=False)
        assert redirect.status_code == 302 and redirect.headers["location"] == "/?page=schedule"
        assert "scheduleSidebar" in admin.get("/?page=schedule").text
        templates = admin.get("/api/permission-groups/templates").json()
        manager, _, _ = support["_create_user"](admin, app, "manager", dict.fromkeys(templates["manager"], True))
        assert manager.get("/api/summary").is_success
        assert manager.get("/api/purchase").is_success
        assert manager.get("/api/settings").status_code == 403
        assert manager.get("/api/backup").status_code == 403
        assert manager.get("/api/users").status_code == 403
        assert manager.get("/api/permission-groups/templates").status_code == 403

        day = date.today().isoformat()
        payload = {
            "staff": [{"id": "s1", "name": "一"}, {"id": "s2", "name": "二", "notes": "协作者信息"}],
            "services": [], "daysOff": {day: ["s2"]},
            "appointments": [
                {"id": "a1", "staffId": "s1", "date": day, "start": "09:00", "duration": 30, "status": "pending", "phoneTail": "998877"},
                {"id": "a2", "staffId": "s2", "date": day, "start": "10:00", "duration": 30, "status": "pending", "customer": "其他员工客户"},
            ],
            "bookingGroups": [],
        }
        assert admin.post("/api/schedule/migrate", json={"payload": payload}).is_success
        stylist, uid, group = support["_create_user"](admin, app, "stylist", dict.fromkeys(templates["stylist"], True))
        assert stylist.get("/api/schedule/state").json()["payload"]["appointments"] == []
        assert admin.put("/api/schedule/staff-links", json={"user_id": uid, "staff_id": "s1"}).is_success
        assert "scheduleSidebar" in stylist.get("/?page=schedule").text
        state = stylist.get("/api/schedule/state").json()
        assert [a["id"] for a in state["payload"]["appointments"]] == ["a1"]
        assert state["payload"]["appointments"][0]["phoneTail"] == ""
        assert state["payload"]["daysOff"] == {}
        assert "协作者信息" not in str(state)
        assert stylist.get("/api/purchase").status_code == 403
        assert stylist.get("/api/products").is_success
        assert stylist.get("/api/schedule/transfers").status_code == 403
        patch = {"status": "done", "expected_revision": state["revision"]}
        assert stylist.patch("/api/schedule/appointments/a2/status", json=patch).status_code == 403
        assert stylist.patch("/api/schedule/appointments/a1/status", json=patch).is_success
        assert admin.delete(f"/api/permission-groups/{group}/members/{uid}").is_success
        assert stylist.get("/api/schedule/state").status_code == 403
        assert "scheduleSidebar" not in stylist.get("/?page=schedule").text
    finally:
        support["_close_project"](project, old_cwd)
