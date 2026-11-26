"""Wire concrete adapters at the application boundary; no global service state."""
from __future__ import annotations

import sqlite3
from typing import Callable

from .defaults import GROUP_FORBIDDEN_PERMISSIONS, PERMISSION_LABELS
from .registry import PermissionExtensionRegistry
from .service import PermissionService
from .sqlite_groups import SQLiteGroupGrantRepository


def create_permission_service(connect: Callable[[], sqlite3.Connection]) -> PermissionService:
    return PermissionService(
        registry=PermissionExtensionRegistry(),
        groups=SQLiteGroupGrantRepository(connect),
        legacy_labels=PERMISSION_LABELS,
        group_forbidden=GROUP_FORBIDDEN_PERMISSIONS,
    )

