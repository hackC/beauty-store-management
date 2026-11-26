"""Inventory application use cases and stock policy."""
from __future__ import annotations

from typing import Callable, Mapping

from .domain import InventoryError
from .ports import InventoryRepository
from store_inventory.platform.audit import AuditActor


class InventoryService:
    ALLOWED_TYPES = frozenset({"入库", "出库", "盘点调整"})

    def __init__(self, repository: InventoryRepository, backup_guard: Callable[[], None]):
        self.repository = repository
        self.backup_guard = backup_guard

    def stock_for_products(self, product_ids: list[str]) -> dict[str, float]:
        return self.repository.stock_for_products(product_ids)

    def current_stock(self, product_id: str) -> float:
        return float(self.repository.stock_for_products([product_id]).get(product_id, 0))

    def submit_document(self, command: Mapping, actor: AuditActor) -> dict:
        movement_type = str(command.get("type") or "")
        if movement_type not in self.ALLOWED_TYPES:
            raise InventoryError(400, "不支持的业务类型")
        items = list(command.get("items") or [])
        if not items:
            raise InventoryError(400, "请至少添加一条商品明细")
        seen = set()
        normalized_items = []
        for item in items:
            product_id = str(item.get("product_id") or "")
            if product_id in seen:
                raise InventoryError(400, f"商品 {product_id} 重复，请合并为一行")
            seen.add(product_id)
            quantity = float(item.get("quantity", 0))
            if movement_type in {"入库", "出库"} and quantity <= 0:
                raise InventoryError(400, "入库和出库数量必须大于 0")
            if movement_type == "盘点调整" and quantity < 0:
                raise InventoryError(400, "盘点实盘数量不能小于 0")
            normalized_items.append({"product_id": product_id, "quantity": quantity,
                                     "note": str(item.get("note") or "")})
        normalized = {**dict(command), "type": movement_type, "items": normalized_items}
        try:
            self.backup_guard()
        except Exception as exc:
            raise InventoryError(503, "安全备份未成功，已阻止本次库存修改：" + str(exc)) from exc
        return self.repository.submit_document(normalized, actor)

    def list_transactions(self, q: str = "", movement_type: str = "全部",
                          start: str = "", end: str = "", limit: int = 500,
                          offset: int = 0) -> list[dict]:
        return self.repository.list_transactions(q, movement_type, start, end, limit, offset)
