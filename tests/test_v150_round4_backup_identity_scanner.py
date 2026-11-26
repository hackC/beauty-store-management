# -*- coding: utf-8 -*-
"""Round-4 regression tests for portable backups, Safari photo scanning and identity badges."""
import hashlib
import importlib
import io
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

from application_test_support import unload_test_application


def _isolated_project(source: Path, temp: Path):
    project = temp / "project"
    shutil.copytree(source, project, ignore=shutil.ignore_patterns(
        "__pycache__", ".pytest_cache", "*.pyc", "release", "upgrade_payload", "logs", "build", "dist"
    ))
    old_cwd = Path.cwd()
    sys.path.insert(0, str(project))
    os.chdir(project)
    sys.modules.pop("main", None)
    module = importlib.import_module("main")
    module.DB_PATH = project / "data" / "round4_inventory.db"
    module.CONFIG_PATH = project / "data" / "config.json"
    module.PRODUCT_IMAGE_DIR = project / "data" / "product_images"
    module.PURCHASE_DIR = project / "data" / "purchase"
    module.BACKUP_DIR = project / "data" / "backups"
    module.DATA_DIR = project / "data"
    module.PRODUCT_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    module.PURCHASE_DIR.mkdir(parents=True, exist_ok=True)
    module.CONFIG = {**module.CONFIG, "backup_dir": "data/backups", "purchase_dir": "data/purchase", "port": 8123, "system_name": "Round4 测试门店"}
    module.save_config(module.CONFIG)
    module.init_db()
    return module, project, old_cwd


def _cleanup(project: Path, old_cwd: Path):
    os.chdir(old_cwd)
    sys.path.remove(str(project))
    unload_test_application()


def test_full_backup_bundle_restores_database_config_photos_and_purchase_files():
    source = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="round4_full_backup_") as td:
        main, project, old_cwd = _isolated_project(source, Path(td))
        try:
            admin = TestClient(main.app, base_url="https://testserver")
            assert admin.post("/api/auth/bootstrap", json={
                "username": "manager", "display_name": "店长", "password": "ManagerPass123"
            }).status_code == 200
            created = admin.post("/api/products", json={
                "id": "BUNDLE-001", "barcode": "0691234567890", "name": "完整备份测试商品", "location": "A-01"
            })
            assert created.status_code == 200, created.text
            photo = Image.new("RGB", (800, 400), color=(220, 230, 240))
            photo_bytes = io.BytesIO(); photo.save(photo_bytes, format="JPEG")
            uploaded = admin.post("/api/products/BUNDLE-001/image", files={
                "file": ("item.jpg", photo_bytes.getvalue(), "image/jpeg")
            })
            assert uploaded.status_code == 200, uploaded.text
            image_name = uploaded.json()["image_filename"]
            (main.PURCHASE_DIR / "purchase_note.txt").write_text("采购附件内容", encoding="utf-8")

            result = admin.post("/api/backup")
            assert result.status_code == 200, result.text
            names = result.json()
            assert names["bundle_archive"].endswith("_完整备份.zip")
            downloaded = admin.get("/api/backup/download/" + names["bundle_archive"])
            assert downloaded.status_code == 200
            with zipfile.ZipFile(io.BytesIO(downloaded.content)) as archive:
                assert archive.testzip() is None
                manifest = json.loads(archive.read("manifest.json"))
                assert manifest["format"] == "store-inventory-backup"
                assert manifest["format_version"] == 1
                assert "data/inventory.db" in manifest["files"]
                assert "data/config.json" in manifest["files"]
                assert f"data/product_images/{image_name}" in manifest["files"]
                assert "data/purchase/purchase_note.txt" in manifest["files"]

            # Damage live state, then restore the complete archive.
            main.CONFIG["port"] = 8999
            main.CONFIG["system_name"] = "当前门店"
            main.CONFIG["https"] = False
            main.save_config(main.CONFIG)
            current_config = main.CONFIG_PATH.read_bytes()
            current_layout = {p: p.read_bytes() for p in main.STATIC_DIR.iterdir() if p.is_file()}
            (main.PURCHASE_DIR / "purchase_note.txt").unlink()
            (main.PRODUCT_IMAGE_DIR / image_name).unlink()
            conn = sqlite3.connect(main.DB_PATH)
            try:
                conn.execute("UPDATE products SET name='已被修改' WHERE id='BUNDLE-001'")
                conn.commit()
            finally:
                conn.close()

            restored = admin.post("/api/restore", files={
                "file": (names["bundle_archive"], downloaded.content, "application/zip")
            })
            assert restored.status_code == 200, restored.text
            assert restored.json()["restart_required"] is False
            assert restored.json()["login_required"] is True
            assert admin.get("/api/auth/me").status_code == 401
            assert main.CONFIG["port"] == 8999
            assert main.CONFIG["system_name"] == "当前门店"
            assert main.CONFIG["https"] is False
            assert main.CONFIG_PATH.read_bytes() == current_config
            assert all(p.read_bytes() == contents for p, contents in current_layout.items())
            assert (main.PURCHASE_DIR / "purchase_note.txt").read_text("utf-8") == "采购附件内容"
            assert (main.PRODUCT_IMAGE_DIR / image_name).is_file()
            conn = sqlite3.connect(main.DB_PATH)
            try:
                assert conn.execute("SELECT name FROM products WHERE id='BUNDLE-001'").fetchone()[0] == "完整备份测试商品"
                assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
                assert conn.execute("SELECT value FROM settings WHERE key='port'").fetchone()[0] == "8999"
            finally:
                conn.close()
        finally:
            _cleanup(project, old_cwd)


def test_daily_backup_does_not_treat_legacy_db_only_file_as_complete_backup():
    source = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="round4_daily_backup_") as td:
        main, project, old_cwd = _isolated_project(source, Path(td))
        try:
            from datetime import datetime
            today = datetime.now().strftime("%Y%m%d")
            legacy = main.BACKUP_DIR / f"库存备份_{today}_legacy.db"
            shutil.copy2(main.DB_PATH, legacy)
            result = main.ensure_daily_backup()
            assert result and result.get("bundle_archive")
            assert (main.BACKUP_DIR / result["bundle_archive"]).is_file()
        finally:
            _cleanup(project, old_cwd)


def test_legacy_database_backup_import_remains_supported():
    source = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="round4_legacy_restore_") as td:
        main, project, old_cwd = _isolated_project(source, Path(td))
        try:
            admin = TestClient(main.app, base_url="https://testserver")
            assert admin.post("/api/auth/bootstrap", json={"username":"manager","display_name":"店长","password":"ManagerPass123"}).status_code == 200
            assert admin.post("/api/products", json={"id":"LEGACY-001","barcode":"LEGACY001","name":"旧版恢复商品"}).status_code == 200
            made = admin.post("/api/backup").json()
            legacy_db = admin.get("/api/backup/download/" + made["name"]).content
            conn = sqlite3.connect(main.DB_PATH)
            try:
                conn.execute("UPDATE products SET name='临时改动' WHERE id='LEGACY-001'")
                conn.commit()
            finally:
                conn.close()
            restored = admin.post("/api/restore", files={"file":(made["name"],legacy_db,"application/x-sqlite3")})
            assert restored.status_code == 200, restored.text
            conn = sqlite3.connect(main.DB_PATH)
            try:
                assert conn.execute("SELECT name FROM products WHERE id='LEGACY-001'").fetchone()[0] == "旧版恢复商品"
            finally:
                conn.close()
        finally:
            _cleanup(project, old_cwd)


def test_full_backup_rejects_tampered_file_and_unsafe_paths():
    source = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="round4_bad_bundle_") as td:
        main, project, old_cwd = _isolated_project(source, Path(td))
        try:
            admin = TestClient(main.app, base_url="https://testserver")
            admin.post("/api/auth/bootstrap", json={"username":"manager","display_name":"店长","password":"ManagerPass123"})
            made = admin.post("/api/backup").json()
            bundle = admin.get("/api/backup/download/" + made["bundle_archive"]).content
            with zipfile.ZipFile(io.BytesIO(bundle)) as z:
                entries = {n: z.read(n) for n in z.namelist()}
            entries["data/config.json"] += b" "
            bad_io = io.BytesIO()
            with zipfile.ZipFile(bad_io, "w", compression=zipfile.ZIP_DEFLATED) as z:
                for name, data in entries.items(): z.writestr(name, data)
            response = admin.post("/api/restore", files={"file":("tampered.zip",bad_io.getvalue(),"application/zip")})
            assert response.status_code == 400
            assert "校验失败" in response.json()["detail"]

            unsafe_io = io.BytesIO()
            with zipfile.ZipFile(unsafe_io, "w", compression=zipfile.ZIP_DEFLATED) as z:
                for name, data in entries.items(): z.writestr(name, data)
                z.writestr("../outside.txt", b"nope")
            unsafe = admin.post("/api/restore", files={"file":("unsafe.zip",unsafe_io.getvalue(),"application/zip")})
            assert unsafe.status_code == 400
            assert "不安全" in unsafe.json()["detail"]

            # Even a hash-valid old configuration pointing at the application cannot select a target.
            manifest = json.loads(entries["manifest.json"])
            cfg = json.loads(entries["data/config.json"])
            cfg["purchase_dir"] = "static"
            cfg["port"] = "old obsolete setting"
            cfg_bytes = json.dumps(cfg, ensure_ascii=False, indent=2).encode("utf-8")
            entries["data/config.json"] = cfg_bytes
            manifest["files"]["data/config.json"] = {"size":len(cfg_bytes),"sha256":hashlib.sha256(cfg_bytes).hexdigest()}
            entries["manifest.json"] = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
            safe_io = io.BytesIO()
            with zipfile.ZipFile(safe_io, "w", compression=zipfile.ZIP_DEFLATED) as z:
                for name, data in entries.items(): z.writestr(name, data)
            main.CONFIG["purchase_dir"] = "data/current-purchase"
            main.save_config(main.CONFIG)
            safe_restore = admin.post("/api/restore", files={"file":("unsafe-config.zip",safe_io.getvalue(),"application/zip")})
            assert safe_restore.status_code == 200, safe_restore.text
            assert main.CONFIG["purchase_dir"] == "data/current-purchase"
            assert json.loads(main.CONFIG_PATH.read_text("utf-8"))["purchase_dir"] == "data/current-purchase"
            assert (main.STATIC_DIR / "index.html").is_file()
            assert (main.DATA_DIR / "current-purchase").is_dir()
        finally:
            _cleanup(project, old_cwd)


def test_mobile_identity_and_ios_photo_barcode_fallback_are_present():
    root = Path(__file__).resolve().parents[1]
    inventory = (root / "static" / "index.html").read_text("utf-8")
    schedule = (root / "static" / "index_V2_5_5.html").read_text("utf-8")
    main = (root / "main.py").read_text("utf-8")
    assert 'class="muted user-badge"' in inventory
    assert '.top-right .user-badge{display:inline-flex!important' in inventory
    assert 'id="scheduleUserBadge"' in schedule
    assert 'id="scheduleUserBadge"' in schedule
    assert 'scheduleUser.display_name || scheduleUser.username' in schedule
    assert "scheduleUser.role === 'admin' ?" in schedule
    assert 'capture="environment"' in inventory and 'id="barcodePhotoInput"' in inventory
    assert 'function decodeEANCanvas(canvas, allowRotate = true, options = {})' in inventory
    assert 'function decodeEANFromRuns(runs)' in inventory
    assert 'function eanRunsFromGray(gray, mode=0)' in inventory
    assert 'AUTO_ASSIGN_BALANCE_WINDOW = 10' in schedule
    assert 'ScheduleGroup.solve' in schedule
    assert 'getStaffDayLoad' in schedule
    assert 'EAN-13 / EAN-8 / UPC-A' in inventory
    assert 'BACKUP_FORMAT_VERSION = 1' in main
    assert 'data/config.json' in main and 'bundle_archive' in main
    assert 'endswith((".db", ".zip"))' in main
