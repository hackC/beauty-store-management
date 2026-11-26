"""Trusted installation example. Not automatically mounted in production.

The composition root calls install(app, PERMISSION_SERVICE) during startup.
The example deliberately has no database writes or invented member data.
"""
from fastapi import APIRouter, FastAPI, Request

from store_inventory.permissions.http import require_permissions
from store_inventory.permissions.ports import ModulePermissions


def install(app: FastAPI, permissions: ModulePermissions) -> None:
    permissions.register_module('loyalty', '会员管理示例', {
        'view': '查看会员', 'create': '新增会员',
    })
    permissions.register_route('loyalty', 'GET', '/api/extensions/loyalty/records', 'view')
    router = APIRouter()

    @router.get('/api/extensions/loyalty/records')
    def records(request: Request):
        require_permissions(permissions, request, 'ext.loyalty.view')
        return {'items': [], 'example': True}

    app.include_router(router)
