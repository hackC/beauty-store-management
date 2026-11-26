"""Backup catalog and path policy; archive construction remains an injected adapter."""
from __future__ import annotations
from datetime import datetime
from pathlib import Path


class BackupService:
    def __init__(self, directory_resolver, create_backup):
        self._directory_resolver = directory_resolver
        self._create_backup = create_backup

    def list_backups(self):
        folder = Path(self._directory_resolver()).resolve()
        folder.mkdir(parents=True, exist_ok=True)
        items = []
        for file in sorted(folder.glob("*.db"), key=lambda item: item.stat().st_mtime, reverse=True):
            images = folder / f"{file.stem}_images.zip"
            bundle = folder / f"{file.stem}_完整备份.zip"
            items.append({
                "name": file.name,
                "size": file.stat().st_size,
                "images_archive": images.name if images.is_file() else "",
                "bundle_archive": bundle.name if bundle.is_file() else "",
                "bundle_size": bundle.stat().st_size if bundle.is_file() else 0,
                "backup_type": "完整备份" if bundle.is_file() else "旧版数据库备份",
                "time": datetime.fromtimestamp(file.stat().st_mtime).isoformat(timespec="seconds"),
            })
        return items

    def create_backup(self):
        return self._create_backup()

    def resolve_download(self, name: str) -> Path | None:
        folder = Path(self._directory_resolver()).resolve()
        target = (folder / str(name)).resolve()
        if target.parent != folder or not target.is_file():
            return None
        return target
