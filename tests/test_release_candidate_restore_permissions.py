from contextlib import closing
from pathlib import Path
import json
import shutil
import sqlite3
import subprocess
import threading

from fastapi.testclient import TestClient
import pytest

from test_v160_dev9_catalog_inventory_permissions import _close_project, _create_user, _load_project


@pytest.fixture
def store(tmp_path):
    project, old_cwd, main = _load_project(tmp_path)
    try:
        admin = TestClient(main.app, base_url="https://testserver")
        response = admin.post("/api/auth/bootstrap", json={
            "username": "manager", "display_name": "店长", "password": "ManagerPass1234",
        })
        assert response.status_code == 200, response.text
        yield main, admin
    finally:
        _close_project(project, old_cwd)


def test_restore_swaps_roll_back_and_blocks_concurrent_api(store, monkeypatch):
    main, admin = store
    (main.PURCHASE_DIR / "old.txt").write_text("archive")
    (main.PRODUCT_IMAGE_DIR / "image.txt").write_text("archive")
    backup = main.create_backup()
    zip_content = (main.BACKUP_DIR / backup["bundle_archive"]).read_bytes()
    (main.PURCHASE_DIR / "old.txt").write_text("current purchase")
    (main.PRODUCT_IMAGE_DIR / "image.txt").write_text("current image")
    assert admin.post("/api/products", json={"id": "KEEP", "name": "当前商品"}).is_success
    entered, release = threading.Event(), threading.Event()
    replace = main.os.replace

    def fail_install(src, dst):
        if Path(dst) == main.PURCHASE_DIR and Path(src).name.startswith(".purchase.restore_"):
            entered.set()
            assert release.wait(timeout=8)
            raise OSError("simulated attachment swap failure")
        return replace(src, dst)

    monkeypatch.setattr(main.os, "replace", fail_install)
    result = []
    worker = threading.Thread(target=lambda: result.append(admin.post(
        "/api/restore", files={"file": ("backup.zip", zip_content)},
    )))
    worker.start()
    try:
        assert entered.wait(timeout=8)
        assert admin.get("/api/health/live").status_code == 200
        assert admin.get("/api/health/ready").status_code == 503
        assert admin.post("/api/products", json={"id": "BLOCK", "name": "恢复中"}).status_code == 503
    finally:
        release.set()
        worker.join(timeout=8)
    assert result[0].status_code == 503
    assert (main.PURCHASE_DIR / "old.txt").read_text() == "current purchase"
    assert (main.PRODUCT_IMAGE_DIR / "image.txt").read_text() == "current image"
    assert admin.get("/api/health/ready").is_success
    assert any(item["id"] == "KEEP" for item in admin.get("/api/products").json())


def test_old_raw_database_is_migrated_and_future_schema_rejected(store, tmp_path):
    main, admin = store
    incoming = tmp_path / "old.db"
    with closing(main.db()) as source, closing(sqlite3.connect(incoming)) as target:
        source.backup(target)
        target.execute("UPDATE meta SET value='0' WHERE key='schema_version'")
        target.execute("DROP TABLE permission_group_members")
        target.execute("DROP TABLE permission_groups")
        target.commit()
    restored = admin.post("/api/restore", files={"file": ("old.db", incoming.read_bytes())})
    assert restored.status_code == 200, restored.text
    assert admin.post("/api/auth/login", json={
        "username": "manager", "password": "ManagerPass1234",
    }).is_success
    with closing(main.db()) as restored_db:
        assert restored_db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == str(main.DB_SCHEMA_VERSION)
        assert restored_db.execute("SELECT COUNT(*) FROM permission_groups").fetchone()[0] == 0
    with closing(sqlite3.connect(incoming)) as future, future:
        future.execute("UPDATE meta SET value='999' WHERE key='schema_version'")
    rejected = admin.post("/api/restore", files={"file": ("future.db", incoming.read_bytes())})
    assert rejected.status_code == 400
    assert "高于当前程序支持版本" in rejected.json()["detail"]
    assert admin.get("/api/auth/me").is_success


def test_legacy_rule_editor_uses_granular_actions_and_revokes_only_selected_action(store):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for the permission editor simulation")
    main, admin = store
    employee, _, group_id = _create_user(admin, main, "inventoryrc", {"products_manage": True})
    assert employee.post("/api/products", json={"id": "RC-P", "name": "商品"}).is_success
    group = next(g for g in admin.get("/api/permission-groups").json() if g["id"] == group_id)
    result = subprocess.run(
        [node, str(Path(__file__).with_name("dev129_permissions.cjs")), str(main.STATIC_DIR / "index.html")],
        input=json.dumps({
            "catalog": admin.get("/api/permissions/catalog").json(),
            "templates": admin.get("/api/permission-groups/templates").json(),
            "savedGroup": group,
        }), capture_output=True, text=True, check=True, timeout=15,
    )
    save = json.loads(result.stdout)
    path = save.pop("path")
    assert admin.patch(path, json=save).is_success
    assert employee.delete("/api/products/RC-P").status_code == 403
    assert employee.post("/api/products", json={"id": "RC-P", "name": "编辑保留"}).is_success
    assert employee.get("/api/products").is_success
    duplicate = admin.post("/api/permission-groups", json=save)
    assert duplicate.status_code == 409
    assert "同名" in duplicate.json()["detail"]
