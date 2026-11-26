"""Framework-independent permission values."""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Mapping

@dataclass(frozen=True)
class PermissionInfo:
    key: str
    module: str
    module_label: str
    action: str
    label: str
    group_assignable: bool


@dataclass(frozen=True)
class RoutePolicy:
    method: str
    template: str
    matcher: re.Pattern
    permissions: tuple[str, ...]
    mode: str


@dataclass(frozen=True)
class GroupGrant:
    id: str
    permissions: Mapping[str, bool]


@dataclass(frozen=True)
class AccessDecision:
    allowed: bool
    detail: str = ''
