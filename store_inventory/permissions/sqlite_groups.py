"""SQLite adapter for the group-grant repository port."""
from __future__ import annotations

import json
import sqlite3
from typing import Callable

from .domain import GroupGrant


class SQLiteGroupGrantRepository:
    def __init__(self, connect: Callable[[], sqlite3.Connection]):
        self._connect = connect

    def for_user(self, user_id: str) -> tuple[GroupGrant, ...]:
        con = self._connect()
        try:
            rows = con.execute(
                """SELECT g.id, g.permissions FROM permission_groups g
                   JOIN permission_group_members m ON g.id=m.group_id
                   WHERE m.user_id=? ORDER BY g.name""", (user_id,)
            ).fetchall()
            groups = []
            for group_id, encoded in rows:
                try:
                    values = json.loads(encoded or '{}')
                except (TypeError, ValueError):
                    values = {}
                # Malformed JSON shapes must not grant access or crash login.
                if not isinstance(values, dict):
                    values = {}
                groups.append(GroupGrant(group_id, values))
            return tuple(groups)
        finally:
            con.close()

