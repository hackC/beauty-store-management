import json
import sqlite3
from datetime import datetime
from pathlib import Path

from store_inventory.platform.configuration import JsonConfigStore
from store_inventory.platform.reports import OperationsReportService, ReportValidationError
from store_inventory.platform.backups import BackupService
from store_inventory.platform.backup_archive import BackupArchiveService
from store_inventory.platform.backup_restore import BackupRestoreService, BackupRestoreError
from store_inventory.platform.desktop import DesktopManagementService
from store_inventory.platform.settings import SystemSettingsService
from store_inventory.platform.exports import ExcelExportService, ExportValidationError


def test_json_config_store_merge_and_atomic_save(tmp_path):
    path = tmp_path / "data" / "config.json"
    store = JsonConfigStore(path, {"port": 8000, "https": True})
    assert store.load() == {"port": 8000, "https": True}
    store.save({"port": 9000})
    assert store.load() == {"port": 9000, "https": True}
    assert not path.with_suffix(".json.tmp").exists()


def test_json_config_store_preserves_last_saved_paths_and_invalid_file(tmp_path):
    path = tmp_path / "config.json"
    store = JsonConfigStore(path, {"port": 8000})
    saved = {"port": 9000, "backup_dir": "custom/backups", "purchase_dir": "custom/purchase"}
    store.save(saved)
    path.write_text("{broken", encoding="utf-8")
    assert store.load() == saved
    assert path.read_text(encoding="utf-8") == "{broken"
    store.save(saved)
    assert store.load() == saved
    assert next(tmp_path.glob("config.json.*.invalid")).read_text() == "{broken"


def make_report_service(tmp_path):
    path = tmp_path / "report.db"
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.executescript("""
      CREATE TABLE transactions(type TEXT,time TEXT,qty_in REAL,qty_out REAL,qty_adjust REAL);
      CREATE TABLE products(id TEXT,active INTEGER);
      CREATE TABLE customer_profiles(id TEXT,created_at TEXT);
      CREATE TABLE schedule_state(id INTEGER,payload TEXT);
      INSERT INTO transactions VALUES('入库','2026-11-15 10:00:00',5,0,0);
      INSERT INTO transactions VALUES('出库','2026-11-14 10:00:00',0,2,0);
      INSERT INTO products VALUES('p1',1);
      INSERT INTO customer_profiles VALUES('c1','2026-11-15T09:00:00');
      INSERT INTO schedule_state VALUES(1,'{"staff":[{"id":"s1","name":"员工甲"}],"appointments":[{"id":"a1","date":"2026-11-15","staffId":"s1","duration":45},{"id":"a2","date":"2026-11-15","staffId":"s1","duration":30,"status":"cancelled"}]}');
    """)
    con.commit()
    con.close()

    def connect():
        db = sqlite3.connect(path)
        db.row_factory = sqlite3.Row
        return db

    def product_rows(_con):
        return [{"stock": 2, "effective_threshold": 3}]

    def schedule_row(con):
        return con.execute("SELECT payload FROM schedule_state").fetchone()

    return OperationsReportService(connect, product_rows, schedule_row)


def test_operations_report_date_filter_and_aggregation(tmp_path):
    report = make_report_service(tmp_path).get_operations_report("2026-11-15", "2026-11-15")
    assert report["inventory"]["active_products"] == 1
    assert report["inventory"]["low_stock_products"] == 1
    assert report["inventory"]["transactions"][0]["type"] == "入库"
    assert report["appointments"]["count"] == 1
    assert report["appointments"]["service_minutes"] == 45
    assert report["appointments"]["staff"][0]["staff_name"] == "员工甲"
    assert report["customers"] == {"total": 1, "new_in_period": 1}


def test_operations_report_rejects_invalid_period(tmp_path):
    service = make_report_service(tmp_path)
    try:
        service.get_operations_report("2026-11-16", "2026-11-15")
        assert False, "expected validation error"
    except ReportValidationError as exc:
        assert exc.status_code == 400


def test_backup_catalog_lists_and_blocks_traversal(tmp_path):
    folder = tmp_path / "backups"
    folder.mkdir()
    (folder / "one.db").write_bytes(b"db")
    (folder / "one_完整备份.zip").write_bytes(b"zip")
    service = BackupService(lambda: folder, lambda: {"ok": True})
    listed = service.list_backups()
    assert listed[0]["name"] == "one.db"
    assert listed[0]["backup_type"] == "完整备份"
    assert service.resolve_download("one.db") == (folder / "one.db").resolve()
    assert service.resolve_download("../outside.db") is None
    assert service.create_backup() == {"ok": True}


def test_desktop_management_service_uses_injected_adapters(tmp_path):
    calls = []
    service = DesktopManagementService(
        lambda: {"ok": True},
        lambda path: calls.append(path),
        tmp_path / "data",
        tmp_path / "server.log",
    )
    assert service.create_backup() == {"ok": True}
    service.open_data_directory()
    service.open_server_log()
    assert calls == [str(tmp_path / "data"), str(tmp_path / "server.log")]


def test_system_settings_service_persists_config_and_database_audit(tmp_path):
    path = tmp_path / "settings.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT)")
    con.commit()
    con.close()
    saved = []
    audit = []

    def connect():
        return sqlite3.connect(path)

    def save(config):
        saved.append(dict(config))

    def write_audit(connection, *args):
        audit.append(args)
        connection.execute("INSERT INTO settings(key,value) VALUES('audit_marker','1')")

    service = SystemSettingsService(save, connect, write_audit)
    config = {"port": 8123, "https": True}
    assert service.persist(config) == config
    assert saved == [config]
    assert audit and audit[0][0] == "修改系统设置"
    check = sqlite3.connect(path)
    assert check.execute("SELECT value FROM settings WHERE key='port'").fetchone()[0] == "8123"
    assert check.execute("SELECT value FROM settings WHERE key='audit_marker'").fetchone()[0] == "1"
    check.close()


def test_backup_archive_service_creates_verified_full_bundle(tmp_path):
    db_path = tmp_path / "live.db"
    con = sqlite3.connect(db_path)
    con.execute("CREATE TABLE products(id TEXT PRIMARY KEY,image_filename TEXT)")
    con.execute("INSERT INTO products VALUES('p1',NULL)")
    con.execute("CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT)")
    con.commit()
    con.close()

    backup_dir = tmp_path / "backups"
    images_dir = tmp_path / "product_images"
    purchase_dir = tmp_path / "purchase"
    images_dir.mkdir()
    purchase_dir.mkdir()
    (purchase_dir / "purchase-note.txt").write_text("采购记录", encoding="utf-8")
    config_path = tmp_path / "config.json"
    config_path.write_text('{"port":8000}', encoding="utf-8")

    def connect():
        return sqlite3.connect(db_path)

    service = BackupArchiveService(
        connection_factory=connect,
        backup_directory=lambda: backup_dir,
        product_image_directory=images_dir,
        purchase_directory=lambda: purchase_dir,
        config_path=config_path,
        config_provider=lambda: {"backup_keep_days": 30},
        app_name="StoreInventory",
        app_version="1.6.0-dev.11",
        schema_version=8,
        backup_format="store-inventory-backup",
        backup_format_version=1,
    )
    result = service.create_backup()
    assert result["ok"] is True
    assert (backup_dir / result["name"]).is_file()
    bundle = backup_dir / result["bundle_archive"]
    assert bundle.is_file()
    import zipfile
    with zipfile.ZipFile(bundle) as archive:
        assert archive.testzip() is None
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["files"]["data/inventory.db"]["sha256"]
        assert archive.read("data/purchase/purchase-note.txt") == "采购记录".encode()


def test_excel_export_service_builds_filtered_product_workbook(tmp_path):
    service = ExcelExportService(
        product_reader=lambda **kwargs: [{
            "id": "P-1", "barcode": "001", "brand": "品牌", "name": "商品",
            "spec": "规格", "category": "分类", "location": "A-1", "unit": "个",
            "opening": 2, "stock": 3, "effective_threshold": 1, "status": "正常",
        }],
        transaction_reader=lambda **kwargs: [],
        purchase_reader=lambda: [],
        output_directory=lambda kind: tmp_path,
    )
    output = service.export("products", q="商品")
    assert output.is_file()
    from openpyxl import load_workbook
    workbook = load_workbook(output, read_only=False)
    sheet = workbook.active
    assert sheet.title == "商品档案"
    assert sheet.freeze_panes == "A3"
    assert sheet["B3"].value == "001"
    assert sheet.auto_filter.ref.startswith("A2:L")
    workbook.close()


def test_excel_export_service_rejects_unknown_kind(tmp_path):
    service = ExcelExportService(
        product_reader=lambda **kwargs: [], transaction_reader=lambda **kwargs: [],
        purchase_reader=lambda: [], output_directory=lambda kind: tmp_path,
    )
    try:
        service.export("users")
        assert False, "expected export validation error"
    except ExportValidationError as exc:
        assert exc.status_code == 400


def test_backup_restore_service_validates_and_stages_manifest_bundle(tmp_path):
    database = tmp_path / "valid.db"
    con = sqlite3.connect(database)
    con.executescript("""
      CREATE TABLE products(id TEXT);
      CREATE TABLE documents(id TEXT);
      CREATE TABLE transactions(id TEXT);
      CREATE TABLE settings(key TEXT,value TEXT);
      CREATE TABLE meta(key TEXT,value TEXT);
      INSERT INTO meta VALUES('schema_version','8');
    """)
    con.commit()
    con.close()
    config_bytes = json.dumps({"port": 8000, "backup_keep_days": 30, "backup_time": "18:00"}).encode()
    db_bytes = database.read_bytes()
    import hashlib, zipfile, io
    files = {
        "data/inventory.db": db_bytes,
        "data/config.json": config_bytes,
    }
    manifest = {
        "format": "store-inventory-backup", "format_version": 1,
        "files": {name: {"size": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}
                  for name, payload in files.items()},
    }
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
        archive.writestr("manifest.json", json.dumps(manifest).encode())
    service = BackupRestoreService(
        schema_version=8, backup_format="store-inventory-backup", backup_format_version=1,
        default_config={"port": 8000, "backup_keep_days": 30, "backup_time": "18:00",
                        "backup_dir": "data/backups", "purchase_dir": "data/purchase"},
        base_directory=tmp_path, config_file_resolver=lambda value: tmp_path / value,
    )
    stage = tmp_path / "stage"
    result_manifest, extracted = service.read_full_bundle(payload.getvalue(), stage)
    assert result_manifest["format_version"] == 1
    assert extracted["data/inventory.db"].is_file()
    assert extracted["data/config.json"].read_bytes() == files["data/config.json"]


def test_backup_restore_service_rejects_newer_schema_and_unsafe_config(tmp_path):
    db_path = tmp_path / "newer.db"
    con = sqlite3.connect(db_path)
    con.executescript("""
      CREATE TABLE products(id TEXT);
      CREATE TABLE documents(id TEXT);
      CREATE TABLE transactions(id TEXT);
      CREATE TABLE settings(key TEXT,value TEXT);
      CREATE TABLE meta(key TEXT,value TEXT);
      INSERT INTO meta VALUES('schema_version','99');
    """)
    con.commit()
    con.close()
    service = BackupRestoreService(
        schema_version=8, backup_format="store-inventory-backup", backup_format_version=1,
        default_config={"port": 8000, "backup_keep_days": 30, "backup_time": "18:00",
                        "backup_dir": "data/backups", "purchase_dir": "data/purchase"},
        base_directory=tmp_path, config_file_resolver=lambda value: tmp_path / value,
    )
    try:
        service.validate_database(db_path)
        assert False, "expected schema rejection"
    except BackupRestoreError as exc:
        assert "高于当前程序支持版本" in exc.message
    normalized = service.normalize_config({"purchase_dir": "../../../../outside"})
    assert normalized["purchase_dir"] == "data/purchase"
