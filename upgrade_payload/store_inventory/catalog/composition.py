"""Catalog dependency composition."""
from pathlib import Path
from typing import Callable
import sqlite3

from .images import FileProductImageStore, PillowImageProcessor
from .service import CatalogService
from .sqlite import SQLiteProductRepository


def create_catalog_service(connect: Callable[[], sqlite3.Connection], stock_reader,
                          image_directory: Path, default_threshold: Callable[[], float], *, clock=None):
    return CatalogService(
        repository=SQLiteProductRepository(connect),
        stock_reader=stock_reader,
        image_processor=PillowImageProcessor(),
        image_store=FileProductImageStore(image_directory),
        default_threshold=default_threshold,
        clock=clock,
    )
