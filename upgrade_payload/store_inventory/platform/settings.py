"""System settings application service; HTTP validation stays in the API adapter."""
from __future__ import annotations
import json


class SystemSettingsService:
    def __init__(self, config_saver, connection_factory, audit_writer):
        self._save_config = config_saver
        self._connect = connection_factory
        self._write_audit = audit_writer

    def persist(self, config: dict, request=None) -> dict:
        # Keep legacy ordering: durable config file first, then mirrored DB settings + audit.
        self._save_config(config)
        con = self._connect()
        try:
            for key, value in config.items():
                con.execute("""
                    INSERT INTO settings(key,value) VALUES(?,?)
                    ON CONFLICT(key) DO UPDATE SET value=excluded.value
                """, (key, json.dumps(value, ensure_ascii=False)))
            self._write_audit(
                con, "修改系统设置", "更新服务端配置", request, "settings", "server", None,
                {"port": config.get("port"), "https": config.get("https"),
                 "ssl_certfile": config.get("ssl_certfile"), "system_name": config.get("system_name")}
            )
            con.commit()
        finally:
            con.close()
        return config
