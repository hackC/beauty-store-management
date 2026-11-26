# -*- coding: utf-8 -*-
"""Round-3 safeguards: group-only staff, stored product photos, and photo backups."""
import hashlib
import importlib
import io
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


def test_product_photo_upload_crop_read_permissions_and_paired_backup():
    source = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="round3_media_") as td:
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
            main.DB_PATH = project / "data" / "round3_test.db"
            main.init_db()
            admin = TestClient(main.app, base_url="https://testserver")
            assert admin.post("/api/auth/bootstrap", json={
                "username": "manager", "display_name": "管理员", "password": "ManagerPass123"
            }).status_code == 200
            created = admin.post("/api/products", json={
                "id": "PHOTO-001", "barcode": "0000Photo001", "name": "图片商品", "location": "A-01"
            })
            assert created.status_code == 200, created.text
            empty = admin.get("/api/products?q=0000Photo001").json()
            assert len(empty) == 1 and empty[0]["image_filename"] == ""
            missing = admin.get("/api/products/PHOTO-001/image")
            assert missing.status_code == 404
            invalid = admin.post("/api/products/PHOTO-001/image", files={
                "file": ("evil.jpg", b"not-an-image", "image/jpeg")
            })
            assert invalid.status_code == 400, invalid.text
            too_large = admin.post("/api/products/PHOTO-001/image", files={
                "file": ("massive.jpg", b"B" * (6 * 1024 * 1024 + 100), "image/jpeg")
            })
            assert too_large.status_code == 413
            photo = Image.new("RGB", (1100, 500), color=(245, 245, 245))
            out = io.BytesIO()
            photo.save(out, format="JPEG", quality=90)
            before_hash = hashlib.sha256(main.DB_PATH.read_bytes()).hexdigest()
            up = admin.post("/api/products/PHOTO-001/image", files={
                "file": ("photo.jpg", out.getvalue(), "image/jpeg")
            })
            assert up.status_code == 200, up.text
            saved = admin.get("/api/products?q=PHOTO-001").json()[0]
            assert saved["image_filename"] == up.json()["image_filename"]
            assert len(saved["image_filename"]) == 36
            returned = admin.get("/api/products/PHOTO-001/image")
            assert returned.status_code == 200, returned.text
            assert returned.headers["content-type"] == "image/jpeg"
            with Image.open(io.BytesIO(returned.content)) as decoded:
                assert decoded.size == (640, 640)
                assert decoded.mode == "RGB"
            assert before_hash != hashlib.sha256(main.DB_PATH.read_bytes()).hexdigest()
            admin_photo_file = main.PRODUCT_IMAGE_DIR / saved["image_filename"]
            assert admin_photo_file.exists()
            # Product photos must propagate through inventory summary and history APIs.
            conn = sqlite3.connect(main.DB_PATH)
            try:
                conn.execute("INSERT INTO documents(id,type,created_at) VALUES('photo-document','入库',datetime('now'))")
                conn.execute("INSERT INTO transactions(id,doc_id,time,type,doc_no,product_id,product_name,qty_in) VALUES('photo-transaction','photo-document',datetime('now'),'入库','photo-document','PHOTO-001','图片商品',3)")
                conn.commit()
            finally:
                conn.close()
            tx = admin.get("/api/transactions").json()
            assert any(row["id"] == "photo-transaction" and row["image_filename"] == saved["image_filename"] for row in tx)
            recent = admin.get("/api/summary").json()["recent"]
            assert any(row["id"] == "photo-transaction" and row["image_filename"] == saved["image_filename"] for row in recent)
            # Registration does not issue personal rights even to an old-style client.
            user_created = admin.post("/api/users", json={
                "username": "newstaff", "display_name": "员工", "password": "StaffPass12345",
                "permissions": {"products_manage": True, "backup_restore": True}
            })
            assert user_created.status_code == 200
            staff = TestClient(main.app, base_url="https://testserver")
            assert staff.post("/api/auth/login", json={"username":"newstaff", "password":"StaffPass12345"}).status_code == 200
            assert not any(staff.get("/api/auth/me").json()["permissions"].values())
            assert staff.get("/api/products/PHOTO-001/image").status_code == 403
            assert staff.post("/api/products/PHOTO-001/image", files={
                "file": ("photo.jpg", out.getvalue(), "image/jpeg")
            }).status_code == 403
            assert admin.patch(f"/api/users/{user_created.json()['id']}", json={
                "permissions": {"products_view":True}
            }).status_code == 400
            rule = admin.post("/api/permission-groups", json={
                "name": "查看商品图片", "permissions": {"products_view":True}
            }).json()
            assert admin.put(f"/api/permission-groups/{rule['id']}/members/{user_created.json()['id']}").status_code == 200
            assert staff.get("/api/products/PHOTO-001/image").status_code == 200
            assert staff.post("/api/products/PHOTO-001/image", files={
                "file": ("photo.jpg", out.getvalue(), "image/jpeg")
            }).status_code == 403
            assert admin.delete(f"/api/permission-groups/{rule['id']}/members/{user_created.json()['id']}").status_code == 200
            assert staff.get("/api/products/PHOTO-001/image").status_code == 403
            # A DB snapshot without its image archive is not a complete photo backup.
            result = admin.post("/api/backup")
            assert result.status_code == 200, result.text
            names = result.json()
            assert names["images_archive"].endswith("_images.zip")
            archive = admin.get("/api/backup/download/" + names["images_archive"])
            assert archive.status_code == 200
            with zipfile.ZipFile(io.BytesIO(archive.content)) as z:
                assert z.testzip() is None
                assert "product_images/" + saved["image_filename"] in z.namelist()
                assert z.read("product_images/" + saved["image_filename"]) == returned.content
            con = sqlite3.connect(main.DB_PATH)
            try:
                assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
                assert con.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == "8"
            finally:
                con.close()
        finally:
            os.chdir(old_cwd)
            sys.path.remove(str(project))
            unload_test_application()


def test_round3_frontend_mobile_contracts():
    root = Path(__file__).resolve().parents[1]
    inv = (root / "static/index.html").read_text("utf-8")
    sched = (root / "static/index_V2_5_5.html").read_text("utf-8")
    assert 'id="floatingHome"' not in inv and "home-float" not in inv
    assert 'class="schedule-home-float"' not in sched and "schedule-home-float" not in sched
    assert '.modal-back {z-index:110!important;}' in sched
    assert 'id="productPhotoCamera"' in inv
    assert 'capture="environment"' in inv
    assert 'document.createElement("canvas")' in inv
    assert '640' in inv
    assert 'id="imageViewerModal"' in inv
    assert 'class="product-photo placeholder' in inv
    assert 'id="userGroupChoices"' in inv
    assert 'data-user-group' in inv
    assert 'configureCameraFocus' in inv
    assert 'focusMode' in inv
    assert 'chooseOpProduct(target, product.id);' in inv
