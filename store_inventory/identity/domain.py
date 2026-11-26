"""Framework-independent identity values and errors."""
from __future__ import annotations

from dataclasses import dataclass


class IdentityError(Exception):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


@dataclass(frozen=True)
class AuditActor:
    user_id: str = ""
    display_name: str = ""
    ip_address: str = ""


@dataclass(frozen=True)
class LoginResult:
    token: str
    must_set_password: bool
