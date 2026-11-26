"""Compatibility imports for dev.5 extensions.

Use an injected ModulePermissions interface for new modules. REGISTRY is retained
for callers constructing standalone registries; the application owns its instance.
"""
from store_inventory.permissions.domain import PermissionInfo, RoutePolicy
from store_inventory.permissions.registry import PermissionExtensionRegistry

REGISTRY = PermissionExtensionRegistry()
