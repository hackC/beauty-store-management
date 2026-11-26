# -*- coding: utf-8 -*-
"""安全准备 release 运行目录：仅在目标缺失时复制，不覆盖现有数据库/配置。"""
import argparse, pathlib, shutil

parser = argparse.ArgumentParser()
parser.add_argument('--project-dir', default=str(pathlib.Path(__file__).resolve().parents[1]))
parser.add_argument('--no-seed', action='store_true')
args = parser.parse_args()
root = pathlib.Path(args.project_dir).resolve()
release = root / 'release'
(release / 'data' / 'backups').mkdir(parents=True, exist_ok=True)
(release / 'data' / 'purchase').mkdir(parents=True, exist_ok=True)
(release / 'logs').mkdir(parents=True, exist_ok=True)
source_db = root / 'data' / 'inventory.db'
target_db = release / 'data' / 'inventory.db'
if args.no_seed:
    print("运行数据库由服务器首次启动初始化，保留现有数据库。")
elif not target_db.exists() and source_db.exists():
    shutil.copy2(source_db, target_db)
    print('已在目标缺失时复制初始数据库到 release/data/inventory.db')
else:
    print('保留现有 release 数据库；未覆盖。')
    if source_db.exists():
        import hashlib
        h1 = hashlib.sha256(source_db.read_bytes()).hexdigest()
        h2 = hashlib.sha256(target_db.read_bytes()).hexdigest()
        if h1 != h2:
            print('警告：根目录数据候选库与 release 运行库不同；本脚本继续使用 release 运行库，不做合并。')
source_config = root / 'config.example.json'
target_config = release / 'data' / 'config.json'
if not target_config.exists() and source_config.exists():
    shutil.copy2(source_config, target_config)
    print('已创建 release/data/config.json（仅因目标不存在）')
else:
    print('保留现有 release 配置；未覆盖。')
print('release 目录准备完成。')
