"""Catalog domain errors and actor-independent product values."""
from __future__ import annotations


class CatalogError(Exception):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message
