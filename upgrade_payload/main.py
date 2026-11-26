
# -*- coding: utf-8 -*-
"""
门店管理系统 V1.6.0-dev.12 全站双端界面精修检查点
FastAPI + SQLite + Tkinter
Windows LAN Server
"""

from __future__ import annotations

import os
import asyncio
import faulthandler
import sys
import json
import socket
import threading
import signal
import webbrowser
import logging
from logging.handlers import RotatingFileHandler
import shutil
import time
import ssl
import hashlib
import hmac
import secrets
import uuid
import ipaddress
import re
import zipfile
import sqlite3
import tempfile
from pathlib import PurePosixPath
from contextlib import closing, contextmanager, nullcontext
from queue import Empty, SimpleQueue
from PIL import Image, ImageOps, UnidentifiedImageError

from pathlib import Path
from datetime import datetime, date, timedelta

import tkinter as tk
from tkinter import ttk, messagebox, filedialog

from fastapi import (
    FastAPI,
    HTTPException,
    WebSocket,
    WebSocketDisconnect,
    UploadFile,
    File,
    Request,
    Query,
    Response,
)
from starlette.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
import uvicorn

from store_inventory.permissions.composition import create_permission_service
from store_inventory.identity.composition import create_identity_service
from store_inventory.identity.domain import AuditActor as IdentityAuditActor, IdentityError
from store_inventory.authorization.composition import create_permission_group_service
from store_inventory.authorization.domain import AuditActor as AuthorizationAuditActor, AuthorizationError
from store_inventory.catalog.composition import create_catalog_service
from store_inventory.catalog.domain import CatalogError
from store_inventory.inventory.composition import create_inventory_service
from store_inventory.inventory.domain import InventoryError
from store_inventory.procurement.composition import create_procurement_service
from store_inventory.platform.audit import AuditActor as BusinessAuditActor, SQLiteAuditWriter
from store_inventory.platform.operations import PlatformOperations
from store_inventory.platform.configuration import JsonConfigStore
from store_inventory.platform.reports import OperationsReportService, ReportValidationError
from store_inventory.platform.backups import BackupService
from store_inventory.platform.backup_archive import BackupArchiveService, copy_sqlite_snapshot
from store_inventory.platform.backup_restore import BackupRestoreService, BackupRestoreError
from store_inventory.platform.desktop import DesktopManagementService
from store_inventory.platform.desktop_theme import configure_console
from store_inventory.platform.settings import SystemSettingsService
from store_inventory.platform.exports import ExcelExportService, ExportValidationError, export_definition
from store_inventory.platform.export_templates import ExcelExportTemplates
from store_inventory.scheduling.permissions import MODULES as SCHEDULING_PERMISSION_MODULES
from store_inventory.scheduling.policy import SchedulingError
from store_inventory.scheduling.rules import _validate_time
from store_inventory.scheduling.sqlite import SQLiteSchedulingRepository, _schedule_row, _schedule_decode
from store_inventory.customers.sqlite import SQLiteCustomersRepository, sync_from_appointments
from store_inventory.waitlist.sqlite import SQLiteWaitlistRepository
from store_inventory.scheduling.repository import SchedulingRepository, CustomerRepository, WaitlistRepository
from store_inventory.permissions.presets import group_presets
from store_inventory.permissions.defaults import DEFAULT_STAFF_PERMISSIONS, GROUP_FORBIDDEN_PERMISSIONS
from store_inventory.permissions.http import PermissionCheckIn, create_permission_router, require_permissions


# ============================================================
# 路径与配置
# ============================================================

from store_inventory.runtime.lifecycle import Lifecycle, ServerStop
from store_inventory.runtime.local_control import LocalControl
from store_inventory.runtime.health import Endpoint, LoopbackProbe, HealthMonitor
from store_inventory.runtime.network import discover_lan_ip
from store_inventory.runtime.windows_shutdown import subscribe_shutdown

APP_NAME = "门店管理系统"
APP_VERSION = "1.6.0-rc.18"

if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent
    RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", BASE_DIR))
else:
    BASE_DIR = Path(__file__).resolve().parent
    RESOURCE_DIR = BASE_DIR

DATA_DIR = BASE_DIR / "data"
BACKUP_DIR = DATA_DIR / "backups"
PURCHASE_DIR = DATA_DIR / "purchase"
PRODUCT_IMAGE_DIR = DATA_DIR / "product_images"
LOG_DIR = BASE_DIR / "logs"
STATIC_DIR = RESOURCE_DIR / "static"

for folder in (DATA_DIR, BACKUP_DIR, PURCHASE_DIR, PRODUCT_IMAGE_DIR, LOG_DIR):
    folder.mkdir(parents=True, exist_ok=True)

DB_PATH = DATA_DIR / "inventory.db"
CONFIG_PATH = DATA_DIR / "config.json"

# Increment only when a database schema migration is introduced.
DB_SCHEMA_VERSION = 8

_server_log_handler = RotatingFileHandler(
    LOG_DIR / "server.log", maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
)
logging.basicConfig(
    handlers=[_server_log_handler],
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    force=True,
)

DEFAULT_CONFIG = {
    "port": 8000,
    "domain": "ku.home.arpa",
    "access_ip": "",
    "https": True,
    "ssl_certfile": "",
    "ssl_keyfile": "",
    "auto_backup": True,
    "backup_time": "18:00",
    "backup_keep_days": 30,
    "backup_dir": str(Path("data") / "backups"),
    "purchase_dir": str(Path("data") / "purchase"),
    "auto_start": False,
    "default_threshold": 5,
    "system_name": APP_NAME,
}


CONFIG_STORE = JsonConfigStore(CONFIG_PATH, DEFAULT_CONFIG, logging)


def save_config(config: dict):
    """Compatibility facade; JSON persistence belongs to platform.configuration."""
    CONFIG_STORE.save(config)


def load_config():
    """Compatibility facade; defaults and recovery belong to platform.configuration."""
    return CONFIG_STORE.load()


CONFIG = load_config()
CONFIG_LOCK = threading.RLock()


def update_runtime_config(changes):
    global CONFIG
    with CONFIG_LOCK:
        # A desktop window can coexist with the headless instance. Preserve
        # directory changes that instance saved after this window was opened.
        saved = load_config()
        protected = {key: saved[key] for key in ("backup_dir", "purchase_dir")}
        updated = {**CONFIG, **protected, **changes}
        save_config(updated)
        CONFIG = updated


def configured_dir(key: str, fallback: Path) -> Path:
    """将相对配置路径稳定解析到 EXE/项目目录，避免依赖启动时工作目录。"""
    raw = CONFIG.get(key)
    target = Path(str(raw)) if raw else fallback
    if not target.is_absolute():
        target = BASE_DIR / target
    return target.resolve()


def resolve_config_file(raw: str | None) -> Path | None:
    if not raw:
        return None
    target = Path(str(raw))
    if not target.is_absolute():
        target = BASE_DIR / target
    return target.resolve()


def configured_file(key: str) -> Path | None:
    return resolve_config_file(CONFIG.get(key))


# ============================================================
# 数据库
# ============================================================

APP_LIFECYCLE = Lifecycle(lambda name: logging.exception("Owned worker failed: %s", name))
SERVER_STOP = ServerStop()
APP_LIFECYCLE.add_target(SERVER_STOP)
SERVER_INSTANCE_ID = uuid.uuid4().hex
ACTIVE_ENDPOINT = None
_shutdown_complete = False
_shutdown_stacks_captured = False
_shutdown_lock = threading.Lock()
_restore_state = threading.Lock()
_active_database_requests = 0
_restoring = False
_restore_recovery_required = False
_background_database_work = threading.RLock()


def db(database_path=None):
    con = sqlite3.connect(database_path if database_path is not None else DB_PATH, timeout=20)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA busy_timeout=20000")
    return con


_REQUIRED_DB_TABLES = {
    "products", "documents", "transactions", "settings", "audit_logs", "meta", "inventory_receipts",
    "users", "user_sessions", "schedule_state", "schedule_staff_users",
    "schedule_transfer_history", "app_notifications", "device_presence",
    "customer_profiles", "schedule_waitlist", "schedule_requests",
    "schedule_reminder_log", "permission_groups", "permission_group_members",
}
_REQUIRED_DB_COLUMNS = {
    "products": {"barcode", "image_filename"},
    "audit_logs": {
        "actor_user_id", "actor_name", "entity_type", "entity_id",
        "ip_address", "before_json", "after_json",
    },
    "schedule_requests": {"target_staff_id"},
}


def database_schema_needs_migration() -> bool:
    """Read-only schema preflight; never creates or modifies the database."""
    if not DB_PATH.exists() or DB_PATH.stat().st_size == 0:
        return False

    uri = DB_PATH.resolve().as_uri() + "?mode=ro"
    con = sqlite3.connect(uri, uri=True, timeout=20)
    try:
        tables = {
            row[0] for row in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }

        # Check the recorded version before attempting to fill in missing
        # structures; a newer database must never be downgraded by this build.
        if "meta" in tables:
            version_row = con.execute(
                "SELECT value FROM meta WHERE key='schema_version'"
            ).fetchone()
            if version_row:
                try:
                    recorded_version = int(version_row[0])
                except (TypeError, ValueError):
                    recorded_version = 0
                if recorded_version > DB_SCHEMA_VERSION:
                    raise RuntimeError(
                        f"数据库结构版本 {recorded_version} 高于当前程序支持版本 "
                        f"{DB_SCHEMA_VERSION}，为避免降级破坏数据，已停止启动。"
                    )

        if not _REQUIRED_DB_TABLES.issubset(tables):
            return True

        for table, required_columns in _REQUIRED_DB_COLUMNS.items():
            columns = {
                row[1] for row in con.execute(f'PRAGMA table_info("{table}")')
            }
            if not required_columns.issubset(columns):
                return True

        barcode_index = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='index' "
            "AND name='ux_products_barcode_nonempty'"
        ).fetchone()
        if not barcode_index:
            return True

        version_row = con.execute(
            "SELECT value FROM meta WHERE key='schema_version'"
        ).fetchone()
        if not version_row:
            return True

        try:
            current_version = int(version_row[0])
        except (TypeError, ValueError):
            return True

        if current_version > DB_SCHEMA_VERSION:
            raise RuntimeError(
                f"数据库结构版本 {current_version} 高于当前程序支持版本 "
                f"{DB_SCHEMA_VERSION}，为避免降级破坏数据，已停止启动。"
            )
        if current_version < DB_SCHEMA_VERSION:
            return True

        setting_keys = {
            row[0] for row in con.execute("SELECT key FROM settings")
        }
        if any(key not in setting_keys for key in CONFIG):
            return True

        return False
    finally:
        con.close()


def backup_database_before_migration() -> Path | None:
    """Create and verify an SQLite online backup before any schema write."""
    if not database_schema_needs_migration():
        return None

    if not DB_PATH.exists() or DB_PATH.stat().st_size == 0:
        return None

    backup_dir = DB_PATH.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    backup_path = backup_dir / f"pre_migration_{stamp}.db"

    try:
        with closing(sqlite3.connect(DB_PATH, timeout=30)) as source:
            with closing(sqlite3.connect(backup_path, timeout=30)) as destination:
                copy_sqlite_snapshot(source, destination)
                destination.commit()
    except Exception:
        logging.exception("Database pre-migration backup failed; migration aborted.")
        try:
            backup_path.unlink(missing_ok=True)
        except Exception:
            logging.exception("Could not remove incomplete pre-migration backup.")
        raise

    check = None
    try:
        check = sqlite3.connect(
            backup_path.resolve().as_uri() + "?mode=ro",
            uri=True,
            timeout=20,
        )
        integrity = check.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(
                f"迁移前备份完整性检查失败：{integrity}；已中止数据库迁移。"
            )
    except Exception:
        backup_path.unlink(missing_ok=True)
        raise
    finally:
        if check is not None:
            check.close()

    logging.warning(
        "Verified pre-migration database backup created: %s",
        backup_path,
    )
    return backup_path


def init_db(database_path=None):
    # A restore migrates its upload in isolation. The upload itself is the original.
    pre_migration_backup = backup_database_before_migration() if database_path is None else None
    con = db() if database_path is None else db(database_path)
    try:
        con.executescript(
            """
            BEGIN IMMEDIATE;
        CREATE TABLE IF NOT EXISTS products(
            id TEXT PRIMARY KEY,
            brand TEXT DEFAULT '',
            barcode TEXT DEFAULT '',
            name TEXT NOT NULL,
            spec TEXT DEFAULT '',
            category TEXT DEFAULT '',
            location TEXT DEFAULT '',
            image_filename TEXT NOT NULL DEFAULT '',
            unit TEXT DEFAULT '',
            opening REAL NOT NULL DEFAULT 0,
            threshold REAL,
            status TEXT NOT NULL DEFAULT '正常',
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS documents(
            id TEXT PRIMARY KEY,
            type TEXT NOT NULL,
            external_no TEXT DEFAULT '',
            operator TEXT DEFAULT '',
            note TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            request_id TEXT UNIQUE
        );

        CREATE TABLE IF NOT EXISTS inventory_receipts(
            request_id TEXT PRIMARY KEY,
            fingerprint TEXT NOT NULL,
            response TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS transactions(
            id TEXT PRIMARY KEY,
            doc_id TEXT NOT NULL,
            time TEXT NOT NULL,
            type TEXT NOT NULL,
            doc_no TEXT NOT NULL,
            product_id TEXT NOT NULL,
            product_name TEXT NOT NULL,
            qty_in REAL NOT NULL DEFAULT 0,
            qty_out REAL NOT NULL DEFAULT 0,
            qty_adjust REAL NOT NULL DEFAULT 0,
            operator TEXT DEFAULT '',
            note TEXT DEFAULT '',
            actual_count REAL,
            FOREIGN KEY(doc_id) REFERENCES documents(id),
            FOREIGN KEY(product_id) REFERENCES products(id)
        );

        CREATE INDEX IF NOT EXISTS idx_tx_time
        ON transactions(time);

        CREATE INDEX IF NOT EXISTS idx_tx_product
        ON transactions(product_id);

        CREATE INDEX IF NOT EXISTS idx_tx_doc
        ON transactions(doc_id);

        CREATE TABLE IF NOT EXISTS settings(
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS audit_logs(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            time TEXT NOT NULL,
            action TEXT NOT NULL,
            detail TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS meta(
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS users(
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

        CREATE TABLE IF NOT EXISTS user_sessions(
            token_hash TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_user_sessions_user
        ON user_sessions(user_id);

        -- Permission groups are additive; existing direct staff permissions are preserved.
        CREATE TABLE IF NOT EXISTS permission_groups(
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            permissions TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS permission_group_members(
            group_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY(group_id, user_id),
            FOREIGN KEY(group_id) REFERENCES permission_groups(id) ON DELETE CASCADE,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_permission_group_members_user
        ON permission_group_members(user_id);

        CREATE TABLE IF NOT EXISTS schedule_state(
            id INTEGER PRIMARY KEY CHECK(id=1),
            payload TEXT NOT NULL,
            revision INTEGER NOT NULL DEFAULT 1,
            updated_at TEXT NOT NULL,
            updated_by TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS schedule_staff_users(
            user_id TEXT PRIMARY KEY,
            staff_id TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS schedule_transfer_history(
            id TEXT PRIMARY KEY,
            appointment_id TEXT NOT NULL,
            group_id TEXT DEFAULT '',
            appointment_date TEXT NOT NULL,
            source_staff_id TEXT NOT NULL,
            target_staff_id TEXT NOT NULL,
            segment_start TEXT NOT NULL,
            segment_end TEXT NOT NULL,
            action_by TEXT NOT NULL,
            created_at TEXT NOT NULL,
            detail_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS idx_schedule_transfer_date
        ON schedule_transfer_history(appointment_date, created_at);

        CREATE TABLE IF NOT EXISTS app_notifications(
            id TEXT PRIMARY KEY,
            recipient_user_id TEXT,
            kind TEXT NOT NULL DEFAULT 'system',
            title TEXT NOT NULL,
            body TEXT NOT NULL DEFAULT '',
            target_url TEXT NOT NULL DEFAULT '',
            source_key TEXT UNIQUE,
            created_at TEXT NOT NULL,
            read_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_notifications_recipient
        ON app_notifications(recipient_user_id, created_at DESC);

        CREATE TABLE IF NOT EXISTS device_presence(
            device_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            device_name TEXT NOT NULL DEFAULT '',
            platform TEXT NOT NULL DEFAULT '',
            ip_address TEXT NOT NULL DEFAULT '',
            first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_device_presence_last_seen
        ON device_presence(last_seen DESC);

        CREATE TABLE IF NOT EXISTS customer_profiles(
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            phone_tail TEXT NOT NULL DEFAULT '',
            notes TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_customer_profiles_name
        ON customer_profiles(name);
        CREATE INDEX IF NOT EXISTS idx_customer_profiles_phone
        ON customer_profiles(phone_tail);

        CREATE TABLE IF NOT EXISTS schedule_waitlist(
            id TEXT PRIMARY KEY,
            customer_name TEXT NOT NULL,
            phone_tail TEXT NOT NULL DEFAULT '',
            service_id TEXT NOT NULL DEFAULT '',
            preferred_date TEXT NOT NULL DEFAULT '',
            preferred_time TEXT NOT NULL DEFAULT '',
            notes TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'waiting',
            created_by TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_schedule_waitlist_status
        ON schedule_waitlist(status, preferred_date);

        CREATE TABLE IF NOT EXISTS schedule_requests(
            id TEXT PRIMARY KEY,
            request_type TEXT NOT NULL,
            staff_id TEXT NOT NULL,
            target_staff_id TEXT NOT NULL DEFAULT '',
            user_id TEXT NOT NULL,
            request_date TEXT NOT NULL,
            start_time TEXT NOT NULL DEFAULT '',
            end_time TEXT NOT NULL DEFAULT '',
            details TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'pending',
            reviewed_by TEXT NOT NULL DEFAULT '',
            reviewed_at TEXT,
            review_note TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_schedule_requests_status
        ON schedule_requests(status, created_at DESC);

        CREATE TABLE IF NOT EXISTS schedule_reminder_log(
            appointment_id TEXT NOT NULL,
            reminder_type TEXT NOT NULL,
            appointment_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY(appointment_id, reminder_type)
        );
        """
        )

        # 首次登录自主设置密码：旧账号默认无需重新激活。
        user_columns = {row[1] for row in con.execute("PRAGMA table_info(users)").fetchall()}
        for column, definition in {
            "must_set_password": "INTEGER NOT NULL DEFAULT 0",
            "activation_salt": "TEXT NOT NULL DEFAULT ''",
            "activation_code_hash": "TEXT NOT NULL DEFAULT ''",
        }.items():
            if column not in user_columns:
                con.execute(f"ALTER TABLE users ADD COLUMN {column} {definition}")

        request_columns = {row[1] for row in con.execute("PRAGMA table_info(schedule_requests)").fetchall()}
        if "target_staff_id" not in request_columns:
            con.execute("ALTER TABLE schedule_requests ADD COLUMN target_staff_id TEXT NOT NULL DEFAULT ''")

        # 审计日志字段只增量扩展，兼容旧数据库。
        audit_columns = {row[1] for row in con.execute("PRAGMA table_info(audit_logs)").fetchall()}
        for column, definition in {
            "actor_user_id": "TEXT NOT NULL DEFAULT ''",
            "actor_name": "TEXT NOT NULL DEFAULT ''",
            "entity_type": "TEXT NOT NULL DEFAULT ''",
            "entity_id": "TEXT NOT NULL DEFAULT ''",
            "ip_address": "TEXT NOT NULL DEFAULT ''",
            "before_json": "TEXT NOT NULL DEFAULT ''",
            "after_json": "TEXT NOT NULL DEFAULT ''",
        }.items():
            if column not in audit_columns:
                con.execute(f"ALTER TABLE audit_logs ADD COLUMN {column} {definition}")

        # 向旧数据库做幂等、只增量的结构迁移；不重写商品数据。
        product_columns = {
            row[1] for row in con.execute("PRAGMA table_info(products)").fetchall()
        }
        if "barcode" not in product_columns:
            con.execute("ALTER TABLE products ADD COLUMN barcode TEXT DEFAULT ''")
        if "image_filename" not in product_columns:
            con.execute("ALTER TABLE products ADD COLUMN image_filename TEXT NOT NULL DEFAULT ''")
        # 空条码可重复；非空条码唯一。保留原始字符串，不做 trim/补零/格式化。
        con.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS ux_products_barcode_nonempty
            ON products(barcode)
            WHERE barcode IS NOT NULL AND barcode <> ''
        """)

        for key, value in CONFIG.items():
            con.execute(
                """
                INSERT OR IGNORE INTO settings(key,value)
                VALUES(?,?)
                """,
                (key, json.dumps(value, ensure_ascii=False)),
            )

        con.execute(
            """
            INSERT OR IGNORE INTO meta(key,value)
            VALUES('revision','1')
            """
        )

        con.execute(
            """
            INSERT INTO meta(key,value) VALUES('schema_version',?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """,
            (str(DB_SCHEMA_VERSION),),
        )
        if pre_migration_backup:
            con.execute(
                """
                INSERT INTO audit_logs(time,action,detail)
                VALUES(?,?,?)
                """,
                (
                    datetime.now().isoformat(timespec="seconds"),
                    "database_schema_migration",
                    f"schema_version={DB_SCHEMA_VERSION}; backup={pre_migration_backup.name}",
                ),
            )
        con.commit()
    except Exception:
        con.rollback()
        logging.exception(
            "Database migration failed and transaction was rolled back. Backup=%s",
            pre_migration_backup,
        )
        raise
    finally:
        con.close()

    logging.info(
        "Database initialization completed; schema_version=%s; pre_migration_backup=%s",
        DB_SCHEMA_VERSION,
        pre_migration_backup,
    )


# ============================================================
# 用户、密码与会话
# ============================================================

# Composition root: dependencies flow into the permission service here.
# Resolve db lazily so configured/restored database paths remain effective.
PERMISSION_SERVICE = create_permission_service(lambda: db())
INVENTORY_PERMISSION_KEYS = {}
INVENTORY_PERMISSION_KEYS["catalog"] = PERMISSION_SERVICE.register_module("catalog", "商品档案", {
    "read": "查看商品档案", "create": "新增商品", "update": "编辑商品",
    "disable": "停用或删除商品", "image_read": "查看商品图片",
    "image_update": "上传或替换商品图片", "export": "导出商品档案",
})
INVENTORY_PERMISSION_KEYS["inventory"] = PERMISSION_SERVICE.register_module("inventory", "库存业务", {
    "read": "查看库存及预警", "inbound_create": "提交入库单",
    "outbound_create": "提交出库单", "stocktake_adjust": "盘点差异调整",
})
INVENTORY_PERMISSION_KEYS["transactions"] = PERMISSION_SERVICE.register_module("transactions", "库存流水", {
    "read": "查看库存流水", "export": "导出库存流水",
})
INVENTORY_PERMISSION_KEYS["procurement"] = PERMISSION_SERVICE.register_module("procurement", "采购管理", {
    "read": "查看采购建议", "export": "导出采购清单",
})
for namespace, (label, actions) in SCHEDULING_PERMISSION_MODULES.items():
    PERMISSION_SERVICE.register_module(namespace, label, actions)
EXTENSION_PERMISSIONS = PERMISSION_SERVICE.registry
PERMISSION_LABELS = PERMISSION_SERVICE.permission_labels
# Identity and permission-group use cases are composed here; route handlers remain
# thin HTTP adapters while persistence and policy live behind module ports.
IDENTITY_SERVICE = create_identity_service(lambda: db(), PERMISSION_SERVICE)
AUTHORIZATION_SERVICE = create_permission_group_service(lambda: db(), PERMISSION_SERVICE)
AUDIT_WRITER = SQLiteAuditWriter()
SCHEDULING_SERVICE: SchedulingRepository = SQLiteSchedulingRepository(lambda: db(), AUDIT_WRITER, sync_from_appointments)
CUSTOMERS_SERVICE: CustomerRepository = SQLiteCustomersRepository(lambda: db(), AUDIT_WRITER)
WAITLIST_SERVICE: WaitlistRepository = SQLiteWaitlistRepository(lambda: db(), AUDIT_WRITER)
PLATFORM_OPERATIONS = PlatformOperations(lambda: db())
OPERATIONS_REPORT_SERVICE = OperationsReportService(
    lambda: db(), lambda connection: product_rows(connection), lambda connection: _schedule_row(connection)
)

# Extension registrations live in trusted backend code, not writable client state.
# They are intentionally additive: legacy permission keys remain unchanged.
def register_permission_module(namespace: str, label: str, actions: dict[str, str],
                               *, admin_only: tuple[str, ...] = ()) -> tuple[str, ...]:
    """dev.5 compatibility facade; new modules receive ModulePermissions."""
    return PERMISSION_SERVICE.register_module(namespace, label, actions, admin_only=admin_only)

def register_permission_route(namespace: str, method: str, path: str,
                              actions: tuple[str, ...] | list[str] | str,
                              *, mode: str = 'all') -> None:
    PERMISSION_SERVICE.register_route(namespace, method, path, actions, mode=mode)

def permission_granted(user: dict | None, key: str) -> bool:
    return PERMISSION_SERVICE.granted(user, key)

def require_permission(request: Request, *keys: str, mode: str = 'all') -> None:
    require_permissions(PERMISSION_SERVICE, request, *keys, mode=mode)


class BootstrapIn(BaseModel):
    username: str = Field(min_length=3, max_length=40)
    display_name: str = Field(min_length=1, max_length=40)
    password: str = Field(min_length=8, max_length=128)


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=40)
    password: str = Field(default="", max_length=128)
    activation_code: str = Field(default="", max_length=32)
    remember_browser: bool = False
    cookie_probe: str | None = Field(default=None, min_length=32, max_length=64)


class FirstPasswordSetIn(BaseModel):
    password: str = Field(min_length=8, max_length=128)


class ActivationCodeIn(BaseModel):
    activation_code: str | None = Field(default=None, max_length=8)
    use_default_activation_code: bool = False


class ActivationDefaultIn(BaseModel):
    activation_code: str | None = Field(max_length=8)


class UserCreateIn(ActivationCodeIn):
    username: str = Field(min_length=3, max_length=40)
    display_name: str = Field(min_length=1, max_length=40)
    # New accounts normally use a one-time activation code and set their own password.
    # Optional password remains for backward compatibility with older API clients.
    password: str | None = Field(default=None, min_length=8, max_length=128)
    # Legacy clients may still supply permissions; they are intentionally ignored.
    permissions: dict[str, bool] = Field(default_factory=dict)
    group_ids: list[str] = Field(default_factory=list)


class UserUpdateIn(BaseModel):
    display_name: str | None = None
    permissions: dict[str, bool] | None = None
    enabled: bool | None = None


class PasswordResetIn(BaseModel):
    password: str = Field(min_length=8, max_length=128)


class PermissionGroupIn(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    permissions: dict[str, bool] = Field(default_factory=dict)


def make_password(password: str):
    """Compatibility facade; password hashing is owned by the identity module."""
    return IDENTITY_SERVICE.make_password(password)


def verify_password(password: str, salt: str, saved_hash: str):
    """Compatibility facade for existing account-management code."""
    return IDENTITY_SERVICE.verify_password(password, salt, saved_hash)


def group_rights_for_user(user_id: str) -> tuple[dict[str, bool], list[str]]:
    return PERMISSION_SERVICE.group_rights(user_id)


def public_user(row):
    return IDENTITY_SERVICE.public_user(dict(row))


def user_from_token(token: str | None):
    return IDENTITY_SERVICE.resolve_session(token)


def _set_session_cookie(response: Response, token: str, *, secure: bool | None = None, max_age: int | None = 14 * 86400):
    response.set_cookie(
        key="inventory_session", value=token, max_age=max_age,
        httponly=True, secure=bool(CONFIG.get("https", False)) if secure is None else secure,
        samesite="strict", path="/",
    )


def issue_session(user_id: str, response: Response):
    """Compatibility facade; raw tokens are only returned to the cookie adapter."""
    _set_session_cookie(response, IDENTITY_SERVICE.issue_session(user_id))


def required_permissions(method: str, path: str):
    return PERMISSION_SERVICE.required_permissions(method, path)

PUBLIC_PATHS = {
    "/",
    "/api/health",
    "/api/health/live",
    "/api/health/ready",
    "/api/auth/status",
    "/api/auth/cookie-probe",
    "/api/auth/bootstrap",
    "/api/auth/login",
    "/api/auth/logout",
}


# ============================================================
# FastAPI 初始化
# ============================================================

app = FastAPI(
    title=APP_NAME,
    version=APP_VERSION,
)

app.mount(
    "/static",
    StaticFiles(directory=str(STATIC_DIR), check_dir=False),
    name="static",
)


@app.middleware("http")
async def authentication_middleware(request: Request, call_next):
    global _active_database_requests
    path = request.url.path
    if not path.startswith("/api/") or path == "/api/health/live":
        return await _authenticate_request(request, call_next)

    with _restore_state:
        if SERVER_STOP.requested.is_set() or APP_LIFECYCLE.stop_event.is_set():
            return JSONResponse(status_code=503, content={"detail": "服务正在停止"})
        if _restore_recovery_required:
            return JSONResponse(status_code=503, content={"detail": "备份恢复回滚未完成，请检查 server.log"})
        if _restoring:
            return JSONResponse(status_code=503, headers={"Retry-After": "3"},
                                content={"detail": "正在恢复门店数据，请稍后重试"})
        _active_database_requests += 1
    try:
        return await _authenticate_request(request, call_next)
    finally:
        with _restore_state:
            _active_database_requests -= 1


async def _authenticate_request(request: Request, call_next):
    path = request.url.path

    if path in PUBLIC_PATHS:
        return await call_next(request)

    if not path.startswith("/api/"):
        return await call_next(request)

    user = await run_in_threadpool(user_from_token,
        request.cookies.get("inventory_session")
    )

    if not user:
        return JSONResponse(
            status_code=401,
            content={"detail": "请先登录或登录状态已过期"},
        )

    request.state.user = user

    if path == "/api/auth/me":
        return await call_next(request)

    if path == "/api/auth/set-first-password":
        if not user.get("must_set_password"):
            return JSONResponse(status_code=409, content={"detail": "当前账号已完成首次密码设置"})
        return await call_next(request)

    if user.get("must_set_password"):
        return JSONResponse(
            status_code=403,
            content={"detail": "首次登录请先设置登录密码"},
        )

    decision = await run_in_threadpool(PERMISSION_SERVICE.authorize_route, user, request.method, path)
    if not decision.allowed:
        return JSONResponse(status_code=403, content={"detail": decision.detail})
    response = await call_next(request)
    if path.startswith(('/api/schedule/', '/api/customers', '/api/export/')):
        response.headers['Cache-Control'] = 'no-store, private'
    return response


def _permission_catalog() -> dict:
    return PERMISSION_SERVICE.catalog()


app.include_router(create_permission_router(PERMISSION_SERVICE))


@app.get("/api/auth/status")
def auth_status(request: Request, response: Response):
    response.headers["Cache-Control"] = "no-store"
    # 只要曾经创建过账号，就不能再次公开初始化管理员
    count = IDENTITY_SERVICE.count_users()

    token = request.cookies.get("inventory_session")
    user = user_from_token(token)
    return {
        "initialized": count > 0,
        "authenticated": user is not None,
        "session_cookie_received": bool(token),
        "user": user,
    }


@app.post("/api/auth/bootstrap")
def auth_bootstrap(data: BootstrapIn, response: Response, request: Request):
    try:
        token = IDENTITY_SERVICE.bootstrap_admin(
            data.username, data.display_name, data.password
        )
    except IdentityError as exc:
        raise HTTPException(exc.status_code, exc.message) from exc
    _set_session_cookie(response, token, secure=request.url.scheme == "https")
    logging.info("Initial administrator created: %s", data.username)
    return {"ok": True, "message": "管理员创建成功"}


class SessionPolicyIn(BaseModel):
    days: int | None = Field(default=None, ge=1, le=3650)


def _remembered_session_days():
    with closing(db()) as con:
        row = con.execute("SELECT value FROM settings WHERE key='remembered_session_days'").fetchone()
    return json.loads(row[0]) if row else None


@app.get("/api/users/session-policy")
def get_session_policy():
    return {"days": _remembered_session_days()}


@app.put("/api/users/session-policy")
def update_session_policy(data: SessionPolicyIn):
    with closing(db()) as con, con:
        con.execute("""INSERT INTO settings (key, value) VALUES ('remembered_session_days', ?)
                       ON CONFLICT(key) DO UPDATE SET value=excluded.value""", (json.dumps(data.days),))
    return {"days": data.days}


@app.post("/api/auth/cookie-probe")
def auth_cookie_probe(request: Request, response: Response):
    probe = secrets.token_urlsafe(32)
    response.headers["Cache-Control"] = "no-store"
    response.set_cookie("inventory_cookie_probe", probe, max_age=120, httponly=True,
                        secure=request.url.scheme == "https", samesite="strict", path="/")
    return {"probe": probe}


@app.post("/api/auth/login")
def auth_login(data: LoginIn, response: Response, request: Request):
    response.headers["Cache-Control"] = "no-store"
    secure = request.url.scheme == "https"
    if data.cookie_probe is not None:
        received = request.cookies.get("inventory_cookie_probe", "")
        if not received or not secrets.compare_digest(received.encode(), data.cookie_probe.encode()):
            raise HTTPException(400, "此浏览器没有送回本站 Cookie，本次未验证账号或首次登录码。请允许本站 Cookie，并用当前地址在普通浏览器中重试。",
                                headers={"Cache-Control": "no-store"})
        response.delete_cookie("inventory_cookie_probe", path="/", httponly=True,
                               secure=secure, samesite="strict")
    remember = data.remember_browser and secure
    days = _remembered_session_days() if remember else 14
    try:
        result = IDENTITY_SERVICE.login(
            data.username, data.password, data.activation_code, session_days=days
        )
    except IdentityError as exc:
        raise HTTPException(exc.status_code, exc.message) from exc
    cookie_age = min(days or 365, 365) * 86400 if remember and not result.must_set_password else None
    _set_session_cookie(response, result.token, secure=secure, max_age=cookie_age)
    logging.info("User login: %s; first_login=%s", data.username, result.must_set_password)
    return {
        "ok": True,
        "message": "首次登录验证成功，请设置登录密码" if result.must_set_password else "登录成功",
        "must_set_password": result.must_set_password,
        "remembered": bool(cookie_age),
        "username": data.username.strip(),
    }


@app.post("/api/auth/logout")
def auth_logout(request: Request, response: Response):
    IDENTITY_SERVICE.logout(request.cookies.get("inventory_session"))
    response.delete_cookie(
        "inventory_session", path="/", httponly=True, samesite="strict"
    )
    return {"ok": True}


@app.get("/api/auth/me")
def auth_me(request: Request):
    return request.state.user


@app.post("/api/auth/set-first-password")
def auth_set_first_password(data: FirstPasswordSetIn, request: Request):
    user = request.state.user
    actor = IdentityAuditActor(
        user_id=str(user.get("id", "")),
        display_name=str(user.get("display_name", "")),
        ip_address=request.client.host if request.client else "",
    )
    try:
        IDENTITY_SERVICE.set_first_password(
            user, request.cookies.get("inventory_session"), data.password, actor
        )
    except IdentityError as exc:
        raise HTTPException(exc.status_code, exc.message) from exc
    except Exception as exc:
        logging.exception("First-login password setup failed")
        raise HTTPException(500, "设置登录密码失败，请稍后重试") from exc
    return {"ok": True, "message": "登录密码设置成功"}


@app.get("/api/users")
def list_users():
    return IDENTITY_SERVICE.list_users()


def _identity_actor(request: Request) -> IdentityAuditActor:
    user = getattr(request.state, "user", {}) or {}
    return IdentityAuditActor(
        user_id=str(user.get("id", "")),
        display_name=str(user.get("display_name", "")),
        ip_address=request.client.host if request.client else "",
    )


@app.post("/api/users")
def create_user(data: UserCreateIn, request: Request):
    valid_keys = set(PERMISSION_LABELS)
    if set(data.permissions) - valid_keys:
        raise HTTPException(400, "包含未知权限项")
    if len(data.group_ids) != len(set(data.group_ids)):
        raise HTTPException(400, "权限规则组不能重复")
    try:
        return IDENTITY_SERVICE.create_staff(
            data.username, data.display_name, data.password, data.group_ids,
            _identity_actor(request), activation_code=data.activation_code,
            use_default=data.use_default_activation_code,
        )
    except IdentityError as exc:
        raise HTTPException(exc.status_code, exc.message) from exc


@app.get("/api/users/activation-default")
def activation_default_status():
    return {"configured": IDENTITY_SERVICE.has_activation_default()}


@app.put("/api/users/activation-default")
def set_activation_default(data: ActivationDefaultIn):
    try:
        IDENTITY_SERVICE.set_activation_default(data.activation_code)
    except IdentityError as exc:
        raise HTTPException(exc.status_code, exc.message) from exc
    return {"configured": data.activation_code is not None}


@app.patch("/api/users/{user_id}")
def update_user(user_id: str, data: UserUpdateIn, request: Request):
    try:
        IDENTITY_SERVICE.update_staff(
            user_id, data.display_name, data.enabled, data.permissions,
            _identity_actor(request),
        )
    except IdentityError as exc:
        raise HTTPException(exc.status_code, exc.message) from exc
    return {"ok": True}


@app.post("/api/users/{user_id}/activation-code")
def regenerate_user_activation_code(user_id: str, request: Request, data: ActivationCodeIn | None = None):
    data = data or ActivationCodeIn()
    try:
        activation_code = IDENTITY_SERVICE.regenerate_activation_code(
            user_id, _identity_actor(request), activation_code=data.activation_code,
            use_default=data.use_default_activation_code,
        )
    except IdentityError as exc:
        raise HTTPException(exc.status_code, exc.message) from exc
    return {"ok": True, "activation_code": activation_code,
            "used_default_activation_code": data.use_default_activation_code}


@app.post("/api/users/{user_id}/password")
def reset_user_password(user_id: str, data: PasswordResetIn, request: Request):
    try:
        IDENTITY_SERVICE.reset_password(user_id, data.password, _identity_actor(request))
    except IdentityError as exc:
        raise HTTPException(exc.status_code, exc.message) from exc
    return {"ok": True}


# ============================================================
# 统一消息、审计、在线设备与客户/排班申请模型
# ============================================================

class DeviceHeartbeatIn(BaseModel):
    device_id: str = Field(min_length=8, max_length=100)
    device_name: str = Field(default="", max_length=120)
    platform: str = Field(default="", max_length=80)

class CustomerIn(BaseModel):
    id: str = ""
    name: str = Field(min_length=1, max_length=120)
    phone_tail: str = Field(default="", max_length=11)
    notes: str = Field(default="", max_length=4000)

class WaitlistIn(BaseModel):
    customer_name: str = Field(min_length=1, max_length=120)
    phone_tail: str = Field(default="", max_length=11)
    service_id: str = ""
    preferred_date: str = ""
    preferred_time: str = ""
    notes: str = Field(default="", max_length=2000)

class ScheduleRequestIn(BaseModel):
    request_type: str = Field(pattern="^(休息申请|换班申请)$")
    staff_id: str = Field(min_length=1, max_length=100)
    target_staff_id: str = Field(default="", max_length=100)
    request_date: str = Field(min_length=10, max_length=10)
    start_time: str = ""
    end_time: str = ""
    details: str = Field(default="", max_length=2000)

class ScheduleRequestReviewIn(BaseModel):
    status: str = Field(pattern="^(approved|rejected)$")
    review_note: str = Field(default="", max_length=2000)


def write_audit(con, action, detail="", request=None, entity_type="", entity_id="", before=None, after=None):
    user = getattr(getattr(request, "state", None), "user", None) if request else None
    actor = BusinessAuditActor(
        user_id=str(user.get("id", "")) if user else "",
        display_name=str(user.get("display_name", "")) if user else "",
        ip_address=request.client.host if request and request.client else "",
        username=str(user.get("username", "")) if user else "",
    )
    AUDIT_WRITER.write(con, action, detail, actor, entity_type, entity_id, before, after)


def create_notification(con, title, body="", kind="system", recipient_user_id=None, target_url="", source_key=None):
    notification_id = uuid.uuid4().hex
    con.execute("""
        INSERT OR IGNORE INTO app_notifications(id,recipient_user_id,kind,title,body,target_url,source_key,created_at)
        VALUES(?,?,?,?,?,?,?,?)
    """, (notification_id, recipient_user_id, kind, title, body, target_url, source_key,
          datetime.now().isoformat(timespec="seconds")))
    return notification_id


# ============================================================
# 商品与库存模型
# ============================================================

class ProductIn(BaseModel):
    id: str = ""
    brand: str = ""
    barcode: str | None = ""
    name: str
    spec: str = ""
    category: str = ""
    location: str = ""
    unit: str = ""
    opening: float = 0
    threshold: float | None = None
    active: bool = True


class ItemIn(BaseModel):
    product_id: str
    quantity: float = Field(ge=0)
    note: str = ""


class SubmitIn(BaseModel):
    type: str
    external_no: str = ""
    operator: str = ""
    note: str = ""
    request_id: str = Field(default="", max_length=128)
    items: list[ItemIn]


class SettingsIn(BaseModel):
    port: int = Field(default=8000, ge=1024, le=65535)
    domain: str = "ku.home.arpa"
    access_ip: str = ""
    https: bool = True
    ssl_certfile: str = ""
    ssl_keyfile: str = ""
    auto_backup: bool = True
    backup_time: str = "18:00"
    backup_keep_days: int = Field(default=30, ge=1, le=365)
    backup_dir: str = ""
    purchase_dir: str = ""
    default_threshold: float = Field(default=5, ge=0)


def current_stock(con, product_id):
    """Compatibility facade; stock calculations are owned by inventory."""
    return INVENTORY_SERVICE.current_stock(product_id)


def product_rows(con=None, search="", status="全部", category="全部"):
    """Compatibility read facade; product and stock projections are composed from catalog/inventory."""
    return CATALOG_SERVICE.list_products(search, status, category)


class WSManager:
    def __init__(self):
        self.clients = set()
        self.lock = threading.Lock()

    async def connect(self, ws):
        await ws.accept()
        with self.lock:
            self.clients.add(ws)

    def disconnect(self, ws):
        with self.lock:
            self.clients.discard(ws)

    async def broadcast(self, payload):
        with self.lock:
            clients = list(self.clients)

        async def notify(ws):
            try:
                permissions = {
                    "inventory": ("ext.inventory.read", "ext.inventory.inbound_create",
                                  "ext.inventory.outbound_create", "ext.inventory.stocktake_adjust",
                                  "ext.transactions.read", "ext.procurement.read", "dashboard"),
                    "products": ("ext.catalog.read", "ext.inventory.read", "ext.procurement.read"),
                    "schedule": ("ext.scheduling.read", "ext.appointments.read", "ext.appointments.read_self"),
                }.get(payload.get("type"), ())
                user = await run_in_threadpool(user_from_token, ws.cookies.get("inventory_session"))
                if not user or user.get("must_set_password"):
                    self.disconnect(ws)
                    await ws.close(code=4401)
                    return
                if not any(permission_granted(user, key) for key in permissions):
                    return
                await asyncio.wait_for(ws.send_json(payload), timeout=1.5)
            except TimeoutError:
                self.disconnect(ws)
                logging.warning("WebSocket revision notification timed out")
            except Exception:
                self.disconnect(ws)

        await asyncio.gather(*(notify(ws) for ws in clients))


wsmanager = WSManager()


def bump_revision():
    with closing(db()) as con, con:
        con.execute("UPDATE meta SET value=CAST(value AS INTEGER)+1 WHERE key='revision'")
        return int(con.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0])


# ============================================================
# 基础 API
# ============================================================

@app.get("/", response_class=HTMLResponse)
def home(request: Request, page: str = ""):
    filename = "index.html"
    if page == "schedule":
        user = user_from_token(request.cookies.get("inventory_session"))
        if user and not user.get("must_set_password") and any(
            permission_granted(user, key)
            for key in ("ext.scheduling.read", "ext.appointments.read", "ext.appointments.read_self")
        ):
            filename = "index_V2_5_5.html"
    page_file = STATIC_DIR / filename
    if not page_file.is_file():
        raise HTTPException(500, f"前端文件 static/{filename} 不存在")
    return FileResponse(page_file, headers={"Cache-Control": "no-store", "Vary": "Cookie"})


@app.get("/schedule", include_in_schema=False)
def schedule_home():
    return RedirectResponse("/?page=schedule", status_code=302,
                            headers={"Cache-Control": "no-store"})


@app.get("/api/system/identity")
def system_identity():
    return {"system_name": str(CONFIG.get("system_name") or APP_NAME), "app_name": APP_NAME, "version": APP_VERSION}


@app.get("/api/health/live")
async def health_live(response: Response):
    """Lightweight process liveness probe; never waits for SQLite or business locks."""
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return {
        "ok": True,
        "version": APP_VERSION,
        "instance_id": SERVER_INSTANCE_ID,
        "time": datetime.now().isoformat(timespec="seconds"),
    }


@app.get("/api/health")
def health():
    # Keep the legacy database-aware health response for existing integrations.
    con = db()
    try:
        revision_row = con.execute(
            "SELECT value FROM meta WHERE key='revision'"
        ).fetchone()
    finally:
        con.close()

    return {
        "ok": True,
        "version": APP_VERSION,
        "time": datetime.now().isoformat(timespec="seconds"),
        "revision": int(revision_row[0]) if revision_row else 0,
    }


@app.get("/api/health/ready")
def health_ready():
    if APP_LIFECYCLE.stop_event.is_set():
        return JSONResponse(status_code=503, content={"ok": False, "code": "stopping"})
    try:
        # A dedicated bounded read; never opens/creates a missing business database.
        con = sqlite3.connect(DB_PATH.resolve().as_uri() + "?mode=ro", uri=True, timeout=0.5)
        try:
            con.execute("SELECT value FROM meta WHERE key='revision'").fetchone()
        finally:
            con.close()
        health_ready.last_error = None
        return {"ok": True, "version": APP_VERSION, "instance_id": SERVER_INSTANCE_ID}
    except Exception as exc:
        # Health monitor logs transitions; retain the underlying DB cause locally.
        signature = (type(exc).__name__, str(exc))
        if getattr(health_ready, "last_error", None) != signature:
            logging.exception("Database readiness failed")
            health_ready.last_error = signature
        return JSONResponse(status_code=503, content={"ok": False, "code": "database_not_ready"})


@app.get("/api/summary")
def summary():
    con = db()
    products = product_rows(con)
    today = date.today().isoformat()

    docs = con.execute(
        """
        SELECT COUNT(*) AS n
        FROM documents
        WHERE substr(created_at,1,10)=?
        """,
        (today,),
    ).fetchone()["n"]

    lines = con.execute(
        """
        SELECT COUNT(*) AS n
        FROM transactions
        WHERE substr(time,1,10)=?
        """,
        (today,),
    ).fetchone()["n"]

    total_stock = sum(item["stock"] for item in products)

    recent = [
        dict(row)
        for row in con.execute(
            """
            SELECT t.id,t.time,t.type,t.doc_no,t.product_id,t.product_name,
                   t.qty_in,t.qty_out,t.qty_adjust,t.operator,
                   COALESCE(p.image_filename,'') AS image_filename
            FROM transactions t LEFT JOIN products p ON p.id=t.product_id
            ORDER BY t.time DESC
            LIMIT 8
            """
        ).fetchall()
    ]

    con.close()

    return {
        "product_count": len(products),
        "low_count": sum(
            1 for item in products
            if item["status"] == "补货"
        ),
        "today_docs": docs,
        "today_lines": lines,
        "total_stock_units": round(total_stock, 2),
        "recent": recent,
    }


@app.get("/api/product-options")
def get_product_options():
    return CATALOG_SERVICE.product_options()


@app.get("/api/products")
def get_products(q: str = "", status: str = "全部", category: str = "全部"):
    return CATALOG_SERVICE.list_products(q, status, category)


def _business_actor(request: Request) -> BusinessAuditActor:
    user = getattr(request.state, "user", {}) or {}
    return BusinessAuditActor(
        user_id=str(user.get("id", "")),
        display_name=str(user.get("display_name", "")),
        ip_address=request.client.host if request.client else "",
        username=str(user.get("username", "")),
    )


@app.post("/api/products")
async def save_product(p: ProductIn, request: Request):
    require_permission(request, "ext.catalog.create", "ext.catalog.update", mode="any")
    actor = _business_actor(request)
    try:
        product_id = await run_in_threadpool(CATALOG_SERVICE.save_product,
            p.model_dump(), actor,
            can_create=permission_granted(request.state.user, "ext.catalog.create"),
            can_update=permission_granted(request.state.user, "ext.catalog.update"),
        )
    except CatalogError as exc:
        raise HTTPException(exc.status_code, exc.message) from exc
    revision = await run_in_threadpool(bump_revision)
    await wsmanager.broadcast({"type": "products", "revision": revision})
    return {"ok": True, "id": product_id}


@app.get("/api/products/{product_id}/image")
def get_product_image(product_id: str):
    try:
        file_path = CATALOG_SERVICE.image_path(product_id)
    except CatalogError as exc:
        raise HTTPException(exc.status_code, exc.message) from exc
    return FileResponse(
        file_path, media_type="image/jpeg",
        headers={"Cache-Control": "private, max-age=300", "X-Content-Type-Options": "nosniff"},
    )


def _upload_catalog_image(product_id, data, content_type, actor):
    with _background_database_work:
        return CATALOG_SERVICE.upload_image(product_id, data, content_type, actor)


@app.post("/api/products/{product_id}/image")
async def upload_product_image(product_id: str, request: Request, file: UploadFile = File(...)):
    require_permission(request, "ext.catalog.image_update")
    data = await file.read(6 * 1024 * 1024 + 1)
    try:
        filename = await run_in_threadpool(_upload_catalog_image,
            product_id, data, file.content_type or "", _business_actor(request)
        )
    except CatalogError as exc:
        raise HTTPException(exc.status_code, exc.message) from exc
    return {"ok": True, "id": product_id, "image_filename": filename}


@app.delete("/api/products/{product_id}")
def disable_product(product_id: str, request: Request):
    require_permission(request, "ext.catalog.disable")
    try:
        CATALOG_SERVICE.disable_product(product_id, _business_actor(request))
    except CatalogError as exc:
        raise HTTPException(exc.status_code, exc.message) from exc
    bump_revision()
    return {"ok": True}


# ============================================================
# 日常登记与库存流水
# ============================================================

@app.post("/api/submit")
async def submit(payload: SubmitIn, request: Request):
    action_by_type = {
        "入库": "ext.inventory.inbound_create",
        "出库": "ext.inventory.outbound_create",
        "盘点调整": "ext.inventory.stocktake_adjust",
    }
    action = action_by_type.get(payload.type)
    if not action:
        raise HTTPException(400, "不支持的业务类型")
    require_permission(request, action)
    command = {
        "type": payload.type,
        "external_no": payload.external_no,
        "operator": request.state.user["display_name"],
        "note": payload.note,
        "request_id": payload.request_id,
        "items": [item.model_dump() for item in payload.items],
    }
    try:
        result = await run_in_threadpool(INVENTORY_SERVICE.submit_document, command, _business_actor(request))
    except InventoryError as exc:
        raise HTTPException(exc.status_code, exc.message) from exc
    except Exception as exc:
        logging.exception("Inventory document submission failed")
        raise HTTPException(500, "提交失败，请稍后重试") from exc
    if result.get("duplicate") or result.get("unchanged"):
        return result
    await wsmanager.broadcast({"type": "inventory", "revision": result["revision"]})
    return result


@app.get("/api/transactions")
def get_transactions(q: str = "", type: str = "全部", start: str = "", end: str = "",
                     limit: int = 500, offset: int = 0):
    return INVENTORY_SERVICE.list_transactions(q, type, start, end, limit, offset)


@app.get("/api/purchase")
def purchase():
    return PROCUREMENT_SERVICE.purchase_suggestions()


# ============================================================
# 服务器设置
# ============================================================

def get_lan_ip():
    return discover_lan_ip()


def get_access_ip():
    configured = str(CONFIG.get("access_ip", "")).strip()
    return configured or get_lan_ip()


def get_scheme():
    return "https" if CONFIG.get("https", False) else "http"


def ensure_self_signed_certificate(domain: str = "") -> tuple[str, str]:
    """首次启用 HTTPS 时生成本机私有 CA 与服务器证书；不覆盖已存在的证书。"""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
    from datetime import timezone

    cert_dir = DATA_DIR / "certs"
    cert_dir.mkdir(parents=True, exist_ok=True)
    ca_key_path, ca_cert_path = cert_dir / "ca.key", cert_dir / "ca.crt"
    server_key_path, server_cert_path = cert_dir / "server.key", cert_dir / "server.crt"
    if server_key_path.exists() and server_cert_path.exists():
        return str(server_cert_path), str(server_key_path)
    if server_key_path.exists() or server_cert_path.exists():
        raise RuntimeError("检测到不完整的本地证书文件；为避免覆盖手动证书，请检查 data/certs/server.crt 与 server.key")

    now = datetime.now(timezone.utc)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Store Inventory Local LAN CA")])
    ca_cert = (x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name)
        .public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False, key_encipherment=False,
            data_encipherment=False, key_agreement=False, key_cert_sign=True, crl_sign=True,
            encipher_only=False, decipher_only=False), critical=True).sign(ca_key, hashes.SHA256()))
    server_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    common_name = domain.strip() or get_lan_ip()
    san = [x509.IPAddress(ipaddress.ip_address(get_lan_ip()))]
    if domain.strip():
        san.append(x509.DNSName(domain.strip()))
    server_cert = (x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)]))
        .issuer_name(ca_cert.subject).public_key(server_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=825))
        .add_extension(x509.SubjectAlternativeName(san), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .sign(ca_key, hashes.SHA256()))
    ca_key_path.write_bytes(ca_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    ca_cert_path.write_bytes(ca_cert.public_bytes(serialization.Encoding.PEM))
    server_key_path.write_bytes(server_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    server_cert_path.write_bytes(server_cert.public_bytes(serialization.Encoding.PEM))
    try:
        os.chmod(ca_key_path, 0o600); os.chmod(server_key_path, 0o600)
    except OSError:
        pass
    logging.warning("Generated local HTTPS certificate. Install data/certs/ca.crt on each client device to trust it; never distribute private keys.")
    return str(server_cert_path), str(server_key_path)


def normalized_group_permissions(permissions: dict[str, bool]) -> dict[str, bool]:
    """Compatibility facade; group grant validation is owned by authorization."""
    try:
        return PERMISSION_SERVICE.normalize_group_permissions(permissions)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


def _authorization_actor(request: Request) -> AuthorizationAuditActor:
    user = getattr(request.state, "user", {}) or {}
    return AuthorizationAuditActor(
        user_id=str(user.get("id", "")),
        display_name=str(user.get("display_name", "")),
        ip_address=request.client.host if request.client else "",
    )


def _authorization_http_error(exc: AuthorizationError):
    raise HTTPException(exc.status_code, exc.message) from exc


@app.get("/api/permission-groups")
def list_permission_groups():
    return AUTHORIZATION_SERVICE.list_groups()


@app.get("/api/permission-groups/templates")
def list_permission_templates():
    return group_presets(PERMISSION_SERVICE.catalog())


@app.post("/api/permission-groups", status_code=201)
def create_permission_group(data: PermissionGroupIn, request: Request):
    try:
        group_id = AUTHORIZATION_SERVICE.create_group(
            data.name, data.permissions, _authorization_actor(request)
        )
    except AuthorizationError as exc:
        _authorization_http_error(exc)
    return {"id": group_id, "ok": True}


@app.patch("/api/permission-groups/{group_id}")
def update_permission_group(group_id: str, data: PermissionGroupIn, request: Request):
    try:
        AUTHORIZATION_SERVICE.update_group(
            group_id, data.name, data.permissions, _authorization_actor(request)
        )
    except AuthorizationError as exc:
        _authorization_http_error(exc)
    return {"ok": True}


@app.delete("/api/permission-groups/{group_id}")
def delete_permission_group(group_id: str, request: Request):
    try:
        AUTHORIZATION_SERVICE.delete_group(group_id, _authorization_actor(request))
    except AuthorizationError as exc:
        _authorization_http_error(exc)
    return {"ok": True}


@app.put("/api/permission-groups/{group_id}/members/{user_id}")
def add_permission_group_member(group_id: str, user_id: str, request: Request):
    try:
        AUTHORIZATION_SERVICE.add_member(
            group_id, user_id, _authorization_actor(request)
        )
    except AuthorizationError as exc:
        _authorization_http_error(exc)
    return {"ok": True}


@app.delete("/api/permission-groups/{group_id}/members/{user_id}")
def remove_permission_group_member(group_id: str, user_id: str, request: Request):
    try:
        AUTHORIZATION_SERVICE.remove_member(
            group_id, user_id, _authorization_actor(request)
        )
    except AuthorizationError as exc:
        _authorization_http_error(exc)
    return {"ok": True}


@app.get("/api/settings")
def get_settings():
    access_ip = get_access_ip()
    domain = str(CONFIG.get("domain", "")).strip()
    scheme = get_scheme()
    port = int(CONFIG.get("port", 8000))

    return {
        **CONFIG,
        "lan_ip": get_lan_ip(),
        "access_ip": access_ip,
        "ip_url": f"{scheme}://{access_ip}:{port}",
        "access_url": (
            f"{scheme}://{domain}:{port}"
            if domain else ""
        ),
    }


@app.put("/api/settings")
def put_settings(data: SettingsIn, request: Request):
    with CONFIG_LOCK:
        return _put_settings(data, request)


def _put_settings(data: SettingsIn, request: Request):
    global CONFIG
    # Omitted fields mean retain the saved value. An explicitly blank path resets it.
    current = {key: CONFIG[key] for key in SettingsIn.model_fields if key in CONFIG}
    data = SettingsIn.model_validate({**current, **data.model_dump(exclude_unset=True)})

    domain = data.domain.strip()
    access_ip = data.access_ip.strip()
    certfile = data.ssl_certfile.strip()
    keyfile = data.ssl_keyfile.strip()

    if domain and (
        "://" in domain
        or "/" in domain
        or ":" in domain
        or " " in domain
    ):
        raise HTTPException(
            400,
            "域名只填写主机名，不要包含协议、端口或路径",
        )

    if access_ip:
        try:
            ipaddress.ip_address(access_ip)
        except ValueError:
            raise HTTPException(400, "访问 IP 地址格式不正确")

    if data.https:
        if not certfile and not keyfile:
            ensure_self_signed_certificate(domain)
            certfile, keyfile = "data/certs/server.crt", "data/certs/server.key"
        elif not certfile or not keyfile:
            raise HTTPException(400, "请同时填写证书和私钥，或两项都留空以自动生成自签名证书")

        cert_path = resolve_config_file(certfile)
        key_path = resolve_config_file(keyfile)
        if not cert_path or not cert_path.is_file():
            raise HTTPException(400, "HTTPS 证书文件不存在")

        if not key_path or not key_path.is_file():
            raise HTTPException(400, "HTTPS 私钥文件不存在")

    new_config = {
        **CONFIG,
        **data.model_dump(),
        "domain": domain,
        "access_ip": access_ip,
        "ssl_certfile": certfile,
        "ssl_keyfile": keyfile,
        "backup_dir": (
            data.backup_dir.strip() or "data/backups"
        ),
        "purchase_dir": (
            data.purchase_dir.strip() or "data/purchase"
        ),
    }

    settings_service = SystemSettingsService(save_config, lambda: db(), write_audit)
    restart_fields = ("port", "https", "ssl_certfile", "ssl_keyfile")
    restart_required = any(CONFIG.get(key) != new_config[key] for key in restart_fields)
    CONFIG = settings_service.persist(new_config, request)

    return {
        "ok": True,
        "restart_required": restart_required,
        "settings": CONFIG,
    }


# ============================================================
# 数据备份与恢复
# ============================================================

BACKUP_FORMAT = "store-inventory-backup"
BACKUP_FORMAT_VERSION = 1


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _safe_backup_relative(name: str) -> str:
    """Reject archive paths that could escape the restore staging directory."""
    if not isinstance(name, str) or not name or "\\" in name or "\x00" in name:
        raise RuntimeError("备份包含非法文件路径")
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        raise RuntimeError("备份包含不安全的文件路径")
    if path.parts[0] not in {"data"}:
        raise RuntimeError("备份包含不允许恢复的目录")
    if name not in {"data/inventory.db", "data/config.json"} and not (
        name.startswith("data/product_images/") or name.startswith("data/purchase/")
    ):
        raise RuntimeError("备份包含不支持的文件类型")
    return path.as_posix()


# BackupArchiveService owns the portable bundle entries (data/config.json) and
# preserves the legacy API result contract including bundle_archive.
BACKUP_ARCHIVE_SERVICE = BackupArchiveService(
    connection_factory=lambda: db(),
    backup_directory=lambda: configured_dir("backup_dir", BACKUP_DIR),
    product_image_directory=lambda: PRODUCT_IMAGE_DIR,
    purchase_directory=lambda: configured_dir("purchase_dir", PURCHASE_DIR),
    config_path=CONFIG_PATH,
    config_provider=lambda: CONFIG,
    app_name=APP_NAME,
    app_version=APP_VERSION,
    schema_version=DB_SCHEMA_VERSION,
    backup_format=BACKUP_FORMAT,
    backup_format_version=BACKUP_FORMAT_VERSION,
    logger=logging,
)


def create_backup():
    """Compatibility facade for scheduler and desktop callers."""
    with _background_database_work:
        if _restore_recovery_required or _restore_journal_path().is_file():
            raise RuntimeError("备份恢复回滚未完成，请检查 server.log")
        return BACKUP_ARCHIVE_SERVICE.create_backup()


_daily_backup_lock = threading.Lock()


def ensure_daily_backup():
    if not _daily_backup_lock.acquire(timeout=8):
        raise TimeoutError("等待今日安全备份超时，请稍后重试")
    try:
        return _ensure_daily_backup()
    finally:
        _daily_backup_lock.release()


def _ensure_daily_backup():
    folder = configured_dir("backup_dir", BACKUP_DIR)
    folder.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y%m%d")
    for file in folder.glob(f"库存备份_{today}_*.db"):
        try:
            with closing(sqlite3.connect(file.resolve().as_uri() + "?mode=ro", uri=True)) as check:
                valid = check.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            bundle = folder / f"{file.stem}_完整备份.zip"
            if valid and bundle.is_file() and bundle.stat().st_size > 0:
                # Avoid decompressing a potentially large archive every minute;
                # it was fully CRC-checked when created and is fully re-checked on import.
                with zipfile.ZipFile(bundle, "r") as archive:
                    if "manifest.json" in archive.namelist():
                        manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
                        required = {"data/inventory.db", "data/config.json"}
                        if (manifest.get("format") == BACKUP_FORMAT
                                and manifest.get("format_version") == BACKUP_FORMAT_VERSION
                                and required.issubset(set(manifest.get("files", {})))):
                            return None
        except Exception:
            continue
    # An old .db-only backup is not considered a completed full backup.
    return create_backup()


# Business module composition: inventory provides stock read models; catalog consumes
# that port; procurement consumes the catalog query port. All DB adapters are injected.
INVENTORY_SERVICE = create_inventory_service(
    lambda: db(), ensure_daily_backup, lambda: CONFIG.get("default_threshold", 5)
)
CATALOG_SERVICE = create_catalog_service(
    lambda: db(), INVENTORY_SERVICE, PRODUCT_IMAGE_DIR,
    lambda: CONFIG.get("default_threshold", 5),
)
PROCUREMENT_SERVICE = create_procurement_service(CATALOG_SERVICE)


def backup_scheduler():
    while not (APP_LIFECYCLE.stop_event.is_set() or SERVICE_BACKGROUND_STOP.is_set()):
        try:
            if CONFIG.get("auto_backup", True):
                now = datetime.now()
                target = CONFIG.get("backup_time", "18:00")

                if now.strftime("%H:%M") >= target:
                    ensure_daily_backup()

        except Exception:
            logging.exception("scheduled backup failed")

        SERVICE_BACKGROUND_STOP.wait(60)


def run_appointment_reminders(now=None):
    """为管理员生成预约前 24 小时和 2 小时的内部提醒；不向客户外发短信。"""
    now = now or datetime.now()
    con = db()
    try:
        state = _schedule_row(con)
        if not state:
            return 0
        payload = json.loads(state["payload"])
        staff_names = {str(x.get("id")): str(x.get("name") or "") for x in payload.get("staff", []) if isinstance(x, dict)}
        admins = [row["id"] for row in con.execute("SELECT id FROM users WHERE role='admin' AND enabled=1").fetchall()]
        if not admins:
            return 0
        created = 0
        for appt in payload.get("appointments", []):
            if not isinstance(appt, dict) or appt.get("status") in ("cancelled", "done"):
                continue
            if not _validate_time(appt.get("start")):
                continue
            try:
                appointment_at = datetime.fromisoformat(f"{appt.get('date')}T{appt.get('start')}:00")
            except (TypeError, ValueError):
                continue
            if appointment_at <= now:
                continue
            for reminder_type, lead in (("24h", timedelta(hours=24)), ("2h", timedelta(hours=2))):
                if now < appointment_at - lead:
                    continue
                exists = con.execute("SELECT 1 FROM schedule_reminder_log WHERE appointment_id=? AND reminder_type=?", (str(appt.get("id")), reminder_type)).fetchone()
                if exists:
                    continue
                customer = str(appt.get("customer") or "客户")
                tail = str(appt.get("phoneTail") or "")
                staff_name = staff_names.get(str(appt.get("staffId") or ""), "美容师")
                body = f"{appointment_at:%Y-%m-%d %H:%M} · {customer} · {staff_name}" + (f" · 手机尾号 {tail}" if tail else "")
                for admin_id in admins:
                    create_notification(con, f"预约前{24 if reminder_type == '24h' else 2}小时提醒", body, "appointment", recipient_user_id=admin_id,
                                        target_url="/?page=schedule", source_key=f"appointment-reminder:{appt.get('id')}:{reminder_type}:{admin_id}")
                con.execute("INSERT INTO schedule_reminder_log(appointment_id,reminder_type,appointment_at,created_at) VALUES(?,?,?,?)",
                            (str(appt.get("id")), reminder_type, appointment_at.isoformat(timespec="seconds"), now.isoformat(timespec="seconds")))
                created += 1
        con.commit()
        return created
    finally:
        con.close()


def appointment_reminder_scheduler():
    while not (APP_LIFECYCLE.stop_event.is_set() or SERVICE_BACKGROUND_STOP.is_set()):
        try:
            with _background_database_work:
                if not _restore_recovery_required:
                    run_appointment_reminders()
        except Exception:
            logging.exception("appointment reminder scheduler failed")
        SERVICE_BACKGROUND_STOP.wait(60)


BACKUP_SERVICE = BackupService(lambda: configured_dir("backup_dir", BACKUP_DIR), create_backup)


@app.get("/api/backup")
def list_backups():
    return BACKUP_SERVICE.list_backups()


@app.post("/api/backup")
def api_create_backup(request: Request):
    result = BACKUP_SERVICE.create_backup()
    con = db()
    write_audit(con, "创建数据库备份", str(result.get("name", "")), request, "backup", str(result.get("name", "")), None, result)
    con.commit(); con.close()
    return result


@app.get("/api/backup/download/{name}")
def download_backup(name: str):
    target = BACKUP_SERVICE.resolve_download(name)
    if target is None:
        raise HTTPException(404, "备份不存在")
    return FileResponse(target, filename=target.name)


BACKUP_RESTORE_SERVICE = BackupRestoreService(
    schema_version=DB_SCHEMA_VERSION,
    backup_format=BACKUP_FORMAT,
    backup_format_version=BACKUP_FORMAT_VERSION,
    default_config=DEFAULT_CONFIG,
    base_directory=BASE_DIR,
    config_file_resolver=resolve_config_file,
)


def _backup_restore_http_error(exc: BackupRestoreError):
    raise HTTPException(exc.status_code, exc.message) from exc


def _validate_restore_database(path: Path):
    try:
        return BACKUP_RESTORE_SERVICE.validate_database(path)
    except BackupRestoreError as exc:
        _backup_restore_http_error(exc)


def _read_full_backup_bundle(content, stage_root: Path):
    try:
        return BACKUP_RESTORE_SERVICE.read_full_bundle(content, stage_root)
    except BackupRestoreError as exc:
        _backup_restore_http_error(exc)


def _restore_media_targets(stage_root: Path):
    product = PRODUCT_IMAGE_DIR.resolve()
    purchase = configured_dir("purchase_dir", PURCHASE_DIR)
    data = DATA_DIR.resolve()

    def overlaps(left, right):
        return left.is_relative_to(right) or right.is_relative_to(left)

    protected = [
        DB_PATH.resolve(), CONFIG_PATH.resolve(), STATIC_DIR.resolve(),
        LOG_DIR.resolve(), configured_dir("backup_dir", BACKUP_DIR),
        BACKUP_DIR.resolve(), (DATA_DIR / "certs").resolve(), stage_root.resolve(),
    ]
    protected.extend(path for key in ("ssl_certfile", "ssl_keyfile")
                     if (path := configured_file(key)) is not None)
    if (not product.is_relative_to(data) or overlaps(product, purchase)
            or any(overlaps(target, path) for target in (product, purchase) for path in protected)
            or any(overlaps(purchase, root.resolve()) for root in (
                BASE_DIR / "store_inventory", BASE_DIR / "upgrade_payload",
                BASE_DIR / "release", BASE_DIR / "source", BASE_DIR / "scripts",
                BASE_DIR / "tests", BASE_DIR / "docs", BASE_DIR / "design_preview",
            ))
            or purchase in (BASE_DIR.resolve(), data)
            or (purchase.is_relative_to(BASE_DIR.resolve()) and not purchase.is_relative_to(data)
                and purchase != (BASE_DIR / "purchase").resolve())
            or BASE_DIR.resolve().is_relative_to(purchase)):
        raise HTTPException(409, "当前附件目录与程序或其他数据目录冲突，请在当前系统设置中修正后重试")
    return ((product, stage_root / "data" / "product_images"),
            (purchase, stage_root / "data" / "purchase"))


def _prepare_restored_database(staged_db, request, filename, manifest):
    _validate_restore_database(staged_db)
    try:
        init_db(staged_db)
        with closing(db(staged_db)) as con, con:
            # Keep the current instance's configuration mirror and invalidate old sessions.
            con.executemany(
                """INSERT INTO settings(key,value) VALUES(?,?)
                   ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
                [(key, json.dumps(value, ensure_ascii=False)) for key, value in CONFIG.items()],
            )
            con.execute("DELETE FROM user_sessions")
            write_audit(con, "恢复完整备份" if manifest else "恢复数据库", filename, request,
                        "backup", filename, None,
                        {"source": "desktop" if request is None else "web",
                         "backup_app_version": manifest.get("app_version") if manifest else None,
                         "db_schema_version": DB_SCHEMA_VERSION, "integrity_check": "ok"})
    except sqlite3.DatabaseError as exc:
        logging.exception("Uploaded database migration failed")
        raise HTTPException(400, "备份数据库无法迁移到当前版本，请检查 server.log") from exc
    _validate_restore_database(staged_db)


def _restore_journal_path():
    return DB_PATH.parent / ".restore-pending.json"


def _record_restore_state(payload):
    # Stage and fsync before publishing the journal. Preserve it until the compound swap is committed.
    target = _restore_journal_path()
    fd, name = tempfile.mkstemp(prefix=".restore-journal-", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(payload, out, ensure_ascii=False)
            out.flush()
            os.fsync(out.fileno())
        os.replace(name, target)
    finally:
        Path(name).unlink(missing_ok=True)


def _restore_data(content, request: Request | None, filename: str, limit_mb: int):
    content.seek(0, os.SEEK_END)
    size = content.tell()
    content.seek(0)
    if not size:
        raise HTTPException(400, "备份文件为空")
    if size > limit_mb * 1024 * 1024:
        raise HTTPException(413, f"备份文件超过 {limit_mb} MB 限制")
    global _restore_recovery_required
    with _background_database_work:
        stage_root = Path(tempfile.mkdtemp(prefix=".restore_", dir=DB_PATH.parent))
        directory_swaps = []
        replacements = []
        db_replaced = False
        keep_recovery = False
        rollback_db = stage_root / "rollback.db"
        journal = {"phase": "swapping", "stage": str(stage_root), "database": str(DB_PATH), "media": []}
        journal_started = False
        try:
            manifest = None
            targets = ()
            if filename.lower().endswith(".zip"):
                manifest, extracted = _read_full_backup_bundle(content, stage_root)
                staged_db = extracted["data/inventory.db"]
                targets = _restore_media_targets(stage_root)
            else:
                staged_db = stage_root / "uploaded.db"
                with staged_db.open("wb") as output:
                    shutil.copyfileobj(content, output, length=1024 * 1024)
            _prepare_restored_database(staged_db, request, filename, manifest)
            if not DB_PATH.is_file():
                raise HTTPException(503, "当前数据库不可用，无法创建恢复前备份")
            try:
                create_backup()
            except RuntimeError as exc:
                if not manifest or "找不到被数据库引用的图片" not in str(exc):
                    raise
                logging.warning("Pre-restore ZIP unavailable; snapshotting DB and current media: %s", exc)
            with closing(db()) as live, closing(sqlite3.connect(rollback_db)) as snapshot:
                copy_sqlite_snapshot(live, snapshot)
                snapshot.commit()
                # A leftover WAL must never be replayed into the incoming database.
                if live.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0]:
                    raise HTTPException(503, "当前数据库被其他程序占用，请关闭其他服务实例后重试")
            if any(DB_PATH.with_name(DB_PATH.name + suffix).exists()
                   for suffix in ("-wal", "-shm", "-journal")):
                raise HTTPException(503, "当前数据库被其他程序占用，请关闭其他服务实例后重试")

            _record_restore_state(journal)
            journal_started = True
            for target, source in targets:
                target.parent.mkdir(parents=True, exist_ok=True)
                replacement = target.with_name(f".{target.name}.restore_{stage_root.name}")
                rollback = target.with_name(f".{target.name}.rollback_{stage_root.name}")
                replacements.append(replacement)
                shutil.copytree(source, replacement)
                had_old = target.exists()
                journal["media"].append({"target":str(target), "rollback":str(rollback), "had_old":had_old})
                _record_restore_state(journal)
                if had_old:
                    os.replace(target, rollback)
                directory_swaps.append((target, rollback, had_old))
                os.replace(replacement, target)
            os.replace(staged_db, DB_PATH)
            db_replaced = True
            with closing(sqlite3.connect(DB_PATH.resolve().as_uri() + "?mode=ro", uri=True)) as installed:
                installed.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
            _restore_journal_path().unlink()
            journal_started = False
        except Exception as exc:
            # Even if one component cannot roll back, attempt the others and retain copies.
            if db_replaced:
                try:
                    os.replace(rollback_db, DB_PATH)
                except OSError:
                    keep_recovery = True
                    logging.exception("Restore rollback DB retained at %s", rollback_db)
            for target, rollback, had_old in reversed(directory_swaps):
                try:
                    if target.exists():
                        shutil.rmtree(target)
                    if had_old:
                        os.replace(rollback, target)
                except OSError:
                    keep_recovery = True
                    logging.exception("Restore rollback media failed: target=%s snapshot=%s", target, rollback)
            if journal_started and not keep_recovery:
                try:
                    _restore_journal_path().unlink(missing_ok=True)
                except OSError:
                    keep_recovery = True
            if keep_recovery:
                with _restore_state:
                    _restore_recovery_required = True
                raise HTTPException(503, "自动回滚未完成，恢复副本已保留，请查看 server.log") from exc
            if isinstance(exc, HTTPException):
                raise
            logging.exception("Restore failed and live data was retained")
            raise HTTPException(503, "备份恢复失败，当前数据已保留，请查看 server.log") from exc
        else:
            for _, rollback, had_old in directory_swaps:
                if had_old:
                    shutil.rmtree(rollback, ignore_errors=True)
        finally:
            for replacement in replacements:
                shutil.rmtree(replacement, ignore_errors=True)
            if not keep_recovery:
                shutil.rmtree(stage_root, ignore_errors=True)

    return {
        "ok": True,
        "message": ("业务数据、商品图片和采购附件已恢复，请重新登录。"
                    if manifest else "数据库已恢复，请重新登录。"),
        "restart_required": False,
        "login_required": True,
        "backup_format_version": manifest.get("format_version") if manifest else None,
        "app_version": manifest.get("app_version") if manifest else None,
    }


def _restore_limit(filename):
    if not filename or not filename.lower().endswith((".db", ".zip")):
        raise HTTPException(400, "请选择门店完整备份 .zip 或兼容旧版 .db 文件")
    return 1024 if filename.lower().endswith(".zip") else 512


@contextmanager
def _reserve_restore(*, allowed_requests=0):
    global _restoring
    with _restore_state:
        if _restore_recovery_required or _restore_journal_path().is_file():
            raise HTTPException(503, "上次恢复尚未确认完成，请检查恢复凭据和 server.log")
        if _restoring or _active_database_requests > allowed_requests:
            raise HTTPException(409, "当前仍有门店操作执行中，请稍后重试恢复")
        _restoring = True
    try:
        yield
    finally:
        with _restore_state:
            _restoring = False


def restore_local_backup(path: Path):
    control = LocalControl(DATA_DIR)
    marker = control.observe()
    limit_mb = _restore_limit(path.name)
    guard = nullcontext() if marker.get("pid") == os.getpid() else control.offline_maintenance()
    try:
        with _reserve_restore(), guard, path.open("rb") as content:
            return _restore_data(content, None, path.name, limit_mb)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.post("/api/restore")
async def restore_backup(request: Request, file: UploadFile = File(...)):
    limit_mb = _restore_limit(file.filename)
    if file.size is not None and file.size > limit_mb * 1024 * 1024:
        raise HTTPException(413, f"备份文件超过 {limit_mb} MB 限制")
    with _reserve_restore(allowed_requests=1):
        return await run_in_threadpool(_restore_data, file.file, request, file.filename, limit_mb)


# ============================================================
# Excel 导出
# ============================================================

EXPORT_SERVICE = ExcelExportService(
    product_reader=lambda **kwargs: product_rows(None, kwargs.get("q", ""), kwargs.get("status", "全部"), kwargs.get("category", "全部")),
    transaction_reader=lambda **kwargs: get_transactions(**kwargs),
    purchase_reader=lambda: purchase(),
    output_directory=lambda kind: configured_dir("purchase_dir", PURCHASE_DIR) if kind == "purchase" else DATA_DIR,
)


EXPORT_TEMPLATES = ExcelExportTemplates(lambda: db())
EXPORT_PERMISSIONS = {
    "products": "ext.catalog.export",
    "transactions": "ext.transactions.export",
    "purchase": "ext.procurement.export",
}


def _export_user_id(request, kind):
    action = EXPORT_PERMISSIONS.get(kind)
    if not action:
        raise HTTPException(400, "不支持的导出类型")
    require_permission(request, action)
    return request.state.user["id"]


class ExportTemplateIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    fields: list[str] = Field(min_length=1, max_length=20)
    is_default: bool = False


@app.get("/api/export/{kind}/options")
def list_export_options(kind: str, request: Request):
    user_id = _export_user_id(request, kind)
    title, labels = export_definition(kind)
    return {
        "title": title,
        "fields": [{"key": key, "label": label} for key, label in labels.items()],
        "templates": EXPORT_TEMPLATES.list(user_id, kind),
    }


@app.post("/api/export/{kind}/templates", status_code=201)
def create_export_template(kind: str, data: ExportTemplateIn, request: Request):
    return save_export_template(kind, data, request, None)


@app.put("/api/export/{kind}/templates/{template_id}")
def update_export_template(kind: str, template_id: str, data: ExportTemplateIn, request: Request):
    return save_export_template(kind, data, request, template_id)


def save_export_template(kind, data, request, template_id):
    user_id = _export_user_id(request, kind)
    try:
        return EXPORT_TEMPLATES.save(user_id, kind, template_id, **data.model_dump())
    except ExportValidationError as exc:
        raise HTTPException(exc.status_code, exc.message) from exc


@app.delete("/api/export/{kind}/templates/{template_id}")
def delete_export_template(kind: str, template_id: str, request: Request):
    user_id = _export_user_id(request, kind)
    try:
        EXPORT_TEMPLATES.delete(user_id, kind, template_id)
    except ExportValidationError as exc:
        raise HTTPException(exc.status_code, exc.message) from exc
    return {"ok": True}


@app.get("/api/export/{kind}")
def export_xlsx(
    kind: str,
    request: Request,
    q: str = "",
    type: str = "全部",
    start: str = "",
    end: str = "",
    status: str = "全部",
    category: str = "全部",
    fields: list[str] | None = Query(default=None),
):
    _export_user_id(request, kind)
    try:
        with (_background_database_work if kind == "purchase" else nullcontext()):
            output_file = EXPORT_SERVICE.export(
                kind, fields=fields, q=q, type=type, start=start, end=end, status=status, category=category
            )
    except ExportValidationError as exc:
        raise HTTPException(exc.status_code, exc.message) from exc
    return FileResponse(output_file, filename=output_file.name)


# ============================================================
# 统一排班服务端存储、revision 并发控制与员工本人权限
# ============================================================

class ScheduleStateIn(BaseModel):
    payload: dict
    revision: int = Field(ge=0)


class ScheduleMigrateIn(BaseModel):
    payload: dict


class ScheduleStaffLinkIn(BaseModel):
    user_id: str
    staff_id: str


class ScheduleTransferIn(BaseModel):
    appointment_id: str
    target_staff_id: str
    segment_start: str
    segment_end: str
    expected_revision: int = Field(ge=1)


















@app.get("/api/schedule/state")
def get_schedule_state(request: Request):
    try:
        result = SCHEDULING_SERVICE.get_schedule_state(request.state.user, _business_actor(request))
    except SchedulingError as exc:
        raise HTTPException(exc.status_code, exc.message) from exc
    return result


@app.post("/api/schedule/migrate")
async def migrate_schedule(data: ScheduleMigrateIn, request: Request):
    try:
        result = await run_in_threadpool(SCHEDULING_SERVICE.migrate_schedule, request.state.user, _business_actor(request), data.model_dump())
    except SchedulingError as exc:
        raise HTTPException(exc.status_code, exc.message) from exc
    if result.get("created"):
        await wsmanager.broadcast({"type": "schedule", "revision": result["revision"]})
    return result


@app.put("/api/schedule/state")
async def put_schedule_state(data: ScheduleStateIn, request: Request):
    try:
        result = await run_in_threadpool(SCHEDULING_SERVICE.put_schedule_state, request.state.user, _business_actor(request), data.model_dump())
    except SchedulingError as exc:
        raise HTTPException(exc.status_code, exc.message) from exc
    await wsmanager.broadcast({"type": "schedule", "revision": result["revision"]})
    return result


@app.get("/api/schedule/staff-links")
def list_schedule_staff_links(request: Request):
    try:
        result = SCHEDULING_SERVICE.list_schedule_staff_links(request.state.user, _business_actor(request))
    except SchedulingError as exc:
        raise HTTPException(exc.status_code, exc.message) from exc
    return result


@app.put("/api/schedule/staff-links")
def put_schedule_staff_link(request: Request, data: ScheduleStaffLinkIn):
    try:
        result = SCHEDULING_SERVICE.put_schedule_staff_link(request.state.user, _business_actor(request), data.model_dump())
    except SchedulingError as exc:
        raise HTTPException(exc.status_code, exc.message) from exc
    return result


@app.delete("/api/schedule/staff-links/{user_id}")
def delete_schedule_staff_link(request: Request, user_id: str):
    try:
        result = SCHEDULING_SERVICE.delete_schedule_staff_link(request.state.user, _business_actor(request), user_id)
    except SchedulingError as exc:
        raise HTTPException(exc.status_code, exc.message) from exc
    return result


@app.get("/api/schedule/transfers")
def get_schedule_transfers(request: Request, date_from: str='', date_to: str=''):
    try:
        result = SCHEDULING_SERVICE.get_schedule_transfers(request.state.user, _business_actor(request), date_from, date_to)
    except SchedulingError as exc:
        raise HTTPException(exc.status_code, exc.message) from exc
    return result


@app.post("/api/schedule/appointments/transfer")
async def transfer_schedule_segment(data: ScheduleTransferIn, request: Request):
    try:
        result = await run_in_threadpool(SCHEDULING_SERVICE.transfer_schedule_segment, request.state.user, _business_actor(request), data.model_dump())
    except SchedulingError as exc:
        raise HTTPException(exc.status_code, exc.message) from exc
    await wsmanager.broadcast({"type": "schedule", "revision": result["revision"]})
    return result


class AppointmentStatusIn(BaseModel):
    status: str = Field(pattern="^(pending|serving|done|cancelled)$")
    expected_revision: int = Field(ge=1)


@app.patch("/api/schedule/appointments/{appointment_id}/status")
async def change_appointment_status(appointment_id: str, data: AppointmentStatusIn, request: Request):
    try:
        result = await run_in_threadpool(SCHEDULING_SERVICE.change_appointment_status, request.state.user, _business_actor(request), appointment_id, data.model_dump())
    except SchedulingError as exc:
        raise HTTPException(exc.status_code, exc.message) from exc
    await wsmanager.broadcast({"type": "schedule", "revision": result["revision"]})
    return result


# ============================================================
# WebSocket
# ============================================================

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    user = await run_in_threadpool(user_from_token,
        ws.cookies.get("inventory_session")
    )

    if not user:
        await ws.close(code=4401)
        return

    if user.get("must_set_password"):
        await ws.close(code=4403)
        return

    await wsmanager.connect(ws)

    try:
        while True:
            try:
                await asyncio.wait_for(ws.receive_text(), timeout=20)
            except TimeoutError:
                user = await run_in_threadpool(user_from_token, ws.cookies.get("inventory_session"))
                if not user or user.get("must_set_password"):
                    await ws.close(code=4401)
                    return

    except WebSocketDisconnect:
        pass
    finally:
        wsmanager.disconnect(ws)


# ============================================================
# Windows 服务端线程
# ============================================================

server_thread = None
server_obj = None
server_error = None
SERVICE_BACKGROUND_STOP = threading.Event()
REQUEST_DRAIN_SECONDS = 25
APPLICATION_SHUTDOWN_SECONDS = 35


def run_server():
    global server_obj, server_error, ACTIVE_ENDPOINT

    try:
        if APP_LIFECYCLE.stop_event.is_set() or SERVER_STOP.requested.is_set():
            return
        port = int(CONFIG.get("port", 8000))
        ACTIVE_ENDPOINT = Endpoint(port, bool(CONFIG.get("https", False)), APP_VERSION, SERVER_INSTANCE_ID)

        cert_path = configured_file("ssl_certfile")
        key_path = configured_file("ssl_keyfile")
        certfile = str(cert_path) if cert_path else None
        keyfile = str(key_path) if key_path else None

        if CONFIG.get("https", False):
            if not CONFIG.get("ssl_certfile") and not CONFIG.get("ssl_keyfile"):
                certfile, keyfile = ensure_self_signed_certificate(str(CONFIG.get("domain") or ""))
                update_runtime_config({"ssl_certfile": "data/certs/server.crt", "ssl_keyfile": "data/certs/server.key"})
            elif not certfile or not keyfile:
                raise RuntimeError("HTTPS 已启用，但手动证书路径未同时配置")

            if not Path(certfile).is_file():
                raise FileNotFoundError(
                    f"证书文件不存在：{certfile}"
                )

            if not Path(keyfile).is_file():
                raise FileNotFoundError(
                    f"私钥文件不存在：{keyfile}"
                )

        logging.info(
            "Starting Uvicorn: host=0.0.0.0 port=%s https=%s keep_alive=8s backlog=1024",
            port,
            bool(CONFIG.get("https", False)),
        )

        for logger_name in ("uvicorn", "uvicorn.error"):
            logger = logging.getLogger(logger_name)
            logger.handlers.clear()
            logger.propagate = True
            logger.setLevel(logging.INFO)
        config = uvicorn.Config(
            app=app,
            host="0.0.0.0",
            port=port,
            log_level="info",
            access_log=False,
            proxy_headers=False,
            log_config=None,
            timeout_keep_alive=8,
            ws_ping_interval=20,
            ws_ping_timeout=20,
            # Let in-flight operations finish before cancelling stalled requests.
            timeout_graceful_shutdown=REQUEST_DRAIN_SECONDS,
            backlog=1024,
            ssl_certfile=certfile if CONFIG.get("https", False) else None,
            ssl_keyfile=keyfile if CONFIG.get("https", False) else None,
        )

        server_obj = uvicorn.Server(config)
        SERVER_STOP.attach(server_obj)
        if SERVER_STOP.requested.is_set() or APP_LIFECYCLE.stop_event.is_set():
            return
        logging.info("Runtime identity: version=%s pid=%s executable=%s base=%s uvicorn=%s instance=%s",
                     APP_VERSION, os.getpid(), sys.executable, BASE_DIR, uvicorn.__version__, SERVER_INSTANCE_ID)
        server_obj.run()

        if not server_obj.started and not server_obj.should_exit:
            raise RuntimeError(
                "Uvicorn 未能完成服务启动，请检查端口是否被占用"
            )

    except BaseException as exc:
        server_error = f"{type(exc).__name__}: {exc}"
        logging.exception("server start failed")

    finally:
        with wsmanager.lock:
            wsmanager.clients.clear()
        logging.info("Server thread ended; requested_stop=%s error=%s", SERVER_STOP.requested.is_set(), server_error)


def start_server_thread(*, prepare=False):
    global server_thread, server_error, SERVER_INSTANCE_ID, ACTIVE_ENDPOINT, server_obj
    if APP_LIFECYCLE.stop_event.is_set():
        return
    if server_thread and server_thread.is_alive():
        return
    SERVER_STOP.reset()
    # Recheck shutdown after resetting a previous restart's stop latch.
    if APP_LIFECYCLE.stop_event.is_set():
        SERVER_STOP.request_stop()
        return
    server_error = None
    server_obj = None
    SERVER_INSTANCE_ID = uuid.uuid4().hex
    ACTIVE_ENDPOINT = Endpoint(int(CONFIG.get("port", 8000)), bool(CONFIG.get("https", False)), APP_VERSION, SERVER_INSTANCE_ID)
    def start():
        global server_error
        try:
            if not prepare:
                run_server()
                return
            with LocalControl(DATA_DIR).lease(ACTIVE_ENDPOINT) as marker:
                SERVICE_BACKGROUND_STOP.clear()
                APP_LIFECYCLE.start("LocalServerControl", lambda: LocalControl(DATA_DIR).watch(
                    marker, SERVICE_BACKGROUND_STOP, lambda: request_application_shutdown("local_stop"),
                ))
                try:
                    if _restore_journal_path().is_file():
                        raise RuntimeError(f"上次恢复未确认完成，保留所有数据以供核对：{_restore_journal_path()}")
                    init_db()
                    if APP_LIFECYCLE.stop_event.is_set() or SERVER_STOP.requested.is_set():
                        return
                    start_background_tasks()
                    run_server()
                finally:
                    SERVICE_BACKGROUND_STOP.set()
                    # The service lease is released only after all managed service workers finish.
                    while ({"LocalServerControl", "BackupScheduler", "AppointmentReminderScheduler", "DesktopBackup", "DesktopRestore"} & set(APP_LIFECYCLE.pending())
                           or external_non_daemon_workers() - set(APP_LIFECYCLE.pending())):
                        time.sleep(0.05)
                    LocalControl(DATA_DIR).confirm_stopped(marker)
        except Exception as exc:
            server_error = str(exc)
            logging.exception("Server preparation failed")
        finally:
            if prepare:
                SERVICE_BACKGROUND_STOP.set()

    server_thread = APP_LIFECYCLE.start("InventoryServer", start)


def start_background_tasks():
    SERVICE_BACKGROUND_STOP.clear()
    APP_LIFECYCLE.start("BackupScheduler", backup_scheduler)
    APP_LIFECYCLE.start("AppointmentReminderScheduler", appointment_reminder_scheduler)


def request_application_shutdown(reason="window_close"):
    SERVICE_BACKGROUND_STOP.set()
    if APP_LIFECYCLE.request_stop(reason):
        logging.info("Application shutdown requested: reason=%s deadline=%ss",
                     reason, APPLICATION_SHUTDOWN_SECONDS)


def external_non_daemon_workers():
    # Uvicorn cancellation can leave a synchronous request running in an AnyIO
    # worker. Wait for non-daemon threads too before logging a clean exit.
    return {
        thread.name for thread in threading.enumerate()
        if thread is not threading.main_thread() and not thread.daemon and thread.is_alive()
    }


def pending_shutdown_workers():
    return sorted(set(APP_LIFECYCLE.pending()) | external_non_daemon_workers())


def finish_application_shutdown():
    global _shutdown_complete
    with _shutdown_lock:
        if _shutdown_complete:
            return True
        if not APP_LIFECYCLE.stop_event.is_set() or pending_shutdown_workers():
            return False
        with wsmanager.lock:
            wsmanager.clients.clear()
        logging.info("Application resources released: pending_workers=0")
        _server_log_handler.flush()
        logging.getLogger().removeHandler(_server_log_handler)
        _server_log_handler.close()
        _shutdown_complete = True
        return True


def shutdown_seconds_remaining():
    started = APP_LIFECYCLE.stop_started_at
    if started is None:
        return APPLICATION_SHUTDOWN_SECONDS
    return max(0, APPLICATION_SHUTDOWN_SECONDS - (time.monotonic() - started))


def capture_shutdown_stacks_if_slow():
    global _shutdown_stacks_captured
    started = APP_LIFECYCLE.stop_started_at
    if _shutdown_stacks_captured or started is None:
        return
    elapsed = time.monotonic() - started
    if elapsed < min(5, APPLICATION_SHUTDOWN_SECONDS):
        return
    _shutdown_stacks_captured = True
    try:
        with (LOG_DIR / "shutdown-stacks.log").open("w", encoding="utf-8") as trace:
            trace.write(f"Version {APP_VERSION} | {datetime.now().isoformat()} | {APP_LIFECYCLE.reason}\n")
            pending_names = set(pending_shutdown_workers())
            pending_threads = [thread for thread in threading.enumerate()
                               if thread.name in pending_names and thread.ident is not None]
            for thread in pending_threads:
                trace.write(f"{thread.name}: 0x{thread.ident:x}\n")
            trace.flush()
            faulthandler.dump_traceback(file=trace, all_threads=True)
    except OSError:
        logging.exception("Could not write shutdown thread stacks")


def force_exit_if_shutdown_expired():
    if shutdown_seconds_remaining() > 0:
        return
    try:
        logging.critical("Shutdown deadline exceeded; forcing process exit: reason=%s pending=%s",
                         APP_LIFECYCLE.reason, ", ".join(pending_shutdown_workers()))
    finally:
        # Exiting only the Tk loop would still leave Python waiting for non-daemon
        # threads. At the deadline the OS must release this process's handles.
        os._exit(0)


def wait_application_shutdown():
    while not finish_application_shutdown():
        capture_shutdown_stacks_if_slow()
        force_exit_if_shutdown_expired()
        time.sleep(min(0.1, shutdown_seconds_remaining()))


# ============================================================
# Tkinter 管理窗口
# ============================================================

class Manager:

    def __init__(self, root):
        self.root = root
        self._closing = False
        self._after_ids = set()
        self._restart_pending = False
        self._stop_pending = False
        self._restart_deadline = None
        self._restore_results = SimpleQueue()
        self._import_pending = False
        self._backup_pending = False
        self._backup_results = SimpleQueue()
        root.protocol("WM_DELETE_WINDOW", self.quit)
        self.desktop_service = DesktopManagementService(
            create_backup, getattr(os, "startfile", lambda path: None),
            DATA_DIR, LOG_DIR / "server.log", restore_action=restore_local_backup,
        )

        root.title(f"门店管理系统服务器 V{APP_VERSION}")
        root.geometry("940x555")
        root.minsize(870, 505)
        configure_console(root, ttk.Style)
        self.status = tk.StringVar(value="服务未启动")
        self.database_status = tk.StringVar(value="等待启动")
        self.ip_address = tk.StringVar()
        self.domain_address = tk.StringVar()

        shell = ttk.Frame(root, style="Console.TFrame")
        shell.pack(fill="both", expand=True)
        nav = ttk.Frame(shell, style="ConsoleNav.TFrame", padding=(12, 18))
        nav.pack(side="left", fill="y")
        ttk.Label(nav, text="门店服务器", style="ConsoleNavTitle.TLabel").pack(anchor="w", padx=9, pady=(4, 18))
        ttk.Button(nav, text="概览", style="ConsoleSelected.TButton",
                   command=lambda: self.open_button.focus_set()).pack(fill="x", pady=3)
        for label, action in (("服务器设置", self.settings), ("打开日志", self.open_log),
                              ("登录后自启设置", self.toggle_autostart)):
            ttk.Button(nav, text=label, style="ConsoleNav.TButton", command=action).pack(fill="x", pady=3)
        ttk.Label(nav, text=f"V{APP_VERSION}", style="ConsoleNavHint.TLabel").pack(side="bottom", anchor="w", padx=9)

        panel = ttk.Frame(shell, style="Console.TFrame", padding=(18, 16, 18, 9))
        panel.pack(side="left", fill="both", expand=True)
        card = ttk.Frame(panel, style="ConsolePaper.TFrame", padding=17)
        card.pack(fill="both", expand=True)
        ttk.Label(card, text="门店管理系统服务器", style="ConsoleTitle.TLabel").pack(anchor="w", pady=(0, 11))

        state_row = ttk.Frame(card, style="ConsolePaper.TFrame")
        state_row.pack(fill="x", pady=(0, 13))
        self.status_badge = ttk.Label(state_row, textvariable=self.status, style="ConsolePending.TLabel",
                                      padding=(11, 6), wraplength=440)
        self.status_badge.pack(side="left")
        ttk.Label(state_row, textvariable=self.database_status,
                  style="ConsoleMuted.TLabel").pack(side="left", padx=12)

        for label, variable in (("IP 访问", self.ip_address), ("域名访问", self.domain_address)):
            row = ttk.Frame(card, style="ConsolePaper.TFrame")
            row.pack(fill="x", pady=3)
            ttk.Label(row, text=label, width=10, padding=(8, 4), style="ConsoleSection.TLabel").pack(side="left")
            ttk.Label(row, textvariable=variable, padding=(9, 5), wraplength=420, style="ConsoleAddress.TLabel").pack(side="left", fill="x", expand=True)
        ttk.Label(card, text="监听地址  0.0.0.0", style="ConsoleMuted.TLabel").pack(anchor="w", pady=(5, 9))

        actions = ttk.Frame(card, style="ConsolePaper.TFrame")
        actions.pack(fill="x", pady=(2, 13))
        for attr, label, action, style in (
            ("open_button", "打开库存系统", self.open, "ConsolePrimary.TButton"),
            ("start_button", "启动服务", self.start_service, "ConsoleAction.TButton"),
            ("stop_button", "停止服务", self.stop_service, "ConsoleAction.TButton"),
            ("restart_button", "重新启动服务", self.restart_server, "ConsoleAction.TButton"),
        ):
            button = ttk.Button(actions, text=label, command=action, style=style)
            button.pack(side="left", padx=(0, 7))
            setattr(self, attr, button)

        ttk.Label(card, text="数据维护", style="ConsoleSection.TLabel").pack(anchor="w", pady=(0, 5))
        maintenance = ttk.Frame(card, style="ConsolePaper.TFrame")
        maintenance.pack(fill="x")
        self.backup_button = ttk.Button(maintenance, text="全量备份（ZIP）", command=self.backup, style="ConsoleAction.TButton")
        self.backup_button.pack(side="left", padx=(0, 7))
        self.import_button = ttk.Button(maintenance, text="导入数据备份", command=self.import_backup, style="ConsoleAction.TButton")
        self.import_button.pack(side="left", padx=(0, 7))
        ttk.Button(maintenance, text="打开数据目录", command=self.open_data,
                   style="ConsoleAction.TButton").pack(side="left")

        ttk.Label(panel, text="关闭窗口将停止由本窗口启动的服务",
                  style="ConsoleFooter.TLabel").pack(anchor="w", pady=(9, 0))

        self.health_monitor = HealthMonitor(
            LoopbackProbe(), self._monitored_endpoint,
            APP_LIFECYCLE.stop_event,
            lambda result: logging.info("Health state: live=%s ready=%s detail=%s", result.live, result.ready, result.detail),
        )
        APP_LIFECYCLE.start("HealthMonitor", self.health_monitor.run)
        self._display_endpoint = None
        self.refresh()

    def _monitored_endpoint(self):
        if server_thread and server_thread.is_alive() and ACTIVE_ENDPOINT:
            return ACTIVE_ENDPOINT
        marker = LocalControl(DATA_DIR).observe()
        if marker and all(key in marker for key in ("port", "https", "version", "instance")):
            return Endpoint(marker["port"], marker["https"], marker["version"], marker["instance"])
        return Endpoint(int(CONFIG.get("port", 8000)), get_scheme() == "https", APP_VERSION, "")

    def refresh_address(self):
        endpoint = self._monitored_endpoint()
        active = endpoint if endpoint.instance else None
        port = active.port if active else int(CONFIG.get("port", 8000))
        ip = get_access_ip()
        domain = str(CONFIG.get("domain", "")).strip()
        scheme = ("https" if active.https else "http") if active else get_scheme()

        self.ip_address.set(f"{scheme}://{ip}:{port}")
        self.domain_address.set(f"{scheme}://{domain}:{port}" if domain else "未配置")

    def _schedule(self, delay, callback):
        if self._closing:
            return None
        token = None
        def invoke():
            self._after_ids.discard(token)
            if not self._closing:
                callback()
        token = self.root.after(delay, invoke)
        self._after_ids.add(token)
        return token

    def refresh(self):
        if self._closing:
            return
        if APP_LIFECYCLE.stop_event.is_set():
            self.quit()
            return
        result = self.health_monitor.result
        running = bool(server_thread and server_thread.is_alive())
        busy = self._import_pending or self._backup_pending or self._restart_pending or self._stop_pending
        endpoint = self._monitored_endpoint()
        external = not running and bool(endpoint.instance)
        if self._display_endpoint != endpoint:
            self._display_endpoint = endpoint
            self.refresh_address()
        ready = bool(endpoint.instance and result and result.endpoint == endpoint and result.ready)
        for button, enabled in ((self.start_button, not running and not busy and not external),
                                (self.stop_button, running and not busy),
                                (self.restart_button, ready and not busy),
                                (self.open_button, ready and not busy),
                                (self.backup_button, not busy and not external),
                                (self.import_button, not busy and not external)):
            button.state(["!disabled" if enabled else "disabled"])
        self.database_status.set("数据库就绪" if ready else "等待就绪" if running or external else "等待启动")
        badge = ("ConsoleIssue.TLabel" if server_error and not external else
                 "ConsoleReady.TLabel" if ready and not busy else "ConsolePending.TLabel")
        self.status_badge.configure(style=badge)
        if self._backup_pending:
            self.status.set("正在生成全量备份")
        elif self._import_pending:
            self.status.set("正在导入门店数据，请等待结果…")
        elif self._stop_pending or self._restart_pending:
            self.status.set("正在停止服务，等待操作完成…")
        elif external:
            self.status.set("● 后台实例运行正常" if ready else "已发现后台实例，正在检查就绪状态")
        elif server_error:
            self.status.set("服务启动失败：" + str(server_error)[:200])
        elif not server_thread or not server_thread.is_alive():
            self.status.set("服务未启动")
        elif result and result.endpoint == ACTIVE_ENDPOINT and result.ready:
            self.status.set("● 服务运行正常")
        elif result and result.endpoint == ACTIVE_ENDPOINT and result.live:
            self.status.set("服务已启动，数据库暂未就绪：" + result.detail)
        elif result and result.endpoint == ACTIVE_ENDPOINT:
            self.status.set("等待本机服务响应：" + result.detail)
        else:
            self.status.set("正在启动服务，等待本机检查…")
        self._schedule(500, self.refresh)

    def settings(self):
        if self._import_pending:
            return
        win = tk.Toplevel(self.root)
        win.title("服务器设置")
        win.geometry("560x500")
        win.resizable(False, False)
        win.transient(self.root)
        win.grab_set()

        port_var = tk.StringVar(
            value=str(CONFIG.get("port", 8000))
        )

        ip_var = tk.StringVar(
            value=str(CONFIG.get("access_ip", ""))
        )

        domain_var = tk.StringVar(
            value=str(CONFIG.get("domain", "ku.com"))
        )

        https_var = tk.BooleanVar(
            value=bool(CONFIG.get("https", False))
        )

        cert_var = tk.StringVar(
            value=str(CONFIG.get("ssl_certfile", ""))
        )

        key_var = tk.StringVar(
            value=str(CONFIG.get("ssl_keyfile", ""))
        )

        frm = ttk.Frame(win, padding=18)
        frm.pack(fill="both", expand=True)

        fields = [
            ("访问 IP（留空自动检测）", ip_var),
            ("服务端口", port_var),
            ("访问域名", domain_var),
            ("证书文件路径", cert_var),
            ("私钥文件路径", key_var),
        ]

        for row, (label, variable) in enumerate(fields):
            ttk.Label(
                frm,
                text=label,
            ).grid(
                row=row,
                column=0,
                sticky="w",
                pady=9,
            )

            ttk.Entry(
                frm,
                textvariable=variable,
                width=42,
            ).grid(
                row=row,
                column=1,
                sticky="ew",
                pady=9,
            )

        ttk.Checkbutton(
            frm,
            text="启用 HTTPS（证书路径留空则自动创建）",
            variable=https_var,
        ).grid(
            row=5,
            column=0,
            columnspan=2,
            sticky="w",
            pady=10,
        )

        ttk.Label(
            frm,
            text=(
                "监听地址始终为 0.0.0.0。"
                "\n域名需要路由器 DNS 或客户端 hosts 解析。"
                "\n自签名 HTTPS 证书需要每台手机手动信任本地 CA。"
            ),
            foreground="#64748b",
            wraplength=500,
        ).grid(
            row=6,
            column=0,
            columnspan=2,
            sticky="w",
            pady=12,
        )

        def save():
            try:
                new_port = int(port_var.get().strip())

                if not 1024 <= new_port <= 65535:
                    raise ValueError(
                        "端口必须在 1024 至 65535 之间"
                    )

                new_ip = ip_var.get().strip()

                if new_ip:
                    ipaddress.ip_address(new_ip)

                new_domain = domain_var.get().strip()

                if new_domain and (
                    "://" in new_domain
                    or "/" in new_domain
                    or ":" in new_domain
                ):
                    raise ValueError(
                        "域名不能包含协议、路径或端口"
                    )

                new_https = https_var.get()
                new_cert = cert_var.get().strip()
                new_key = key_var.get().strip()

                if new_https:
                    if not new_cert and not new_key:
                        ensure_self_signed_certificate(new_domain)
                        new_cert, new_key = "data/certs/server.crt", "data/certs/server.key"
                    elif not new_cert or not new_key:
                        raise ValueError("证书与私钥必须同时填写；两项留空则自动生成本地证书")

                    if not bool(resolve_config_file(new_cert) and resolve_config_file(new_cert).is_file()):
                        raise ValueError("证书文件不存在")

                    if not bool(resolve_config_file(new_key) and resolve_config_file(new_key).is_file()):
                        raise ValueError("私钥文件不存在")

            except Exception as exc:
                messagebox.showerror(
                    "设置错误",
                    str(exc),
                    parent=win,
                )
                return

            old_values = (
                int(CONFIG.get("port", 8000)),
                bool(CONFIG.get("https", False)),
                CONFIG.get("ssl_certfile", ""),
                CONFIG.get("ssl_keyfile", ""),
            )

            try:
                update_runtime_config({
                    "port": new_port,
                    "access_ip": new_ip,
                    "domain": new_domain,
                    "https": new_https,
                    "ssl_certfile": new_cert,
                    "ssl_keyfile": new_key,
                })
            except Exception as exc:
                messagebox.showerror(
                    "保存失败",
                    str(exc),
                    parent=win,
                )
                return

            win.destroy()
            self.refresh_address()

            new_values = (
                new_port,
                new_https,
                new_cert,
                new_key,
            )

            if old_values != new_values:
                messagebox.showinfo(
                    "设置已保存",
                    "端口与 HTTPS 配置将在下次启动或手动重启服务时生效。",
                )
            else:
                messagebox.showinfo(
                    "设置已保存",
                    "访问 IP 和域名设置已生效。",
                )

        buttons = ttk.Frame(frm)
        buttons.grid(
            row=7,
            column=0,
            columnspan=2,
            pady=12,
        )

        ttk.Button(
            buttons,
            text="保存设置",
            command=save,
        ).pack(side="left", padx=8)

        ttk.Button(
            buttons,
            text="取消",
            command=win.destroy,
        ).pack(side="left", padx=8)

    def start_service(self):
        if self._closing or self._stop_pending or self._restart_pending or self._import_pending or self._backup_pending or _restoring:
            return
        start_server_thread(prepare=True)
        self.refresh_address()

    def stop_service(self):
        self._begin_service_stop(restart=False)

    def restart_server(self):
        self._begin_service_stop(restart=True)

    def _begin_service_stop(self, *, restart):
        if (self._closing or self._stop_pending or self._restart_pending or self._import_pending
                or self._backup_pending or _restoring or not server_thread or not server_thread.is_alive()):
            return
        self._restart_pending = restart
        self._stop_pending = True
        self._restart_deadline = time.monotonic() + APPLICATION_SHUTDOWN_SECONDS
        self.status.set("正在停止服务，等待操作完成…")
        SERVER_STOP.request_stop()
        SERVICE_BACKGROUND_STOP.set()
        self._schedule(100, self._wait_server_stop)

    def _wait_server_stop(self):
        if self._closing:
            return
        pending = set(APP_LIFECYCLE.pending()) - {"HealthMonitor"}
        if pending or external_non_daemon_workers() - set(APP_LIFECYCLE.pending()):
            if time.monotonic() >= self._restart_deadline:
                self._restart_pending = False
                self.status.set("部分服务操作仍在结束，请查看日志")
            self._schedule(250, self._wait_server_stop)
            return
        restart = self._restart_pending
        self._restart_pending = self._stop_pending = False
        self.refresh_address()
        if restart:
            self.start_service()

    def open(self):
        endpoint = self._monitored_endpoint()
        result = self.health_monitor.result
        if endpoint and result and result.endpoint == endpoint and result.ready:
            scheme = "https" if endpoint.https else "http"
            webbrowser.open(f"{scheme}://127.0.0.1:{endpoint.port}")

    def toggle_autostart(self):
        if os.name != "nt" or not getattr(sys, "frozen", False):
            messagebox.showinfo("登录后自启", "此设置适用于 Windows EXE。", parent=self.root)
            return
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as key:
                winreg.QueryValueEx(key, "StoreInventoryServer")
        except FileNotFoundError:
            if messagebox.askyesno("登录后自启", "当前关闭。是否在登录 Windows 后自动启动服务器？", parent=self.root):
                self.autostart()
        except OSError as exc:
            messagebox.showerror("登录后自启", str(exc), parent=self.root)
        else:
            if messagebox.askyesno("登录后自启", "当前已开启。是否取消登录后自动启动？", parent=self.root):
                self.remove_autostart()

    def autostart(self):
        if not getattr(sys, "frozen", False):
            messagebox.showinfo(
                "提示",
                "请先编译为 EXE，再设置开机启动。",
            )
            return

        try:
            import winreg

            key_path = (
                r"Software\Microsoft\Windows\CurrentVersion\Run"
            )

            with winreg.CreateKey(
                winreg.HKEY_CURRENT_USER,
                key_path,
            ) as key:
                winreg.SetValueEx(
                    key,
                    "StoreInventoryServer",
                    0,
                    winreg.REG_SZ,
                    f'"{sys.executable}" --headless',
                )

            update_runtime_config({"auto_start": True})

            messagebox.showinfo(
                "设置成功",
                "已设置当前 Windows 用户登录后自动启动。",
            )

        except Exception as exc:
            messagebox.showerror(
                "设置失败",
                str(exc),
            )

    def remove_autostart(self):
        try:
            import winreg

            key_path = (
                r"Software\Microsoft\Windows\CurrentVersion\Run"
            )

            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                key_path,
                0,
                winreg.KEY_SET_VALUE,
            ) as key:
                try:
                    winreg.DeleteValue(
                        key,
                        "StoreInventoryServer",
                    )
                except FileNotFoundError:
                    pass

            update_runtime_config({"auto_start": False})

            messagebox.showinfo(
                "操作成功",
                "已取消当前用户的开机启动。",
            )

        except Exception as exc:
            messagebox.showerror(
                "操作失败",
                str(exc),
            )

    def backup(self):
        if self._closing or self._import_pending or self._backup_pending or self._stop_pending:
            return
        self._backup_pending = True
        self.backup_button.state(("disabled",))
        if APP_LIFECYCLE.start("DesktopBackup", self._run_backup) is None:
            self._backup_pending = False
            self.backup_button.state(("!disabled",))
            return
        self._schedule(100, self._poll_backup)

    def _run_backup(self):
        try:
            control = LocalControl(DATA_DIR)
            marker = control.observe()
            guard = nullcontext() if marker.get("pid") == os.getpid() else control.offline_maintenance()
            with guard:
                if not DB_PATH.is_file():
                    raise RuntimeError("当前没有可备份的门店数据")
                result = self.desktop_service.create_backup()
                archive = configured_dir("backup_dir", BACKUP_DIR) / result["bundle_archive"]
            self._backup_results.put((True, str(archive)))
        except Exception as exc:
            logging.exception("Desktop full backup failed")
            self._backup_results.put((False, str(exc)))

    def _poll_backup(self):
        try:
            ok, value = self._backup_results.get_nowait()
        except Empty:
            self._schedule(100, self._poll_backup)
            return
        self._backup_pending = False
        self.backup_button.state(("!disabled",))
        if ok:
            messagebox.showinfo("全量备份完成",
                                f"数据库、配置、商品图片和采购附件已保存为：\n{value}",
                                parent=self.root)
        else:
            messagebox.showerror("全量备份失败", value, parent=self.root)

    def import_backup(self):
        if self._closing or self._restart_pending or self._import_pending or self._backup_pending or self._stop_pending:
            return
        filename = filedialog.askopenfilename(
            parent=self.root, title="选择门店数据备份",
            filetypes=[("门店备份", "*.zip *.db"), ("完整备份", "*.zip"), ("旧版数据库", "*.db")],
        )
        if not filename:
            return
        if not messagebox.askyesno(
            "恢复门店数据",
            "将从所选备份恢复业务数据，并保留当前页面和服务器配置。"
            "\n完整 ZIP 同时恢复图片和采购附件。恢复成功后需重新登录。\n\n确定继续？",
            parent=self.root,
        ):
            return
        self._import_pending = True
        self.import_button.state(("disabled",))
        self.status.set("正在导入门店数据，请等待结果…")
        if APP_LIFECYCLE.start("DesktopRestore", lambda: self._run_restore(Path(filename))) is None:
            self._import_pending = False
            self.import_button.state(("!disabled",))
            return
        self._schedule(100, self._poll_restore)

    def _run_restore(self, path):
        try:
            result = self.desktop_service.restore_backup(path)
            self._restore_results.put((True, result["message"]))
        except HTTPException as exc:
            self._restore_results.put((False, str(exc.detail)))
        except Exception:
            logging.exception("Desktop restore failed")
            self._restore_results.put((False, "无法导入所选备份，请查看 server.log"))

    def _poll_restore(self):
        if self._closing:
            return
        try:
            ok, message = self._restore_results.get_nowait()
        except Empty:
            self._schedule(100, self._poll_restore)
            return
        self._import_pending = False
        self.import_button.state(("!disabled",))
        self.status.set("数据导入完成" if ok else "数据导入失败")
        dialog = messagebox.showinfo if ok else messagebox.showerror
        dialog("数据导入", message, parent=self.root)

    def open_data(self):
        try:
            self.desktop_service.open_data_directory()
        except Exception as exc:
            messagebox.showerror(
                "打开失败",
                str(exc),
            )

    def open_log(self):
        try:
            if not self.desktop_service.server_log_exists():
                logging.info("Log file opened.")
            self.desktop_service.open_server_log()

        except Exception as exc:
            messagebox.showerror(
                "打开日志失败",
                str(exc),
            )

    def quit(self):
        # Clicking X means stop this application, not destroy Tk ahead of workers.
        if self._closing:
            return
        self._closing = True
        for token in list(self._after_ids):
            try:
                self.root.after_cancel(token)
            except tk.TclError:
                pass
        self._after_ids.clear()
        def disable_buttons(widget):
            for child in widget.winfo_children():
                if isinstance(child, ttk.Button):
                    child.state(["disabled"])
                disable_buttons(child)
        disable_buttons(self.root)
        self.status.set("正在关闭：停止监听、后台任务并释放资源…")
        request_application_shutdown("window_close")
        self.root.after(50, self._poll_shutdown)

    def _poll_shutdown(self):
        if finish_application_shutdown():
            self.root.destroy()
            return
        capture_shutdown_stacks_if_slow()
        force_exit_if_shutdown_expired()
        pending = ", ".join(pending_shutdown_workers())
        seconds = int(shutdown_seconds_remaining() + 0.999)
        self.status.set(f"正在等待当前操作结束（最多还有 {seconds} 秒）：{pending}")
        self.root.after(100, self._poll_shutdown)


@app.get("/api/reports/operations")
def operations_report(date_from: str = "", date_to: str = ""):
    """经营汇总 HTTP adapter; report calculations live in platform.reports."""
    try:
        return OPERATIONS_REPORT_SERVICE.get_operations_report(date_from, date_to)
    except ReportValidationError as exc:
        raise HTTPException(exc.status_code, exc.message) from exc


# ============================================================
# V1.3.1 新增：统一消息中心、在线设备、低库存预警、客户与审批
# ============================================================

@app.get("/api/inventory/alerts")
def inventory_alerts():
    alerts = CATALOG_SERVICE.list_products(status="补货")
    return {"count": len(alerts), "items": alerts}


@app.get("/api/notifications")
def list_notifications(request: Request, limit: int = 100):
    return PLATFORM_OPERATIONS.list_notifications(request.state.user["id"], limit)


@app.post("/api/notifications/{notification_id}/read")
def mark_notification_read(notification_id: str, request: Request):
    if not PLATFORM_OPERATIONS.mark_notification_read(notification_id, request.state.user["id"]):
        raise HTTPException(404, "消息不存在")
    return {"ok": True}


@app.post("/api/notifications/read-all")
def mark_all_notifications_read(request: Request):
    PLATFORM_OPERATIONS.mark_all_notifications_read(request.state.user["id"])
    return {"ok": True}


@app.post("/api/devices/heartbeat")
def device_heartbeat(data: DeviceHeartbeatIn, request: Request):
    user = request.state.user
    ip = request.client.host if request.client else ""
    return PLATFORM_OPERATIONS.heartbeat(device_id=data.device_id, user_id=user["id"],
        device_name=data.device_name, platform=data.platform, ip_address=ip)


@app.get("/api/devices")
def list_online_devices():
    return PLATFORM_OPERATIONS.list_online_devices()


@app.get("/api/audit")
def list_audit_logs(limit: int = 200):
    return PLATFORM_OPERATIONS.list_audit_logs(limit)


@app.get("/api/customers")
def list_customers(request: Request, q: str='', limit: int=200):
    try:
        result = CUSTOMERS_SERVICE.list_customers(request.state.user, _business_actor(request), q, limit)
    except SchedulingError as exc:
        raise HTTPException(exc.status_code, exc.message) from exc
    return result


@app.post("/api/customers")
def save_customer(data: CustomerIn, request: Request):
    try:
        result = CUSTOMERS_SERVICE.save_customer(request.state.user, _business_actor(request), data.model_dump(exclude_unset=True) | {"id": data.id, "name": data.name})
    except SchedulingError as exc:
        raise HTTPException(exc.status_code, exc.message) from exc
    return result


@app.delete("/api/customers/{customer_id}")
def delete_customer(customer_id: str, request: Request):
    try:
        result = CUSTOMERS_SERVICE.delete_customer(request.state.user, _business_actor(request), customer_id)
    except SchedulingError as exc:
        raise HTTPException(exc.status_code, exc.message) from exc
    return result


@app.get("/api/schedule/waitlist")
def list_schedule_waitlist(request: Request, status: str='waiting'):
    try:
        result = WAITLIST_SERVICE.list_schedule_waitlist(request.state.user, _business_actor(request), status)
    except SchedulingError as exc:
        raise HTTPException(exc.status_code, exc.message) from exc
    return result


@app.post("/api/schedule/waitlist")
def add_schedule_waitlist(data: WaitlistIn, request: Request):
    try:
        result = WAITLIST_SERVICE.add_schedule_waitlist(request.state.user, _business_actor(request), data.model_dump())
    except SchedulingError as exc:
        raise HTTPException(exc.status_code, exc.message) from exc
    return result


@app.patch("/api/schedule/waitlist/{item_id}")
def update_schedule_waitlist(item_id: str, status: str, request: Request):
    try:
        result = WAITLIST_SERVICE.update_schedule_waitlist(request.state.user, _business_actor(request), item_id, status)
    except SchedulingError as exc:
        raise HTTPException(exc.status_code, exc.message) from exc
    return result


@app.get("/api/schedule/workload")
def schedule_workload(request: Request, date_from: str='', date_to: str=''):
    try:
        result = SCHEDULING_SERVICE.schedule_workload(request.state.user, _business_actor(request), date_from, date_to)
    except SchedulingError as exc:
        raise HTTPException(exc.status_code, exc.message) from exc
    return result


@app.get("/api/schedule/requests")
def list_schedule_requests(request: Request, status: str='all'):
    try:
        result = SCHEDULING_SERVICE.list_schedule_requests(request.state.user, _business_actor(request), status)
    except SchedulingError as exc:
        raise HTTPException(exc.status_code, exc.message) from exc
    return result


@app.post("/api/schedule/requests")
def create_schedule_request(data: ScheduleRequestIn, request: Request):
    try:
        result = SCHEDULING_SERVICE.create_schedule_request(request.state.user, _business_actor(request), data.model_dump())
    except SchedulingError as exc:
        raise HTTPException(exc.status_code, exc.message) from exc
    return result




@app.patch("/api/schedule/requests/{request_id}")
async def review_schedule_request(request_id: str, data: ScheduleRequestReviewIn, request: Request):
    try:
        result = await run_in_threadpool(SCHEDULING_SERVICE.review_schedule_request, request.state.user, _business_actor(request), request_id, data.model_dump())
    except SchedulingError as exc:
        raise HTTPException(exc.status_code, exc.message) from exc
    if result.get("schedule_revision") is not None:
        await wsmanager.broadcast({"type": "schedule", "revision": result["schedule_revision"]})
    return result


# ============================================================
# 程序入口
# ============================================================

def main():
    import argparse
    parser = argparse.ArgumentParser(description="门店管理系统服务端")
    parser.add_argument("--headless", action="store_true", help="无管理窗口后台运行服务端")
    parser.add_argument("--stop-local", action="store_true", help="请求当前安装的服务器正常停止")
    args, _ = parser.parse_known_args()
    if args.stop_local:
        try:
            stopped = LocalControl(DATA_DIR).request_stop()
        except OSError:
            logging.exception("Local stop request could not be submitted")
            stopped = False
        raise SystemExit(0 if stopped else 2)
    previous_signals = {}
    signal_received = threading.Event()

    def stop_signal(signum, frame):
        signal_received.set()
    for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        value = getattr(signal, name, None)
        if value is not None:
            previous_signals[value] = signal.signal(value, stop_signal)
    root = None
    exit_code = 0
    try:
        if not args.headless:
            root = tk.Tk()
            root.withdraw()
        if args.headless:
            start_server_thread(prepare=True)
            logging.info("Starting in headless background mode.")
            while server_thread and server_thread.is_alive():
                if signal_received.wait(0.2):
                    request_application_shutdown("process_signal")
                    break
                if APP_LIFECYCLE.stop_event.is_set():
                    break
            if not APP_LIFECYCLE.stop_event.is_set():
                exit_code = 1
                logging.error("Headless server exited unexpectedly.")
        else:
            manager = Manager(root)
            root.deiconify()

            def poll_signal():
                if signal_received.is_set():
                    manager.quit()
                else:
                    manager._schedule(250, poll_signal)

            manager._schedule(250, poll_signal)
            def session_end():
                request_application_shutdown("windows_session_end")
                APP_LIFECYCLE.join(3)
                finish_application_shutdown()
            with subscribe_shutdown(root, session_end):
                root.mainloop()
    except Exception:
        exit_code = 1
        logging.exception("Application startup or UI failed")
        if root is not None:
            try:
                messagebox.showerror("服务器错误", "启动或窗口发生错误，请查看 server.log", parent=root)
            except tk.TclError:
                pass
    finally:
        request_application_shutdown("main_exit")
        wait_application_shutdown()
        if root is not None:
            try:
                root.destroy()
            except tk.TclError:
                pass
        for signum, handler in previous_signals.items():
            signal.signal(signum, handler)
    if exit_code:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
