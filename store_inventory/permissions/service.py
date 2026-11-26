"""Application policy. Dependencies are ports, not SQLite/FastAPI/main."""
from __future__ import annotations

from typing import Mapping

from .domain import AccessDecision
from .legacy_policy import legacy_required_permissions
from .ports import GroupGrantRepository, PermissionRegistry
from .migration import LEGACY_ACTION_ALIASES, expand_legacy_permissions


class PermissionService:
    def __init__(self, registry: PermissionRegistry, groups: GroupGrantRepository,
                 legacy_labels: Mapping[str, str], group_forbidden: frozenset[str]):
        self.registry = registry
        self._groups = groups
        self._legacy_labels = dict(legacy_labels)
        self._group_forbidden = frozenset(group_forbidden)
        # Compatibility view for main.PERMISSION_LABELS; owned by this instance.
        self.permission_labels = dict(legacy_labels)
        self.permission_labels.update((p.key, p.label) for p in registry.all_permissions())

    def register_module(self, namespace: str, label: str, actions: Mapping[str, str],
                        *, admin_only: tuple[str, ...] = ()) -> tuple[str, ...]:
        keys = self.registry.register_module(namespace, label, actions, admin_only=admin_only)
        for key in keys:
            self.permission_labels[key] = self.registry.permission_info(key).label
        return keys

    def register_route(self, namespace: str, method: str, path: str,
                       actions: tuple[str, ...] | list[str] | str, *, mode: str = 'all') -> None:
        self.registry.bind_route(namespace, method, path, actions, mode=mode)

    def _known(self, key: str) -> bool:
        return key in self._legacy_labels or self.registry.permission_info(key) is not None

    def _group_assignable(self, key: str) -> bool:
        info = self.registry.permission_info(key)
        return self._known(key) and key not in self._group_forbidden and (
            info is None or info.group_assignable
        )

    def granted(self, user: Mapping | None, key: str) -> bool:
        if not user or not self._known(key):
            return False
        info = self.registry.permission_info(key)
        if info and not info.group_assignable and user.get('role') != 'admin':
            return False
        return user.get('role') == 'admin' or bool((user.get('permissions') or {}).get(key))

    def expand_legacy_permissions(self, permissions: Mapping[str, bool]) -> dict[str, bool]:
        """Expose additive action grants for existing users/groups during staged migration."""
        return expand_legacy_permissions(permissions)

    def group_rights(self, user_id: str) -> tuple[dict[str, bool], list[str]]:
        groups = self._groups.for_user(user_id)
        rights = {
            key: True for group in groups for key, granted in group.permissions.items()
            if granted and self._group_assignable(key)
        }
        return rights, [group.id for group in groups]

    def normalize_group_permissions(self, permissions: Mapping[str, bool]) -> dict[str, bool]:
        unknown = {key for key in permissions if not self._known(key)}
        if unknown:
            raise ValueError('规则包含未知权限：' + ', '.join(sorted(unknown)))
        if any(granted and not self._group_assignable(key) for key, granted in permissions.items()):
            raise ValueError('包含不能通过普通权限组授予的敏感权限')
        return {key: bool(value) for key, value in permissions.items()}

    def catalog(self) -> dict:
        legacy = [{'key': key, 'label': label, 'action': 'legacy',
                   'group_assignable': key not in self._group_forbidden}
                  for key, label in self._legacy_labels.items()]
        aliases = {
            key: actions for key, actions in LEGACY_ACTION_ALIASES.items()
            if self._group_assignable(key) and all(self._group_assignable(action) for action in actions)
        }
        return {'schema_version': 1, 'legacy_aliases': aliases,
                'modules': [{'id': 'core', 'label': '门店系统现有功能',
                             'source': 'core', 'permissions': legacy}]
                           + self.registry.module_catalog()}

    def snapshot(self, user: Mapping) -> dict:
        keys = (*self._legacy_labels, *(info.key for info in self.registry.all_permissions()))
        return {'schema_version': 1, 'role': user['role'],
                'permissions': {key: self.granted(user, key) for key in keys},
                'group_ids': list(user.get('group_ids', []))}

    def check(self, user: Mapping | None, keys: tuple[str, ...] | list[str],
              *, mode: str = 'all') -> dict:
        if not keys or mode not in {'all', 'any'}:
            raise ValueError('权限校验必须声明至少一个权限及 all/any 模式')
        results = {key: self.granted(user, key) for key in keys}
        allowed = all(results.values()) if mode == 'all' else any(results.values())
        return {'schema_version': 1, 'allowed': allowed, 'mode': mode, 'results': results}

    def required_permissions(self, method: str, path: str) -> tuple[str, ...]:
        if path.startswith('/api/extensions/'):
            policy = self.registry.route_policy(method, path)
            return policy.permissions if policy else ()
        return legacy_required_permissions(method, path)

    def authorize_route(self, user: Mapping, method: str, path: str) -> AccessDecision:
        """Authorize an authenticated user after first-password/session checks."""
        extension = path.startswith('/api/extensions/')
        policy = self.registry.route_policy(method, path) if extension else None
        required = policy.permissions if policy else self.required_permissions(method, path)
        if extension and not required:
            return AccessDecision(False, '扩展接口尚未注册授权策略')
        # Preserve legacy admin access; extension routes still need a policy.
        if user.get('role') == 'admin' or '__authenticated__' in required:
            return AccessDecision(True)
        if '__admin__' in required:
            return AccessDecision(False, '只有管理员可以执行此操作')
        if not required:
            return AccessDecision(False, '接口没有配置访问权限')
        mode = policy.mode if policy else 'any'
        if not self.check(user, required, mode=mode)['allowed']:
            return AccessDecision(False, '当前账号没有此功能权限')
        return AccessDecision(True)

