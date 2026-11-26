"""File-backed settings with atomic writes and a recoverable last saved copy."""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import shutil
import tempfile


class JsonConfigStore:
    def __init__(self, path: Path, defaults: dict, logger=None):
        self.path = Path(path)
        self.last_good = self.path.with_name(self.path.name + ".last-good")
        self.defaults = dict(defaults)
        self.logger = logger or logging.getLogger(__name__)

    def _read(self, path: Path) -> dict:
        saved = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(saved, dict):
            raise ValueError("配置根节点必须为对象")
        return {**self.defaults, **saved}

    def load(self) -> dict:
        try:
            saved = self._read(self.path)
        except FileNotFoundError:
            if not self.last_good.exists():
                self.save(self.defaults)
                return dict(self.defaults)
        except (OSError, ValueError):
            self.logger.exception("读取配置文件失败，原文件已保留：%s", self.path)
        else:
            if not self.last_good.exists():
                try:
                    self._replace(self.last_good, json.dumps(saved, ensure_ascii=False, indent=2))
                except OSError:
                    self.logger.exception("无法创建最近配置副本：%s", self.last_good)
            return saved
        try:
            restored = self._read(self.last_good)
        except (OSError, ValueError) as exc:
            raise RuntimeError(f"配置无法读取。请从备份还原配置文件：{self.path}") from exc
        self.logger.warning("使用最近一次保存的配置：%s", self.last_good)
        return restored

    @staticmethod
    def _replace(path: Path, content: str) -> None:
        fd, temp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as out:
                out.write(content)
                out.flush()
                os.fsync(out.fileno())
            os.replace(temp, path)
        finally:
            Path(temp).unlink(missing_ok=True)

    def save(self, config: dict) -> None:
        if not isinstance(config, dict):
            raise TypeError("配置必须为对象")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._read(self.path)
        except FileNotFoundError:
            pass
        except (ValueError, UnicodeError):
            fd, name = tempfile.mkstemp(prefix=self.path.name + ".", suffix=".invalid", dir=self.path.parent)
            os.close(fd)
            shutil.copyfile(self.path, name)
        content = json.dumps(config, ensure_ascii=False, indent=2)
        self._replace(self.path, content)
        try:
            self._replace(self.last_good, content)
        except OSError:
            self.logger.exception("当前配置已保存，但最近配置副本更新失败：%s", self.last_good)
