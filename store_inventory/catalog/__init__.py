"""Product catalog module."""
from .composition import create_catalog_service
from .domain import CatalogError

__all__ = ["create_catalog_service", "CatalogError"]
