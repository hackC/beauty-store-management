from io import BytesIO
from pathlib import Path
import shutil
import subprocess

from fastapi.testclient import TestClient
from openpyxl import load_workbook
import pytest

from test_v160_dev9_catalog_inventory_permissions import _close_project, _create_user, _load_project
from store_inventory.platform.exports import ExcelExportService


ROOT = Path(__file__).resolve().parents[1]


def test_schedule_focus_moves_to_account_column_and_local_time_once():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for schedule focus verification")
    subprocess.run([node, str(ROOT / "tests/rc3_schedule_focus.cjs"), str(ROOT / "static")],
                   timeout=15, check=True)


def test_excel_dialog_applies_default_and_keeps_filters_on_download():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for the export dialog simulation")
    subprocess.run([node, str(ROOT / "tests/rc3_excel_dialog.cjs"), str(ROOT / "static/index.html")],
                   timeout=15, check=True)


def test_export_templates_are_personal_scoped_and_have_one_default(tmp_path):
    project, old_cwd, main = _load_project(tmp_path)
    try:
        admin = TestClient(main.app, base_url="https://testserver")
        assert admin.post("/api/auth/bootstrap", json={
            "username": "manager", "display_name": "店长", "password": "ManagerPass1234",
        }).is_success
        employee, _, _ = _create_user(admin, main, "exportstaff", {"ext.catalog.export": True})
        assert admin.post("/api/products", json={"id": "RC3", "name": "护理商品", "barcode": "0000321"}).is_success
        root = "/api/export/products"
        options = employee.get(root + "/options")
        assert options.status_code == 200
        assert options.headers["cache-control"] == "no-store, private"
        payload = {"name": "扫码", "fields": ["name", "barcode"], "is_default": True}
        first = employee.post(root + "/templates", json=payload)
        assert first.status_code == 201, first.text
        assert employee.post(root + "/templates", json=payload).status_code == 409
        assert employee.post(root + "/templates", json={**payload, "fields": ["secret"]}).status_code == 400
        second = employee.post(root + "/templates", json={
            **payload, "name": "仓位", "fields": ["location", "name"],
        })
        assert second.status_code == 201
        templates = employee.get(root + "/options").json()["templates"]
        assert [x["id"] for x in templates if x["is_default"]] == [second.json()["id"]]
        assert admin.get(root + "/options").json()["templates"] == []
        assert admin.delete(root + "/templates/" + first.json()["id"]).status_code == 404
        assert employee.get("/api/export/transactions/options").status_code == 403
        assert employee.post("/api/export/purchase/templates", json=payload).status_code == 403
        assert employee.get(root, params=[("fields", "secret")]).status_code == 400
        response = employee.get(root, params=[("q", "RC3"), ("fields", "name"), ("fields", "barcode")])
        assert response.is_success, response.text
        workbook = load_workbook(BytesIO(response.content))
        try:
            sheet = workbook.active
            assert [cell.value for cell in sheet[2]] == ["品名", "条形码"]
            assert [cell.value for cell in sheet[3]] == ["护理商品", "0000321"]
        finally:
            workbook.close()
        update = employee.put(root + "/templates/" + first.json()["id"], json=payload)
        assert update.status_code == 200
        templates = employee.get(root + "/options").json()["templates"]
        assert [x["id"] for x in templates if x["is_default"]] == [first.json()["id"]]
        assert employee.delete(root + "/templates/" + first.json()["id"]).is_success
        assert not any(x["is_default"] for x in employee.get(root + "/options").json()["templates"])
    finally:
        _close_project(project, old_cwd)


def test_export_sizes_for_chinese_and_formats_overflow_without_truncating(tmp_path):
    note = "客户备注和门店交接记录" * 120
    service = ExcelExportService(product_reader=lambda **_: [], purchase_reader=lambda: [],
                                 transaction_reader=lambda **_: [{"note": note, "doc_no": "=00001"}],
                                 output_directory=lambda _: tmp_path)
    path = service.export("transactions", fields=["note", "doc_no"])
    workbook = load_workbook(path)
    try:
        sheet = workbook.active
        assert sheet["A3"].value == note
        assert sheet["B3"].data_type == "s"
        assert sheet["A3"].alignment.wrap_text
        assert sheet.column_dimensions["A"].width > sheet.column_dimensions["B"].width
        assert sheet.row_dimensions[3].height > 30
        assert sheet["A3"].comment
        continuation = workbook["长文本续页"]
        assert "".join(cell.value.replace("\n", "") for cell in list(continuation["D"])[1:]) == note
    finally:
        workbook.close()
