"""SQLite adapter for product master data. It does not write inventory movement tables."""
from __future__ import annotations

import sqlite3
from typing import Callable, Mapping

from .domain import CatalogError
from store_inventory.platform.audit import AuditActor, SQLiteAuditWriter


class SQLiteProductRepository:
    def __init__(self, connect: Callable[[], sqlite3.Connection], audit: SQLiteAuditWriter | None = None):
        self._connect = connect
        self._audit = audit or SQLiteAuditWriter()

    def list_active_products(self, search: str = "", category: str = "全部") -> list[dict]:
        sql = "SELECT * FROM products WHERE active=1"
        args = []
        if search:
            sql += r""" AND (
                id LIKE ? ESCAPE '\' OR name LIKE ? ESCAPE '\' OR brand LIKE ? ESCAPE '\'
                OR spec LIKE ? ESCAPE '\' OR location LIKE ? ESCAPE '\' OR barcode LIKE ? ESCAPE '\'
            )"""
            literal = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            args.extend([f"%{literal}%"] * 6)
        if category and category != "全部":
            sql += " AND category=?"
            args.append(category)
        sql += " ORDER BY id"
        con = self._connect()
        try:
            return [dict(row) for row in con.execute(sql, args).fetchall()]
        finally:
            con.close()

    def product_options(self) -> dict:
        fields = {"brands": "brand", "specs": "spec", "categories": "category",
                  "locations": "location", "units": "unit"}
        con = self._connect()
        try:
            result = {}
            for key, column in fields.items():
                rows = con.execute(
                    f"""SELECT DISTINCT TRIM({column}) AS value FROM products
                        WHERE active=1 AND TRIM(COALESCE({column},''))<>''
                        ORDER BY value COLLATE NOCASE"""
                ).fetchall()
                result[key] = [row["value"] for row in rows]
            return result
        finally:
            con.close()

    def save_product(self, product: Mapping, actor: AuditActor, now: str,
                     can_create: bool, can_update: bool) -> str:
        con = self._connect()
        try:
            con.execute("BEGIN IMMEDIATE")
            product_id = str(product.get("id") or "").strip()
            if not product_id:
                number = 1
                while con.execute("SELECT 1 FROM products WHERE id=?", (f"SYS-{number:03d}",)).fetchone():
                    number += 1
                product_id = f"SYS-{number:03d}"
            name = str(product.get("name") or "").strip()
            if not name:
                raise CatalogError(400, "商品名称不能为空")
            before = con.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone()
            if before and not can_update:
                raise CatalogError(403, "当前账号没有编辑商品权限")
            if not before and not can_create:
                raise CatalogError(403, "当前账号没有新增商品权限")
            if before:
                con.execute("""UPDATE products SET brand=?,barcode=?,name=?,spec=?,category=?,
                    location=?,unit=?,threshold=?,active=?,updated_at=? WHERE id=?""", (
                    str(product.get("brand") or "").strip(), product.get("barcode"), name,
                    str(product.get("spec") or "").strip(), str(product.get("category") or "").strip(),
                    str(product.get("location") or "").strip(), str(product.get("unit") or "").strip(),
                    product.get("threshold"), 1 if product.get("active", True) else 0, now, product_id,
                ))
                action = "编辑商品"
            else:
                con.execute("""INSERT INTO products(
                    id,brand,barcode,name,spec,category,location,unit,opening,threshold,status,
                    active,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                    product_id, str(product.get("brand") or "").strip(), product.get("barcode"), name,
                    str(product.get("spec") or "").strip(), str(product.get("category") or "").strip(),
                    str(product.get("location") or "").strip(), str(product.get("unit") or "").strip(),
                    product.get("opening", 0), product.get("threshold"), "正常",
                    1 if product.get("active", True) else 0, now, now,
                ))
                action = "新增商品"
            after = con.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone()
            self._audit.write(con, action, product_id, actor, "product", product_id,
                              dict(before) if before else None, dict(after) if after else None, now)
            con.commit()
            return product_id
        except CatalogError:
            con.rollback()
            raise
        except sqlite3.IntegrityError as exc:
            con.rollback()
            if "products.barcode" in str(exc):
                raise CatalogError(409, "条形码已被其他商品使用") from exc
            raise CatalogError(409, "商品 ID 重复或数据关联冲突") from exc
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    def disable_product(self, product_id: str, actor: AuditActor, now: str) -> None:
        con = self._connect()
        try:
            con.execute("BEGIN IMMEDIATE")
            before = con.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone()
            if not before:
                raise CatalogError(404, "商品不存在")
            used = con.execute("SELECT 1 FROM transactions WHERE product_id=? LIMIT 1", (product_id,)).fetchone()
            if used:
                con.execute("UPDATE products SET active=0,updated_at=? WHERE id=?", (now, product_id))
            else:
                con.execute("DELETE FROM products WHERE id=?", (product_id,))
            after = con.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone()
            self._audit.write(con, "停用/删除商品", product_id, actor, "product", product_id,
                              dict(before), dict(after) if after else None, now)
            con.commit()
        except CatalogError:
            con.rollback()
            raise
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    def image_filename(self, product_id: str) -> str | None:
        con = self._connect()
        try:
            row = con.execute("SELECT image_filename FROM products WHERE id=?", (product_id,)).fetchone()
            return row["image_filename"] if row else None
        finally:
            con.close()

    def set_image_filename(self, product_id: str, filename: str,
                           actor: AuditActor, now: str) -> None:
        con = self._connect()
        try:
            con.execute("BEGIN IMMEDIATE")
            old = con.execute("SELECT image_filename FROM products WHERE id=? AND active=1", (product_id,)).fetchone()
            if not old:
                raise CatalogError(404, "商品不存在或已停用")
            con.execute("UPDATE products SET image_filename=?,updated_at=? WHERE id=?",
                        (filename, now, product_id))
            self._audit.write(con, "修改商品图片", product_id, actor, "product", product_id,
                              {"image_filename": old["image_filename"]},
                              {"image_filename": filename}, now)
            con.commit()
        except CatalogError:
            con.rollback()
            raise
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()
