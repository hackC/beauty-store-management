"""dev.8 tests for identity and permission-group application boundaries."""
from __future__ import annotations

from datetime import datetime
import json
import sqlite3
from pathlib import Path

import pytest

from store_inventory.identity.domain import AuditActor, IdentityError
from store_inventory.identity.security import PBKDF2PasswordHasher, SecureSessionTokenFactory
from store_inventory.identity.service import IdentityService
from store_inventory.authorization.domain import AuditActor as AuthorizationActor, AuthorizationError
from store_inventory.authorization.service import PermissionGroupService


class FixedClock:
    def now(self):
        return datetime(2026, 11, 15, 10, 0, 0)


class PermissionView:
    permission_labels = {"dashboard": "首页", "products_view": "查看商品"}

    def __init__(self):
        self.grants = {}

    def group_rights(self, user_id):
        return self.grants.get(user_id, ({}, []))


class MemoryIdentityRepository:
    def __init__(self):
        self.users = {}
        self.sessions = {}
        self.audit = []

    def count_users(self):
        return len(self.users)

    def find_user_by_username(self, username):
        return next((dict(row) for row in self.users.values()
                     if row["username"] == username and row["enabled"]), None)

    def find_session_user(self, token_hash):
        session = self.sessions.get(token_hash)
        if not session:
            return None
        user = self.users.get(session["user_id"])
        return ({**user, "expires_at": session["expires_at"]} if user else None)

    def list_users(self):
        return [dict(row) for row in self.users.values()]

    def create_first_admin(self, user, created_at):
        if self.users:
            return False
        self.users[user["id"]] = {
            **dict(user), "role": "admin", "permissions": "{}", "enabled": 1,
            "must_set_password": 0, "created_at": created_at, "updated_at": created_at,
        }
        return True

    def add_staff(self, user):
        self.users[user["id"]] = dict(user)

    def consume_activation_code(self, user_id, expected_salt, expected_hash, token_hash, expires_at, created_at):
        user = self.users.get(user_id)
        if (not user or not user["must_set_password"]
                or user["activation_salt"] != expected_salt
                or user["activation_code_hash"] != expected_hash):
            return False
        user["activation_salt"] = ""
        user["activation_code_hash"] = ""
        self.delete_user_sessions(user_id)
        self.create_session(token_hash, user_id, expires_at, created_at)
        return True

    def create_session(self, token_hash, user_id, expires_at, created_at):
        self.sessions[token_hash] = {
            "user_id": user_id, "expires_at": expires_at, "created_at": created_at,
        }

    def delete_session(self, token_hash):
        self.sessions.pop(token_hash, None)

    def delete_user_sessions(self, user_id, keep_token_hash=None):
        for token_hash, session in list(self.sessions.items()):
            if session["user_id"] == user_id and token_hash != keep_token_hash:
                self.sessions.pop(token_hash, None)

    def set_first_password(self, user_id, salt, password_hash, updated_at,
                           keep_token_hash, actor):
        user = self.users.get(user_id)
        if not user:
            raise IdentityError(404, "用户不存在")
        if not user["must_set_password"]:
            raise IdentityError(409, "登录密码已经设置，请使用新密码登录")
        user.update({"salt": salt, "password_hash": password_hash,
                     "must_set_password": 0, "activation_salt": "",
                     "activation_code_hash": "", "updated_at": updated_at})
        self.delete_user_sessions(user_id, keep_token_hash)
        self.audit.append((actor, user_id))


def make_identity_service(repo=None):
    return IdentityService(
        repository=repo or MemoryIdentityRepository(),
        hasher=PBKDF2PasswordHasher(),
        tokens=SecureSessionTokenFactory(),
        permissions=PermissionView(),
        clock=FixedClock(),
    )


def test_identity_service_password_login_session_resolution_and_logout():
    repo = MemoryIdentityRepository()
    service = make_identity_service(repo)
    salt, digest = service.make_password("StaffPass123")
    repo.add_staff({
        "id": "staff-1", "username": "staff", "display_name": "员工",
        "salt": salt, "password_hash": digest, "role": "staff",
        "permissions": '{"dashboard":true}', "enabled": 1,
        "must_set_password": 0,
    })
    result = service.login("staff", "StaffPass123")
    user = service.resolve_session(result.token)
    assert result.must_set_password is False
    assert user["id"] == "staff-1"
    assert user["permissions"]["dashboard"] is True
    service.logout(result.token)
    assert service.resolve_session(result.token) is None


def test_identity_service_single_use_activation_and_first_password_flow():
    repo = MemoryIdentityRepository()
    service = make_identity_service(repo)
    activation_salt, activation_hash = service.make_password("12345678")
    repo.add_staff({
        "id": "pending-1", "username": "pending", "display_name": "待激活",
        "salt": "", "password_hash": "", "role": "staff", "permissions": "{}",
        "enabled": 1, "must_set_password": 1,
        "activation_salt": activation_salt, "activation_code_hash": activation_hash,
    })
    result = service.login("pending", activation_code="12345678")
    assert result.must_set_password is True
    with pytest.raises(IdentityError) as replay:
        service.login("pending", activation_code="12345678")
    assert replay.value.status_code == 401
    current = service.resolve_session(result.token)
    service.set_first_password(
        current, result.token, "NewStaffPass123", AuditActor("pending-1", "待激活", "127.0.0.1")
    )
    assert service.resolve_session(result.token)["must_set_password"] is False
    assert service.login("pending", "NewStaffPass123").must_set_password is False
    assert len(repo.audit) == 1


def test_identity_bootstrap_is_single_use_and_password_hash_format_is_compatible():
    repo = MemoryIdentityRepository()
    service = make_identity_service(repo)
    token = service.bootstrap_admin("manager", "店长", "ManagerPass123")
    assert service.resolve_session(token)["role"] == "admin"
    with pytest.raises(IdentityError) as duplicate:
        service.bootstrap_admin("manager2", "第二管理员", "ManagerPass456")
    assert duplicate.value.status_code == 409
    salt, digest = service.make_password("LegacyPass123")
    assert len(salt) == 32 and len(digest) == 64
    assert service.verify_password("LegacyPass123", salt, digest)
    assert not service.verify_password("wrong", salt, digest)


class MemoryPermissionGroupRepository:
    def __init__(self):
        self.groups = {}
        self.members = {}
        self.calls = []

    def list_groups(self):
        return []

    def get_group(self, group_id):
        return self.groups.get(group_id)

    def create_group(self, group_id, name, permissions, now, actor):
        self.groups[group_id] = {"id": group_id, "name": name, "permissions": dict(permissions)}
        self.calls.append(("create", actor))

    def update_group(self, group_id, name, permissions, known_permission_keys, now, actor):
        current = self.groups.get(group_id, {}).get("permissions", {})
        final = dict(permissions)
        final.update({key: bool(value) for key, value in current.items()
                      if key not in known_permission_keys})
        self.groups[group_id] = {"id": group_id, "name": name, "permissions": final}
        self.calls.append(("update", actor))

    def delete_group(self, group_id, actor, now):
        self.groups.pop(group_id, None)

    def add_member(self, group_id, user_id, actor, now):
        self.members.setdefault(group_id, set()).add(user_id)

    def remove_member(self, group_id, user_id, actor, now):
        self.members.setdefault(group_id, set()).discard(user_id)


def test_permission_group_service_validates_grants_and_preserves_dormant_module_rights():
    repo = MemoryPermissionGroupRepository()
    known = {"dashboard", "products_view"}

    def normalize(values):
        unknown = set(values) - known
        if unknown:
            raise ValueError("规则包含未知权限：" + ", ".join(sorted(unknown)))
        return {key: bool(value) for key, value in values.items()}

    service = PermissionGroupService(repo, normalize, known, clock=FixedClock().now)
    actor = AuthorizationActor("admin-1", "店长", "127.0.0.1")
    group_id = service.create_group("前台", {"dashboard": True}, actor)
    repo.groups[group_id]["permissions"]["ext.retired.view"] = True
    service.update_group(group_id, "前台新版", {"products_view": True}, actor)
    assert repo.groups[group_id]["permissions"] == {
        "products_view": True, "ext.retired.view": True,
    }
    with pytest.raises(AuthorizationError) as unknown:
        service.create_group("非法组", {"ext.unknown.view": True}, actor)
    assert unknown.value.status_code == 400


def test_dev8_core_modules_do_not_depend_on_main_or_web_frameworks():
    root = Path(__file__).resolve().parents[1]
    forbidden = ("from main", "import main", "from fastapi", "import fastapi", "import tkinter")
    for relative in (
        "store_inventory/identity/domain.py", "store_inventory/identity/ports.py",
        "store_inventory/identity/service.py", "store_inventory/identity/security.py",
        "store_inventory/authorization/domain.py", "store_inventory/authorization/ports.py",
        "store_inventory/authorization/service.py",
    ):
        content = (root / relative).read_text("utf-8").lower()
        assert not any(item in content for item in forbidden), relative


def test_dev8_app_shell_is_shared_by_inventory_and_schedule_pages():
    root = Path(__file__).resolve().parents[1]
    for relative, page in (("static/index.html", "inventory"),
                           ("static/index_V2_5_5.html", "schedule")):
        content = (root / relative).read_text("utf-8")
        assert '/static/app_shell.css' in content
        assert '/static/app_shell.js' in content
        assert f'data-app-page="{page}"' in content
    assert (root / "static/app_shell.css").is_file()
    assert (root / "static/app_shell.js").is_file()


def test_dev8_runtime_payload_matches_all_new_module_files():
    root = Path(__file__).resolve().parents[1]
    for relative in (
        "main.py", "scripts/verify_project.py", "static/app_shell.css", "static/app_shell.js",
        "static/index.html", "static/index_V2_5_5.html",
        "docs/ARCHITECTURE_DEV8_IDENTITY_AUTHORIZATION.md",
    ):
        assert (root / relative).read_bytes() == (root / "upgrade_payload" / relative).read_bytes()
    for package in ("identity", "authorization"):
        source_dir = root / "store_inventory" / package
        payload_dir = root / "upgrade_payload" / "store_inventory" / package
        for source in source_dir.glob("*.py"):
            assert source.read_bytes() == (payload_dir / source.name).read_bytes()
