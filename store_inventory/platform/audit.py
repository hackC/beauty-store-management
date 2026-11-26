"""Shared audit values and SQLite writer; accepts an existing transaction connection."""
from __future__ import annotations

from dataclasses import dataclass
import json


@dataclass(frozen=True)
class AuditActor:
    user_id: str = ""
    display_name: str = ""
    ip_address: str = ""
    username: str = ""


class SQLiteAuditWriter:
    def write(self, connection, action: str, detail: str = "", actor: AuditActor | None = None,
              entity_type: str = "", entity_id: str = "", before=None, after=None,
              created_at: str | None = None) -> None:
        from datetime import datetime
        actor = actor or AuditActor()
        timestamp = created_at or datetime.now().isoformat(timespec="seconds")
        connection.execute("""
            INSERT INTO audit_logs(
                time,action,detail,actor_user_id,actor_name,entity_type,entity_id,
                ip_address,before_json,after_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?)
        """, (
            timestamp, action, detail, actor.user_id, actor.display_name,
            entity_type, str(entity_id or ""), actor.ip_address,
            json.dumps(before, ensure_ascii=False, default=str) if before is not None else "",
            json.dumps(after, ensure_ascii=False, default=str) if after is not None else "",
        ))
