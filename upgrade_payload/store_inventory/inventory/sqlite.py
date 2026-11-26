"""SQLite adapter for stock calculations, inventory documents and movement history."""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
from datetime import datetime, date
from typing import Callable, Mapping

from .domain import InventoryError
from store_inventory.platform.audit import AuditActor, SQLiteAuditWriter


class SQLiteInventoryRepository:
    def __init__(self, connect: Callable[[], sqlite3.Connection],
                 default_threshold: Callable[[], float], audit: SQLiteAuditWriter | None = None,
                 notifications=None):
        from store_inventory.platform.notifications import SQLiteLowStockNotificationWriter
        self._connect = connect
        self._default_threshold = default_threshold
        self._audit = audit or SQLiteAuditWriter()
        self._notifications = notifications or SQLiteLowStockNotificationWriter()

    def stock_for_products(self, product_ids: list[str]) -> dict[str, float]:
        if not product_ids:
            return {}
        placeholders = ",".join("?" for _ in product_ids)
        con = self._connect()
        try:
            rows = con.execute(f"""
                SELECT p.id, COALESCE(p.opening,0) + COALESCE(
                    SUM(COALESCE(t.qty_in,0)-COALESCE(t.qty_out,0)+COALESCE(t.qty_adjust,0)),0
                ) AS stock
                FROM products p LEFT JOIN transactions t ON t.product_id=p.id
                WHERE p.id IN ({placeholders}) GROUP BY p.id,p.opening
            """, product_ids).fetchall()
            return {str(row["id"]): float(row["stock"] or 0) for row in rows}
        finally:
            con.close()

    def current_stock(self, product_id: str) -> float:
        values = self.stock_for_products([product_id])
        return float(values.get(product_id, 0))

    def submit_document(self, command: Mapping, actor: AuditActor) -> dict:
        movement_type = str(command["type"])
        items = list(command["items"])
        now = datetime.now().isoformat(timespec="seconds")
        prefix = {"入库": "RK", "出库": "CK", "盘点调整": "PD"}[movement_type]
        stamp = datetime.now()
        doc_id = f"{prefix}-{stamp:%Y%m%d}-{stamp:%H%M%S}-{secrets.token_hex(2).upper()}"
        doc_no = str(command.get("external_no") or "").strip() or doc_id
        operator = actor.display_name
        con = self._connect()
        try:
            con.execute("BEGIN IMMEDIATE")
            request_id = str(command.get("request_id") or "").strip()
            fingerprint = hashlib.sha256(json.dumps({
                "actor": actor.user_id, "type": movement_type,
                "external_no": command.get("external_no", ""), "note": command.get("note", ""),
                "items": sorted(items, key=lambda item: item["product_id"]),
            }, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
            if request_id:
                prior = con.execute("SELECT fingerprint, response FROM inventory_receipts WHERE request_id=?",
                                    (request_id,)).fetchone()
                if prior:
                    if prior["fingerprint"] != fingerprint:
                        raise InventoryError(409, "该请求编号已用于其他业务明细，请核对旧单据")
                    con.rollback()
                    return {**json.loads(prior["response"]), "duplicate": True}
                if con.execute("SELECT 1 FROM documents WHERE request_id=?", (request_id,)).fetchone():
                    raise InventoryError(409, "该编号属于已处理的旧单据，请核对库存流水")

            for item in items:
                product = con.execute(
                    "SELECT * FROM products WHERE id=? AND active=1", (item["product_id"],)
                ).fetchone()
                if not product:
                    raise InventoryError(400, f"商品 {item['product_id']} 不存在或已停用")
                stock = self._current_stock_in_transaction(con, item["product_id"])
                if movement_type == "出库" and float(item["quantity"]) > stock:
                    raise InventoryError(400, f"{product['name']} 库存不足，当前库存 {stock}")

            con.execute("""INSERT INTO documents(
                id,type,external_no,operator,note,created_at,request_id
            ) VALUES(?,?,?,?,?,?,?)""", (
                doc_id, movement_type, command.get("external_no", ""), operator,
                command.get("note", ""), now, request_id or None,
            ))

            for item in items:
                product = con.execute("SELECT * FROM products WHERE id=?", (item["product_id"],)).fetchone()
                stock = self._current_stock_in_transaction(con, item["product_id"])
                quantity = float(item["quantity"])
                qty_in = qty_out = qty_adjust = 0.0
                actual_count = None
                note = str(item.get("note") or "")
                if movement_type == "入库":
                    qty_in = quantity
                elif movement_type == "出库":
                    qty_out = quantity
                else:
                    actual_count = quantity
                    qty_adjust = quantity - stock
                    if qty_adjust == 0:
                        continue
                    label = "盘盈" if qty_adjust > 0 else "盘亏"
                    note = (f"{label}(实盘:{quantity}) " + note).strip()
                transaction_id = "LS" + datetime.now().strftime("%y%m%d%H%M%S") + secrets.token_hex(4).upper()
                con.execute("""INSERT INTO transactions(
                    id,doc_id,time,type,doc_no,product_id,product_name,
                    qty_in,qty_out,qty_adjust,operator,note,actual_count
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                    transaction_id, doc_id, now, movement_type, doc_no,
                    item["product_id"], product["name"], qty_in, qty_out,
                    qty_adjust, operator, note, actual_count,
                ))

            count = con.execute("SELECT COUNT(*) FROM transactions WHERE doc_id=?", (doc_id,)).fetchone()[0]
            if count == 0:
                con.execute("DELETE FROM documents WHERE id=?", (doc_id,))
                return self._commit_receipt(con, request_id, fingerprint, {
                    "ok": True, "unchanged": True, "message": "实盘数量与系统库存一致，无需调整",
                })

            detail = f"{doc_id} | 经办人：{operator} | 账号：{actor.username}"
            self._audit.write(con, "提交单据", detail, actor, "inventory_document", doc_id,
                              None, {"type": movement_type, "items": items}, now)

            for item in items:
                product_row = con.execute("SELECT * FROM products WHERE id=?", (item["product_id"],)).fetchone()
                stock_now = self._current_stock_in_transaction(con, item["product_id"])
                threshold = float(product_row["threshold"] if product_row["threshold"] is not None
                                  else self._default_threshold())
                if stock_now <= threshold:
                    self._notifications.write(
                        con, item["product_id"], product_row["name"], stock_now,
                        threshold, now, date.today().isoformat(),
                    )
            con.execute("UPDATE meta SET value=CAST(value AS INTEGER)+1 WHERE key='revision'")
            revision = int(con.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0])
            return self._commit_receipt(con, request_id, fingerprint, {
                "ok": True, "doc_id": doc_id, "doc_no": doc_no, "revision": revision,
            })
        except InventoryError:
            con.rollback()
            raise
        except sqlite3.IntegrityError as exc:
            con.rollback()
            if "documents.request_id" in str(exc):
                raise InventoryError(409, "本次库存单据请求已处理，请刷新后查看流水") from exc
            raise
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    @staticmethod
    def _commit_receipt(con, request_id, fingerprint, result):
        if request_id:
            con.execute("INSERT INTO inventory_receipts(request_id, fingerprint, response) VALUES (?,?,?)",
                        (request_id, fingerprint, json.dumps(result, ensure_ascii=False)))
        con.commit()
        return result

    @staticmethod
    def _current_stock_in_transaction(con, product_id: str) -> float:
        product = con.execute("SELECT opening FROM products WHERE id=?", (product_id,)).fetchone()
        if not product:
            return 0.0
        movements = con.execute("""SELECT COALESCE(SUM(qty_in-qty_out+qty_adjust),0) AS total
            FROM transactions WHERE product_id=?""", (product_id,)).fetchone()["total"]
        return float(product["opening"] or 0) + float(movements or 0)

    def list_transactions(self, q: str = "", movement_type: str = "全部",
                          start: str = "", end: str = "", limit: int = 500,
                          offset: int = 0) -> list[dict]:
        sql = """SELECT t.*,COALESCE(p.image_filename,'') AS image_filename
                 FROM transactions t LEFT JOIN products p ON p.id=t.product_id WHERE 1=1"""
        args = []
        if q:
            sql += " AND (t.product_id LIKE ? OR t.product_name LIKE ? OR t.doc_no LIKE ? OR t.operator LIKE ?)"
            args.extend([f"%{q}%"] * 4)
        if movement_type != "全部":
            sql += " AND t.type=?"
            args.append(movement_type)
        if start:
            sql += " AND substr(t.time,1,10)>=?"
            args.append(start)
        if end:
            sql += " AND substr(t.time,1,10)<=?"
            args.append(end)
        sql += " ORDER BY t.time DESC LIMIT ? OFFSET ?"
        args.extend([min(max(int(limit), 1), 2000), max(int(offset), 0)])
        con = self._connect()
        try:
            return [dict(row) for row in con.execute(sql, args).fetchall()]
        finally:
            con.close()
