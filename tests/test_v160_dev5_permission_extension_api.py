# -*- coding: utf-8 -*-
"""Permission extension contracts: metadata, session-scoped checks and fail-closed API guard."""
import importlib
import os
from pathlib import Path
import shutil
import sys
import tempfile

import pytest
from fastapi.testclient import TestClient

from application_test_support import unload_test_application


def project_copy():
    source = Path(__file__).resolve().parents[1]
    td = tempfile.TemporaryDirectory(prefix='store_permission_ext_')
    project = Path(td.name) / 'source'
    shutil.copytree(source, project, ignore=shutil.ignore_patterns(
        '__pycache__', '.pytest_cache', '*.pyc', 'release', 'upgrade_payload', 'logs'
    ))
    return td, project


def test_extension_registration_and_session_revocation():
    td, project = project_copy()
    old_cwd = os.getcwd()
    sys.path.insert(0, str(project))
    os.chdir(project)
    sys.modules.pop('main', None)
    sys.modules.pop('permission_extension', None)
    try:
        main = importlib.import_module('main')
        main.init_db()
        keys = main.register_permission_module('loyalty', '会员管理', {
            'view': '查看会员', 'create': '新增会员',
            'delete': '删除会员', 'audit': '管理敏感会员审计'
        }, admin_only=('audit',))
        assert 'ext.loyalty.view' in keys
        main.register_permission_route('loyalty', 'GET',
                                       '/api/extensions/loyalty/records/{record_id}', 'view')
        main.register_permission_route('loyalty', 'POST',
                                       '/api/extensions/loyalty/records', 'create')
        main.register_permission_route('loyalty', 'DELETE',
                                       '/api/extensions/loyalty/records/{record_id}',
                                       ('view', 'delete'), mode='all')

        @main.app.get('/api/extensions/loyalty/records/{record_id}')
        def view_record(record_id: str):
            return {'id': record_id}

        @main.app.post('/api/extensions/loyalty/records')
        def new_record():
            return {'ok': True}

        @main.app.delete('/api/extensions/loyalty/records/{record_id}')
        def delete_record(record_id: str):
            return {'deleted': record_id}

        @main.app.get('/api/extensions/loyalty/unregistered')
        def unregistered_route():
            return {'unsafe': True}

        admin = TestClient(main.app, base_url='https://testserver')
        staff = TestClient(main.app, base_url='https://testserver')
        assert staff.get('/api/permissions/catalog').status_code == 401
        assert staff.post('/api/permissions/check', json={'keys': ['ext.loyalty.view']}).status_code == 401
        assert admin.post('/api/auth/bootstrap', json={
            'username': 'manager', 'display_name': '店长', 'password': 'ManagerPass1234'
        }).status_code == 200
        user = admin.post('/api/users', json={
            'username': 'assistant1', 'display_name': '前台', 'password': 'StaffPass1234'
        })
        assert user.status_code == 200, user.text
        uid = user.json()['id']
        assert staff.post('/api/auth/login', json={
            'username': 'assistant1', 'password': 'StaffPass1234'
        }).status_code == 200

        catalog = staff.get('/api/permissions/catalog')
        assert catalog.status_code == 200
        assert catalog.headers['cache-control'] == 'no-store'
        assert catalog.json()['schema_version'] == 1
        loyalty = next(m for m in catalog.json()['modules'] if m['id'] == 'loyalty')
        assert next(p for p in loyalty['permissions'] if p['action'] == 'audit')['group_assignable'] is False
        assert next(p for p in loyalty['permissions'] if p['action'] == 'view')['group_assignable'] is True

        check = staff.post('/api/permissions/check', json={'keys': [
            'ext.loyalty.view', 'ext.loyalty.create'
        ], 'mode': 'any'})
        assert check.status_code == 200 and check.json()['allowed'] is False
        assert staff.post('/api/permissions/check', json={'keys': [], 'mode': 'all'}).status_code == 422
        assert staff.post('/api/permissions/check', json={'keys': ['ext.loyalty.view'], 'user_id': 'manager'}).status_code == 422
        assert staff.get('/api/extensions/loyalty/records/A-10').status_code == 403
        assert staff.get('/api/extensions/loyalty/unregistered').status_code == 403
        assert staff.post('/api/extensions/loyalty/records').status_code == 403

        bad = admin.post('/api/permission-groups', json={
            'name': '禁止敏感操作', 'permissions': {'ext.loyalty.audit': True}
        })
        assert bad.status_code == 400
        assert admin.post('/api/permission-groups', json={
            'name': '非法权限', 'permissions': {'ext.loyalty.superuser': True}
        }).status_code == 400

        group = admin.post('/api/permission-groups', json={
            'name': '会员查看', 'permissions': {'ext.loyalty.view': True}
        })
        assert group.status_code == 201, group.text
        group_id = group.json()['id']
        assert admin.put(f'/api/permission-groups/{group_id}/members/{uid}').status_code == 200
        # Simulate an installed module that was removed in a later upgrade.
        # Editing another rule must not silently erase its dormant grant.
        con = main.db()
        stored = {'ext.loyalty.view': True, 'ext.retired.view': True}
        con.execute('UPDATE permission_groups SET permissions=? WHERE id=?',
                    (__import__('json').dumps(stored), group_id))
        con.commit()
        con.close()
        assert staff.get('/api/permissions/me').json()['permissions'].get('ext.retired.view') is None
        assert staff.get('/api/extensions/loyalty/records/A-10').status_code == 200
        assert staff.post('/api/extensions/loyalty/records').status_code == 403
        assert staff.delete('/api/extensions/loyalty/records/A-10').status_code == 403
        snap = staff.get('/api/permissions/me')
        assert snap.status_code == 200
        assert snap.headers['cache-control'] == 'no-store'
        assert snap.json()['permissions']['ext.loyalty.view'] is True
        assert snap.json()['permissions']['ext.loyalty.create'] is False
        assert staff.post('/api/permissions/check', json={
            'keys': ['ext.loyalty.view', 'ext.loyalty.create'], 'mode': 'any'
        }).json()['allowed'] is True
        assert staff.post('/api/permissions/check', json={
            'keys': ['ext.loyalty.view', 'ext.loyalty.create'], 'mode': 'all'
        }).json()['allowed'] is False

        update = admin.patch(f'/api/permission-groups/{group_id}', json={
            'name': '会员查看', 'permissions': {'ext.loyalty.view': True,
                                             'ext.loyalty.create': True,
                                             'ext.loyalty.delete': True}
        })
        assert update.status_code == 200, update.text
        grants = next(g for g in admin.get('/api/permission-groups').json() if g['id'] == group_id)['permissions']
        assert grants['ext.retired.view'] is True
        assert staff.post('/api/extensions/loyalty/records').status_code == 200
        assert staff.delete('/api/extensions/loyalty/records/A-10').status_code == 200
        # Already-issued cookies reflect group revocation immediately.
        assert admin.delete(f'/api/permission-groups/{group_id}/members/{uid}').status_code == 200
        assert staff.get('/api/extensions/loyalty/records/A-10').status_code == 403
        assert staff.get('/api/permissions/me').json()['permissions']['ext.loyalty.view'] is False
        assert staff.get('/api/extensions/loyalty/unregistered').status_code == 403
        assert admin.get('/api/extensions/loyalty/unregistered').status_code == 403

        # Unknown actions remain false even for administrator, avoiding accidental grants.
        assert admin.post('/api/permissions/check', json={
            'keys': ['ext.loyalty.superuser']
        }).json()['allowed'] is False
    finally:
        os.chdir(old_cwd)
        sys.path.remove(str(project))
        unload_test_application()
        sys.modules.pop('permission_extension', None)
        td.cleanup()


def test_registration_validation_and_route_policy_contract():
    from permission_extension import PermissionExtensionRegistry
    registry = PermissionExtensionRegistry()
    with pytest.raises(ValueError):
        registry.register_module('admin', '模块', {'View': '查看'})
    with pytest.raises(ValueError):
        registry.register_module('core', '恶意覆盖', {'view': '查看'})
    with pytest.raises(ValueError):
        registry.register_module('a', '模块', {'view': '查看'})
    with pytest.raises(ValueError):
        registry.register_module('future', '模块', {'view': '查看'}, admin_only=('delete',))
    registry.register_module('future', '新网页', {'view': '查看', 'create': '新增'})
    with pytest.raises(ValueError):
        registry.register_module('future', '同名', {'view': '查看'})
    with pytest.raises(ValueError):
        registry.bind_route('future', 'POST', '/api/auth/login', 'view')
    with pytest.raises(ValueError):
        registry.bind_route('future', 'POST', '/api/extensions/future/items', 'missing')
    registry.bind_route('future', 'GET', '/api/extensions/future/items/{item_id}', 'view')
    with pytest.raises(ValueError):
        registry.bind_route('future', 'GET', '/api/extensions/future/items/sample', 'view')
    p = registry.route_policy('GET', '/api/extensions/future/items/123')
    assert p is not None and p.permissions == ('ext.future.view',)
    assert registry.route_policy('DELETE', '/api/extensions/future/items/123') is None
    assert registry.route_policy('GET', '/api/extensions/future/items/123/child') is None


def test_frontend_sdk_and_dynamic_catalog_in_both_pages():
    root = Path(__file__).resolve().parents[1]
    main = (root / 'static/index.html').read_text(encoding='utf-8')
    schedule = (root / 'static/index_V2_5_5.html').read_text(encoding='utf-8')
    sdk = (root / 'static/permission_client.js').read_text(encoding='utf-8')
    assert 'loadPermissionCatalog()' in main
    assert 'data-group-permission' in main
    assert 'permissionModuleTitles' in main
    assert 'groupForbiddenKeys.add(key)' in main
    assert '/api/permissions/catalog' in main
    for html in (main, schedule):
        assert '/static/permission_client.js' in html
    for api in ('catalog()', 'refresh()', 'can(key)', 'canAll(keys)', 'canAny(keys)', 'check(keys, mode'):
        assert api in sdk
