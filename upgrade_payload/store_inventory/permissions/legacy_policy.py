"""Legacy route permission mapping; preserves the existing any-of semantics.

New modules register their own policies through the extension port.
"""

from store_inventory.scheduling.permissions import STATE_WRITE_KEYS

def legacy_required_permissions(method: str, path: str):
    method = method.upper()

    if path.startswith("/api/users") or path.startswith("/api/permission-groups"):
        return ("__admin__",)

    if path in ("/api/permissions/catalog", "/api/permissions/me", "/api/permissions/check"):
        return ("__authenticated__",)

    if path == "/api/summary":
        return ("dashboard",)

    if path == "/api/system/identity":
        return ("dashboard", "products_view", "products_manage", "operation", "schedule_view", "schedule_manage", "schedule_self_manage", "ext.scheduling.read", "ext.appointments.read", "ext.appointments.read_self")

    if path == "/api/products":
        if method == "GET":
            return ("products_view", "operation", "products_manage", "ext.catalog.read")
        # The handler resolves create vs update from the real persisted product state.
        return ("products_manage", "ext.catalog.create", "ext.catalog.update")

    if path == "/api/product-options":
        return ("products_view", "operation", "products_manage", "ext.catalog.read")

    if path.startswith("/api/products/"):
        if method == "GET" and path.endswith("/image"):
            return ("products_view", "products_manage", "operation", "purchase_view", "ext.catalog.image_read")
        if method == "POST" and path.endswith("/image"):
            return ("products_manage", "ext.catalog.image_update")
        if method == "DELETE":
            return ("products_manage", "ext.catalog.disable")
        return ("products_manage",)

    if path == "/api/submit":
        return ("operation", "ext.inventory.inbound_create", "ext.inventory.outbound_create", "ext.inventory.stocktake_adjust")

    if path == "/api/transactions":
        return ("transactions_view", "ext.transactions.read")

    if path == "/api/purchase":
        return ("purchase_view", "ext.procurement.read")

    if path == "/api/settings":
        if method == "GET":
            return ("settings_view", "settings_manage")
        return ("settings_manage",)

    if path == "/api/backup":
        if method == "GET":
            return ("backup_view", "backup_manage", "backup_restore")
        return ("backup_manage",)

    if path.startswith("/api/backup/download/"):
        return ("backup_view", "backup_manage", "backup_restore")

    if path == "/api/restore":
        return ("backup_restore",)

    if path.startswith("/api/export/"):
        return ("export", "ext.catalog.export", "ext.transactions.export", "ext.procurement.export")

    if path.startswith("/api/schedule/staff-links"):
        return ("__admin__",)

    if path == "/api/schedule/state":
        if method == "GET":
            return ("schedule_view", "schedule_manage", "schedule_self_manage", "ext.scheduling.read", "ext.appointments.read", "ext.appointments.read_self")
        return ("schedule_manage", "schedule_self_manage", *STATE_WRITE_KEYS)

    if path.startswith("/api/notifications"):
        return ("notifications_view",)

    if path.startswith("/api/devices"):
        if method == "POST":
            return ("dashboard", "schedule_view", "products_view", "operation", "ext.catalog.read", "ext.scheduling.read", "ext.appointments.read", "ext.appointments.read_self")
        return ("devices_view",)

    if path.startswith("/api/audit"):
        return ("audit_view",)

    if path.startswith("/api/inventory/alerts"):
        return ("products_view", "operation", "products_manage", "ext.inventory.read")

    if path.startswith("/api/reports"):
        return ("reports_view",)

    if path.startswith("/api/customers"):
        if method == "GET":
            return ("customers_view", "customers_manage", "ext.customers.read")
        if method == 'DELETE':
            return ("customers_manage", "ext.customers.delete")
        return ("customers_manage", "ext.customers.create", "ext.customers.update")

    if path.startswith("/api/schedule/waitlist"):
        if method == "GET":
            return ("waitlist_manage", "schedule_manage", "ext.waitlist.read")
        if method == 'PATCH':
            return ("waitlist_manage", "schedule_manage", "ext.waitlist.status_change")
        return ("waitlist_manage", "schedule_manage", "ext.waitlist.create")

    if path == "/api/schedule/workload":
        return ("schedule_view", "schedule_manage", "schedule_self_manage", "ext.scheduling.workload_read_all", "ext.scheduling.workload_read_self")

    if path.startswith("/api/schedule/requests"):
        if method == "GET":
            return ("schedule_view", "schedule_manage", "schedule_approve", "schedule_self_manage", "ext.schedule_requests.read_all", "ext.schedule_requests.read_self")
        if method == "PATCH":
            return ("schedule_approve", "schedule_manage", "ext.schedule_requests.review")
        return ("schedule_self_manage", "schedule_manage", "ext.schedule_requests.create_self")

    if path.startswith("/api/schedule/transfers"):
        if method == "GET":
            return ("schedule_view", "schedule_manage", "schedule_self_manage", "ext.appointments.read")
        return ("schedule_manage", "ext.appointments.transfer")

    if path == "/api/schedule/appointments/transfer":
        return ("schedule_manage", "ext.appointments.transfer")

    if method == 'PATCH' and path.startswith('/api/schedule/appointments/') and path.endswith('/status'):
        return ('ext.appointments.status_change', 'ext.appointments.status_change_self_today')

    if path == "/api/schedule/migrate":
        return ("schedule_manage", "ext.scheduling.initialize")

    return ()

