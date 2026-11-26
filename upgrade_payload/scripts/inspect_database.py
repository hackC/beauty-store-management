# -*- coding: utf-8 -*-
"""以 SQLite mode=ro 打印数据库结构和计数，不输出商品/账号明文。"""
import argparse, hashlib, pathlib, sqlite3

parser = argparse.ArgumentParser()
parser.add_argument('database', nargs='?', default='data/inventory.db')
args = parser.parse_args()
p = pathlib.Path(args.database).resolve()
if not p.is_file():
    raise SystemExit(f'数据库不存在：{p}')
con = sqlite3.connect(f'file:{p}?mode=ro', uri=True)
try:
    print('file:', p)
    print('bytes:', p.stat().st_size)
    print('sha256:', hashlib.sha256(p.read_bytes()).hexdigest())
    print('integrity_check:', con.execute('PRAGMA integrity_check').fetchone()[0])
    for (name,) in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"):
        count = con.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
        print(f'{name}: {count}')
finally:
    con.close()
