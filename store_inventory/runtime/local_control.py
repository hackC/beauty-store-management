"""Per-installation lease and cooperative stop for the desktop and local scripts."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
from secrets import token_hex
import time


class LocalControl:
    def __init__(self, directory: Path):
        self.directory = directory
        self.lock = directory / ".server.lock"
        self.current = directory / ".server-instance.json"
        self.stop = directory / ".server-stop.json"
        self.stopped = directory / ".server-stopped.json"

    @staticmethod
    def _read(path):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    @staticmethod
    def _write(path, value):
        temp = path.with_name(f"{path.name}.{token_hex(8)}.tmp")
        try:
            temp.write_text(json.dumps(value), encoding="utf-8")
            os.replace(temp, path)
        finally:
            temp.unlink(missing_ok=True)

    @staticmethod
    def _try_lock(fh):
        fh.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except (BlockingIOError, PermissionError):
            return False
        except OSError as exc:
            if os.name == "nt" and exc.errno == 13:
                return False
            raise

    @contextmanager
    def _lock_file(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        with self.lock.open("a+b") as fh:
            if fh.tell() == 0:
                fh.write(b"\0")
                fh.flush()
            yield fh

    def _is_free(self):
        with self._lock_file() as fh:
            return self._try_lock(fh)

    @contextmanager
    def offline_maintenance(self):
        with self._lock_file() as fh:
            if not self._try_lock(fh):
                raise RuntimeError("当前数据目录已有服务器实例或维护任务")
            self.current.unlink(missing_ok=True)
            yield

    @contextmanager
    def lease(self, endpoint):
        with self._lock_file() as fh:
            if not self._try_lock(fh):
                raise RuntimeError("当前数据目录已有服务器实例运行，请先停止该实例")
            marker = {"pid": os.getpid(), "instance": endpoint.instance,
                      "version": endpoint.version, "port": endpoint.port, "https": endpoint.https}
            self.stop.unlink(missing_ok=True)
            self.stopped.unlink(missing_ok=True)
            self._write(self.current, marker)
            try:
                yield marker
            finally:
                self.current.unlink(missing_ok=True)
                self.stop.unlink(missing_ok=True)

    def observe(self):
        marker = self._read(self.current)
        if marker and not self._is_free():
            return marker
        return {}

    def watch(self, marker, worker_stop, stop_instance):
        while not worker_stop.wait(0.25):
            if self._read(self.stop) == marker:
                stop_instance()
                return

    def confirm_stopped(self, marker):
        if self._read(self.stop) == marker:
            self._write(self.stopped, marker)

    def request_stop(self, timeout=45):
        marker = self.observe()
        if not marker:
            return self._is_free()
        self._write(self.stop, marker)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._read(self.stopped) == marker:
                return True
            if self._is_free():
                return self._read(self.stopped) == marker
            time.sleep(0.25)
        return False
