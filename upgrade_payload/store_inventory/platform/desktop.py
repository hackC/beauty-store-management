"""Desktop manager adapter for file opening and backup actions."""
from __future__ import annotations
from pathlib import Path
from typing import Callable


class DesktopManagementService:
    def __init__(self, backup_action: Callable, open_path: Callable[[str], None],
                 data_dir: Path, log_file: Path, *, restore_action: Callable | None = None):
        self._backup_action = backup_action
        self._restore_action = restore_action
        self._open_path = open_path
        self._data_dir = Path(data_dir)
        self._log_file = Path(log_file)

    def create_backup(self):
        return self._backup_action()

    def restore_backup(self, path: Path):
        if self._restore_action is None:
            raise RuntimeError("当前环境未配置备份恢复")
        return self._restore_action(path)

    def open_data_directory(self):
        self._open_path(str(self._data_dir))

    def server_log_exists(self) -> bool:
        return self._log_file.exists()

    def open_server_log(self):
        self._open_path(str(self._log_file))
