"""Built-in role presets. Applying a preset never alters the account role."""
from store_inventory.scheduling.permissions import MODULES


def group_presets(catalog):
    allowed = {
        permission["key"]
        for module in catalog["modules"]
        if module["id"] in {"core", "catalog", "inventory", "transactions", "procurement", *MODULES}
        for permission in module["permissions"]
        if permission["group_assignable"]
    }
    manager = allowed - {"settings_view", "backup_view"}
    return {
        "manager": sorted(manager),
        "frontdesk": [
            "schedule_view", "ext.appointments.create", "ext.appointments.update",
            "ext.appointments.contact_edit", "customers_view", "ext.customers.create",
            "ext.customers.update", "ext.customers.phone_edit", "waitlist_manage", "notifications_view",
        ],
        "inventory": [
            "dashboard", "operation", "products_view", "products_manage", "transactions_view",
            "purchase_view", "export", "notifications_view",
        ],
        "stylist": [
            "ext.catalog.read", "ext.catalog.image_read", "ext.inventory.read",
            "ext.inventory.outbound_create", "ext.appointments.read_self",
            "ext.appointments.status_change_self_today", "ext.schedule_requests.read_self",
            "ext.schedule_requests.create_self", "ext.scheduling.workload_read_self", "notifications_view",
        ],
        "auditor": ["dashboard", "transactions_view", "reports_view", "audit_view"],
    }
