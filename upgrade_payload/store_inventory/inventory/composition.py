"""Inventory dependency composition."""
from typing import Callable
import sqlite3

from .service import InventoryService
from .sqlite import SQLiteInventoryRepository


def create_inventory_service(connect: Callable[[], sqlite3.Connection],
                             backup_guard: Callable[[], None],
                             default_threshold: Callable[[], float]):
    return InventoryService(
        repository=SQLiteInventoryRepository(connect, default_threshold),
        backup_guard=backup_guard,
    )
