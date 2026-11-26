# -*- coding: utf-8 -*-
"""First-login activation and user-owned password setup regression tests."""
import importlib
import os
from pathlib import Path
import shutil
import sys
import tempfile
import re
import pytest
from starlette.websockets import WebSocketDisconnect

from fastapi.testclient import TestClient

from application_test_support import unload_test_application


def _load_isolated_main(project):
    sys.path.insert(0, str(project))
    os.chdir(project)
    sys.modules.pop("main", None)
    main = importlib.import_module("main")
    main.DB_PATH = project / "data" / "first_login_test.db"
    main.init_db()
    return main


def test_new_user_uses_single_use_activation_code_then_sets_own_password():
    source = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="store_first_login_") as td:
        project = Path(td) / "project"
        shutil.copytree(source, project, ignore=shutil.ignore_patterns(
            "__pycache__", ".pytest_cache", "*.pyc", "release", "upgrade_payload", "logs"
        ))
        old_cwd = os.getcwd()
        main = None
        try:
            main = _load_isolated_main(project)
            admin = TestClient(main.app)
            bootstrap = admin.post("/api/auth/bootstrap", json={
                "username": "manager", "display_name": "店长", "password": "ManagerPass123"
            })
            assert bootstrap.status_code == 200, bootstrap.text
            assert admin.get("/api/auth/me").json()["must_set_password"] is False

            created = admin.post("/api/users", json={
                "username": "newstaff", "display_name": "新员工"
            })
            assert created.status_code == 200, created.text
            payload = created.json()
            assert payload["must_set_password"] is True
            assert re.fullmatch(r"\d{8}", payload["activation_code"])
            user_id = payload["id"]

            listed = {x["id"]: x for x in admin.get("/api/users").json()}[user_id]
            assert listed["must_set_password"] is True

            staff = TestClient(main.app)
            login = staff.post("/api/auth/login", json={
                "username": "newstaff", "activation_code": payload["activation_code"]
            })
            assert login.status_code == 200, login.text
            assert login.json()["must_set_password"] is True
            assert staff.get("/api/auth/me").json()["must_set_password"] is True

            # The activation code is single-use, and pending users cannot access business APIs.
            replay = TestClient(main.app).post("/api/auth/login", json={
                "username": "newstaff", "activation_code": payload["activation_code"]
            })
            assert replay.status_code == 401
            blocked = staff.get("/api/products")
            assert blocked.status_code == 403
            assert "先设置登录密码" in blocked.json()["detail"]
            with pytest.raises(WebSocketDisconnect) as ws_error:
                with staff.websocket_connect("/ws"):
                    pass
            assert ws_error.value.code == 4403

            set_password = staff.post("/api/auth/set-first-password", json={
                "password": "MyOwnPass123"
            })
            assert set_password.status_code == 200, set_password.text
            assert staff.get("/api/auth/me").json()["must_set_password"] is False

            # Once setup is complete, only the chosen password works.
            old_code_login = TestClient(main.app).post("/api/auth/login", json={
                "username": "newstaff", "activation_code": payload["activation_code"]
            })
            assert old_code_login.status_code == 401
            normal_login = TestClient(main.app).post("/api/auth/login", json={
                "username": "newstaff", "password": "MyOwnPass123"
            })
            assert normal_login.status_code == 200, normal_login.text
        finally:
            os.chdir(old_cwd)
            if str(project) in sys.path:
                sys.path.remove(str(project))
            unload_test_application()


def test_admin_can_regenerate_lost_first_login_code_and_legacy_password_create_still_works():
    source = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="store_first_login_regen_") as td:
        project = Path(td) / "project"
        shutil.copytree(source, project, ignore=shutil.ignore_patterns(
            "__pycache__", ".pytest_cache", "*.pyc", "release", "upgrade_payload", "logs"
        ))
        old_cwd = os.getcwd()
        try:
            main = _load_isolated_main(project)
            admin = TestClient(main.app)
            assert admin.post("/api/auth/bootstrap", json={
                "username": "manager", "display_name": "店长", "password": "ManagerPass123"
            }).status_code == 200

            pending = admin.post("/api/users", json={
                "username": "pending", "display_name": "待激活员工"
            })
            assert pending.status_code == 200
            user_id = pending.json()["id"]
            old_code = pending.json()["activation_code"]

            regenerated = admin.post(f"/api/users/{user_id}/activation-code", json={})
            assert regenerated.status_code == 200, regenerated.text
            new_code = regenerated.json()["activation_code"]
            assert new_code != old_code
            assert TestClient(main.app).post("/api/auth/login", json={
                "username": "pending", "activation_code": old_code
            }).status_code == 401
            assert TestClient(main.app).post("/api/auth/login", json={
                "username": "pending", "activation_code": new_code
            }).status_code == 200

            # Existing API clients that still submit an initial password remain compatible.
            legacy = admin.post("/api/users", json={
                "username": "legacy", "display_name": "兼容账号", "password": "LegacyPass123"
            })
            assert legacy.status_code == 200
            assert legacy.json()["must_set_password"] is False
            assert "activation_code" not in legacy.json()
            assert TestClient(main.app).post("/api/auth/login", json={
                "username": "legacy", "password": "LegacyPass123"
            }).status_code == 200
        finally:
            os.chdir(old_cwd)
            if str(project) in sys.path:
                sys.path.remove(str(project))
            unload_test_application()


def test_existing_database_accounts_default_to_no_forced_password_change():
    import sqlite3

    source = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="store_first_login_migration_") as td:
        project = Path(td) / "project"
        shutil.copytree(source, project, ignore=shutil.ignore_patterns(
            "__pycache__", ".pytest_cache", "*.pyc", "release", "upgrade_payload", "logs"
        ))
        old_cwd = os.getcwd()
        try:
            sys.path.insert(0, str(project))
            os.chdir(project)
            sys.modules.pop("main", None)
            main = importlib.import_module("main")
            legacy_db = project / "data" / "legacy_accounts.db"
            con = sqlite3.connect(legacy_db)
            con.executescript("""
                CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                INSERT INTO meta VALUES('schema_version','7');
                CREATE TABLE users(
                    id TEXT PRIMARY KEY,
                    username TEXT NOT NULL UNIQUE,
                    display_name TEXT NOT NULL,
                    salt TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'staff',
                    permissions TEXT NOT NULL DEFAULT '{}',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                INSERT INTO users(
                    id,username,display_name,salt,password_hash,role,permissions,enabled,created_at,updated_at
                ) VALUES(
                    'existing-id','existing','旧员工','00112233445566778899aabbccddeeff',
                    'existing-hash','staff','{}',1,'2026-01-01','2026-01-01'
                );
            """)
            con.commit()
            con.close()

            main.DB_PATH = legacy_db
            main.init_db()
            con = main.db()
            columns = {row[1] for row in con.execute("PRAGMA table_info(users)").fetchall()}
            assert {"must_set_password", "activation_salt", "activation_code_hash"} <= columns
            row = con.execute(
                "SELECT must_set_password FROM users WHERE id='existing-id'"
            ).fetchone()
            assert row["must_set_password"] == 0
            assert con.execute(
                "SELECT value FROM meta WHERE key='schema_version'"
            ).fetchone()[0] == "8"
            con.close()
            assert list((legacy_db.parent / "backups").glob("pre_migration_*.db"))
        finally:
            os.chdir(old_cwd)
            if str(project) in sys.path:
                sys.path.remove(str(project))
            unload_test_application()


def test_frontend_forces_first_password_and_does_not_ask_admin_for_initial_password():
    root = Path(__file__).resolve().parents[1]
    html = (root / "static" / "index.html").read_text(encoding="utf-8")
    assert 'id="loginActivationCode"' not in html
    assert 'JSON.stringify({username, password, cookie_probe:check.probe, remember_browser:' in html
    assert 'id="firstPasswordPane"' in html
    assert 'onclick="doSetFirstPassword()"' in html
    assert 'function doSetFirstPassword()' in html
    assert 'currentUser.must_set_password' in html
    assert 'id="userPassword"' not in html
    assert 'created.activation_code' in html
    assert 'regenerateActivationCode' in html


def test_admin_can_directly_reset_password_for_a_pending_user():
    source = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="store_first_login_admin_reset_") as td:
        project = Path(td) / "project"
        shutil.copytree(source, project, ignore=shutil.ignore_patterns(
            "__pycache__", ".pytest_cache", "*.pyc", "release", "upgrade_payload", "logs"
        ))
        old_cwd = os.getcwd()
        try:
            main = _load_isolated_main(project)
            admin = TestClient(main.app)
            assert admin.post("/api/auth/bootstrap", json={
                "username": "manager", "display_name": "店长", "password": "ManagerPass123"
            }).status_code == 200
            created = admin.post("/api/users", json={
                "username": "pending_reset", "display_name": "待重置"
            })
            assert created.status_code == 200
            user_id = created.json()["id"]
            reset = admin.post(f"/api/users/{user_id}/password", json={
                "password": "AdminReset123"
            })
            assert reset.status_code == 200, reset.text
            listed = {x["id"]: x for x in admin.get("/api/users").json()}[user_id]
            assert listed["must_set_password"] is False
            assert TestClient(main.app).post("/api/auth/login", json={
                "username": "pending_reset", "password": "AdminReset123"
            }).status_code == 200
        finally:
            os.chdir(old_cwd)
            if str(project) in sys.path:
                sys.path.remove(str(project))
            unload_test_application()
