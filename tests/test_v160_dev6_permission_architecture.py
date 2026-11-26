"""Behavioral and dependency-boundary regressions for the permission module."""
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from store_inventory.permissions.composition import create_permission_service
from store_inventory.permissions.defaults import GROUP_FORBIDDEN_PERMISSIONS, PERMISSION_LABELS
from store_inventory.permissions.domain import GroupGrant
from store_inventory.permissions.http import create_permission_router
from store_inventory.permissions.registry import PermissionExtensionRegistry
from store_inventory.permissions.service import PermissionService
from store_inventory.permissions.sqlite_groups import SQLiteGroupGrantRepository


class MemoryGroups:
    def __init__(self):
        self.grants = ()

    def for_user(self, user_id):
        return self.grants if user_id == 'staff-1' else ()


def make_service(groups=None):
    return PermissionService(PermissionExtensionRegistry(), groups or MemoryGroups(),
                             PERMISSION_LABELS, GROUP_FORBIDDEN_PERMISSIONS)


def test_core_imports_without_web_database_or_desktop_dependencies(tmp_path):
    root = Path(__file__).resolve().parents[1]
    code = f"""
import sys
sys.path.insert(0, {str(root)!r})
from store_inventory.permissions.service import PermissionService
from store_inventory.permissions.registry import PermissionExtensionRegistry
assert not {{'main', 'fastapi', 'pydantic', 'sqlite3', 'tkinter'}}.intersection(sys.modules)
"""
    subprocess.run([sys.executable, '-I', '-S', '-c', code], cwd=tmp_path, check=True)
    assert list(tmp_path.iterdir()) == []


def test_repository_substitution_union_and_revocation_without_database():
    groups = MemoryGroups()
    service = make_service(groups)
    service.register_module('loyalty', '会员', {'view': '查看', 'create': '新增', 'audit': '审计'},
                            admin_only=('audit',))
    groups.grants = (
        GroupGrant('group-a', {'products_view': True, 'ext.loyalty.view': True}),
        GroupGrant('group-b', {'products_view': False, 'ext.loyalty.create': True,
                              'backup_restore': True, 'ext.loyalty.audit': True,
                              'ext.retired.view': True}),
    )
    rights, ids = service.group_rights('staff-1')
    assert ids == ['group-a', 'group-b']
    assert rights == {'products_view': True, 'ext.loyalty.view': True, 'ext.loyalty.create': True}
    assert service.group_rights('different-user') == ({}, [])
    groups.grants = ()
    assert service.group_rights('staff-1') == ({}, [])


def test_independent_services_have_no_shared_registration_or_database_side_effects():
    def must_not_connect():
        raise AssertionError('Creating a service must not open a database')
    first = create_permission_service(must_not_connect)
    second = create_permission_service(must_not_connect)
    first.register_module('loyalty', '会员', {'view': '查看'})
    first.register_route('loyalty', 'GET', '/api/extensions/loyalty/items', 'view')
    assert 'ext.loyalty.view' not in second.permission_labels
    assert len(second.catalog()['modules']) == 1
    assert second.registry.route_policy('GET', '/api/extensions/loyalty/items') is None
    second.register_module('loyalty', '另一实例', {'view': '查看'})
    assert second.catalog()['modules'][-1]['label'] == '另一实例'
    assert first.catalog()['modules'][-1]['label'] == '会员'


@pytest.mark.parametrize('reverse', [False, True])
@pytest.mark.parametrize('paths', [
    ('/items/{item_id}/edit', '/items/fixed/{action_id}'),
    ('/items/{item_id}', '/items/fixed'),
    ('/items/{item_id}', '/items/{other_id}/'),
])
def test_overlapping_policies_rejected_in_either_order(reverse, paths):
    registry = PermissionExtensionRegistry()
    registry.register_module('loyalty', '会员', {'view': '查看', 'delete': '删除'})
    ordered = paths[::-1] if reverse else paths
    registry.bind_route('loyalty', 'GET', '/api/extensions/loyalty' + ordered[0], 'view')
    with pytest.raises(ValueError, match='重叠'):
        registry.bind_route('loyalty', 'GET', '/api/extensions/loyalty' + ordered[1], 'delete')
    assert registry.permission_info('ext.loyalty.delete') is not None


def test_distinct_routes_and_methods_remain_usable():
    registry = PermissionExtensionRegistry()
    registry.register_module('loyalty', '会员', {'view': '查看', 'delete': '删除'})
    registry.bind_route('loyalty', 'GET', '/api/extensions/loyalty/items/{item_id}/edit', 'view')
    registry.bind_route('loyalty', 'GET', '/api/extensions/loyalty/items/fixed/view', 'view')
    registry.bind_route('loyalty', 'DELETE', '/api/extensions/loyalty/items/fixed/{action_id}', 'delete')
    assert registry.route_policy('GET', '/api/extensions/loyalty/items/fixed/view') is not None
    assert registry.route_policy('DELETE', '/api/extensions/loyalty/items/fixed/edit').permissions == ('ext.loyalty.delete',)
    assert registry.route_policy('POST', '/api/extensions/loyalty/items/fixed/edit') is None


@pytest.mark.parametrize('mode,expected', [('all', False), ('any', True)])
def test_route_decisions_enforce_combination_unknown_and_admin_boundaries(mode, expected):
    service = make_service()
    service.register_module('loyalty', '会员', {'view': '查看', 'create': '新增', 'audit': '审计'},
                            admin_only=('audit',))
    path = '/api/extensions/loyalty/records'
    service.register_route('loyalty', 'POST', path, ('view', 'create'), mode=mode)
    staff = {'role': 'staff', 'permissions': {'ext.loyalty.view': True, 'ext.loyalty.audit': True}}
    admin = {'role': 'admin'}
    assert service.authorize_route(staff, 'POST', path).allowed is expected
    assert service.authorize_route(admin, 'POST', path).allowed
    assert not service.authorize_route(admin, 'DELETE', path).allowed
    assert not service.authorize_route(staff, 'GET', '/api/users').allowed
    assert not service.authorize_route(staff, 'GET', '/api/unmapped').allowed
    assert not service.granted(staff, 'ext.loyalty.audit')
    assert service.granted(admin, 'ext.loyalty.audit')
    assert not service.granted(admin, 'ext.loyalty.unknown')
    assert service.authorize_route(staff, 'GET', '/api/permissions/me').allowed
    assert not service.snapshot(staff)['permissions']['ext.loyalty.audit']


def test_legacy_route_any_semantics_and_sensitive_group_rules():
    service = make_service()
    staff = {'role': 'staff', 'permissions': {'operation': True, 'schedule_self_manage': True}}
    for method, path, allowed in [
        ('GET', '/api/products', True), ('POST', '/api/products', False),
        ('GET', '/api/product-options', True), ('POST', '/api/submit', True),
        ('GET', '/api/schedule/state', True), ('PUT', '/api/schedule/state', True),
        ('POST', '/api/schedule/appointments/transfer', False),
        ('GET', '/api/permission-groups', False), ('POST', '/api/backup', False),
    ]:
        assert service.authorize_route(staff, method, path).allowed is allowed
    for key in GROUP_FORBIDDEN_PERMISSIONS:
        with pytest.raises(ValueError, match='敏感权限'):
            service.normalize_group_permissions({key: True})
        assert service.normalize_group_permissions({key: False}) == {key: False}
    with pytest.raises(ValueError, match='未知权限'):
        service.normalize_group_permissions({'ext.retired.view': True})


def test_sqlite_adapter_reads_fresh_and_handles_invalid_stored_json(tmp_path):
    path = tmp_path / 'groups.db'
    con = sqlite3.connect(path)
    con.executescript('''
        CREATE TABLE permission_groups(id TEXT, name TEXT, permissions TEXT);
        CREATE TABLE permission_group_members(group_id TEXT, user_id TEXT);
    ''')
    values = ['{"products_view":true}', '[]', 'null', '"string"', '{invalid', '7']
    for i, value in enumerate(values):
        con.execute('INSERT INTO permission_groups VALUES(?,?,?)', (str(i), str(i), value))
        con.execute('INSERT INTO permission_group_members VALUES(?,?)', (str(i), 'staff-1'))
    con.commit()
    adapter = SQLiteGroupGrantRepository(lambda: sqlite3.connect(path))
    groups = adapter.for_user('staff-1')
    assert groups[0].permissions == {'products_view': True}
    assert all(group.permissions == {} for group in groups[1:])
    con.execute('DELETE FROM permission_group_members WHERE user_id=?', ('staff-1',))
    con.commit()
    con.close()
    assert adapter.for_user('staff-1') == ()


def test_sqlite_adapter_closes_connection_on_failure():
    class BrokenConnection:
        closed = False
        def execute(self, *args):
            raise sqlite3.OperationalError('read failure')
        def close(self):
            self.closed = True
    connection = BrokenConnection()
    adapter = SQLiteGroupGrantRepository(lambda: connection)
    with pytest.raises(sqlite3.OperationalError):
        adapter.for_user('staff-1')
    assert connection.closed


def test_router_injection_isolated_instances_and_defensive_authentication():
    first = make_service()
    first.register_module('loyalty', '会员', {'view': '查看'})
    second = make_service()

    def application(service, user):
        app = FastAPI()
        @app.middleware('http')
        async def inject_user(request, call_next):
            if user is not None:
                request.state.user = user
            return await call_next(request)
        app.include_router(create_permission_router(service))
        return TestClient(app)

    admin = {'role': 'admin', 'group_ids': ['group-1']}
    first_client = application(first, admin)
    second_client = application(second, admin)
    payload = {'keys': ['ext.loyalty.view']}
    assert first_client.post('/api/permissions/check', json=payload).json()['allowed']
    assert not second_client.post('/api/permissions/check', json=payload).json()['allowed']
    snapshot = first_client.get('/api/permissions/me')
    assert snapshot.json()['group_ids'] == ['group-1']
    assert snapshot.headers['cache-control'] == 'no-store'
    assert first_client.get('/api/permissions/catalog').headers['cache-control'] == 'no-store'
    assert first_client.post('/api/permissions/check', json={**payload, 'user_id': 'other'}).status_code == 422
    assert first_client.post('/api/permissions/check', json={'keys': []}).status_code == 422
    assert first_client.post('/api/permissions/check', json={**payload, 'mode': 'wrong'}).status_code == 422
    for user, expected in [(None, 401), ({'role': 'admin', 'must_set_password': True}, 403)]:
        client = application(first, user)
        assert client.get('/api/permissions/catalog').status_code == expected
        assert client.get('/api/permissions/me').status_code == expected
        assert client.post('/api/permissions/check', json=payload).status_code == expected


def test_trusted_module_example_installs_without_importing_main():
    from examples.loyalty_module import install
    service = make_service()
    app = FastAPI()
    user = {'role': 'staff', 'permissions': {}}
    @app.middleware('http')
    async def authenticated_host(request, call_next):
        request.state.user = user
        return await call_next(request)
    install(app, service)
    client = TestClient(app)
    assert client.get('/api/extensions/loyalty/records').status_code == 403
    user['permissions']['ext.loyalty.view'] = True
    result = client.get('/api/extensions/loyalty/records')
    assert result.status_code == 200 and result.json() == {'items': [], 'example': True}
    user['permissions'].clear()
    assert client.get('/api/extensions/loyalty/records').status_code == 403
