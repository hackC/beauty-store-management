"""Versioned additive mapping from legacy permissions to business actions."""

from store_inventory.scheduling.permissions import LEGACY_SCHEDULE_ACTIONS

LEGACY_TO_INVENTORY_ACTIONS = {
    # The old products_manage permission covered all product writes and image replacement.
    "products_manage": (
        "ext.catalog.read", "ext.catalog.create", "ext.catalog.update",
        "ext.catalog.disable", "ext.catalog.image_read", "ext.catalog.image_update",
    ),
    "products_view": (
        "ext.catalog.read", "ext.catalog.image_read", "ext.inventory.read",
    ),
    # operation covered inbound, outbound and stocktake submission, plus product lookup.
    "operation": (
        "ext.catalog.read", "ext.catalog.image_read", "ext.inventory.read",
        "ext.inventory.inbound_create", "ext.inventory.outbound_create",
        "ext.inventory.stocktake_adjust",
    ),
    "transactions_view": ("ext.transactions.read",),
    # Purchase users could read purchase suggestions and retrieve product photos, not the full catalog.
    "purchase_view": ("ext.procurement.read", "ext.catalog.image_read"),
    # Legacy export was shared across these three existing export kinds.
    "export": (
        "ext.catalog.export", "ext.transactions.export", "ext.procurement.export",
    ),
}


LEGACY_ACTION_ALIASES = {**LEGACY_TO_INVENTORY_ACTIONS, **LEGACY_SCHEDULE_ACTIONS}


def expand_legacy_permissions(permissions):
    expanded = dict(permissions or {})
    for legacy_key, action_keys in LEGACY_ACTION_ALIASES.items():
        if expanded.get(legacy_key):
            for action_key in action_keys:
                expanded[action_key] = True
    return expanded
