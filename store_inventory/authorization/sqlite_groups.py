"""SQLite persistence adapter for permission-group administration."""
from __future__ import annotations

import json
import sqlite3
from typing import Callable, Mapping

from .domain import AuditActor, AuthorizationError


class SQLitePermissionGroupRepository:
    def __init__(self, connect: Callable[[], sqlite3.Connection]):
        self._connect = connect

    @staticmethod
    def _decode_permissions(value) -> dict:
        try:
            result = json.loads(value or "{}")
        except (TypeError, ValueError):
            result = {}
        return result if isinstance(result, dict) else {}

    @staticmethod
    def _audit(con, now: str, action: str, detail: str, actor: AuditActor,
               entity_type: str, entity_id: str, before=None, after=None):
        con.execute(
            """INSERT INTO audit_logs(
                time,action,detail,actor_user_id,actor_name,entity_type,entity_id,
                ip_address,before_json,after_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (now, action, detail, actor.user_id, actor.display_name, entity_type,
             str(entity_id or ""), actor.ip_address,
             json.dumps(before, ensure_ascii=False, default=str) if before is not None else "",
             json.dumps(after, ensure_ascii=False, default=str) if after is not None else ""),
        )

    def list_groups(self) -> list[dict]:
        con = self._connect()
        try:
            groups = con.execute(
                "SELECT * FROM permission_groups ORDER BY name COLLATE NOCASE"
            ).fetchall()
            members = con.execute(
                "SELECT group_id,user_id FROM permission_group_members ORDER BY created_at"
            ).fetchall()
            mapping: dict[str, list[str]] = {}
            for member in members:
                mapping.setdefault(member["group_id"], []).append(member["user_id"])
            return [{"id": group["id"], "name": group["name"],
                     "permissions": self._decode_permissions(group["permissions"]),
                     "member_ids": mapping.get(group["id"], [])} for group in groups]
        finally:
            con.close()

    def get_group(self, group_id: str) -> dict | None:
        con = self._connect()
        try:
            row = con.execute("SELECT * FROM permission_groups WHERE id=?", (group_id,)).fetchone()
            if not row:
                return None
            return {"id": row["id"], "name": row["name"],
                    "permissions": self._decode_permissions(row["permissions"])}
        finally:
            con.close()

    def create_group(self, group_id: str, name: str, permissions: Mapping[str, bool],
                     now: str, actor: AuditActor) -> None:
        con = self._connect()
        try:
            con.execute(
                "INSERT INTO permission_groups(id,name,permissions,created_at,updated_at) VALUES(?,?,?,?,?)",
                (group_id, name, json.dumps(dict(permissions), ensure_ascii=False), now, now),
            )
            self._audit(con, now, "新增权限规则组", name, actor, "permission_group", group_id,
                        None, {"name": name, "permissions": dict(permissions)})
            con.commit()
        except sqlite3.IntegrityError as exc:
            con.rollback()
            raise AuthorizationError(409, "已存在同名权限规则组") from exc
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    def update_group(self, group_id: str, name: str, permissions: Mapping[str, bool],
                     known_permission_keys: frozenset[str], now: str,
                     actor: AuditActor) -> None:
        con = self._connect()
        try:
            con.execute("BEGIN IMMEDIATE")
            old = con.execute("SELECT * FROM permission_groups WHERE id=?", (group_id,)).fetchone()
            if not old:
                raise AuthorizationError(404, "权限规则组不存在")
            before_permissions = self._decode_permissions(old["permissions"])
            # Merge dormant module grants while holding the same write transaction;
            # this prevents a concurrent group edit from dropping uninstalled grants.
            final_permissions = dict(permissions)
            final_permissions.update({
                key: bool(value) for key, value in before_permissions.items()
                if key not in known_permission_keys
            })
            con.execute(
                "UPDATE permission_groups SET name=?,permissions=?,updated_at=? WHERE id=?",
                (name, json.dumps(final_permissions, ensure_ascii=False), now, group_id),
            )
            self._audit(con, now, "编辑权限规则组", name, actor, "permission_group", group_id,
                        {"name": old["name"], "permissions": before_permissions},
                        {"name": name, "permissions": final_permissions})
            con.commit()
        except AuthorizationError:
            con.rollback()
            raise
        except sqlite3.IntegrityError as exc:
            con.rollback()
            raise AuthorizationError(409, "已存在同名权限规则组") from exc
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    def delete_group(self, group_id: str, actor: AuditActor, now: str) -> None:
        con = self._connect()
        try:
            old = con.execute("SELECT * FROM permission_groups WHERE id=?", (group_id,)).fetchone()
            if not old:
                raise AuthorizationError(404, "权限规则组不存在")
            con.execute("DELETE FROM permission_groups WHERE id=?", (group_id,))
            self._audit(con, now, "删除权限规则组", old["name"], actor,
                        "permission_group", group_id, {"name": old["name"]}, {"deleted": True})
            con.commit()
        except AuthorizationError:
            con.rollback()
            raise
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    def add_member(self, group_id: str, user_id: str, actor: AuditActor, now: str) -> None:
        con = self._connect()
        try:
            group = con.execute("SELECT name FROM permission_groups WHERE id=?", (group_id,)).fetchone()
            if not group:
                raise AuthorizationError(404, "权限规则组不存在")
            user = con.execute("SELECT username,role FROM users WHERE id=?", (user_id,)).fetchone()
            if not user:
                raise AuthorizationError(404, "员工不存在")
            if user["role"] == "admin":
                raise AuthorizationError(400, "不能把管理员加入普通权限组")
            con.execute(
                "INSERT OR IGNORE INTO permission_group_members(group_id,user_id,created_at) VALUES(?,?,?)",
                (group_id, user_id, now),
            )
            self._audit(con, now, "人员加入权限组", group["name"], actor,
                        "user", user_id, None, {"group_id": group_id, "username": user["username"]})
            con.commit()
        except AuthorizationError:
            con.rollback()
            raise
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    def remove_member(self, group_id: str, user_id: str, actor: AuditActor, now: str) -> None:
        con = self._connect()
        try:
            if not con.execute("SELECT 1 FROM permission_groups WHERE id=?", (group_id,)).fetchone():
                raise AuthorizationError(404, "权限规则组不存在")
            con.execute("DELETE FROM permission_group_members WHERE group_id=? AND user_id=?",
                        (group_id, user_id))
            self._audit(con, now, "人员移出权限组", group_id, actor, "user", user_id,
                        {"group_id": group_id}, {"removed": True})
            con.commit()
        except AuthorizationError:
            con.rollback()
            raise
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()
