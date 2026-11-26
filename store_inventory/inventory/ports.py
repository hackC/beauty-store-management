"""Inventory repository and backup guard ports."""
from __future__ import annotations

from typing import Mapping, Protocol
from store_inventory.platform.audit import AuditActor


class InventoryRepository(Protocol):
    def stock_for_products(self, product_ids: list[str]) -> dict[str, float]: ...
    def submit_document(self, command: Mapping, actor: AuditActor) -> dict: ...
    def list_transactions(self, q: str = "", movement_type: str = "全部",
                          start: str = "", end: str = "", limit: int = 500,
                          offset: int = 0) -> list[dict]: ...


class DailyBackupGuard(Protocol):
    def ensure(self) -> None: ...


class TransactionNotificationWriter(Protocol):
    def write(self, connection, product_id: str, product_name: str,
              stock: float, threshold: float, created_at: str, date_key: str) -> None: ...
