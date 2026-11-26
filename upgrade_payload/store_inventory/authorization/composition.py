"""Authorization administration composition root."""
from __future__ import annotations

import sqlite3
from typing import Callable

from .service import PermissionGroupService
from .sqlite_groups import SQLitePermissionGroupRepository


def create_permission_group_service(connect: Callable[[], sqlite3.Connection], permission_service,
                                    *, clock=None) -> PermissionGroupService:
    return PermissionGroupService(
        repository=SQLitePermissionGroupRepository(connect),
        normalize_permissions=permission_service.normalize_group_permissions,
        known_permission_keys=lambda: set(permission_service.permission_labels),
        clock=clock,
    )
