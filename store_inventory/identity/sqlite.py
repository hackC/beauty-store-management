"""SQLite adapter for identity and session persistence."""
from __future__ import annotations

import json
import sqlite3
from typing import Callable, Mapping

from .domain import AuditActor, IdentityError


class SQLiteIdentityRepository:
    def __init__(self, connect: Callable[[], sqlite3.Connection]):
        self._connect = connect

    @staticmethod
    def _dict(row):
        return dict(row) if row is not None else None

    def get_activation_default(self) -> tuple[str, str] | None:
        con = self._connect()
        try:
            row = con.execute("SELECT value FROM settings WHERE key='identity_activation_default'").fetchone()
            return tuple(json.loads(row[0])) if row else None
        finally:
            con.close()

    def save_activation_default(self, verifier: tuple[str, str] | None) -> None:
        con = self._connect()
        try:
            with con:
                if verifier is None:
                    con.execute("DELETE FROM settings WHERE key='identity_activation_default'")
                else:
                    con.execute(
                        "INSERT OR REPLACE INTO settings (key,value) VALUES ('identity_activation_default',?)",
                        (json.dumps(verifier),),
                    )
        finally:
            con.close()

    def count_users(self) -> int:
        con = self._connect()
        try:
            return int(con.execute("SELECT COUNT(*) FROM users").fetchone()[0])
        finally:
            con.close()

    def find_user_by_username(self, username: str) -> dict | None:
        con = self._connect()
        try:
            return self._dict(con.execute(
                "SELECT * FROM users WHERE username=? AND enabled=1", (username,)
            ).fetchone())
        finally:
            con.close()

    def find_session_user(self, token_hash: str) -> dict | None:
        con = self._connect()
        try:
            return self._dict(con.execute(
                """SELECT u.*, s.expires_at FROM user_sessions s
                   JOIN users u ON u.id=s.user_id WHERE s.token_hash=?""",
                (token_hash,),
            ).fetchone())
        finally:
            con.close()

    def list_users(self) -> list[dict]:
        con = self._connect()
        try:
            return [dict(row) for row in con.execute(
                "SELECT * FROM users ORDER BY created_at"
            ).fetchall()]
        finally:
            con.close()


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

    def create_staff(self, user: Mapping, group_ids: list[str], now: str,
                     actor: AuditActor) -> None:
        con = self._connect()
        try:
            con.execute("BEGIN IMMEDIATE")
            if group_ids:
                placeholders = ",".join("?" for _ in group_ids)
                found = con.execute(
                    f"SELECT id FROM permission_groups WHERE id IN ({placeholders})", group_ids
                ).fetchall()
                if len(found) != len(group_ids):
                    raise IdentityError(400, "选择的权限规则组不存在")
            con.execute(
                """INSERT INTO users(
                    id,username,display_name,salt,password_hash,role,permissions,enabled,
                    must_set_password,activation_salt,activation_code_hash,created_at,updated_at
                ) VALUES(?,?,?,?,?,'staff',?,1,?,?,?,?,?)""",
                (user["id"], user["username"], user["display_name"], user["salt"],
                 user["password_hash"], "{}", user["must_set_password"],
                 user["activation_salt"], user["activation_code_hash"], now, now),
            )
            for group_id in group_ids:
                con.execute(
                    "INSERT INTO permission_group_members(group_id,user_id,created_at) VALUES(?,?,?)",
                    (group_id, user["id"], now),
                )
            self._audit(
                con, now, "新增人员", user["username"], actor, "user", user["id"], None,
                {"username": user["username"], "display_name": user["display_name"],
                 "permissions": {}, "group_ids": list(group_ids), "enabled": True,
                 "must_set_password": bool(user["must_set_password"])},
            )
            con.commit()
        except IdentityError:
            con.rollback()
            raise
        except sqlite3.IntegrityError as exc:
            con.rollback()
            raise IdentityError(409, "登录账号已经存在") from exc
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    def update_staff(self, user_id: str, display_name: str | None, enabled: bool | None,
                     now: str, actor: AuditActor) -> None:
        con = self._connect()
        try:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
            if not row:
                raise IdentityError(404, "人员不存在")
            if enabled is False and row["role"] == "admin":
                admin_count = con.execute(
                    "SELECT COUNT(*) FROM users WHERE role='admin' AND enabled=1"
                ).fetchone()[0]
                if admin_count <= 1:
                    raise IdentityError(400, "不能禁用最后一个管理员")
            if display_name is not None:
                con.execute("UPDATE users SET display_name=? WHERE id=?", (display_name, user_id))
            if enabled is not None:
                con.execute("UPDATE users SET enabled=? WHERE id=?", (1 if enabled else 0, user_id))
                if not enabled:
                    con.execute("DELETE FROM user_sessions WHERE user_id=?", (user_id,))
            con.execute("UPDATE users SET updated_at=? WHERE id=?", (now, user_id))
            updated = con.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
            try:
                old_permissions = json.loads(row["permissions"] or "{}")
            except (TypeError, ValueError):
                old_permissions = {}
            try:
                new_permissions = json.loads(updated["permissions"] or "{}")
            except (TypeError, ValueError):
                new_permissions = {}
            self._audit(
                con, now, "修改人员资料或权限", row["username"], actor, "user", user_id,
                {"username": row["username"], "display_name": row["display_name"],
                 "permissions": old_permissions, "enabled": bool(row["enabled"])},
                {"username": updated["username"], "display_name": updated["display_name"],
                 "permissions": new_permissions, "enabled": bool(updated["enabled"])},
            )
            con.commit()
        except IdentityError:
            con.rollback()
            raise
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    def regenerate_activation_code(self, user_id: str, salt: str, code_hash: str,
                                   now: str, actor: AuditActor) -> None:
        con = self._connect()
        try:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute(
                "SELECT username,must_set_password FROM users WHERE id=?", (user_id,)
            ).fetchone()
            if not row:
                raise IdentityError(404, "人员不存在")
            if not row["must_set_password"]:
                raise IdentityError(409, "该账号已经完成首次密码设置，无需重新生成登录码")
            con.execute(
                "UPDATE users SET activation_salt=?,activation_code_hash=?,updated_at=? "
                "WHERE id=? AND must_set_password=1", (salt, code_hash, now, user_id)
            )
            con.execute("DELETE FROM user_sessions WHERE user_id=?", (user_id,))
            self._audit(con, now, "重新生成首次登录码", row["username"], actor,
                        "user", user_id, {"activation_code_reset": False},
                        {"activation_code_reset": True, "sessions_revoked": True})
            con.commit()
        except IdentityError:
            con.rollback()
            raise
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    def reset_password(self, user_id: str, salt: str, password_hash: str,
                       now: str, actor: AuditActor) -> None:
        con = self._connect()
        try:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT username FROM users WHERE id=?", (user_id,)).fetchone()
            if not row:
                raise IdentityError(404, "人员不存在")
            con.execute(
                """UPDATE users SET salt=?,password_hash=?,must_set_password=0,
                   activation_salt='',activation_code_hash='',updated_at=? WHERE id=?""",
                (salt, password_hash, now, user_id),
            )
            con.execute("DELETE FROM user_sessions WHERE user_id=?", (user_id,))
            self._audit(con, now, "管理员重置密码", row["username"], actor,
                        "user", user_id, {"username": row["username"]},
                        {"password_reset": True, "sessions_revoked": True})
            con.commit()
        except IdentityError:
            con.rollback()
            raise
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    def create_first_admin(self, user: Mapping, created_at: str) -> bool:
        con = self._connect()
        try:
            con.execute("BEGIN IMMEDIATE")
            if con.execute("SELECT COUNT(*) FROM users").fetchone()[0] > 0:
                con.rollback()
                return False
            con.execute(
                """INSERT INTO users(
                    id,username,display_name,salt,password_hash,role,permissions,
                    enabled,created_at,updated_at
                ) VALUES(?,?,?,?,?,'admin','{}',1,?,?)""",
                (user["id"], user["username"], user["display_name"], user["salt"],
                 user["password_hash"], created_at, created_at),
            )
            con.execute(
                "INSERT INTO audit_logs(time,action,detail) VALUES(?,?,?)",
                (created_at, "初始化管理员", user["username"]),
            )
            con.commit()
            return True
        except sqlite3.IntegrityError:
            con.rollback()
            return False
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    def consume_activation_code(self, user_id: str, expected_salt: str,
                                expected_hash: str, token_hash: str,
                                expires_at: str, created_at: str) -> bool:
        con = self._connect()
        try:
            con.execute("BEGIN IMMEDIATE")
            current = con.execute(
                """SELECT enabled,must_set_password,activation_salt,activation_code_hash
                   FROM users WHERE id=?""", (user_id,)
            ).fetchone()
            if (not current or not current["enabled"] or not current["must_set_password"]
                    or current["activation_salt"] != expected_salt
                    or current["activation_code_hash"] != expected_hash):
                con.rollback()
                return False
            con.execute(
                "UPDATE users SET activation_salt='',activation_code_hash='' WHERE id=?",
                (user_id,),
            )
            con.execute("DELETE FROM user_sessions WHERE user_id=?", (user_id,))
            con.execute(
                "INSERT INTO user_sessions(token_hash,user_id,expires_at,created_at) VALUES(?,?,?,?)",
                (token_hash, user_id, expires_at, created_at),
            )
            con.commit()
            return True
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    def create_session(self, token_hash: str, user_id: str,
                       expires_at: str, created_at: str) -> None:
        con = self._connect()
        try:
            con.execute(
                "INSERT INTO user_sessions(token_hash,user_id,expires_at,created_at) VALUES(?,?,?,?)",
                (token_hash, user_id, expires_at, created_at),
            )
            con.commit()
        finally:
            con.close()

    def delete_session(self, token_hash: str) -> None:
        con = self._connect()
        try:
            con.execute("DELETE FROM user_sessions WHERE token_hash=?", (token_hash,))
            con.commit()
        finally:
            con.close()

    def delete_user_sessions(self, user_id: str,
                             keep_token_hash: str | None = None) -> None:
        con = self._connect()
        try:
            if keep_token_hash:
                con.execute(
                    "DELETE FROM user_sessions WHERE user_id=? AND token_hash<>?",
                    (user_id, keep_token_hash),
                )
            else:
                con.execute("DELETE FROM user_sessions WHERE user_id=?", (user_id,))
            con.commit()
        finally:
            con.close()

    def set_first_password(self, user_id: str, salt: str, password_hash: str,
                           updated_at: str, keep_token_hash: str,
                           actor: AuditActor) -> None:
        con = self._connect()
        try:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute(
                "SELECT username,must_set_password FROM users WHERE id=?", (user_id,)
            ).fetchone()
            if not row:
                raise IdentityError(404, "用户不存在")
            if not row["must_set_password"]:
                raise IdentityError(409, "登录密码已经设置，请使用新密码登录")
            con.execute(
                """UPDATE users SET salt=?,password_hash=?,must_set_password=0,
                   activation_salt='',activation_code_hash='',updated_at=? WHERE id=?""",
                (salt, password_hash, updated_at, user_id),
            )
            con.execute(
                "DELETE FROM user_sessions WHERE user_id=? AND token_hash<>?",
                (user_id, keep_token_hash),
            )
            con.execute(
                """INSERT INTO audit_logs(
                    time,action,detail,actor_user_id,actor_name,entity_type,entity_id,
                    ip_address,before_json,after_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (updated_at, "员工首次设置登录密码", row["username"], actor.user_id,
                 actor.display_name, "user", user_id, actor.ip_address,
                 json.dumps({"must_set_password": True}, ensure_ascii=False),
                 json.dumps({"must_set_password": False}, ensure_ascii=False)),
            )
            con.commit()
        except IdentityError:
            con.rollback()
            raise
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()
