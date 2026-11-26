"""Framework-independent authorization administration values."""
from __future__ import annotations

from dataclasses import dataclass


class AuthorizationError(Exception):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


@dataclass(frozen=True)
class AuditActor:
    user_id: str = ""
    display_name: str = ""
    ip_address: str = ""
