"""Identity use cases. Depends only on ports and domain values."""
from __future__ import annotations

import json
import uuid
import secrets
from datetime import datetime, timedelta
from typing import Mapping

from .domain import AuditActor, IdentityError, LoginResult
from .ports import Clock, IdentityRepository, PasswordHasher, PermissionView, SessionTokenFactory


class SystemClock:
    def now(self):
        return datetime.now()


class IdentityService:
    def __init__(self, repository: IdentityRepository, hasher: PasswordHasher,
                 tokens: SessionTokenFactory, permissions: PermissionView,
                 clock: Clock | None = None, session_days: int = 14):
        self.repository = repository
        self.hasher = hasher
        self.tokens = tokens
        self.permissions = permissions
        self.clock = clock or SystemClock()
        self.session_days = session_days

    def make_password(self, password: str) -> tuple[str, str]:
        return self.hasher.hash(password)

    def verify_password(self, password: str, salt: str, password_hash: str) -> bool:
        return self.hasher.verify(password, salt, password_hash)

    def public_user(self, row: Mapping) -> dict:
        saved = row.get("permissions") or "{}"
        try:
            saved_direct = json.loads(saved) if isinstance(saved, str) else dict(saved)
        except (TypeError, ValueError):
            saved_direct = {}
        if not isinstance(saved_direct, dict):
            saved_direct = {}
        direct = {key: bool(saved_direct.get(key, False))
                  for key in self.permissions.permission_labels}
        group_rights, group_ids = self.permissions.group_rights(str(row["id"]))
        effective = {**direct, **group_rights}
        expand = getattr(self.permissions, "expand_legacy_permissions", None)
        if callable(expand):
            effective = expand(effective)
        return {
            "id": row["id"], "username": row["username"],
            "display_name": row["display_name"], "role": row["role"],
            "permissions": effective, "direct_permissions": direct,
            "group_ids": group_ids, "enabled": bool(row["enabled"]),
            "must_set_password": bool(row.get("must_set_password", 0)),
        }

    def resolve_session(self, token: str | None) -> dict | None:
        if not token:
            return None
        token_hash = self.tokens.digest(token)
        row = self.repository.find_session_user(token_hash)
        if not row:
            return None
        if not row.get("enabled"):
            self.repository.delete_session(token_hash)
            return None
        try:
            expired = row["expires_at"] != "never" and datetime.fromisoformat(str(row["expires_at"])) < self.clock.now()
        except (TypeError, ValueError):
            expired = True
        if expired:
            self.repository.delete_session(token_hash)
            return None
        return self.public_user(row)

    def session_is_permanent(self, token: str) -> bool:
        row = self.repository.find_session_user(self.tokens.digest(token))
        return bool(row and row["expires_at"] == "never")

    def issue_session(self, user_id: str, *, session_days: int | None = 14) -> str:
        token = self.tokens.create()
        now = self.clock.now()
        expires = "never" if session_days is None else (now + timedelta(days=session_days)).isoformat(timespec="seconds")
        self.repository.create_session(
            self.tokens.digest(token), user_id,
            expires, now.isoformat(timespec="seconds"),
        )
        return token

    def bootstrap_admin(self, username: str, display_name: str, password: str) -> str:
        salt, password_hash = self.hasher.hash(password)
        now = self.clock.now().isoformat(timespec="seconds")
        user_id = uuid.uuid4().hex
        created = self.repository.create_first_admin({
            "id": user_id, "username": username.strip(),
            "display_name": display_name.strip(), "salt": salt,
            "password_hash": password_hash,
        }, now)
        if not created:
            raise IdentityError(409, "管理员已经初始化，不能再次公开初始化")
        return self.issue_session(user_id, session_days=self.session_days)

    def login(self, username: str, password: str = "", activation_code: str = "", *, session_days: int | None = 14) -> LoginResult:
        row = self.repository.find_user_by_username(username.strip())
        if not row:
            raise IdentityError(401, "账号或首次登录码错误")
        if row.get("must_set_password"):
            code = (activation_code or password or "").strip()
            valid = bool(code and row.get("activation_salt") and row.get("activation_code_hash")
                         and self.hasher.verify(code, row["activation_salt"], row["activation_code_hash"]))
            if (activation_code and password) or not valid:
                raise IdentityError(401, "首次登录码错误或已失效。请在密码框输入管理员提供的首次登录码。")
            token = self.tokens.create()
            now = self.clock.now()
            expires = now + timedelta(days=self.session_days)
            if not self.repository.consume_activation_code(
                    row["id"], row["activation_salt"], row["activation_code_hash"],
                    self.tokens.digest(token), expires.isoformat(timespec="seconds"),
                    now.isoformat(timespec="seconds")):
                raise IdentityError(401, "首次登录码已使用或已失效，请联系管理员重新生成")
            return LoginResult(token, True)
        if not password or not self.hasher.verify(password, row.get("salt", ""), row.get("password_hash", "")):
            raise IdentityError(401, "账号或密码错误")
        return LoginResult(self.issue_session(row["id"], session_days=session_days), False)

    def logout(self, token: str | None) -> None:
        if token:
            self.repository.delete_session(self.tokens.digest(token))

    def set_first_password(self, user: Mapping, token: str | None, password: str,
                           actor: AuditActor) -> None:
        if not user.get("must_set_password"):
            raise IdentityError(409, "当前账号已完成首次密码设置")
        if not token:
            raise IdentityError(401, "登录状态已失效，请重新使用首次登录码登录")
        salt, password_hash = self.hasher.hash(password)
        self.repository.set_first_password(
            str(user["id"]), salt, password_hash,
            self.clock.now().isoformat(timespec="seconds"),
            self.tokens.digest(token), actor,
        )

    def has_activation_default(self) -> bool:
        return self.repository.get_activation_default() is not None

    def set_activation_default(self, code: str | None) -> None:
        verifier = self.hasher.hash(self.validate_activation_code(code)) if code is not None else None
        self.repository.save_activation_default(verifier)

    @staticmethod
    def validate_activation_code(code: str) -> str:
        if len(code) != 8 or not code.isascii() or not code.isdecimal():
            raise IdentityError(400, "首次登录码必须为 8 位数字")
        return code

    def activation_verifier(self, code: str | None, use_default: bool):
        if code is not None and use_default:
            raise IdentityError(400, "请只选择一种首次登录码来源")
        if use_default:
            verifier = self.repository.get_activation_default()
            if verifier is None:
                raise IdentityError(409, "尚未设置默认首次登录码")
            return None, *verifier
        code = self.validate_activation_code(code) if code is not None else f"{secrets.randbelow(100_000_000):08d}"
        return code, *self.hasher.hash(code)

    def create_staff(self, username: str, display_name: str, password: str | None,
                     group_ids: list[str], actor: AuditActor, *, activation_code: str | None = None,
                     use_default: bool = False) -> dict:
        clean_username = str(username or "").strip()
        clean_display_name = str(display_name or "").strip()
        if not clean_username or not clean_display_name:
            raise IdentityError(400, "登录账号和姓名不能为空")
        must_set_password = password is None
        if must_set_password:
            salt = password_hash = ""
            activation_code, activation_salt, activation_hash = self.activation_verifier(activation_code, use_default)
        else:
            if activation_code is not None or use_default:
                raise IdentityError(400, "密码与首次登录码不能同时指定")
            salt, password_hash = self.hasher.hash(password)
            activation_salt = activation_hash = ""
        user_id = uuid.uuid4().hex
        now = self.clock.now().isoformat(timespec="seconds")
        self.repository.create_staff({
            "id": user_id, "username": clean_username, "display_name": clean_display_name,
            "salt": salt, "password_hash": password_hash,
            "must_set_password": 1 if must_set_password else 0,
            "activation_salt": activation_salt, "activation_code_hash": activation_hash,
        }, group_ids, now, actor)
        result = {"ok": True, "id": user_id, "must_set_password": must_set_password}
        if activation_code:
            result["activation_code"] = activation_code
        if use_default:
            result["used_default_activation_code"] = True
        return result

    def update_staff(self, user_id: str, display_name: str | None,
                     enabled: bool | None, permissions: Mapping | None,
                     actor: AuditActor) -> None:
        if permissions is not None:
            raise IdentityError(400, "个人功能权限已停用；请通过权限规则组授权")
        clean_name = None if display_name is None else str(display_name).strip()
        if display_name is not None and not clean_name:
            raise IdentityError(400, "姓名不能为空")
        self.repository.update_staff(
            user_id, clean_name, enabled,
            self.clock.now().isoformat(timespec="seconds"), actor,
        )

    def regenerate_activation_code(self, user_id: str, actor: AuditActor, *,
                                   activation_code: str | None = None, use_default: bool = False) -> str | None:
        code, salt, code_hash = self.activation_verifier(activation_code, use_default)
        self.repository.regenerate_activation_code(
            user_id, salt, code_hash, self.clock.now().isoformat(timespec="seconds"), actor
        )
        return code

    def reset_password(self, user_id: str, password: str, actor: AuditActor) -> None:
        salt, password_hash = self.hasher.hash(password)
        self.repository.reset_password(
            user_id, salt, password_hash,
            self.clock.now().isoformat(timespec="seconds"), actor,
        )

    def revoke_user_sessions(self, user_id: str) -> None:
        self.repository.delete_user_sessions(user_id)

    def list_users(self) -> list[dict]:
        return [self.public_user(row) for row in self.repository.list_users()]

    def count_users(self) -> int:
        return self.repository.count_users()
