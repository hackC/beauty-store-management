"""Generate synthetic pre-migration SQLite fixtures at test time."""
import sqlite3
from contextlib import closing


def write_legacy_database(path, count):
    with closing(sqlite3.connect(path)) as con, con:
        con.executescript("""
            CREATE TABLE products (id TEXT PRIMARY KEY, brand TEXT DEFAULT '',
                name TEXT NOT NULL, spec TEXT DEFAULT '', category TEXT DEFAULT '',
                location TEXT DEFAULT '', unit TEXT DEFAULT '', opening REAL NOT NULL DEFAULT 0,
                threshold REAL, status TEXT DEFAULT '正常', active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
        """)
        con.executemany("INSERT INTO products(id,name,created_at,updated_at) VALUES (?,?,'2020-01-01','2020-01-01')",
                        [(f'TEST-{i}', f'迁移测试商品{i}') for i in range(count)])
