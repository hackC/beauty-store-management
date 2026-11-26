"""Replaceable password hashing and session-token primitives."""
from __future__ import annotations

import hashlib
import hmac
import secrets


class PBKDF2PasswordHasher:
    """Compatibility hasher matching the existing PBKDF2-SHA256 storage format."""
    def __init__(self, iterations: int = 310_000):
        self.iterations = iterations

    def hash(self, password: str) -> tuple[str, str]:
        salt = secrets.token_hex(16)
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt), self.iterations
        ).hex()
        return salt, digest

    def verify(self, password: str, salt: str, password_hash: str) -> bool:
        try:
            calculated = hashlib.pbkdf2_hmac(
                "sha256", password.encode("utf-8"), bytes.fromhex(salt), self.iterations
            ).hex()
        except (TypeError, ValueError):
            return False
        return hmac.compare_digest(calculated, str(password_hash or ""))


class SecureSessionTokenFactory:
    def create(self) -> str:
        return secrets.token_urlsafe(48)

    def digest(self, token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()
