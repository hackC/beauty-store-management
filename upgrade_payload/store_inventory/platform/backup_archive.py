"""Portable SQLite + media backup creation engine.

All filesystem, database and application metadata dependencies are injected at the
composition boundary so the backup workflow can be tested without production data.
"""
from __future__ import annotations
from contextlib import closing
import hashlib
import json
import logging
import os
import re
import sqlite3
import time
import zipfile
from datetime import datetime
from pathlib import Path, PurePosixPath


SQLITE_SNAPSHOT_TIMEOUT = 8.0


def copy_sqlite_snapshot(source, destination):
    """Bound retries if another connection holds a SQLite lock."""
    deadline = time.monotonic() + SQLITE_SNAPSHOT_TIMEOUT
    source.execute("PRAGMA busy_timeout=250")
    destination.execute("PRAGMA busy_timeout=250")

    def check_progress(status, remaining, total):
        if status != sqlite3.SQLITE_DONE and time.monotonic() >= deadline:
            raise TimeoutError("SQLite 快照等待超时，请确认数据库没有被其他操作占用后重试")

    source.backup(destination, pages=128, progress=check_progress, sleep=0.05)


class BackupArchiveService:
    def __init__(self, *, connection_factory, backup_directory, product_image_directory,
                 purchase_directory, config_path, config_provider, app_name,
                 app_version, schema_version, backup_format, backup_format_version,
                 logger=None):
        self._connect = connection_factory
        self._backup_directory = backup_directory
        self._product_image_directory = product_image_directory
        self._purchase_directory = purchase_directory
        self._config_path = Path(config_path)
        self._config_provider = config_provider
        self._app_name = app_name
        self._app_version = app_version
        self._schema_version = schema_version
        self._backup_format = backup_format
        self._backup_format_version = backup_format_version
        self._logger = logger or logging.getLogger(__name__)

    def _images_dir(self) -> Path:
        value = self._product_image_directory() if callable(self._product_image_directory) else self._product_image_directory
        return Path(value)

    @staticmethod
    def _sha256(content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()

    @staticmethod
    def _safe_relative(name: str) -> str:
        if not isinstance(name, str) or not name or "\\" in name or "\x00" in name:
            raise RuntimeError("备份包含非法文件路径")
        path = PurePosixPath(name)
        if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
            raise RuntimeError("备份包含不安全的文件路径")
        if path.parts[0] != "data":
            raise RuntimeError("备份包含不允许恢复的目录")
        if name not in {"data/inventory.db", "data/config.json"} and not (
            name.startswith("data/product_images/") or name.startswith("data/purchase/")
        ):
            raise RuntimeError("备份包含不支持的文件类型")
        return path.as_posix()

    def _iter_tree(self, folder: Path, prefix: str):
        if not folder.exists():
            return
        if folder.is_symlink():
            raise RuntimeError(f"备份目录不能是符号链接：{folder}")
        for item in sorted(folder.rglob("*")):
            if item.is_symlink():
                raise RuntimeError(f"备份目录包含符号链接，已停止：{item}")
            if item.is_file():
                relative = item.relative_to(folder).as_posix()
                yield self._safe_relative(f"{prefix}/{relative}"), item

    def _create_bundle(self, db_snapshot: Path, bundle_path: Path):
        partial = bundle_path.with_suffix(bundle_path.suffix + ".part")
        try:
            config_bytes = self._config_path.read_bytes()
            if not isinstance(json.loads(config_bytes.decode("utf-8-sig")), dict):
                raise ValueError("configuration root is not an object")
            # Restore expects UTF-8 JSON without a BOM.
            config_bytes = config_bytes.decode("utf-8-sig").encode("utf-8")
        except (OSError, ValueError):
            config_bytes = json.dumps(self._config_provider(), ensure_ascii=False, indent=2).encode("utf-8")
        files = [("data/inventory.db", db_snapshot), ("data/config.json", None)]
        files.extend(self._iter_tree(self._images_dir(), "data/product_images") or [])
        files.extend(self._iter_tree(Path(self._purchase_directory()), "data/purchase") or [])
        manifest_files = {}
        try:
            with zipfile.ZipFile(partial, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
                for archive_name, source_path in files:
                    if source_path is None:
                        archive.writestr(archive_name, config_bytes)
                        manifest_files[archive_name] = {"size": len(config_bytes), "sha256": self._sha256(config_bytes)}
                        continue
                    size = 0
                    digest = hashlib.sha256()
                    with source_path.open("rb") as source, archive.open(archive_name, "w", force_zip64=True) as entry:
                        for chunk in iter(lambda: source.read(1024 * 1024), b""):
                            entry.write(chunk)
                            digest.update(chunk)
                            size += len(chunk)
                    manifest_files[archive_name] = {"size": size, "sha256": digest.hexdigest()}
                manifest = {
                    "format": self._backup_format,
                    "format_version": self._backup_format_version,
                    "app_name": self._app_name,
                    "app_version": self._app_version,
                    "created_at": datetime.now().isoformat(timespec="seconds"),
                    "db_schema_version": self._schema_version,
                    "directories": ["data/product_images", "data/purchase"],
                    "files": manifest_files,
                    "security_note": "TLS private keys and certificates are not included. After restoring on another server, regenerate/reconfigure HTTPS certificates and trust them on client devices.",
                }
                archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"))
            with zipfile.ZipFile(partial, "r") as check_zip:
                if check_zip.testzip() is not None or "manifest.json" not in check_zip.namelist():
                    raise RuntimeError("完整备份 ZIP 完整性检查失败")
            os.replace(partial, bundle_path)
        except Exception:
            partial.unlink(missing_ok=True)
            bundle_path.unlink(missing_ok=True)
            raise

    def create_backup(self):
        folder = Path(self._backup_directory()).resolve()
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / f"库存备份_{datetime.now():%Y%m%d_%H%M%S_%f}.db"
        try:
            with closing(self._connect()) as source, closing(sqlite3.connect(target)) as destination:
                copy_sqlite_snapshot(source, destination)
                destination.commit()
            with closing(sqlite3.connect(target)) as check:
                if check.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise RuntimeError("备份完整性检查失败")
        except Exception:
            target.unlink(missing_ok=True)
            raise

        media_archive = folder / f"{target.stem}_images.zip"
        partial_archive = folder / f"{target.stem}_images.zip.part"
        bundle_archive = folder / f"{target.stem}_完整备份.zip"
        try:
            saved = sqlite3.connect(target.as_uri() + "?mode=ro", uri=True)
            try:
                product_columns = {r[1] for r in saved.execute("PRAGMA table_info(products)")}
                files = sorted({r[0] for r in saved.execute(
                    "SELECT image_filename FROM products WHERE image_filename IS NOT NULL AND image_filename <> ''"
                )}) if "image_filename" in product_columns else []
            finally:
                saved.close()
            with zipfile.ZipFile(partial_archive, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("README.txt", "Legacy companion archive for product photos. For a full restore, use the matching *_完整备份.zip file.\n")
                for file_name in files:
                    if not re.fullmatch(r"[a-f0-9]{32}\.jpg", file_name):
                        raise RuntimeError("数据库包含非法图片引用，停止备份")
                    photo_file = self._images_dir() / file_name
                    if not photo_file.is_file():
                        raise RuntimeError(f"找不到被数据库引用的图片 {file_name}，备份失败")
                    archive.write(photo_file, arcname=f"product_images/{file_name}")
            with zipfile.ZipFile(partial_archive) as archive:
                if archive.testzip() is not None:
                    raise RuntimeError("图片备份 ZIP 完整性检查失败")
            os.replace(partial_archive, media_archive)
            self._create_bundle(target, bundle_archive)
        except Exception:
            target.unlink(missing_ok=True)
            partial_archive.unlink(missing_ok=True)
            media_archive.unlink(missing_ok=True)
            bundle_archive.unlink(missing_ok=True)
            raise

        keep_days = int(self._config_provider().get("backup_keep_days", 30))
        cutoff = datetime.now().timestamp() - keep_days * 86400
        for old in folder.glob("*.db"):
            if old != target and old.stat().st_mtime < cutoff:
                old.unlink(missing_ok=True)
                (folder / f"{old.stem}_images.zip").unlink(missing_ok=True)
                (folder / f"{old.stem}_完整备份.zip").unlink(missing_ok=True)

        self._logger.info("Full backup bundle created: %s", bundle_archive)
        return {
            "ok": True, "name": target.name, "size": target.stat().st_size,
            "images_archive": media_archive.name, "bundle_archive": bundle_archive.name,
            "bundle_size": bundle_archive.stat().st_size,
            "backup_format_version": self._backup_format_version,
        }
