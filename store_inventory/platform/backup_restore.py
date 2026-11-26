"""Backup restore validation and bundle staging service."""
from __future__ import annotations
import hashlib
import json
import re
import sqlite3
import stat
import zipfile
import io
from pathlib import Path, PurePosixPath, PureWindowsPath


class BackupRestoreError(ValueError):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class BackupRestoreService:
    def __init__(self, *, schema_version, backup_format, backup_format_version,
                 default_config, base_directory, config_file_resolver):
        self.schema_version = int(schema_version)
        self.backup_format = backup_format
        self.backup_format_version = int(backup_format_version)
        self.default_config = dict(default_config)
        self.base_directory = Path(base_directory)
        self._resolve_config_file = config_file_resolver

    @staticmethod
    def _safe_relative(name: str) -> str:
        if not isinstance(name, str) or not name or "\\" in name or "\x00" in name:
            raise BackupRestoreError("备份包含非法文件路径")
        path = PurePosixPath(name)
        if (path.is_absolute() or not path.parts or path.as_posix() != name
                or any(part == ".." or part.endswith((" ", ".")) or PureWindowsPath(part).is_reserved()
                       or re.search(r'[<>:"|?*\x00-\x1f]', part) for part in path.parts)):
            raise BackupRestoreError("备份包含不安全的文件路径")
        if path.parts[0] != "data":
            raise BackupRestoreError("备份包含不允许恢复的目录")
        if name not in {"data/inventory.db", "data/config.json"} and not (
            name.startswith("data/product_images/") or name.startswith("data/purchase/")
        ):
            raise BackupRestoreError("备份包含不支持的文件类型")
        return path.as_posix()

    def validate_database(self, path: Path) -> int:
        try:
            check = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
            try:
                if check.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise BackupRestoreError("备份数据库完整性检查失败")
                tables = {r[0] for r in check.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                required = {"products", "documents", "transactions", "settings", "meta"}
                if not required.issubset(tables):
                    raise BackupRestoreError("备份文件缺少库存系统必需的数据表")
                if check.execute("PRAGMA foreign_key_check").fetchall():
                    raise BackupRestoreError("备份数据库存在外键完整性错误")
                schema = 0
                row = check.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
                schema = int(row[0]) if row and str(row[0]).isdigit() else 0
                if schema > self.schema_version:
                    raise BackupRestoreError(
                        f"备份数据库结构版本 {schema} 高于当前程序支持版本 {self.schema_version}，请使用更新版本程序恢复"
                    )
                return schema
            finally:
                check.close()
        except sqlite3.DatabaseError as exc:
            raise BackupRestoreError("上传文件不是有效的 SQLite 数据库") from exc

    def normalize_config(self, raw: dict) -> dict:
        if not isinstance(raw, dict):
            raise BackupRestoreError("备份中的 config.json 格式不正确")
        restored = {**self.default_config, **raw}
        try:
            port = int(restored.get("port", 8000))
            keep_days = int(restored.get("backup_keep_days", 30))
            if not 1 <= port <= 65535 or not 1 <= keep_days <= 365:
                raise ValueError
            restored["port"] = port
            restored["backup_keep_days"] = keep_days
        except (TypeError, ValueError):
            raise BackupRestoreError("备份配置中的端口或备份保留天数无效")
        backup_time = str(restored.get("backup_time", "18:00"))
        if not re.fullmatch(r"\d{2}:\d{2}", backup_time) or int(backup_time[:2]) > 23 or int(backup_time[3:]) > 59:
            raise BackupRestoreError("备份配置中的自动备份时间无效")
        for key, default in (("backup_dir", "data/backups"), ("purchase_dir", "data/purchase")):
            raw_path = str(restored.get(key) or default)
            candidate = Path(raw_path)
            if candidate.is_absolute():
                try:
                    candidate = candidate.resolve().relative_to(self.base_directory.resolve())
                except (ValueError, OSError):
                    candidate = Path(default)
            if ".." in candidate.parts or not candidate.parts or candidate == Path("."):
                candidate = Path(default)
            restored[key] = candidate.as_posix() or default
        cert_raw = str(restored.get("ssl_certfile") or "")
        key_raw = str(restored.get("ssl_keyfile") or "")
        if bool(cert_raw) != bool(key_raw):
            restored["ssl_certfile"] = ""
            restored["ssl_keyfile"] = ""
        elif cert_raw:
            cert_path = self._resolve_config_file(cert_raw)
            key_path = self._resolve_config_file(key_raw)
            if not cert_path or not key_path or not cert_path.is_file() or not key_path.is_file():
                restored["ssl_certfile"] = ""
                restored["ssl_keyfile"] = ""
        restored["https"] = bool(restored.get("https", True))
        restored["auto_backup"] = bool(restored.get("auto_backup", True))
        return restored

    def read_full_bundle(self, content, stage_root: Path):
        try:
            archive = zipfile.ZipFile(io.BytesIO(content) if isinstance(content, bytes) else content, "r")
        except zipfile.BadZipFile as exc:
            raise BackupRestoreError("上传文件不是有效的门店完整备份 ZIP") from exc
        with archive:
            infos = archive.infolist()
            if len(infos) > 10000 or sum(x.file_size for x in infos) > 1024 * 1024 * 1024:
                raise BackupRestoreError("备份内容超过允许的文件数量或 1 GB 解压限制", 413)
            names = set()
            archive_sizes = {}
            folded_names = set()
            for info in infos:
                if info.is_dir():
                    continue
                name = info.filename
                if name.casefold() in folded_names:
                    raise BackupRestoreError("备份 ZIP 存在重复文件路径")
                names.add(name)
                archive_sizes[name] = info.file_size
                folded_names.add(name.casefold())
                if name != "manifest.json":
                    self._safe_relative(name)
                mode = (info.external_attr >> 16) & 0xFFFF
                if stat.S_ISLNK(mode):
                    raise BackupRestoreError("备份 ZIP 不允许包含符号链接")
            if "manifest.json" not in names:
                raise BackupRestoreError("完整备份缺少 manifest.json")
            if archive_sizes["manifest.json"] > 4 * 1024 * 1024 or archive_sizes.get("data/config.json", 0) > 2 * 1024 * 1024:
                raise BackupRestoreError("备份清单或配置文件超过大小限制", 413)
            try:
                manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
            except Exception as exc:
                raise BackupRestoreError("备份 manifest.json 无法读取") from exc
            if not isinstance(manifest, dict) or manifest.get("format") != self.backup_format:
                raise BackupRestoreError("不支持的备份格式")
            try:
                version = int(manifest.get("format_version", 0))
            except (TypeError, ValueError, OverflowError) as exc:
                raise BackupRestoreError("备份格式版本无效") from exc
            if version < 1 or version > self.backup_format_version:
                raise BackupRestoreError(f"备份格式版本 {version} 不受当前程序支持")
            file_manifest = manifest.get("files")
            if not isinstance(file_manifest, dict) or not {"data/inventory.db", "data/config.json"}.issubset(file_manifest):
                raise BackupRestoreError("完整备份缺少数据库或配置文件")
            expected_names = set(file_manifest) | {"manifest.json"}
            if names != expected_names:
                raise BackupRestoreError("备份文件清单与 manifest 不一致")
            extracted = {}
            for name, meta in file_manifest.items():
                try:
                    self._safe_relative(name)
                    if not isinstance(meta, dict):
                        raise ValueError("invalid manifest entry")
                    size = int(meta.get("size", -1))
                except (ValueError, TypeError, OverflowError) as exc:
                    raise BackupRestoreError("备份文件清单包含非法项目") from exc
                if size != archive_sizes[name]:
                    raise BackupRestoreError(f"备份文件大小校验失败：{name}")
                out = Path(stage_root).joinpath(*PurePosixPath(name).parts)
                digest = hashlib.sha256()
                try:
                    out.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(name) as source, out.open("xb") as file:
                        for chunk in iter(lambda: source.read(1024 * 1024), b""):
                            digest.update(chunk)
                            file.write(chunk)
                except (FileExistsError, NotADirectoryError, IsADirectoryError) as exc:
                    raise BackupRestoreError("备份包含互相冲突的文件路径") from exc
                except (KeyError, RuntimeError, zipfile.BadZipFile, NotImplementedError) as exc:
                    raise BackupRestoreError(f"无法从备份读取文件：{name}") from exc
                if digest.hexdigest() != meta.get("sha256"):
                    raise BackupRestoreError(f"备份文件校验失败：{name}")
                extracted[name] = out
        try:
            config_raw = json.loads(extracted["data/config.json"].read_text(encoding="utf-8"))
        except Exception as exc:
            raise BackupRestoreError("备份配置文件无法解析") from exc
        if not isinstance(config_raw, dict):
            raise BackupRestoreError("备份中的 config.json 格式不正确")
        (Path(stage_root) / "data" / "product_images").mkdir(parents=True, exist_ok=True)
        (Path(stage_root) / "data" / "purchase").mkdir(parents=True, exist_ok=True)
        return manifest, extracted
