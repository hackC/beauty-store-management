from copy import deepcopy
from pathlib import Path
import runpy
import shutil
import subprocess

from fastapi.testclient import TestClient
import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_project_filter_and_all_scan_modes():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for the workflow simulation")
    subprocess.run([node, str(ROOT/"tests/dev128_workflows.cjs"),
                    str(ROOT/"static/schedule_projects.js"), str(ROOT/"static/index.html")],
                   check=True, timeout=15)


def test_categories_persist_without_migrating_existing_appointments(tmp_path):
    support = runpy.run_path(str(Path(__file__).with_name("test_v160_dev9_catalog_inventory_permissions.py")))
    project, old_cwd, app = support["_load_project"](tmp_path)
    try:
        admin = TestClient(app.app, base_url="https://testserver")
        assert admin.post("/api/auth/bootstrap", json={
            "username":"admin128", "display_name":"管理员", "password":"StorePass1234",
        }).is_success
        legacy = {"staff": [{"id":"s1","name":"一"}],
                  "services": [{"id":"p1","name":"补水护理","duration":30}],
                  "appointments": [{"id":"old","staffId":"s1","serviceId":"p1",
                                    "date":"2026-11-15","start":"10:00","duration":30,"note":"原备注"}]}
        assert admin.post("/api/schedule/migrate", json={"payload":legacy}).is_success
        record = admin.get("/api/schedule/state").json()
        assert record["payload"] == legacy
        categorized = deepcopy(legacy)
        categorized["serviceCategories"] = [{"id":"face","name":"面部"}]
        categorized["services"][0]["categoryId"] = "face"
        stylist, _, _ = support["_create_user"](admin, app, "limited128", {
            "ext.scheduling.read":True, "ext.scheduling.shift_update_self_today":True,
        })
        assert stylist.put("/api/schedule/state", json={
            "payload":{**categorized, "appointments":[]}, "revision":1,
        }).status_code == 403
        assert admin.put("/api/schedule/state", json={"payload":categorized,"revision":1}).is_success
        state = admin.get("/api/schedule/state").json()
        assert state["payload"]["appointments"][0]["note"] == "原备注"
        assert state["payload"]["serviceCategories"] == [{"id":"face","name":"面部"}]
        orphan = {**categorized, "serviceCategories":[]}
        assert admin.put("/api/schedule/state", json={"payload":orphan,"revision":2}).status_code == 400
        new_booking = deepcopy(categorized)
        new_booking["appointments"].append({
            **legacy["appointments"][0], "id":"new", "start":"11:00", "projectName":"补水护理",
        })
        assert admin.put("/api/schedule/state", json={"payload":new_booking,"revision":2}).is_success
        new_booking["services"][0]["name"] = "补水升级"
        assert admin.put("/api/schedule/state", json={"payload":new_booking,"revision":3}).is_success
        assert admin.get("/api/schedule/state").json()["payload"]["appointments"][1]["projectName"] == "补水护理"
    finally:
        support["_close_project"](project, old_cwd)
