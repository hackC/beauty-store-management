"""Identity dependency composition at the application boundary."""
from __future__ import annotations

import sqlite3
from typing import Callable

from .security import PBKDF2PasswordHasher, SecureSessionTokenFactory
from .service import IdentityService
from .sqlite import SQLiteIdentityRepository


def create_identity_service(connect: Callable[[], sqlite3.Connection], permissions,
                            *, hasher=None, tokens=None, clock=None) -> IdentityService:
    return IdentityService(
        repository=SQLiteIdentityRepository(connect),
        hasher=hasher or PBKDF2PasswordHasher(),
        tokens=tokens or SecureSessionTokenFactory(),
        permissions=permissions,
        clock=clock,
    )
