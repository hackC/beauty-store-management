"""Procurement read-model ports."""
from typing import Protocol


class CatalogPurchaseReadPort(Protocol):
    def list_products(self, search: str = "", status: str = "全部", category: str = "全部") -> list[dict]: ...
