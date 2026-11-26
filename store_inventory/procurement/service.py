"""Procurement use cases limited to the existing low-stock suggestion list."""
from .ports import CatalogPurchaseReadPort


class ProcurementService:
    def __init__(self, catalog: CatalogPurchaseReadPort):
        self.catalog = catalog

    def purchase_suggestions(self) -> list[dict]:
        return self.catalog.list_products(status="补货")
