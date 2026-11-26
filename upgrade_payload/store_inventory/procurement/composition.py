"""Procurement composition."""
from .service import ProcurementService


def create_procurement_service(catalog):
    return ProcurementService(catalog)
