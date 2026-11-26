"""SQLite notification writer shared by transactional business modules."""
from __future__ import annotations

import secrets


class SQLiteLowStockNotificationWriter:
    """Write a low-stock event using the caller's transaction connection."""
    def write(self, connection, product_id: str, product_name: str,
              stock: float, threshold: float, created_at: str, date_key: str) -> None:
        connection.execute("""INSERT OR IGNORE INTO app_notifications(
            id,recipient_user_id,kind,title,body,target_url,source_key,created_at
        ) VALUES(?,NULL,'inventory',?,?,?, ?,?)""", (
            secrets.token_hex(16), "库存不足预警",
            f"{product_name} 当前库存 {stock:g}，预警阈值 {threshold:g}",
            "", f"low-stock:{product_id}:{date_key}", created_at,
        ))
