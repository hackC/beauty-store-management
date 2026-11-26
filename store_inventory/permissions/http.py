"""FastAPI adapter. The service is supplied explicitly through its read/check port."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .ports import PermissionChecks


class PermissionCheckIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    keys: list[str] = Field(min_length=1, max_length=40)
    mode: str = Field(default='all', pattern='^(all|any)$')

    @field_validator('keys')
    @classmethod
    def validate_key_lengths(cls, keys: list[str]) -> list[str]:
        if any(not key or len(key) > 96 for key in keys):
            raise ValueError('权限名必须是长度1-96的字符串')
        return keys


def _current_user(request: Request) -> dict:
    user = getattr(request.state, 'user', None)
    if not user:
        raise HTTPException(401, '请先登录或登录状态已过期')
    if user.get('must_set_password'):
        raise HTTPException(403, '首次登录请先设置登录密码')
    return user


def require_permissions(service: PermissionChecks, request: Request,
                        *keys: str, mode: str = 'all') -> None:
    """Guard a data-dependent action using the request's authenticated snapshot."""
    # Preserve the dev.5 guard's 403/ValueError contract.
    result = service.check(getattr(request.state, 'user', None), keys, mode=mode)
    if not result['allowed']:
        raise HTTPException(403, '当前账号没有执行此操作的权限')


def create_permission_router(service: PermissionChecks) -> APIRouter:
    router = APIRouter()

    @router.get('/api/permissions/catalog')
    def permission_catalog(request: Request, response: Response):
        _current_user(request)
        response.headers['Cache-Control'] = 'no-store'
        return service.catalog()

    @router.get('/api/permissions/me')
    def my_permission_snapshot(request: Request, response: Response):
        user = _current_user(request)
        response.headers['Cache-Control'] = 'no-store'
        return service.snapshot(user)

    @router.post('/api/permissions/check')
    def check_my_permissions(data: PermissionCheckIn, request: Request, response: Response):
        user = _current_user(request)
        response.headers['Cache-Control'] = 'no-store'
        return service.check(user, data.keys, mode=data.mode)

    return router

