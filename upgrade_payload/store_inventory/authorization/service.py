"""Permission-group use cases, independent of HTTP and SQLite."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Callable, Mapping

from .domain import AuditActor, AuthorizationError
from .ports import PermissionGroupRepository


class PermissionGroupService:
    def __init__(self, repository: PermissionGroupRepository,
                 normalize_permissions: Callable[[Mapping[str, bool]], dict[str, bool]],
                 known_permission_keys: set[str] | frozenset[str], clock=None):
        self.repository = repository
        self.normalize_permissions = normalize_permissions
        self.known_permission_keys = known_permission_keys if callable(known_permission_keys) else lambda: frozenset(known_permission_keys)
        self.clock = clock or datetime.now

    def _now(self) -> str:
        return self.clock().isoformat(timespec="seconds")

    @staticmethod
    def _name(value: str) -> str:
        name = str(value or "").strip()
        if not name:
            raise AuthorizationError(400, "权限组名称不能为空")
        if len(name) > 40:
            raise AuthorizationError(422, "权限组名称不能超过 40 个字符")
        return name

    def _normalize(self, permissions: Mapping[str, bool]) -> dict[str, bool]:
        try:
            return self.normalize_permissions(permissions)
        except ValueError as exc:
            raise AuthorizationError(400, str(exc)) from exc

    def list_groups(self) -> list[dict]:
        return self.repository.list_groups()

    def create_group(self, name: str, permissions: Mapping[str, bool], actor: AuditActor) -> str:
        normalized_name = self._name(name)
        normalized_permissions = self._normalize(permissions)
        group_id = uuid.uuid4().hex
        self.repository.create_group(group_id, normalized_name, normalized_permissions,
                                      self._now(), actor)
        return group_id

    def update_group(self, group_id: str, name: str, permissions: Mapping[str, bool],
                     actor: AuditActor) -> None:
        normalized_name = self._name(name)
        normalized_permissions = self._normalize(permissions)
        # The repository preserves dormant grants inside its write transaction.
        self.repository.update_group(
            group_id, normalized_name, normalized_permissions,
            frozenset(self.known_permission_keys()), self._now(), actor,
        )

    def delete_group(self, group_id: str, actor: AuditActor) -> None:
        self.repository.delete_group(group_id, actor, self._now())

    def add_member(self, group_id: str, user_id: str, actor: AuditActor) -> None:
        self.repository.add_member(group_id, user_id, actor, self._now())

    def remove_member(self, group_id: str, user_id: str, actor: AuditActor) -> None:
        self.repository.remove_member(group_id, user_id, actor, self._now())
