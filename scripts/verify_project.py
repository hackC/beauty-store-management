# -*- coding: utf-8 -*-
"""只读项目预检：不修改源码、配置或数据库。"""
import argparse
import hashlib
import pathlib
import sqlite3
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--database')
    args = parser.parse_args()
    root = pathlib.Path(__file__).resolve().parents[1]
    required = [
        root / 'main.py', root / 'permission_extension.py', root / 'requirements.txt',
        root / 'static' / 'permission_client.js',
        root / 'static' / 'index.html',
        root / 'static' / 'index_V2_5_5.html',
    ]
    required.append(root / 'store_inventory' / '__init__.py')
    required.extend(root / 'store_inventory' / 'runtime' / name for name in (
        '__init__.py', 'health.py', 'lifecycle.py', 'network.py', 'windows_shutdown.py',
    ))
    required.extend(root / 'store_inventory' / 'permissions' / name for name in (
        '__init__.py', 'domain.py', 'ports.py', 'defaults.py', 'legacy_policy.py',
        'service.py', 'registry.py', 'sqlite_groups.py', 'http.py', 'composition.py',
    ))
    required.extend(root / 'store_inventory' / 'identity' / name for name in (
        '__init__.py', 'domain.py', 'ports.py', 'security.py', 'sqlite.py',
        'service.py', 'composition.py',
    ))
    required.extend(root / 'store_inventory' / 'authorization' / name for name in (
        '__init__.py', 'domain.py', 'ports.py', 'sqlite_groups.py',
        'service.py', 'composition.py',
    ))
    required.extend(root / 'store_inventory' / 'catalog' / name for name in (
        '__init__.py', 'domain.py', 'ports.py', 'sqlite.py', 'images.py',
        'service.py', 'composition.py',
    ))
    required.extend(root / 'store_inventory' / 'inventory' / name for name in (
        '__init__.py', 'domain.py', 'ports.py', 'sqlite.py', 'service.py', 'composition.py',
    ))
    required.extend(root / 'store_inventory' / 'procurement' / name for name in (
        '__init__.py', 'ports.py', 'service.py', 'composition.py',
    ))
    required.extend(root / 'store_inventory' / 'platform' / name for name in (
        '__init__.py', 'audit.py', 'notifications.py', 'operations.py',
        'configuration.py', 'reports.py', 'backups.py', 'backup_archive.py', 'backup_restore.py', 'desktop.py', 'settings.py', 'exports.py', 'export_templates.py',
    ))
    required.append(root / 'store_inventory' / 'permissions' / 'migration.py')
    for package, names in {
        'scheduling': ('__init__.py','availability.py','policy.py','permissions.py','rules.py','repository.py','sqlite.py'),
        'customers': ('__init__.py','sqlite.py'), 'waitlist': ('__init__.py','sqlite.py'),
    }.items():
        required.extend(root / 'store_inventory' / package / name for name in names)
    required.extend((root / 'static' / name for name in ('app_shell.css', 'app_shell.js', 'store_network.js', 'schedule_projects.js', 'schedule_display.js', 'schedule_display.css', 'schedule_handoff.js', 'schedule_availability.js', 'schedule_report.js', 'schedule_group.js')))
    missing = [str(p.relative_to(root)) for p in required if not p.is_file()]
    if missing:
        raise SystemExit('缺少必需文件：' + ', '.join(missing))
    for path in required:
        if path.suffix == '.py':
            compile(path.read_text(encoding='utf-8'), str(path), 'exec')
    main_text = (root / 'main.py').read_text(encoding='utf-8')
    for marker in ('/api/schedule/state', '/api/schedule/appointments/transfer', 'ux_products_barcode_nonempty', '/api/reports/operations', 'ensure_self_signed_certificate', '--headless', '/api/schedule/requests'):
        if marker not in main_text:
            raise SystemExit(f'main.py 缺少升级功能标记：{marker}')
    db_path = pathlib.Path(args.database or 'data/inventory.db')
    if not db_path.is_absolute():
        db_path = (root / db_path).resolve()
    if not db_path.is_file():
        if args.database is None:
            print('项目预检通过；未发现运行数据库，首次启动时初始化。')
            return 0
        raise SystemExit(f'数据库不存在：{db_path}')
    con = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True)
    try:
        integrity = con.execute('PRAGMA integrity_check').fetchone()[0]
        if integrity != 'ok':
            raise SystemExit(f'SQLite integrity_check 失败：{integrity}')
        tables = {x[0] for x in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        needed = {'products', 'documents', 'transactions', 'settings', 'meta'}
        if not needed.issubset(tables):
            raise SystemExit('数据库缺少核心表：' + ', '.join(sorted(needed - tables)))
        products = con.execute('SELECT COUNT(*) FROM products').fetchone()[0]
        digest = hashlib.sha256(db_path.read_bytes()).hexdigest()
    finally:
        con.close()
    print(f'项目预检通过；数据库只读完整性检查通过；商品记录数={products}；SHA256={digest}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
