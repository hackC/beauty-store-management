"""Trusted, additive authorization extension points for StoreInventory.

No HTTP endpoint may register permissions or API policies. A future server-side
module registers its permission catalog and route mappings at application startup.
Unmapped /api/extensions/* endpoints fail closed in the main auth middleware.
"""
from __future__ import annotations

import re
from threading import RLock
from typing import Mapping

from .domain import PermissionInfo, RoutePolicy

_NAMESPACE = re.compile(r"^[a-z][a-z0-9_]{1,39}$")
_ACTION = re.compile(r"^[a-z][a-z0-9_]{1,39}$")
_ALLOWED_METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"})


class PermissionExtensionRegistry:
    def __init__(self):
        self._lock = RLock()
        self._modules: dict[str, dict] = {}
        self._rights: dict[str, PermissionInfo] = {}
        self._routes: dict[tuple[str, str], RoutePolicy] = {}

    def register_module(self, namespace: str, label: str,
                        actions: Mapping[str, str], *,
                        admin_only: tuple[str, ...] = ()) -> tuple[str, ...]:
        """Register an extensible permission namespace from trusted server code.

        Example: register_module('loyalty', '会员管理',
                  {'view':'查看会员','create':'新增会员','delete':'删除会员'})
        """
        if not isinstance(namespace, str) or not _NAMESPACE.fullmatch(namespace):
            raise ValueError('模块名必须是小写字母/数字/下划线，以字母开头且长度 2~40')
        if namespace == 'core':
            raise ValueError('core 为现有门店权限的保留命名空间')
        if not isinstance(label, str) or not label.strip() or len(label.strip()) > 60:
            raise ValueError('模块展示名称不能为空，最长60字符')
        if not isinstance(actions, Mapping) or not actions:
            raise ValueError('模块至少需要一个权限动作')
        if len(actions) > 50:
            raise ValueError('单模块最多50个权限动作')
        restricted = set(admin_only)
        if restricted - set(actions):
            raise ValueError('管理员专属动作必须首先在 actions 中声明')
        pending: dict[str, PermissionInfo] = {}
        for action, title in actions.items():
            if not isinstance(action, str) or not _ACTION.fullmatch(action):
                raise ValueError('权限动作必须为小写标识符（2~40字符）')
            if not isinstance(title, str) or not title.strip() or len(title.strip()) > 80:
                raise ValueError('权限动作显示名称不能为空，最长80字符')
            key = f'ext.{namespace}.{action}'
            pending[key] = PermissionInfo(key, namespace, label.strip(),
                                          action, title.strip(), action not in restricted)
        with self._lock:
            if namespace in self._modules:
                raise ValueError(f'模块已注册：{namespace}')
            self._modules[namespace] = {'id': namespace, 'label': label.strip()}
            self._rights.update(pending)
        return tuple(pending)

    def permission_info(self, key: str) -> PermissionInfo | None:
        with self._lock:
            return self._rights.get(key)

    def all_permissions(self) -> tuple[PermissionInfo, ...]:
        with self._lock:
            return tuple(self._rights.values())

    def bind_route(self, namespace: str, method: str, path_template: str,
                   actions: tuple[str, ...] | list[str] | str, *, mode: str = 'all'):
        """Protect one future API route (all/any actions), default-deny elsewhere.

        Only paths under /api/extensions/<namespace>/ may be bound. The handler
        itself must still be registered with FastAPI separately.
        """
        if isinstance(actions, str):
            actions = (actions,)
        method = method.upper()
        if method not in _ALLOWED_METHODS or mode not in {'all', 'any'}:
            raise ValueError('无效的HTTP方法或权限校验模式')
        if not isinstance(path_template, str) or not path_template.startswith(f'/api/extensions/{namespace}/'):
            raise ValueError('扩展接口路径必须属于注册模块的 /api/extensions/<namespace>/ 前缀')
        if '?' in path_template or '#' in path_template or '//' in path_template:
            raise ValueError('扩展接口路径不合法')
        if not actions or len(set(actions)) != len(actions):
            raise ValueError('必须声明非重复的权限动作')
        key_tuple = tuple(f'ext.{namespace}.{action}' for action in actions)
        # Support FastAPI's {identifier} path placeholders, but not greedy paths.
        tokens = path_template.strip('/').split('/')
        regex_parts = []
        for part in tokens:
            if part.startswith('{') and part.endswith('}') and _ACTION.fullmatch(part[1:-1]):
                regex_parts.append(r'[^/]+')
            elif re.fullmatch(r'[a-zA-Z0-9_.-]+', part):
                regex_parts.append(re.escape(part))
            else:
                raise ValueError('扩展接口路径只能包含字母数字、点、下划线、短横线和 {参数}')
        matcher = re.compile(r'^/' + '/'.join(regex_parts) + r'/?$')
        with self._lock:
            if any(key not in self._rights for key in key_tuple):
                raise ValueError('引用了尚未注册的模块或权限动作')
            route_key = (method, path_template)
            if route_key in self._routes:
                raise ValueError('此路由已经配置权限策略')
            # Reject ambiguous static/parameter routes; otherwise the first
            # registered policy could accidentally protect a stricter endpoint.
            for existing in self._routes.values():
                if existing.method != method:
                    continue
                previous = existing.template.strip('/').split('/')
                if len(tokens) == len(previous) and all(
                    left == right or left.startswith('{') or right.startswith('{')
                    for left, right in zip(tokens, previous)
                ):
                    raise ValueError('扩展接口权限策略与已有路由匹配范围重叠')
            self._routes[route_key] = RoutePolicy(method, path_template, matcher,
                                                  key_tuple, mode)

    def route_policy(self, method: str, path: str) -> RoutePolicy | None:
        with self._lock:
            candidates = tuple(self._routes.values())
        for route in candidates:
            if route.method == method.upper() and route.matcher.fullmatch(path):
                return route
        return None

    def module_catalog(self) -> list[dict]:
        with self._lock:
            modules = dict(self._modules)
            rights = tuple(self._rights.values())
        return [{**module, 'source': 'extension',
                 'permissions': [{'key': item.key, 'label': item.label,
                                  'action': item.action, 'group_assignable': item.group_assignable}
                                 for item in rights if item.module == namespace]}
                for namespace, module in modules.items()]

