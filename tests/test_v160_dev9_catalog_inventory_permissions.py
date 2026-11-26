"""dev.9 regression tests for catalog, inventory and procurement module boundaries."""
from __future__ import annotations

import importlib
import os
from pathlib import Path
import shutil
import sys
import tempfile

from fastapi.testclient import TestClient

from application_test_support import unload_test_application


def _load_project(tmp_path):
    source = Path(__file__).resolve().parents[1]
    project = tmp_path / "project"
    shutil.copytree(source, project, ignore=shutil.ignore_patterns(
        "__pycache__", ".pytest_cache", "*.pyc", "release", "upgrade_payload", "logs"
    ))
    old_cwd = os.getcwd()
    sys.path.insert(0, str(project))
    os.chdir(project)
    sys.modules.pop("main", None)
    main = importlib.import_module("main")
    main.DB_PATH = project / "data" / "dev9_test.db"
    main.init_db()
    return project, old_cwd, main


def _close_project(project, old_cwd):
    os.chdir(old_cwd)
    sys.path.remove(str(project))
    unload_test_application()


def _create_user(admin, main, username, group_permissions):
    user = admin.post("/api/users", json={
        "username": username, "display_name": username, "password": "StaffPass12345"
    })
    assert user.status_code == 200, user.text
    group = admin.post("/api/permission-groups", json={
        "name": f"{username}权限", "permissions": group_permissions,
    })
    assert group.status_code == 201, group.text
    group_id = group.json()["id"]
    assigned = admin.put(f"/api/permission-groups/{group_id}/members/{user.json()['id']}")
    assert assigned.status_code == 200, assigned.text
    client = TestClient(main.app, base_url="https://testserver")
    login = client.post("/api/auth/login", json={
        "username": username, "password": "StaffPass12345",
    })
    assert login.status_code == 200, login.text
    return client, user.json()["id"], group_id


def test_fine_grained_catalog_create_and_update_are_checked_against_real_record_state(tmp_path):
    project, old_cwd, main = _load_project(tmp_path)
    try:
        admin = TestClient(main.app, base_url="https://testserver")
        assert admin.post("/api/auth/bootstrap", json={
            "username": "manager", "display_name": "店长", "password": "ManagerPass1234"
        }).status_code == 200
        base = {"brand": "品牌", "barcode": "", "name": "商品", "spec": "规格",
                "category": "类别", "location": "A-01", "unit": "件", "opening": 0}
        assert admin.post("/api/products", json={**base, "id": "DEV9-P1"}).status_code == 200

        creator, _, _ = _create_user(admin, main, "creator", {"ext.catalog.create": True})
        created = creator.post("/api/products", json={**base, "id": "DEV9-P2", "name": "新商品"})
        assert created.status_code == 200, created.text
        denied_update = creator.post("/api/products", json={**base, "id": "DEV9-P1", "name": "越权修改"})
        assert denied_update.status_code == 403

        editor, _, _ = _create_user(admin, main, "editor", {"ext.catalog.update": True})
        updated = editor.post("/api/products", json={**base, "id": "DEV9-P1", "name": "修改商品"})
        assert updated.status_code == 200, updated.text
        denied_create = editor.post("/api/products", json={**base, "id": "DEV9-P3", "name": "越权新增"})
        assert denied_create.status_code == 403
    finally:
        _close_project(project, old_cwd)


def test_inventory_movement_actions_are_separated_by_document_type(tmp_path):
    project, old_cwd, main = _load_project(tmp_path)
    try:
        admin = TestClient(main.app, base_url="https://testserver")
        assert admin.post("/api/auth/bootstrap", json={
            "username": "manager", "display_name": "店长", "password": "ManagerPass1234"
        }).status_code == 200
        assert admin.post("/api/products", json={
            "id": "DEV9-STOCK", "name": "库存测试商品", "opening": 0,
        }).status_code == 200
        inbound, _, _ = _create_user(admin, main, "inbound", {
            "ext.inventory.inbound_create": True,
        })
        inbound_result = inbound.post("/api/submit", json={
            "type": "入库", "external_no": "IN-DEV9", "request_id": "dev9-in-1",
            "items": [{"product_id": "DEV9-STOCK", "quantity": 5}],
        })
        assert inbound_result.status_code == 200, inbound_result.text
        denied_outbound = inbound.post("/api/submit", json={
            "type": "出库", "external_no": "OUT-DEV9",
            "items": [{"product_id": "DEV9-STOCK", "quantity": 1}],
        })
        assert denied_outbound.status_code == 403
        assert admin.get("/api/products?q=DEV9-STOCK").json()[0]["stock"] == 5
        alerts = admin.get("/api/notifications").json()
        assert any("库存不足预警" == item["title"] for item in alerts)

        stocktaker, _, _ = _create_user(admin, main, "stocktaker", {
            "ext.inventory.stocktake_adjust": True,
        })
        adjustment = stocktaker.post("/api/submit", json={
            "type": "盘点调整", "external_no": "COUNT-DEV9",
            "items": [{"product_id": "DEV9-STOCK", "quantity": 3}],
        })
        assert adjustment.status_code == 200, adjustment.text
        denied_inbound = stocktaker.post("/api/submit", json={
            "type": "入库", "external_no": "IN-UNAUTHORIZED",
            "items": [{"product_id": "DEV9-STOCK", "quantity": 1}],
        })
        assert denied_inbound.status_code == 403
        assert admin.get("/api/products?q=DEV9-STOCK").json()[0]["stock"] == 3
    finally:
        _close_project(project, old_cwd)


def test_export_permissions_are_kind_specific(tmp_path):
    project, old_cwd, main = _load_project(tmp_path)
    try:
        admin = TestClient(main.app, base_url="https://testserver")
        assert admin.post("/api/auth/bootstrap", json={
            "username": "manager", "display_name": "店长", "password": "ManagerPass1234"
        }).status_code == 200
        exporter, _, _ = _create_user(admin, main, "catalog_exporter", {
            "ext.catalog.export": True,
        })
        assert exporter.get("/api/export/products").status_code == 200
        assert exporter.get("/api/export/transactions").status_code == 403
        assert exporter.get("/api/export/purchase").status_code == 403
    finally:
        _close_project(project, old_cwd)


def test_legacy_permissions_expand_without_overgranting_purchase_catalog_read():
    from store_inventory.permissions.migration import expand_legacy_permissions

    products_manager = expand_legacy_permissions({"products_manage": True})
    assert products_manager["ext.catalog.create"] is True
    assert products_manager["ext.catalog.update"] is True
    assert products_manager["ext.catalog.disable"] is True
    assert "ext.catalog.export" not in products_manager

    purchase_viewer = expand_legacy_permissions({"purchase_view": True})
    assert purchase_viewer["ext.procurement.read"] is True
    assert purchase_viewer["ext.catalog.image_read"] is True
    assert "ext.catalog.read" not in purchase_viewer
    assert "ext.catalog.create" not in purchase_viewer


def test_catalog_inventory_and_procurement_application_layers_do_not_import_main_or_fastapi():
    root = Path(__file__).resolve().parents[1]
    for relative in (
        "store_inventory/catalog/domain.py", "store_inventory/catalog/ports.py",
        "store_inventory/catalog/service.py", "store_inventory/inventory/domain.py",
        "store_inventory/inventory/ports.py", "store_inventory/inventory/service.py",
        "store_inventory/procurement/ports.py", "store_inventory/procurement/service.py",
    ):
        content = (root / relative).read_text("utf-8").lower()
        assert "from main" not in content and "import main" not in content
        assert "from fastapi" not in content and "import fastapi" not in content


def test_dev9_core_module_files_are_present_in_upgrade_payload():
    root = Path(__file__).resolve().parents[1]
    for package in ("catalog", "inventory", "procurement", "platform"):
        source_dir = root / "store_inventory" / package
        payload_dir = root / "upgrade_payload" / "store_inventory" / package
        assert source_dir.is_dir()
        for source in source_dir.glob("*.py"):
            assert (payload_dir / source.name).read_bytes() == source.read_bytes()
