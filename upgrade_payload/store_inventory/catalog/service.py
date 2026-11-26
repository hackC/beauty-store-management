"""Catalog application use cases."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Callable, Mapping

from .domain import CatalogError
from .ports import InventoryStockReader, ProductImageStore, ProductRepository, ImageProcessor
from store_inventory.platform.audit import AuditActor


class CatalogService:
    def __init__(self, repository: ProductRepository, stock_reader: InventoryStockReader,
                 image_processor: ImageProcessor, image_store: ProductImageStore,
                 default_threshold: Callable[[], float], clock=None):
        self.repository = repository
        self.stock_reader = stock_reader
        self.image_processor = image_processor
        self.image_store = image_store
        self.default_threshold = default_threshold
        self.clock = clock or datetime.now

    def list_products(self, search: str = "", status: str = "全部", category: str = "全部") -> list[dict]:
        rows = self.repository.list_active_products(search, category)
        stock_map = self.stock_reader.stock_for_products([str(row["id"]) for row in rows])
        default = float(self.default_threshold())
        result = []
        for source in rows:
            row = dict(source)
            row["stock"] = round(float(stock_map.get(str(row["id"]), 0)), 3)
            row["effective_threshold"] = default if row.get("threshold") in (None, 0, 0.0) else float(row["threshold"])
            row["status"] = "补货" if row["stock"] <= row["effective_threshold"] else "正常"
            if not status or status == "全部" or row["status"] == status:
                result.append(row)
        return result

    def product_options(self) -> dict:
        return self.repository.product_options()

    def save_product(self, product: Mapping, actor: AuditActor,
                     can_create: bool = True, can_update: bool = True) -> str:
        if not str(product.get("name") or "").strip():
            raise CatalogError(400, "商品名称不能为空")
        return self.repository.save_product(
            product, actor, self.clock().isoformat(timespec="seconds"),
            can_create, can_update,
        )

    def disable_product(self, product_id: str, actor: AuditActor) -> None:
        self.repository.disable_product(
            product_id, actor, self.clock().isoformat(timespec="seconds")
        )

    def upload_image(self, product_id: str, data: bytes, content_type: str,
                     actor: AuditActor) -> str:
        processed = self.image_processor.process(data, content_type)
        filename = uuid.uuid4().hex + ".jpg"
        self.image_store.save(filename, processed)
        try:
            self.repository.set_image_filename(
                product_id, filename, actor, self.clock().isoformat(timespec="seconds")
            )
        except Exception:
            self.image_store.delete(filename)
            raise
        return filename

    def image_path(self, product_id: str):
        filename = self.repository.image_filename(product_id)
        if not filename:
            raise CatalogError(404, "商品暂无图片")
        path = self.image_store.path(filename)
        if not path.is_file():
            raise CatalogError(404, "商品图片文件不存在")
        return path
