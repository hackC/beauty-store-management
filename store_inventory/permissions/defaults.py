"""Legacy permission definitions preserved for compatibility."""

PERMISSION_LABELS = {
    "dashboard": "首页看板",
    "operation": "日常登记",
    "products_view": "查看商品档案",
    "products_manage": "新增、编辑、停用商品",
    "transactions_view": "查看库存流水",
    "purchase_view": "查看采购管理",
    "settings_view": "查看基础设置",
    "settings_manage": "修改系统设置",
    "backup_view": "查看和下载备份",
    "backup_manage": "创建数据库备份",
    "backup_restore": "恢复数据库",
    "export": "导出 Excel",
    "schedule_view": "查看每日排班",
    "schedule_manage": "管理全部排班",
    "schedule_self_manage": "修改本人当天排班",
    "schedule_approve": "审批员工休息与换班申请",
    "notifications_view": "查看统一消息中心",
    "audit_view": "查看操作审计日志",
    "devices_view": "查看在线设备",
    "customers_view": "查看客户档案",
    "customers_manage": "管理客户档案",
    "waitlist_manage": "管理候补预约",
    "reports_view": "查看经营报表",
}


DEFAULT_STAFF_PERMISSIONS = {
    "dashboard": True,
    "operation": True,
    "products_view": True,
    "products_manage": False,
    "transactions_view": True,
    "purchase_view": True,
    "settings_view": False,
    "settings_manage": False,
    "backup_view": False,
    "backup_manage": False,
    "backup_restore": False,
    "export": False,
    "schedule_view": True,
    "schedule_manage": False,
    "schedule_self_manage": False,
    "schedule_approve": False,
    "notifications_view": True,
    "audit_view": False,
    "devices_view": False,
    "customers_view": True,
    "customers_manage": False,
    "waitlist_manage": False,
    "reports_view": False,
}


GROUP_FORBIDDEN_PERMISSIONS = frozenset({
    "backup_restore", "backup_manage", "settings_manage",
})

