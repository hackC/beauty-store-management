# -*- coding: utf-8 -*-
"""数据库迁移前备份与事务回滚测试。"""
import hashlib
import importlib
import pathlib
import shutil
import sqlite3
import sys
import tempfile
from legacy_database_fixture import write_legacy_database

from application_test_support import unload_test_application


def _load_isolated_main(source, project):
    shutil.copytree(
        source,
        project,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache", "build", "dist", "release", "logs"),
    )
    old_cwd = pathlib.Path.cwd()
    import os
    os.chdir(project)
    sys.path.insert(0, str(project))
    sys.modules.pop("main", None)
    module = importlib.import_module("main")
    return module, old_cwd


def _cleanup_isolated_main(project, old_cwd):
    import os
    os.chdir(old_cwd)
    try:
        sys.path.remove(str(project))
    except ValueError:
        pass
    unload_test_application()


def test_schema_migration_creates_verified_backup_and_records_version():
    source = pathlib.Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="v150_schema_backup_") as td:
        project = pathlib.Path(td) / "project"
        main, old_cwd = _load_isolated_main(source, project)
        target = pathlib.Path(td) / "runtime" / "inventory.db"
        target.parent.mkdir(parents=True)
        write_legacy_database(target, 85)
        main.DB_PATH = target

        try:
            main.init_db()
            backups = list((target.parent / "backups").glob("pre_migration_*.db"))
            assert len(backups) == 1
            backup = backups[0]

            backup_con = sqlite3.connect(f"file:{backup}?mode=ro", uri=True)
            try:
                assert backup_con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
                assert backup_con.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 85
                assert "schedule_state" not in {
                    row[0] for row in backup_con.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
            finally:
                backup_con.close()

            con = sqlite3.connect(f"file:{target}?mode=ro", uri=True)
            try:
                assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
                assert con.execute(
                    "SELECT value FROM meta WHERE key='schema_version'"
                ).fetchone()[0] == str(main.DB_SCHEMA_VERSION)
                assert con.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
                assert con.execute(
                    "SELECT COUNT(*) FROM audit_logs WHERE action='database_schema_migration'"
                ).fetchone()[0] == 1
            finally:
                con.close()

            main.init_db()
            assert len(list((target.parent / "backups").glob("pre_migration_*.db"))) == 1
        finally:
            _cleanup_isolated_main(project, old_cwd)


def test_failed_schema_migration_rolls_back_and_keeps_pre_migration_backup():
    source = pathlib.Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="v150_schema_rollback_") as td:
        project = pathlib.Path(td) / "project"
        main, old_cwd = _load_isolated_main(source, project)
        target = pathlib.Path(td) / "runtime" / "inventory.db"
        target.parent.mkdir(parents=True)
        con = sqlite3.connect(target)
        con.executescript(
            """
            CREATE TABLE products(
                id TEXT PRIMARY KEY,
                barcode TEXT,
                name TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            INSERT INTO products VALUES('A','DUPLICATE','商品A','t','t');
            INSERT INTO products VALUES('B','DUPLICATE','商品B','t','t');
            """
        )
        con.commit()
        con.close()
        main.DB_PATH = target

        try:
            try:
                main.init_db()
                assert False, "duplicate non-empty barcodes must block unique-index migration"
            except sqlite3.IntegrityError:
                pass

            backups = list((target.parent / "backups").glob("pre_migration_*.db"))
            assert len(backups) == 1
            backup_con = sqlite3.connect(f"file:{backups[0]}?mode=ro", uri=True)
            try:
                assert backup_con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
                assert backup_con.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 2
                assert backup_con.execute(
                    "SELECT COUNT(*) FROM products WHERE barcode='DUPLICATE'"
                ).fetchone()[0] == 2
                assert {
                    row[0] for row in backup_con.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                } == {"products"}
            finally:
                backup_con.close()

            con = sqlite3.connect(f"file:{target}?mode=ro", uri=True)
            try:
                tables = {
                    row[0] for row in con.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
                assert tables == {"products"}
                assert con.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 2
                assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            finally:
                con.close()
        finally:
            _cleanup_isolated_main(project, old_cwd)



def test_backup_failure_aborts_migration_without_touching_database():
    source = pathlib.Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="v150_backup_failure_") as td:
        project = pathlib.Path(td) / "project"
        main, old_cwd = _load_isolated_main(source, project)
        target = pathlib.Path(td) / "runtime" / "inventory.db"
        target.parent.mkdir(parents=True)
        write_legacy_database(target, 85)
        original_hash = hashlib.sha256(target.read_bytes()).hexdigest()
        # Prevent creation of the required backup directory.
        (target.parent / "backups").write_text("not a directory", encoding="utf-8")
        main.DB_PATH = target

        try:
            try:
                main.init_db()
                assert False, "migration must stop if the pre-migration backup cannot be created"
            except OSError:
                pass

            assert hashlib.sha256(target.read_bytes()).hexdigest() == original_hash
            con = sqlite3.connect(f"file:{target}?mode=ro", uri=True)
            try:
                tables = {
                    row[0] for row in con.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
                assert "schedule_state" not in tables
                assert "schema_version" not in {
                    row[0] for row in con.execute("SELECT key FROM meta")
                }
                assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            finally:
                con.close()
        finally:
            _cleanup_isolated_main(project, old_cwd)



def test_newer_database_schema_is_rejected_without_downgrade():
    source = pathlib.Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="v150_schema_newer_") as td:
        project = pathlib.Path(td) / "project"
        main, old_cwd = _load_isolated_main(source, project)
        target = pathlib.Path(td) / "runtime" / "inventory.db"
        target.parent.mkdir(parents=True)
        con = sqlite3.connect(target)
        con.executescript(
            """
            CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO meta VALUES('schema_version','9');
            """
        )
        con.commit()
        con.close()
        original_hash = hashlib.sha256(target.read_bytes()).hexdigest()
        main.DB_PATH = target

        try:
            try:
                main.init_db()
                assert False, "a newer schema must be rejected"
            except RuntimeError as error:
                assert "高于当前程序支持版本" in str(error)

            assert hashlib.sha256(target.read_bytes()).hexdigest() == original_hash
            assert not (target.parent / "backups").exists()
            con = sqlite3.connect(f"file:{target}?mode=ro", uri=True)
            try:
                assert con.execute(
                    "SELECT value FROM meta WHERE key='schema_version'"
                ).fetchone()[0] == "9"
                assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            finally:
                con.close()
        finally:
            _cleanup_isolated_main(project, old_cwd)
